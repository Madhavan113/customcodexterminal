// Transport-only check. It never creates NSApplication, opens a window, or
// launches a shell. Include the actual host to test its framing/backpressure.
#import <AppKit/AppKit.h>
#include <assert.h>
#include <arpa/inet.h>
static int rejectedFrames = 0;
static void recordRejectedFrame(void) { rejectedFrames++; }
#define NSBeep recordRejectedFrame
#define main dragon_application_main
#include "DragonTerminal.m"
#undef main
#undef NSBeep

static void drain(int descriptor, NSMutableData *capture) {
    unsigned char bytes[65536];
    ssize_t count;
    while ((count = read(descriptor, bytes, sizeof(bytes))) > 0) {
        if (capture) [capture appendBytes:bytes length:(NSUInteger)count];
    }
    assert(count < 0 && errno == EAGAIN);
}

int main(void) {
    @autoreleasepool {
        DTPTY *transport = [DTPTY new];
        int writer = transport.inputPipe.fileHandleForWriting.fileDescriptor;
        int reader = transport.inputPipe.fileHandleForReading.fileDescriptor;
        fcntl(writer, F_SETFL, fcntl(writer, F_GETFL) | O_NONBLOCK);
        fcntl(reader, F_SETFL, fcntl(reader, F_GETFL) | O_NONBLOCK);
        unsigned char filler[4096] = {0};
        while (write(writer, filler, sizeof(filler)) > 0) {}
        assert(errno == EAGAIN);

        NSData *payload = [NSMutableData dataWithLength:65536];
        for (int index = 0; index < 16; index++) [transport queueFrame:1 payload:payload];
        assert(rejectedFrames == 1);
        assert(transport.inputBuffer.length == 15 * (65536 + 5));
        for (int columns = 2; columns <= 1000; columns++) {
            unsigned char size[] = {(unsigned char)(columns >> 8), (unsigned char)columns, 0, 99};
            [transport queueFrame:2 payload:[NSData dataWithBytes:size length:4]];
        }
        assert(transport.inputBuffer.length == 15 * (65536 + 5));
        assert(transport.pendingResize.length == 4);
        drain(reader, nil);
        NSMutableData *capture = [NSMutableData new];
        for (int iteration = 0; iteration < 10000 && (transport.inputBuffer.length || transport.pendingResize); iteration++) {
            [transport flushInput]; drain(reader, capture);
        }
        assert(!transport.inputBuffer.length && !transport.pendingResize);
        const unsigned char *bytes = capture.bytes;
        NSUInteger offset = 0;
        int inputs = 0, resizes = 0;
        while (offset < capture.length) {
            assert(capture.length - offset >= 5);
            uint32_t length;
            memcpy(&length, bytes + offset + 1, 4); length = ntohl(length);
            assert(capture.length - offset >= 5 + length);
            if (bytes[offset] == 1) { assert(length == 65536); inputs++; }
            else {
                assert(bytes[offset] == 2 && length == 4);
                assert(bytes[offset + 5] == 3 && bytes[offset + 6] == 232);
                assert(bytes[offset + 7] == 0 && bytes[offset + 8] == 99);
                resizes++;
            }
            offset += 5 + length;
        }
        assert(inputs == 15 && resizes == 1);
        [transport shutdown];
        puts("PASS: native frame cap, blocked-pipe backpressure, 999 resizes coalesced to the final dimensions");
    }
    return 0;
}
