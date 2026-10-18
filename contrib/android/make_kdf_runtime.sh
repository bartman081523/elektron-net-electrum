#!/bin/bash

# Task #95: package the embedded market runtime (kdf binary) as an AAR that
# buildozer_qml.spec lists in android.add_aars. Gradle merges the AAR's
# lib/<abi>/libkdfbin.so into the APK's jniLibs; the package manager then
# extracts it into nativeLibraryDir, where exec is permitted for
# targetSdk >= 29.
#
# Supply path: the builds.yml fetch step downloads the pinned prebuilt from
# the bartman081523/elektron-net-kdf release "phone-kdf-runtime-aarch64-84"
# into contrib/android/.cache/kdf/libkdfbin.so BEFORE the build; this script
# verifies it (sha256 + ELF + aarch64, in make_kdf_aar.py) and wraps it.
#
# Arch contract mirrors the per-arch make_barcode_scanner.sh calls from
# make_apk.sh: only arm64-v8a ships the binary; every other arch gets an
# empty-but-valid AAR (gradle merges nothing from it).

set -e

CONTRIB_ANDROID="$(dirname "$(readlink -e "$0")")"
CONTRIB="$CONTRIB_ANDROID"/..
CACHEDIR="$CONTRIB_ANDROID"/.cache

. "$CONTRIB"/build_tools_util.sh

TARGET_ARCH="$1"
AAR="$CACHEDIR"/aars/elek-kdf-runtime.aar
BIN="$CACHEDIR"/kdf/libkdfbin.so

case "$TARGET_ARCH" in
    arm64-v8a|armeabi-v7a|x86_64) ;;
    *) fail "usage: make_kdf_runtime.sh <arm64-v8a|armeabi-v7a|x86_64>" ;;
esac

# rebuild fresh on every call: cheap, and keeps the AAR in sync with the
# binary actually staged for this build
rm -f "$AAR"
mkdir -p "$CACHEDIR"/aars

if [ "$TARGET_ARCH" == arm64-v8a ]; then
    python3 "$CONTRIB_ANDROID"/make_kdf_aar.py --aar "$AAR" --lib "$BIN" \
        --abi "$TARGET_ARCH" || fail "make_kdf_aar.py failed"
    info "kdf runtime AAR ready for $TARGET_ARCH: $AAR"
else
    info "task #95: empty kdf-runtime AAR for $TARGET_ARCH (binary ships only in arm64-v8a builds)"
    python3 "$CONTRIB_ANDROID"/make_kdf_aar.py --aar "$AAR" || fail "make_kdf_aar.py failed"
fi