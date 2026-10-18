#!/usr/bin/env python3
# Task #95: build the elek-kdf-runtime AAR that ships the embedded market
# runtime (kdf) binary inside the APK.
#
# An AAR's native libraries must live under jni/<abi>/ (AAR format spec);
# AGP's exploded-AAR merge ignores lib/<abi>/. Proven by run 39106251178
# (b94, controlled comparison in the same APK): libzxingcpp_android.so -
# packaged by gradle itself under jni/ - landed in the APK, while our
# lib/arm64-v8a/libkdfbin.so entry was silently dropped and the APK had no
# kdf entries at all. With the correct arcname the APK ends up with
# lib/arm64-v8a/libkdfbin.so, which the package manager extracts into
# nativeLibraryDir at install time - the one
# app-owned, exec-permitted location for targetSdk >= 29 (W^X applies only
# to app-writable /data paths).
#
# Called by contrib/android/make_kdf_runtime.sh per arch:
#   arm64-v8a:  --lib <binary> --abi arm64-v8a   (binary REQUIRED)
#   other arch: no --lib                          (empty but valid AAR)
#
# The expected binary is the pinned prebuilt from the
# bartman081523/elektron-net-kdf GitHub release "phone-kdf-runtime-aarch64-84"
# (same artifact lineage as the on-device kdf e2e, task #84).
import argparse
import hashlib
import io
import os
import struct
import zipfile

# sha256 of kdf-shim-aarch64 (aarch64-linux-android, 108229720 bytes).
# Must stay in sync with the expected hash in .github/workflows/builds.yml
# (fetch step) - guarded by scratches/61clients/b95_test.py.
PIN_SHA256 = "3be96442ff41a93664f26197d589ca48ed1643831ecd5eddba98c78d692939d1"

EM_AARCH64 = 183  # ELF e_machine for aarch64

# the manifest merger rejects library AAR manifests without a package
# declaration ("Missing 'package' declaration in manifest at
# [:elek-kdf-runtime:]" - run 39099856743); the namespace must not clash
# with the app package (org.elektronnet.market.elektronelectrum)
AAR_PACKAGE = "org.elektronnet.market.kdfruntime"
AAR_MANIFEST = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<manifest xmlns:android="http://schemas.android.com/apk/res/android"'
    ' package="%s"/>\n' % AAR_PACKAGE
)

# classes.jar must exist in a valid AAR and be a jar (it needs a manifest);
# it is intentionally empty - the runtime is native code, no java classes
JAR_MANIFEST = b"Manifest-Version: 1.0\r\n\r\n"


def fail(msg):
    raise SystemExit("make_kdf_aar: %s" % msg)


def verify_elf(path, pin):
    """Integrity gates for the prebuilt: pinned sha256 + ELF magic + aarch64."""
    size = os.path.getsize(path)
    if size < 1000000:
        fail("%s: implausibly small (%d bytes) for the kdf runtime binary" % (path, size))
    with open(path, "rb") as f:
        head = f.read(20)
        sha = hashlib.sha256()
        sha.update(head)
        for chunk in iter(lambda: f.read(1 << 20), b""):
            sha.update(chunk)
    if head[:4] != b"\x7fELF":
        fail("%s: not an ELF file (magic %s)" % (path, head[:4].hex()))
    e_machine = struct.unpack("<H", head[18:20])[0]
    if e_machine != EM_AARCH64:
        fail("%s: ELF e_machine=%d, expected aarch64 (%d)" % (path, e_machine, EM_AARCH64))
    got = sha.hexdigest()
    if got != pin:
        fail("%s: sha256 mismatch: got %s, expected %s" % (path, got, pin))


def write_aar(out_path, so_entries):
    jar = io.BytesIO()
    with zipfile.ZipFile(jar, "w") as z:
        z.writestr("META-INF/MANIFEST.MF", JAR_MANIFEST)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("AndroidManifest.xml", AAR_MANIFEST)
        z.writestr("classes.jar", jar.getvalue())
        for arcname, src in so_entries:
            z.write(src, arcname)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--aar", required=True, help="output AAR path")
    p.add_argument("--lib", help="ELF binary to ship as jni/<abi>/libkdfbin.so")
    p.add_argument("--abi", default="arm64-v8a", help="ABI directory for the library entry")
    p.add_argument("--pin", default=PIN_SHA256, help="expected sha256 of --lib")
    args = p.parse_args()

    entries = []
    if args.lib:
        if not os.path.isfile(args.lib):
            fail("%s: kdf runtime binary not found (fetch step missing?)" % args.lib)
        verify_elf(args.lib, args.pin)
        entries.append(("jni/%s/libkdfbin.so" % args.abi, args.lib))
    write_aar(args.aar, entries)
    print("make_kdf_aar: wrote %s (%s)" % (
        args.aar, entries[0][0] if entries else "empty, no lib for this arch"))


if __name__ == "__main__":
    main()