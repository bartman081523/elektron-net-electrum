#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Watch GitHub Actions Android build, download APK artifact, and install via ADB."""
import argparse
import glob
import json
import os
import subprocess
import sys
import time

REPO = "bartman081523/elektron-net-electrum"
PACKAGE_NAME = "org.elektronnet.elektronelectrum"


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run_cmd(cmd, check=True):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"Command failed ({r.returncode}): {' '.join(cmd)}\n{r.stderr}")
    return r


def get_latest_run_id():
    r = run_cmd(["gh", "run", "list", "--workflow=builds.yml", "-R", REPO, "-L", "1", "--json", "databaseId,status"])
    data = json.loads(r.stdout)
    if not data:
        raise RuntimeError("No workflow runs found")
    return str(data[0]["databaseId"])


def wait_for_run(run_id, poll_interval=20):
    log(f"Monitoring GitHub Actions run {run_id} on {REPO}...")
    while True:
        r = run_cmd(["gh", "run", "view", run_id, "-R", REPO, "--json", "status,conclusion,jobs"])
        info = json.loads(r.stdout)
        status = info.get("status")
        conclusion = info.get("conclusion")
        jobs = info.get("jobs", [])
        android_job = next((j for j in jobs if "Android" in j.get("name", "")), None)
        job_status = f"job '{android_job['name']}': {android_job['status']}" if android_job else "waiting for job"

        log(f"Run {run_id}: status={status}, conclusion={conclusion} ({job_status})")
        if status == "completed":
            if conclusion == "success":
                log(f"Build succeeded!")
                return True
            else:
                raise RuntimeError(f"Build failed with conclusion '{conclusion}'")
        time.sleep(poll_interval)


def download_apk(run_id, out_dir):
    import shutil
    shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir, exist_ok=True)
    log(f"Finding Android artifact for run {run_id}...")
    r = run_cmd(["gh", "api", f"repos/{REPO}/actions/runs/{run_id}/artifacts"])
    artifacts = json.loads(r.stdout).get("artifacts", [])
    apk_art = next((a for a in artifacts if "android" in a.get("name", "").lower() or "arm64" in a.get("name", "").lower()), None)
    if not apk_art:
        raise RuntimeError(f"No Android artifact found in run {run_id}. Available: {[a.get('name') for a in artifacts]}")

    art_name = apk_art["name"]
    log(f"Downloading artifact '{art_name}' (ID {apk_art['id']}, {apk_art['size_in_bytes']} bytes)...")
    run_cmd(["gh", "run", "download", str(run_id), "-R", REPO, "-n", art_name, "-D", out_dir])

    apks = glob.glob(os.path.join(out_dir, "**", "*.apk"), recursive=True)
    if not apks:
        raise RuntimeError(f"No .apk file found in downloaded artifact under {out_dir}")
    apk_path = apks[0]
    log(f"APK downloaded: {apk_path} ({os.path.getsize(apk_path)} bytes)")
    return apk_path


def get_connected_device(target_hint=None):
    r = subprocess.run(["adb", "devices"], capture_output=True, text=True)
    devices = [l.split("\t")[0] for l in r.stdout.splitlines() if l.endswith("\tdevice")]
    if target_hint and target_hint in devices:
        return target_hint
    return devices[0] if devices else None


def install_apk(apk_path, device_hint=None):
    dev = get_connected_device(device_hint)
    if not dev:
        log("No ADB device currently attached. Trying candidate IPs 100.64.0.1:5555 and 192.168.178.26:5555...")
        subprocess.run(["adb", "connect", "100.64.0.1:5555"], capture_output=True, text=True)
        subprocess.run(["adb", "connect", "192.168.178.26:5555"], capture_output=True, text=True)
        dev = get_connected_device()

    if not dev:
        log("Device not reachable over ADB right now.")
        log("To complete installation:")
        log(f"  1. Ensure Wireless Debugging is ON or connect phone via USB.")
        log(f"  2. Run: adb install -r {apk_path}")
        return False

    log(f"Installing APK to device {dev} as update...")
    res = subprocess.run(["adb", "-s", dev, "install", "-r", apk_path], capture_output=True, text=True)
    if res.returncode != 0:
        if "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in res.stderr:
            log("Signature difference detected between upstream release key and committed debug key.")
            log("Preserving user data and performing in-place package update...")
            # Backup data to sdcard
            subprocess.run(["adb", "-s", dev, "shell", f"tar -czf /sdcard/elektron_data_tmp.tar.gz --exclude='*socket*' -C /data/user/0 {PACKAGE_NAME}"], check=True)
            # Uninstall old package binary
            subprocess.run(["adb", "-s", dev, "shell", "pm", "uninstall", "-k", PACKAGE_NAME])
            # Install new package
            run_cmd(["adb", "-s", dev, "install", apk_path])
            # Restore data from archive
            subprocess.run(["adb", "-s", dev, "shell", "tar -xzf /sdcard/elektron_data_tmp.tar.gz -C /data/user/0/"], check=True)
            # Adjust ownership to matching UID
            uid_out = subprocess.run(["adb", "-s", dev, "shell", f"dumpsys package {PACKAGE_NAME} | grep userId="], capture_output=True, text=True).stdout
            for line in uid_out.splitlines():
                if "userId=" in line:
                    uid = line.split("userId=")[-1].strip()
                    subprocess.run(["adb", "-s", dev, "shell", f"chown -R {uid}:{uid} /data/user/0/{PACKAGE_NAME}"])
                    break
            log("App data preserved successfully!")
        else:
            raise RuntimeError(f"Command failed ({res.returncode}): adb install -r\n{res.stderr}")
    else:
        log("APK updated successfully in-place!")

    log(f"Stopping old process {PACKAGE_NAME}...")
    subprocess.run(["adb", "-s", dev, "shell", "am", "force-stop", PACKAGE_NAME], capture_output=True)

    log(f"Launching {PACKAGE_NAME}...")
    subprocess.run(["adb", "-s", dev, "shell", "monkey", "-p", PACKAGE_NAME, "-c", "android.intent.category.LAUNCHER", "1"], capture_output=True)
    log("App launched on device!")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="", help="GitHub run ID (defaults to latest)")
    parser.add_argument("--device", default="", help="Target ADB device (e.g. 100.64.0.1:5555)")
    parser.add_argument("--out-dir", default="dist/apk_ci", help="Artifact output directory")
    args = parser.parse_args()

    run_id = args.run_id or get_latest_run_id()
    wait_for_run(run_id)
    apk_path = download_apk(run_id, args.out_dir)
    install_apk(apk_path, args.device)


if __name__ == "__main__":
    main()
