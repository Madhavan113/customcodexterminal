#!/bin/sh
# Build the patched Ghostty (animated GIF backgrounds) as a macOS app.
#
# Requirements: full Xcode selected with `sudo xcode-select -s /Applications/Xcode.app`
# (the Command Line Tools alone cannot build the macOS app), and Zig 0.15.2. The pinned Zig is
# downloaded to /private/tmp/ghostty-build if missing. The source lives in build/ghostty
# (Ghostty v1.3.1 plus patches/ghostty-animated-background.patch).
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
source_dir="$root/build/ghostty"
zig_dir=/private/tmp/ghostty-build
zig_version=0.15.2

if ! xcode-select -p 2>/dev/null | grep -q 'Xcode.app'; then
    printf '%s\n' "Full Xcode is required. Install it from the App Store, then run:" >&2
    printf '%s\n' "  sudo xcode-select -s /Applications/Xcode.app" >&2
    exit 1
fi

if [ ! -d "$source_dir" ]; then
    git clone --depth 1 --branch v1.3.1 https://github.com/ghostty-org/ghostty "$source_dir"
    git -C "$source_dir" apply "$root/patches/ghostty-animated-background.patch"
fi

zig=$(ls -d "$zig_dir"/zig-*"$zig_version"*/ 2>/dev/null | head -1)zig
if [ ! -x "$zig" ]; then
    mkdir -p "$zig_dir"
    curl -sSL -o "$zig_dir/zig.tar.xz" "https://ziglang.org/download/$zig_version/zig-aarch64-macos-$zig_version.tar.xz"
    tar -xf "$zig_dir/zig.tar.xz" -C "$zig_dir"
    zig=$(ls -d "$zig_dir"/zig-*"$zig_version"*/ | head -1)zig
fi

cd "$source_dir"
# Native-only: the iOS slices of the universal framework need Xcode's iOS SDK and are not used
# by the macOS app. Full Xcode is still required for xcodebuild and the Metal shader compiler.
"$zig" build -Doptimize=ReleaseFast -Dxcframework-target=native -Demit-macos-app=true
printf '%s\n' "Built: $source_dir/macos/build/ReleaseLocal/Ghostty.app"
printf '%s\n' "Install with: rm -rf /Applications/Ghostty.app && cp -R $source_dir/macos/build/ReleaseLocal/Ghostty.app /Applications/"
