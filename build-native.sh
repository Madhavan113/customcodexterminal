#!/bin/sh
set -eu
dragon_source=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
dragon_build="$dragon_source/build"
dragon_app="$dragon_build/Dragon Terminal.app"
mkdir -p "$dragon_app/Contents/MacOS" "$dragon_app/Contents/Resources"
/usr/bin/clang -std=c11 -O2 -target arm64-apple-macos12.0 -Wall -Wextra -Werror \
    "$dragon_source/native/pty-worker.c" -o "$dragon_app/Contents/MacOS/dragon-pty"
/usr/bin/clang -fobjc-arc -fblocks -O2 -target arm64-apple-macos12.0 -Wall -Wextra -Wno-unused-parameter \
    -framework AppKit -framework WebKit \
    "$dragon_source/native/DragonTerminal.m" -o "$dragon_app/Contents/MacOS/DragonTerminal"
cp "$dragon_source/native/Info.plist" "$dragon_app/Contents/Info.plist"
if [ -f "$dragon_source/native/DragonTerminal.icns" ]; then
    cp "$dragon_source/native/DragonTerminal.icns" "$dragon_app/Contents/Resources/DragonTerminal.icns"
fi
if [ -d "$dragon_source/web" ]; then
    /usr/bin/ditto "$dragon_source/web" "$dragon_app/Contents/Resources/web"
fi
/usr/bin/codesign --force --sign - "$dragon_app/Contents/MacOS/dragon-pty"
/usr/bin/codesign --force --sign - "$dragon_app"
printf 'Staged app: %s\n' "$dragon_app"
printf '%s\n' 'No installation performed. Run native/smoke-pty.py and complete UI QA before install-dragon.py --apply.'
