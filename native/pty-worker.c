#define _DARWIN_C_SOURCE
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/time.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <termios.h>
#include <unistd.h>
#include <util.h>

#define QUEUE_CAPACITY (512 * 1024)
#define FRAME_CAPACITY (65536 + 5)

typedef struct {
    unsigned char bytes[QUEUE_CAPACITY];
    size_t start;
    size_t length;
} Queue;

static volatile sig_atomic_t stopping = 0;
static void stop_signal(int signal_number) { (void)signal_number; stopping = 1; }

static void nonblocking(int fd) {
    int flags = fcntl(fd, F_GETFL);
    if (flags < 0 || fcntl(fd, F_SETFL, flags | O_NONBLOCK) < 0) {
        perror("pty-worker fcntl");
        exit(1);
    }
}

static void append(Queue *queue, const void *bytes, size_t length) {
    size_t end = (queue->start + queue->length) % QUEUE_CAPACITY;
    size_t first = length < QUEUE_CAPACITY - end ? length : QUEUE_CAPACITY - end;
    memcpy(queue->bytes + end, bytes, first);
    memcpy(queue->bytes, (const unsigned char *)bytes + first, length - first);
    queue->length += length;
}

static int flush_queue(int fd, Queue *queue) {
    if (!queue->length) return 0;
    size_t contiguous = QUEUE_CAPACITY - queue->start;
    if (contiguous > queue->length) contiguous = queue->length;
    ssize_t count = write(fd, queue->bytes + queue->start, contiguous);
    if (count > 0) {
        queue->start = (queue->start + (size_t)count) % QUEUE_CAPACITY;
        queue->length -= (size_t)count;
        return 0;
    }
    if (count < 0 && errno != EAGAIN && errno != EINTR) return -1;
    return 0;
}

static void sane_terminal(struct termios *settings) {
    memset(settings, 0, sizeof(*settings));
    settings->c_iflag = BRKINT | ICRNL | IXON | IMAXBEL | IUTF8;
    settings->c_oflag = OPOST | ONLCR;
    settings->c_cflag = CREAD | CS8 | HUPCL;
    settings->c_lflag = ISIG | ICANON | ECHO | ECHOE | ECHOK | ECHOCTL | ECHOKE;
    settings->c_cc[VINTR] = 3;
    settings->c_cc[VQUIT] = 28;
    settings->c_cc[VERASE] = 127;
    settings->c_cc[VKILL] = 21;
    settings->c_cc[VEOF] = 4;
    settings->c_cc[VSTART] = 17;
    settings->c_cc[VSTOP] = 19;
    settings->c_cc[VSUSP] = 26;
    settings->c_cc[VWERASE] = 23;
    settings->c_cc[VREPRINT] = 18;
    settings->c_cc[VLNEXT] = 22;
    settings->c_cc[VDISCARD] = 15;
    settings->c_cc[VMIN] = 1;
    cfsetispeed(settings, B38400);
    cfsetospeed(settings, B38400);
}

static int apply_frames(unsigned char *frames, size_t *length, Queue *input, int master) {
    size_t used = 0;
    while (*length - used >= 5) {
        uint32_t payload_length;
        memcpy(&payload_length, frames + used + 1, 4);
        payload_length = ntohl(payload_length);
        if (payload_length > FRAME_CAPACITY - 5) return -1;
        if (*length - used < (size_t)payload_length + 5) break;
        unsigned char kind = frames[used];
        const unsigned char *payload = frames + used + 5;
        if (kind == 1) {
            if (payload_length > QUEUE_CAPACITY - input->length) break;
            append(input, payload, payload_length);
        } else if (kind == 2 && payload_length == 4) {
            uint16_t columns, rows;
            memcpy(&columns, payload, 2);
            memcpy(&rows, payload + 2, 2);
            columns = ntohs(columns);
            rows = ntohs(rows);
            if (columns < 2 || columns > 1000 || rows < 2 || rows > 1000) return -1;
            struct winsize size = {.ws_col = columns, .ws_row = rows};
            if (ioctl(master, TIOCSWINSZ, &size) < 0 && errno != EIO) return -1;
        } else if (kind == 3 && payload_length == 0) {
            stopping = 1;
        } else {
            return -1;
        }
        used += (size_t)payload_length + 5;
    }
    if (used) {
        memmove(frames, frames + used, *length - used);
        *length -= used;
    }
    return 0;
}

static int terminate_owned_session(pid_t child, int master, int already_reaped, int status) {
    if (already_reaped) {
        close(master);
        return status;
    }
    // Both groups are obtained from this worker's controlling PTY/session.
    // A foreground job may have its own process group distinct from the shell.
    pid_t foreground = tcgetpgrp(master);
    if (foreground > 1 && getsid(foreground) == child) kill(-foreground, SIGHUP);
    if (getsid(child) == child) kill(-child, SIGHUP);
    close(master);
    for (int attempt = 0; attempt < 40; attempt++) {
        pid_t result = waitpid(child, &status, WNOHANG);
        if (result == child || (result < 0 && errno == ECHILD)) return status;
        usleep(25000);
    }
    if (foreground > 1 && getsid(foreground) == child) kill(-foreground, SIGKILL);
    if (getsid(child) == child) kill(-child, SIGKILL);
    while (waitpid(child, &status, 0) < 0 && errno == EINTR) {}
    return status;
}

