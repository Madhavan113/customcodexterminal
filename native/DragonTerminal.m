#import <AppKit/AppKit.h>
// Import the used public headers directly: this CLT's umbrella header also
// references unrelated WebKit headers that its SDK does not contain.
#import <WebKit/WKWebView.h>
#import <WebKit/WKWebViewConfiguration.h>
#import <WebKit/WKWebsiteDataStore.h>
#import <WebKit/WKPreferences.h>
#import <WebKit/WKUserContentController.h>
#import <WebKit/WKScriptMessage.h>
#import <WebKit/WKScriptMessageHandler.h>
#import <WebKit/WKFrameInfo.h>
#import <WebKit/WKSecurityOrigin.h>
#import <WebKit/WKNavigationAction.h>
#import <WebKit/WKWindowFeatures.h>
#import <WebKit/WKURLSchemeHandler.h>
#import <WebKit/WKURLSchemeTask.h>
#include <fcntl.h>
#include <math.h>
#include <signal.h>
#include <unistd.h>

@interface DTOptions : NSObject
@property (nonatomic, copy) NSString *mode;
@property (nonatomic, copy) NSString *cwd;
@property (nonatomic, copy) NSString *resume;
@property (nonatomic, copy) NSString *snapshot;
@property (nonatomic, copy) NSString *qaScript;
@property double snapshotDelay;
@property double qaDelay;
+ (instancetype)launchOptions;
@end

@implementation DTOptions
- (instancetype)init {
    if ((self = [super init])) {
        _mode = @"codex"; _cwd = NSFileManager.defaultManager.currentDirectoryPath;
        _snapshotDelay = 3; _qaDelay = 1;
    }
    return self;
}
+ (instancetype)launchOptions {
    DTOptions *options = [DTOptions new];
    NSArray<NSString *> *args = NSProcessInfo.processInfo.arguments;
    for (NSUInteger index = 1; index < args.count; index++) {
        NSString *arg = args[index];
        if ([arg isEqualToString:@"--shell"]) options.mode = @"shell";
        else if ([arg isEqualToString:@"--cwd"] && index + 1 < args.count) options.cwd = args[++index];
        else if ([arg isEqualToString:@"--resume"]) {
            options.resume = @"--last";
            if (index + 1 < args.count && ![args[index + 1] hasPrefix:@"--"]) options.resume = args[++index];
        } else if ([arg isEqualToString:@"--snapshot"] && index + 1 < args.count) options.snapshot = args[++index];
        else if ([arg isEqualToString:@"--snapshot-delay"] && index + 1 < args.count) options.snapshotDelay = MAX(1, MIN(30, args[++index].doubleValue));
        else if ([arg isEqualToString:@"--qa-script"] && index + 1 < args.count) options.qaScript = args[++index];
        else if ([arg isEqualToString:@"--qa-delay"] && index + 1 < args.count) options.qaDelay = MAX(0, MIN(30, args[++index].doubleValue));
    }
    options.cwd = options.cwd.stringByStandardizingPath;
    BOOL directory = NO;
    if ([options.cwd isEqualToString:@"/"] || ![NSFileManager.defaultManager fileExistsAtPath:options.cwd isDirectory:&directory] || !directory) options.cwd = NSHomeDirectory();
    return options;
}
@end

@interface DTResources : NSObject <WKURLSchemeHandler>
@property (nonatomic, copy) NSString *root;
@end
@implementation DTResources
- (void)webView:(WKWebView *)webView startURLSchemeTask:(id<WKURLSchemeTask>)task {
    NSURL *url = task.request.URL;
    if (![url.scheme isEqualToString:@"dragon"] || ![url.host isEqualToString:@"app"]) {
        [task didFailWithError:[NSError errorWithDomain:NSURLErrorDomain code:NSURLErrorUnsupportedURL userInfo:nil]]; return;
    }
    NSString *relative = url.path.length <= 1 ? @"index.html" : [url.path substringFromIndex:1];
    NSString *file = [[self.root stringByAppendingPathComponent:relative].stringByStandardizingPath stringByResolvingSymlinksInPath];
    NSData *data = [file hasPrefix:[self.root stringByAppendingString:@"/"]] ? [NSData dataWithContentsOfFile:file] : nil;
    if (!data) { [task didFailWithError:[NSError errorWithDomain:NSURLErrorDomain code:NSURLErrorFileDoesNotExist userInfo:nil]]; return; }
    NSDictionary *types = @{@"html":@"text/html", @"js":@"application/javascript", @"mjs":@"application/javascript", @"css":@"text/css", @"json":@"application/json", @"svg":@"image/svg+xml", @"png":@"image/png", @"webp":@"image/webp", @"gif":@"image/gif", @"woff2":@"font/woff2"};
    NSURLResponse *response = [[NSURLResponse alloc] initWithURL:url MIMEType:types[file.pathExtension.lowercaseString] ?: @"application/octet-stream" expectedContentLength:data.length textEncodingName:@"utf-8"];
    [task didReceiveResponse:response]; [task didReceiveData:data]; [task didFinish];
}
- (void)webView:(WKWebView *)webView stopURLSchemeTask:(id<WKURLSchemeTask>)task {}
@end

@interface DTPTY : NSObject
@property (nonatomic, strong) NSTask *task;
@property (nonatomic, strong) NSPipe *inputPipe;
@property (nonatomic, strong) NSPipe *outputPipe;
@property (nonatomic, strong) NSMutableData *inputBuffer;
@property (nonatomic, strong) NSData *pendingResize;
@property (nonatomic, strong) NSMutableArray<NSData *> *outputQueue;
@property NSUInteger outputBytes;
@property (nonatomic, strong) dispatch_source_t readSource;
@property (nonatomic, strong) dispatch_source_t writeSource;
@property BOOL readSuspended;
@property BOOL awaitingAck;
@property BOOL outputEOF;
@property BOOL reportedExit;
@property BOOL closing;
@property (nonatomic, strong) NSNumber *exitCode;
@property (copy) void (^onOutput)(NSData *);
@property (copy) void (^onExit)(int);
- (BOOL)start:(DTOptions *)options columns:(int)columns rows:(int)rows error:(NSError **)error;
- (void)input:(NSData *)data;
- (void)resize:(int)columns rows:(int)rows;
- (void)acknowledge;
- (void)shutdown;
- (BOOL)isRunning;
@end

@implementation DTPTY
- (instancetype)init {
    if ((self = [super init])) {
        _task = [NSTask new]; _inputPipe = [NSPipe pipe]; _outputPipe = [NSPipe pipe];
        _inputBuffer = [NSMutableData new]; _outputQueue = [NSMutableArray new];
    }
    return self;
}
- (BOOL)isRunning { return self.task.running && !self.closing; }
- (BOOL)start:(DTOptions *)options columns:(int)columns rows:(int)rows error:(NSError **)error {
    NSString *program = [options.mode isEqualToString:@"shell"] ? @"/bin/zsh" : [NSHomeDirectory() stringByAppendingPathComponent:@".local/bin/codex-noir"];
    if (![NSFileManager.defaultManager isExecutableFileAtPath:program]) {
        if (error) *error = [NSError errorWithDomain:@"DragonTerminal" code:1 userInfo:@{NSLocalizedDescriptionKey:[@"Executable is missing: " stringByAppendingString:program]}];
        return NO;
    }
    NSMutableArray *arguments = [NSMutableArray arrayWithArray:@[@"--cwd", options.cwd, @"--cols", @(columns).stringValue, @"--rows", @(rows).stringValue, @"--", program]];
    if ([options.mode isEqualToString:@"shell"]) [arguments addObject:@"-l"];
    else if (options.resume) [arguments addObjectsFromArray:@[@"resume", options.resume]];
    self.task.executableURL = [NSBundle.mainBundle.bundleURL URLByAppendingPathComponent:@"Contents/MacOS/dragon-pty"];
    self.task.arguments = arguments;
    self.task.currentDirectoryURL = [NSURL fileURLWithPath:options.cwd];
    NSMutableDictionary *environment = [NSProcessInfo.processInfo.environment mutableCopy];
    [environment removeObjectForKey:@"NO_COLOR"];
    environment[@"TERM"] = @"xterm-256color"; environment[@"COLORTERM"] = @"truecolor";
    environment[@"TERM_PROGRAM"] = @"DragonTerminal"; environment[@"CODEX_NOIR_DRAGON"] = @"0";
    environment[@"PATH"] = [NSString stringWithFormat:@"%@/.local/bin:/opt/homebrew/bin:/usr/local/bin:%@", NSHomeDirectory(), environment[@"PATH"] ?: @"/usr/bin:/bin:/usr/sbin:/sbin"];
    self.task.environment = environment;
    self.task.standardInput = self.inputPipe; self.task.standardOutput = self.outputPipe;
    self.task.standardError = NSFileHandle.fileHandleWithStandardError;
    __weak DTPTY *weakSelf = self;
    self.task.terminationHandler = ^(NSTask *task) {
        int code = task.terminationStatus + (task.terminationReason == NSTaskTerminationReasonUncaughtSignal ? 128 : 0);
        dispatch_async(dispatch_get_main_queue(), ^{ DTPTY *strong = weakSelf; strong.exitCode = @(code); [strong reportExit]; });
    };
    if (![self.task launchAndReturnError:error]) return NO;
    int output = self.outputPipe.fileHandleForReading.fileDescriptor;
    int input = self.inputPipe.fileHandleForWriting.fileDescriptor;
    fcntl(output, F_SETFL, fcntl(output, F_GETFL) | O_NONBLOCK);
    fcntl(input, F_SETFL, fcntl(input, F_GETFL) | O_NONBLOCK);
    self.readSource = dispatch_source_create(DISPATCH_SOURCE_TYPE_READ, output, 0, dispatch_get_main_queue());
    dispatch_source_set_event_handler(self.readSource, ^{ [weakSelf readOutput]; });
    dispatch_resume(self.readSource);
    return YES;
}
- (void)queueFrame:(uint8_t)kind payload:(NSData *)payload {
    if (payload.length > 65536 || (kind == 2 && payload.length != 4)) return;
    if (kind == 2) {
        // Keep only the newest size while terminal input is backpressured.
        self.pendingResize = payload;
    } else {
        if (self.inputBuffer.length + payload.length + 5 > 1024 * 1024) { NSBeep(); return; }
        [self appendFrame:kind payload:payload];
    }
    [self flushInput];
    if ((self.inputBuffer.length || self.pendingResize) && !self.writeSource) {
        __weak DTPTY *weakSelf = self;
        self.writeSource = dispatch_source_create(DISPATCH_SOURCE_TYPE_WRITE, self.inputPipe.fileHandleForWriting.fileDescriptor, 0, dispatch_get_main_queue());
        dispatch_source_set_event_handler(self.writeSource, ^{ [weakSelf flushInput]; });
        dispatch_resume(self.writeSource);
    }
}
- (void)appendFrame:(uint8_t)kind payload:(NSData *)payload {
    uint32_t length = (uint32_t)payload.length;
    uint8_t header[] = {kind, (uint8_t)(length >> 24), (uint8_t)(length >> 16), (uint8_t)(length >> 8), (uint8_t)length};
    [self.inputBuffer appendBytes:header length:5]; [self.inputBuffer appendData:payload];
}
- (void)input:(NSData *)data {
    if (!self.isRunning || data.length > 1024 * 1024 || self.inputBuffer.length + data.length + (data.length / 32768 + 1) * 5 > 1024 * 1024) { NSBeep(); return; }
    for (NSUInteger start = 0; start < data.length; start += 32768) [self queueFrame:1 payload:[data subdataWithRange:NSMakeRange(start, MIN(32768, data.length - start))]];
}
- (void)resize:(int)columns rows:(int)rows {
    if (!self.isRunning) return;
    uint8_t payload[] = {(uint8_t)(columns >> 8), (uint8_t)columns, (uint8_t)(rows >> 8), (uint8_t)rows};
    [self queueFrame:2 payload:[NSData dataWithBytes:payload length:4]];
}
- (void)flushInput {
    while (self.inputBuffer.length || self.pendingResize) {
        if (!self.inputBuffer.length && self.pendingResize) {
            NSData *size = self.pendingResize; self.pendingResize = nil;
            [self appendFrame:2 payload:size];
        }
        ssize_t count = write(self.inputPipe.fileHandleForWriting.fileDescriptor, self.inputBuffer.bytes, self.inputBuffer.length);
        if (count > 0) [self.inputBuffer replaceBytesInRange:NSMakeRange(0, (NSUInteger)count) withBytes:NULL length:0];
        else if (errno == EINTR) continue;
        else if (errno == EAGAIN) break;
        else { self.inputBuffer.length = 0; self.pendingResize = nil; break; }
    }
    if (!self.inputBuffer.length && !self.pendingResize && self.writeSource) { dispatch_source_cancel(self.writeSource); self.writeSource = nil; }
}
- (void)cancelRead {
    if (self.readSuspended) { dispatch_resume(self.readSource); self.readSuspended = NO; }
    if (self.readSource) { dispatch_source_cancel(self.readSource); self.readSource = nil; }
}
- (void)readOutput {
    if (self.closing || self.outputEOF) return;
    uint8_t bytes[32768];
    while (self.outputBytes < 512 * 1024) {
        ssize_t count = read(self.outputPipe.fileHandleForReading.fileDescriptor, bytes, MIN(sizeof(bytes), 512 * 1024 - self.outputBytes));
        if (count > 0) { [self.outputQueue addObject:[NSData dataWithBytes:bytes length:(NSUInteger)count]]; self.outputBytes += (NSUInteger)count; }
        else if (!count) { self.outputEOF = YES; [self cancelRead]; break; }
        else if (errno == EINTR) continue;
        else if (errno == EAGAIN) break;
        else { self.outputEOF = YES; [self cancelRead]; break; }
    }
    if (self.outputBytes >= 512 * 1024 && !self.readSuspended && self.readSource) { dispatch_suspend(self.readSource); self.readSuspended = YES; }
    [self deliverNext];
}
- (void)deliverNext {
    if (!self.awaitingAck && self.outputQueue.count) {
        NSData *data = self.outputQueue.firstObject; [self.outputQueue removeObjectAtIndex:0];
        self.outputBytes -= data.length; self.awaitingAck = YES;
        if (self.onOutput) self.onOutput(data);
    }
    if (self.readSuspended && self.outputBytes < 512 * 1024) { dispatch_resume(self.readSource); self.readSuspended = NO; }
    [self reportExit];
}
- (void)acknowledge { self.awaitingAck = NO; [self deliverNext]; }
- (void)reportExit {
    if (!self.reportedExit && self.outputEOF && !self.outputQueue.count && !self.awaitingAck && self.exitCode) {
        self.reportedExit = YES; if (self.onExit) self.onExit(self.exitCode.intValue);
    }
}
- (void)shutdown {
    if (self.closing) return; self.closing = YES;
    [self cancelRead];
    if (self.writeSource) { dispatch_source_cancel(self.writeSource); self.writeSource = nil; }
    [self.inputPipe.fileHandleForWriting closeFile]; [self.outputPipe.fileHandleForReading closeFile];
    [self.outputQueue removeAllObjects]; self.inputBuffer.length = 0; self.pendingResize = nil;
    NSTask *task = self.task;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 1500 * NSEC_PER_MSEC), dispatch_get_main_queue(), ^{ if (task.running) [task terminate]; });
}
@end