int main(int argc, char **argv) {
    const char *cwd = NULL;
    unsigned columns = 100, rows = 30;
    int index = 1;
    while (index < argc && strcmp(argv[index], "--")) {
        if (!strcmp(argv[index], "--cwd") && index + 1 < argc) cwd = argv[++index];
        else if (!strcmp(argv[index], "--cols") && index + 1 < argc) columns = (unsigned)atoi(argv[++index]);
        else if (!strcmp(argv[index], "--rows") && index + 1 < argc) rows = (unsigned)atoi(argv[++index]);
        else { fprintf(stderr, "pty-worker: invalid arguments\n"); return 64; }
        index++;
    }
    if (++index >= argc || argv[index][0] != '/' || !cwd || columns < 2 || columns > 1000 || rows < 2 || rows > 1000) {
        fprintf(stderr, "pty-worker: expected --cwd DIR -- /absolute/program [args]\n");
        return 64;
    }
    struct termios settings;
    sane_terminal(&settings);
    struct winsize size = {.ws_col = (uint16_t)columns, .ws_row = (uint16_t)rows};
    int master;
    pid_t child = forkpty(&master, NULL, &settings, &size);
    if (child < 0) { perror("pty-worker forkpty"); return 1; }
    if (!child) {
        if (chdir(cwd) < 0) { perror("pty-worker chdir"); _exit(126); }
        execv(argv[index], argv + index);
        perror("pty-worker exec");
        _exit(127);
    }
    signal(SIGPIPE, SIG_IGN);
    signal(SIGTERM, stop_signal);
    signal(SIGINT, stop_signal);
    signal(SIGHUP, stop_signal);
    nonblocking(master);
    nonblocking(STDIN_FILENO);
    nonblocking(STDOUT_FILENO);
    Queue *input = calloc(1, sizeof(Queue));
    Queue *output = calloc(1, sizeof(Queue));
    if (!input || !output) {
        terminate_owned_session(child, master, 0, 0);
        return 1;
    }
    unsigned char frames[FRAME_CAPACITY], transfer[32768];
    size_t frame_length = 0;
    int master_eof = 0, reaped = 0, status = 0, failed = 0;
    while (!stopping) {
        if (!reaped) {
            pid_t result = waitpid(child, &status, WNOHANG);
            if (result == child) reaped = 1;
        }
        if (apply_frames(frames, &frame_length, input, master) < 0) {
            fprintf(stderr, "pty-worker: malformed input frame\n");
            failed = 1;
            break;
        }
        if (master_eof && !output->length) break;
        short master_events = (!master_eof && output->length < QUEUE_CAPACITY ? POLLIN : 0) | (input->length ? POLLOUT : 0);
        struct pollfd fds[] = {
            {STDIN_FILENO, frame_length < FRAME_CAPACITY && input->length < QUEUE_CAPACITY ? POLLIN : 0, 0},
            {master_events ? master : -1, master_events, 0},
            {STDOUT_FILENO, output->length ? POLLOUT : 0, 0}
        };
        if (poll(fds, 3, 100) < 0) {
            if (errno == EINTR) continue;
            failed = 1;
            break;
        }
        if (fds[0].revents & (POLLIN | POLLHUP)) {
            ssize_t count = read(STDIN_FILENO, frames + frame_length, FRAME_CAPACITY - frame_length);
            if (count > 0) frame_length += (size_t)count;
            else if (!count) stopping = 1;
            else if (errno != EAGAIN && errno != EINTR) stopping = 1;
        }
        if (fds[1].revents & POLLOUT) {
            if (flush_queue(master, input) < 0) input->length = 0;
        }
        if (!master_eof && (fds[1].revents & (POLLIN | POLLHUP | POLLERR)) && output->length < QUEUE_CAPACITY) {
            size_t available = QUEUE_CAPACITY - output->length;
            if (available > sizeof(transfer)) available = sizeof(transfer);
            ssize_t count = read(master, transfer, available);
            if (count > 0) append(output, transfer, (size_t)count);
            else if (!count || (count < 0 && errno == EIO)) master_eof = 1;
            else if (errno != EAGAIN && errno != EINTR) master_eof = 1;
        }
        if ((fds[2].revents & POLLOUT) && flush_queue(STDOUT_FILENO, output) < 0) stopping = 1;
        if (fds[2].revents & (POLLERR | POLLHUP | POLLNVAL)) stopping = 1;
    }
    status = terminate_owned_session(child, master, reaped, status);
    free(input);
    free(output);
    if (failed) return 64;
    if (WIFEXITED(status)) return WEXITSTATUS(status);
    if (WIFSIGNALED(status)) return 128 + WTERMSIG(status);
    return 0;
}