@class DTWindow;
@interface DTApp : NSObject <NSApplicationDelegate>
@property (nonatomic, strong) NSMutableDictionary<NSString *, DTWindow *> *windows;
@property (nonatomic, strong) DTOptions *initial;
@property BOOL terminating;
- (void)openMode:(NSString *)mode cwd:(NSString *)cwd;
- (void)closed:(NSString *)identifier;
- (NSDictionary *)preferences;
- (void)savePreferences:(NSDictionary *)values;
@end

@interface DTWindow : NSObject <NSWindowDelegate, WKScriptMessageHandler>
@property (nonatomic, copy) NSString *identifier;
@property (nonatomic, strong) NSWindow *window;
@property (nonatomic, strong) WKWebView *web;
@property (weak) DTApp *owner;
@property (nonatomic, strong) DTOptions *options;
@property (nonatomic, strong) DTPTY *session;
@property BOOL ready;
@property int columns;
@property int rows;
- (instancetype)initWithOwner:(DTApp *)owner options:(DTOptions *)options;
- (void)shutdown;
- (void)evaluate:(NSString *)javascript;
- (void)paste;
@end

@implementation DTWindow
- (instancetype)initWithOwner:(DTApp *)owner options:(DTOptions *)options {
    if (!(self = [super init])) return nil;
    self.owner = owner; self.options = options; self.identifier = NSUUID.UUID.UUIDString; self.columns = 100; self.rows = 30;
    self.window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 1280, 820) styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable backing:NSBackingStoreBuffered defer:NO];
    self.window.title = @"Dragon Terminal"; self.window.minSize = NSMakeSize(720, 480);
    self.window.backgroundColor = [NSColor colorWithCalibratedRed:0.02 green:0.025 blue:0.05 alpha:1];
    self.window.releasedWhenClosed = NO; self.window.delegate = self; self.window.tabbingMode = NSWindowTabbingModeDisallowed;
    WKWebViewConfiguration *configuration = [WKWebViewConfiguration new];
    configuration.websiteDataStore = WKWebsiteDataStore.nonPersistentDataStore;
    configuration.preferences.javaScriptCanOpenWindowsAutomatically = NO;
    DTResources *resources = [DTResources new]; resources.root = [NSBundle.mainBundle.resourcePath stringByAppendingPathComponent:@"web"].stringByResolvingSymlinksInPath;
    [configuration setURLSchemeHandler:resources forURLScheme:@"dragon"];
    [configuration.userContentController addScriptMessageHandler:self name:@"terminal"];
    self.web = [[WKWebView alloc] initWithFrame:NSZeroRect configuration:configuration];
    self.web.navigationDelegate = (id)self; self.web.UIDelegate = (id)self; self.web.autoresizingMask = NSViewWidthSizable | NSViewHeightSizable;
    self.window.contentView = self.web; [self.window center]; [self.window makeKeyAndOrderFront:nil];
    [self.web loadRequest:[NSURLRequest requestWithURL:[NSURL URLWithString:@"dragon://app/index.html"]]];
    return self;
}
- (void)evaluate:(NSString *)javascript {
    [self.web evaluateJavaScript:javascript completionHandler:^(id value, NSError *error) { if (error) NSLog(@"Dragon Terminal JavaScript: %@", error.localizedDescription); }];
}
- (void)send:(NSDictionary *)object {
    if (!self.ready || ![NSJSONSerialization isValidJSONObject:object]) return;
    NSData *data = [NSJSONSerialization dataWithJSONObject:object options:0 error:NULL];
    NSString *json = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
    [self evaluate:[NSString stringWithFormat:@"window.DragonNative.receive(%@);void 0;", json]];
}
- (void)startSession {
    [self send:@{@"type":@"session", @"mode":self.options.mode, @"cwd":self.options.cwd}];
    self.session = [DTPTY new]; __weak DTWindow *weakSelf = self;
    self.session.onOutput = ^(NSData *data) { [weakSelf send:@{@"type":@"data", @"data":[data base64EncodedStringWithOptions:0]}]; };
    self.session.onExit = ^(int code) { [weakSelf send:@{@"type":@"exit", @"code":@(code)}]; };
    NSError *error;
    if (![self.session start:self.options columns:self.columns rows:self.rows error:&error]) {
        NSData *message = [[NSString stringWithFormat:@"\r\n%@\r\n", error.localizedDescription] dataUsingEncoding:NSUTF8StringEncoding];
        [self send:@{@"type":@"data", @"data":[message base64EncodedStringWithOptions:0]}];
        [self send:@{@"type":@"exit", @"code":@127}];
    }
}
- (void)userContentController:(WKUserContentController *)controller didReceiveScriptMessage:(WKScriptMessage *)message {
    WKSecurityOrigin *origin = message.frameInfo.securityOrigin;
    if (!message.frameInfo.mainFrame || ![origin.protocol isEqualToString:@"dragon"] || ![origin.host isEqualToString:@"app"] || ![message.name isEqualToString:@"terminal"] || ![message.body isKindOfClass:NSDictionary.class]) return;
    NSDictionary *body = message.body; NSString *type = body[@"type"];
    if (![type isKindOfClass:NSString.class]) return;
    if ([type isEqualToString:@"ready"]) {
        if (self.ready) return; self.ready = YES;
        [self send:@{@"type":@"preferences", @"values":self.owner.preferences}]; [self startSession];
        __weak DTWindow *weakSelf = self;
        if (self.options.qaScript) dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(self.options.qaDelay * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
            DTWindow *strong = weakSelf;
            if (!strong) return;
            NSString *script = [NSString stringWithContentsOfFile:strong.options.qaScript encoding:NSUTF8StringEncoding error:NULL];
            if (script && [script lengthOfBytesUsingEncoding:NSUTF8StringEncoding] <= 1024 * 1024) [strong evaluate:script];
        });
        if (self.options.snapshot) dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(self.options.snapshotDelay * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{ [weakSelf snapshot]; });
    } else if ([type isEqualToString:@"input"] && [body[@"data"] isKindOfClass:NSString.class]) {
        NSString *text = body[@"data"];
        if ([text lengthOfBytesUsingEncoding:NSUTF8StringEncoding] <= 1024 * 1024) [self.session input:[text dataUsingEncoding:NSUTF8StringEncoding]];
    } else if ([type isEqualToString:@"inputBytes"] && [body[@"data"] isKindOfClass:NSString.class]) {
        NSString *encoded = body[@"data"];
        if (encoded.length <= 1400000) { NSData *data = [[NSData alloc] initWithBase64EncodedString:encoded options:0]; if (data) [self.session input:data]; }
    } else if ([type isEqualToString:@"resize"] && [body[@"cols"] isKindOfClass:NSNumber.class] && [body[@"rows"] isKindOfClass:NSNumber.class]) {
        int cols = [body[@"cols"] intValue], rows = [body[@"rows"] intValue];
        if (cols >= 2 && cols <= 1000 && rows >= 2 && rows <= 1000) { self.columns = cols; self.rows = rows; [self.session resize:cols rows:rows]; }
    } else if ([type isEqualToString:@"ack"]) [self.session acknowledge];
    else if ([type isEqualToString:@"newSession"] && [body[@"mode"] isKindOfClass:NSString.class] && [@[@"codex", @"shell"] containsObject:body[@"mode"]]) [self.owner openMode:body[@"mode"] cwd:self.options.cwd];
    else if ([type isEqualToString:@"copy"] && [body[@"text"] isKindOfClass:NSString.class]) {
        NSString *text = body[@"text"];
        if ([text lengthOfBytesUsingEncoding:NSUTF8StringEncoding] <= 4 * 1024 * 1024) { [NSPasteboard.generalPasteboard clearContents]; [NSPasteboard.generalPasteboard setString:text forType:NSPasteboardTypeString]; }
    } else if ([type isEqualToString:@"preferences"] && [body[@"values"] isKindOfClass:NSDictionary.class]) [self.owner savePreferences:body[@"values"]];
}
- (void)paste {
    NSString *text = [NSPasteboard.generalPasteboard stringForType:NSPasteboardTypeString];
    if (!text || [text lengthOfBytesUsingEncoding:NSUTF8StringEncoding] > 1024 * 1024) return;
    NSString *json = [[NSString alloc] initWithData:[NSJSONSerialization dataWithJSONObject:@[text] options:0 error:NULL] encoding:NSUTF8StringEncoding];
    [self evaluate:[NSString stringWithFormat:@"window.DragonNative.paste(%@[0]);void 0;", json]];
}
- (void)webView:(WKWebView *)webView decidePolicyForNavigationAction:(WKNavigationAction *)action decisionHandler:(void (^)(NSInteger))decision {
    NSURL *url = action.request.URL;
    // Public WKNavigationActionPolicy values: cancel = 0, allow = 1.
    decision([url.scheme isEqualToString:@"dragon"] && [url.host isEqualToString:@"app"] && action.targetFrame.mainFrame ? 1 : 0);
}
- (WKWebView *)webView:(WKWebView *)webView createWebViewWithConfiguration:(WKWebViewConfiguration *)configuration forNavigationAction:(WKNavigationAction *)action windowFeatures:(WKWindowFeatures *)features { return nil; }
- (void)webViewWebContentProcessDidTerminate:(WKWebView *)webView {
    [self.session shutdown]; NSAlert *alert = [NSAlert new]; alert.messageText = @"The terminal renderer stopped."; alert.informativeText = @"Open a new window to start another session."; [alert runModal];
}
- (BOOL)windowShouldClose:(NSWindow *)window {
    if (self.session.isRunning && !self.owner.terminating) {
        NSAlert *alert = [NSAlert new]; alert.messageText = @"Close this terminal session?"; alert.informativeText = @"The shell or Codex process in this window will stop.";
        [alert addButtonWithTitle:@"Close Session"]; [alert addButtonWithTitle:@"Cancel"];
        if ([alert runModal] != NSAlertFirstButtonReturn) return NO;
    }
    return YES;
}
- (void)windowWillClose:(NSNotification *)notification { [self shutdown]; [self.owner closed:self.identifier]; }
- (void)shutdown { [self.session shutdown]; [self.web.configuration.userContentController removeScriptMessageHandlerForName:@"terminal"]; }
- (void)snapshot {
    NSString *path = self.options.snapshot;
    [self.web evaluateJavaScript:@"window.DragonNative.snapshot()" completionHandler:^(id value, NSError *error) {
        if (value && [NSJSONSerialization isValidJSONObject:value]) {
            NSData *json = [NSJSONSerialization dataWithJSONObject:value options:NSJSONWritingPrettyPrinted | NSJSONWritingSortedKeys error:NULL];
            if (![json writeToFile:[path stringByAppendingString:@".json"] atomically:YES]) NSLog(@"Dragon state snapshot write failed");
        } else NSLog(@"Dragon state snapshot failed: %@", error);
    }];
    [self.web takeSnapshotWithConfiguration:nil completionHandler:^(NSImage *image, NSError *error) {
        NSBitmapImageRep *bitmap = image ? [NSBitmapImageRep imageRepWithData:image.TIFFRepresentation] : nil;
        NSData *png = [bitmap representationUsingType:NSBitmapImageFileTypePNG properties:@{}];
        if (![png writeToFile:path atomically:YES]) NSLog(@"Dragon image snapshot failed: %@", error);
    }];
}
@end

@implementation DTApp
- (instancetype)init { if ((self = [super init])) { _windows = [NSMutableDictionary new]; _initial = DTOptions.launchOptions; } return self; }
- (void)addWindow:(DTOptions *)options { DTWindow *window = [[DTWindow alloc] initWithOwner:self options:options]; self.windows[window.identifier] = window; }
- (void)openMode:(NSString *)mode cwd:(NSString *)cwd { DTOptions *options = [DTOptions new]; options.mode = mode; options.cwd = cwd; [self addWindow:options]; }
- (void)closed:(NSString *)identifier { [self.windows removeObjectForKey:identifier]; }
- (void)applicationDidFinishLaunching:(NSNotification *)notification { [self buildMenu]; [self addWindow:self.initial]; [NSApp activateIgnoringOtherApps:YES]; }
- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)sender { return YES; }
- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)sender {
    NSUInteger count = 0; for (DTWindow *window in self.windows.allValues) if (window.session.isRunning) count++;
    if (count) {
        NSAlert *alert = [NSAlert new]; alert.messageText = @"Quit Dragon Terminal?";
        alert.informativeText = [NSString stringWithFormat:@"This will stop %lu active terminal session%@.", (unsigned long)count, count == 1 ? @"" : @"s"];
        [alert addButtonWithTitle:@"Quit"]; [alert addButtonWithTitle:@"Cancel"];
        if ([alert runModal] != NSAlertFirstButtonReturn) return NSTerminateCancel;
    }
    self.terminating = YES; for (DTWindow *window in self.windows.allValues) [window shutdown]; return NSTerminateNow;
}
- (void)applicationWillTerminate:(NSNotification *)notification { for (DTWindow *window in self.windows.allValues) [window shutdown]; }
- (NSDictionary *)validatePreferences:(NSDictionary *)values {
    if (![NSJSONSerialization isValidJSONObject:values]) return @{};
    NSData *encoded = [NSJSONSerialization dataWithJSONObject:values options:0 error:NULL]; if (encoded.length >= 4096) return @{};
    NSMutableDictionary *clean = [NSMutableDictionary new];
    NSDictionary *enums = @{@"scene":@[@"dragon", @"aurora", @"embers", @"void"], @"palette":@[@"noir", @"violet", @"ember", @"ice"], @"energy":@[@"auto", @"idle", @"working", @"max", @"ultra"]};
    for (NSString *key in enums) if ([values[key] isKindOfClass:NSString.class] && [enums[key] containsObject:values[key]]) clean[key] = values[key];
    NSDictionary *ranges = @{@"intensity":@[@0, @1], @"glass":@[@0.2, @1], @"fontSize":@[@10, @24]};
    for (NSString *key in ranges) {
        NSNumber *number = values[key];
        if ([number isKindOfClass:NSNumber.class] && CFGetTypeID((__bridge CFTypeRef)number) != CFBooleanGetTypeID()) {
            double value = number.doubleValue;
            if (isfinite(value) && value >= [ranges[key][0] doubleValue] && value <= [ranges[key][1] doubleValue]) clean[key] = number;
        }
    }
    NSNumber *motion = values[@"motion"];
    if ([motion isKindOfClass:NSNumber.class] && CFGetTypeID((__bridge CFTypeRef)motion) == CFBooleanGetTypeID()) clean[@"motion"] = motion;
    return clean;
}
- (NSDictionary *)preferences { return [self validatePreferences:[NSUserDefaults.standardUserDefaults dictionaryForKey:@"DragonVisualPreferences"] ?: @{}]; }
- (void)savePreferences:(NSDictionary *)values { NSDictionary *clean = [self validatePreferences:values]; if (clean.count) [NSUserDefaults.standardUserDefaults setObject:clean forKey:@"DragonVisualPreferences"]; }
- (DTWindow *)activeWindow { for (DTWindow *window in self.windows.allValues) if (window.window == NSApp.keyWindow) return window; return nil; }
- (void)newCodex:(id)sender { [self openMode:@"codex" cwd:self.activeWindow.options.cwd ?: self.initial.cwd]; }
- (void)newShell:(id)sender { [self openMode:@"shell" cwd:self.activeWindow.options.cwd ?: self.initial.cwd]; }
- (void)copyText:(id)sender { [self.activeWindow evaluate:@"window.DragonNative.copySelection();void 0;"]; }
- (void)pasteText:(id)sender { [self.activeWindow paste]; }
- (void)selectAll:(id)sender { [self.activeWindow evaluate:@"window.DragonNative.selectAll();void 0;"]; }
- (void)largerText:(id)sender { [self.activeWindow evaluate:@"window.DragonNative.adjustFont(1);void 0;"]; }
- (void)smallerText:(id)sender { [self.activeWindow evaluate:@"window.DragonNative.adjustFont(-1);void 0;"]; }
- (void)toggleFocus:(id)sender { [self.activeWindow evaluate:@"window.DragonNative.toggleFocus();void 0;"]; }
- (NSMenuItem *)addTo:(NSMenu *)menu title:(NSString *)title action:(SEL)action key:(NSString *)key target:(id)target {
    NSMenuItem *item = [menu addItemWithTitle:title action:action keyEquivalent:key]; item.target = target; return item;
}
- (void)buildMenu {
    NSMenu *main = [NSMenu new];
    NSMenuItem *application = [NSMenuItem new]; [main addItem:application]; application.submenu = [NSMenu new];
    [self addTo:application.submenu title:@"About Dragon Terminal" action:@selector(orderFrontStandardAboutPanel:) key:@"" target:NSApp];
    [application.submenu addItem:NSMenuItem.separatorItem];
    [self addTo:application.submenu title:@"Hide Dragon Terminal" action:@selector(hide:) key:@"h" target:NSApp];
    [self addTo:application.submenu title:@"Quit Dragon Terminal" action:@selector(terminate:) key:@"q" target:NSApp];
    NSMenuItem *file = [NSMenuItem new]; file.title = @"File"; [main addItem:file]; file.submenu = [[NSMenu alloc] initWithTitle:@"File"];
    [self addTo:file.submenu title:@"New Codex" action:@selector(newCodex:) key:@"n" target:self];
    NSMenuItem *shell = [self addTo:file.submenu title:@"New Shell" action:@selector(newShell:) key:@"n" target:self]; shell.keyEquivalentModifierMask = NSEventModifierFlagCommand | NSEventModifierFlagShift;
    [file.submenu addItem:NSMenuItem.separatorItem];
    [self addTo:file.submenu title:@"Close Window" action:@selector(performClose:) key:@"w" target:nil];
    NSMenuItem *edit = [NSMenuItem new]; edit.title = @"Edit"; [main addItem:edit]; edit.submenu = [[NSMenu alloc] initWithTitle:@"Edit"];
    [self addTo:edit.submenu title:@"Copy" action:@selector(copyText:) key:@"c" target:self];
    [self addTo:edit.submenu title:@"Paste" action:@selector(pasteText:) key:@"v" target:self];
    [self addTo:edit.submenu title:@"Select All" action:@selector(selectAll:) key:@"a" target:self];
    NSMenuItem *view = [NSMenuItem new]; view.title = @"View"; [main addItem:view]; view.submenu = [[NSMenu alloc] initWithTitle:@"View"];
    [self addTo:view.submenu title:@"Larger Text" action:@selector(largerText:) key:@"=" target:self];
    [self addTo:view.submenu title:@"Smaller Text" action:@selector(smallerText:) key:@"-" target:self];
    NSMenuItem *focus = [self addTo:view.submenu title:@"Toggle Focus Mode" action:@selector(toggleFocus:) key:@"f" target:self]; focus.keyEquivalentModifierMask = NSEventModifierFlagCommand | NSEventModifierFlagShift;
    NSApp.mainMenu = main;
}
@end

int main(int argc, char **argv) {
    @autoreleasepool {
        signal(SIGPIPE, SIG_IGN);
        NSApplication *application = NSApplication.sharedApplication;
        [application setActivationPolicy:NSApplicationActivationPolicyRegular];
        static DTApp *delegate; delegate = [DTApp new]; application.delegate = delegate;
        [application run];
    }
    return 0;
}
