"""No shell interpolation, network service, telemetry, or automatic device reset."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import plistlib
import pty
import re
import select
import shutil
import subprocess
import sys
import time
from pathlib import Path

MARKER = "ios-app-conflict-fixer"
BUNDLE = re.compile(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\Z")
CONFLICT = re.compile(
    r"App with identifier\s+[`\"']?([A-Za-z0-9.-]+)[`\"']?\s+is already installed, "
    r"so we can[’']t install (?:app extension|App Extension) with that same identifier",
    re.IGNORECASE,
)


class FixerError(Exception):
    pass


def run(argv, *, env=None, timeout=60):
    """Capture output privately; never invoke a shell or echo signing identities."""
    try:
        result = subprocess.run([str(x) for x in argv], capture_output=True,
                                env=env, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise FixerError(f"Could not run {argv[0]}: {e}") from e
    if result.returncode:
        detail = (result.stderr or result.stdout).decode(errors="replace")[-3500:]
        raise FixerError(f"{Path(str(argv[0])).name} failed ({result.returncode}):\n{detail}")
    return result.stdout


def valid_bundle(value):
    if not BUNDLE.fullmatch(value):
        raise FixerError("Invalid bundle identifier")
    return value


def conflicts(text):
    """Extract only the explicit installation error, not generic mentions of an ID."""
    return sorted({m.group(1) for m in CONFLICT.finditer(text) if BUNDLE.fullmatch(m.group(1))})


def evidence(log, app, target):
    valid_bundle(app)
    valid_bundle(target)
    if app.lower().startswith("com.apple.") or target.lower().startswith("com.apple."):
        raise FixerError("Apple system bundle identifiers are outside this tool's scope")
    if not target.startswith(app + "."):
        raise FixerError("Conflict ID must be an extension beneath the specified parent App ID")
    if target not in conflicts(Path(log).read_text(errors="replace")):
        raise FixerError("The supplied log does not contain the exact extension conflict error")


def devices():
    return [s.strip() for s in run(["idevice_id", "-l"]).decode().splitlines() if s.strip()]


def require_device(udid):
    if udid not in devices():
        raise FixerError("Selected USB device is unavailable. Connect, unlock, and trust this Mac.")


def apps(udid, bundle):
    raw = run(["ideviceinstaller", "-u", udid, "list", "--all", "--xml", "-b", bundle])
    try:
        data = plistlib.loads(raw)
    except Exception as e:
        raise FixerError("Could not parse device app inventory; refusing to continue") from e
    if not isinstance(data, list) or any(not isinstance(a, dict) for a in data):
        raise FixerError("Unexpected app inventory format")
    return [a for a in data if a.get("CFBundleIdentifier") == bundle]


def signing_profile(path, identity, udid, bundle):
    if not re.fullmatch(r"[0-9A-Fa-f]{40}", identity):
        raise FixerError("Use a SHA-1 certificate fingerprint from security find-identity")
    try:
        p = plistlib.loads(run(["security", "cms", "-D", "-i", path]))
        expiry = p["ExpirationDate"].replace(tzinfo=dt.timezone.utc)
        ent = p["Entitlements"]
        prefix = p["ApplicationIdentifierPrefix"][0]
        team = ent["com.apple.developer.team-identifier"]
        allowed = ent["application-identifier"]
    except (KeyError, TypeError, ValueError, plistlib.InvalidFileException) as e:
        raise FixerError("Invalid development provisioning profile") from e
    if expiry <= dt.datetime.now(dt.timezone.utc):
        raise FixerError("Provisioning profile has expired")
    if udid not in p.get("ProvisionedDevices", []):
        raise FixerError("Selected device is not included in this profile")
    if not any(hashlib.sha1(c).hexdigest().lower() == identity.lower()
               for c in p.get("DeveloperCertificates", [])):
        raise FixerError("Signing certificate is not included in this profile")
    appid = prefix + "." + bundle
    if not (appid == allowed or (allowed.endswith("*") and appid.startswith(allowed[:-1]))):
        raise FixerError("Profile does not cover the conflict bundle identifier")
    if not ent.get("get-task-allow"):
        raise FixerError("An iOS development profile is required by this tested signing path")
    return {"application-identifier": appid,
            "com.apple.developer.team-identifier": team,
            "get-task-allow": True, "keychain-access-groups": [appid]}


def save_state(work, data):
    tmp = work / "state.tmp"
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.chmod(0o600)
    tmp.replace(work / "state.json")


def build(args):
    evidence(args.log, args.app_id, args.conflict_id)
    ent = signing_profile(args.profile, args.identity, args.udid, args.conflict_id)
    env = dict(os.environ, DEVELOPER_DIR=args.developer_dir)
    sdk = run(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"], env=env).decode().strip()
    work = Path(args.workdir).resolve()
    if work.exists():
        raise FixerError("Work directory already exists. Choose a fresh directory for each attempt.")
    work.mkdir(mode=0o700, parents=True)
    app = work / "ConflictCleanup.app"
    app.mkdir()
    (work / "main.c").write_text("int main(int argc, char **argv) { return 0; }\n")
    info = {"CFBundleIdentifier": args.conflict_id, "CFBundleName": "ConflictCleanup",
            "CFBundleDisplayName": "Conflict Cleanup", "CFBundleExecutable": "ConflictCleanup",
            "CFBundlePackageType": "APPL", "CFBundleVersion": "1",
            "CFBundleShortVersionString": "1.0", "MinimumOSVersion": "18.0",
            "CFBundleSupportedPlatforms": ["iPhoneOS"], "UIDeviceFamily": [1, 2],
            "LSRequiresIPhoneOS": True, "UIRequiredDeviceCapabilities": ["arm64"],
            "IACFCleanupMarker": MARKER}
    (app / "Info.plist").write_bytes(plistlib.dumps(info))
    shutil.copyfile(args.profile, app / "embedded.mobileprovision")
    ep = work / "entitlements.plist"
    ep.write_bytes(plistlib.dumps(ent))
    run(["xcrun", "--sdk", "iphoneos", "clang", "-arch", "arm64", "-isysroot", sdk,
         "-miphoneos-version-min=18.0", work / "main.c", "-o", app / "ConflictCleanup"], env=env)
    run(["codesign", "--force", "--sign", args.identity, "--entitlements", ep,
         "--generate-entitlement-der", "--timestamp=none", app])
    run(["codesign", "--verify", "--strict", app])
    state = {"tool": MARKER, "udid": args.udid, "app_id": args.app_id,
             "conflict_id": args.conflict_id, "phase": "built"}
    save_state(work, state)
    return work, app, state


def installed_cleanup(record):
    # Require our bundle name + file path, not just an ID that could belong to another app.
    return (record.get("CFBundleName") == "ConflictCleanup"
            and record.get("CFBundleDisplayName") == "Conflict Cleanup"
            and str(record.get("Path", "")).endswith("/ConflictCleanup.app"))


def confirmed(args, state):
    print(f"Target: {state['conflict_id']}\nParent: {state['app_id']}")
    print("This may delete local data associated with this extension. "
          "You may need to sign in again or restore configuration.")
    if args.yes:
        if not args.allow_data_loss:
            raise FixerError("Noninteractive repair requires --yes AND --allow-data-loss")
    elif not sys.stdin.isatty():
        raise FixerError("Interactive confirmation required; or use --yes --allow-data-loss")
    elif input("Type the exact conflict ID to continue: ").strip() != state["conflict_id"]:
        raise FixerError("Cancelled; no device mutation performed")


def remove_cleanup(work, state):
    require_device(state["udid"])
    records = apps(state["udid"], state["conflict_id"])
    if not records:
        state["phase"] = "cleanup-absent"
        save_state(work, state)
        return
    if len(records) != 1 or not installed_cleanup(records[0]):
        raise FixerError("Target is not identifiable as this tool's temporary app; refusing removal")
    state["phase"] = "uninstalling"
    save_state(work, state)
    output = run(["ideviceinstaller", "-u", state["udid"], "uninstall", state["conflict_id"]], timeout=180)
    (work / "uninstall.log").write_bytes(output)
    if "Uninstall: Complete" not in output.decode(errors="replace"):
        raise FixerError("Uninstall completion not confirmed. Inspect the local log.")
    if apps(state["udid"], state["conflict_id"]):
        raise FixerError("Temporary app still appears installed; do not retry the original app yet")
    state["phase"] = "temporary-app-removed"
    save_state(work, state)


def repair(args):
    evidence(args.log, args.app_id, args.conflict_id)
    require_device(args.udid)
    if apps(args.udid, args.conflict_id):
        raise FixerError("An app already occupies this ID. Refusing to overwrite it; inspect manually.")
    # Refuse risky replacement of a working host app's extension.
    if apps(args.udid, args.app_id):
        raise FixerError("The parent app is installed. This residual-record workflow requires it to be absent/offloaded.")
    confirmed(args, {"app_id": args.app_id, "conflict_id": args.conflict_id})
    work, app, state = build(args)
    state["phase"] = "installing"
    save_state(work, state)
    try:
        output = run(["ideviceinstaller", "-u", args.udid, "install", app], timeout=180)
        (work / "install.log").write_bytes(output)
        records = apps(args.udid, args.conflict_id)
        if ("InstallComplete" not in output.decode(errors="replace") or len(records) != 1
                or not installed_cleanup(records[0]) or not records[0].get("Container")):
            raise FixerError("Temporary app installation/container could not be verified")
        state["phase"] = "temporary-app-installed"
        save_state(work, state)
        remove_cleanup(work, state)
    except (Exception, KeyboardInterrupt):
        # Never silently issue destructive recovery after an uncertain failure.
        print(f"Attempt interrupted. State: {work / 'state.json'}\n"
              "Use the cleanup command for this work directory after reconnecting the device.", file=sys.stderr)
        raise
    print("Temporary app removed. Reinstall the original app from the App Store, "
          "then run verify. This is not yet a confirmed repair.")


def capture(args):
    require_device(args.udid)
    path = Path(args.output).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FixerError("Output exists; choose a new log path")
    master, slave = pty.openpty()
    proc = None
    try:
        with path.open("xb") as stream:
            path.chmod(0o600)
            proc = subprocess.Popen(["idevicesyslog", "-u", args.udid, "--no-colors", "-p",
                                     "installd|appstored|installcoordinationd"],
                                    stdout=slave, stderr=slave, start_new_session=True)
            os.close(slave)
            slave = -1
            print("Retry installation on the phone now. Ctrl-C stops capture. Logs stay local.", flush=True)
            deadline = time.monotonic() + args.seconds
            while time.monotonic() < deadline:
                ready, _, _ = select.select([master], [], [], 0.25)
                if ready:
                    try:
                        block = os.read(master, 65536)
                    except OSError:
                        break
                    if not block:
                        break
                    stream.write(block)
                    stream.flush()
                elif proc.poll() is not None:
                    break
    except KeyboardInterrupt:
        pass
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        os.close(master)
        if slave != -1:
            os.close(slave)
    print(f"Saved local log: {path}")


def doctor(args):
    tools = ["idevice_id", "ideviceinfo", "ideviceinstaller", "idevicesyslog", "security", "codesign", "xcrun"]
    missing = [tool for tool in tools if not shutil.which(tool)]
    for tool in tools:
        print(f"{tool}: {'found' if tool not in missing else 'MISSING'}")
    print(f"Platform: {sys.platform}; supported host: macOS")
    if sys.platform != "darwin" or missing:
        raise FixerError("Install dependencies on macOS: brew install libimobiledevice ideviceinstaller; install full Xcode")
    for udid in devices():
        print(f"USB device: {udid}")
    print("Signing identities (local output; do not paste unredacted into public issues):")
    print(run(["security", "find-identity", "-v", "-p", "codesigning"]).decode())


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local repair of log-confirmed iOS extension ID conflicts (experimental)")
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Check local tools, USB devices, and signing identities")
    d = sub.add_parser("diagnose", help="Read a log; no device mutation")
    d.add_argument("log", type=Path)
    d.add_argument("--app-id", help="Filter IDs beneath this parent ID")
    c = sub.add_parser("capture", help="Capture local installation-service logs using a PTY")
    c.add_argument("--udid", required=True)
    c.add_argument("--output", type=Path, required=True)
    c.add_argument("--seconds", type=int, default=120, choices=range(1, 601), metavar="1..600")
    for name in ("build", "repair"):
        b = sub.add_parser(name, help="Build only" if name == "build" else "Build, install, and remove a temporary app")
        for flag in ("app-id", "conflict-id", "udid", "identity"):
            b.add_argument("--" + flag, required=True)
        b.add_argument("--log", type=Path, required=True)
        b.add_argument("--profile", type=Path, required=True)
        b.add_argument("--workdir", type=Path, required=True)
        b.add_argument("--developer-dir", default="/Applications/Xcode.app/Contents/Developer")
        if name == "repair":
            b.add_argument("--yes", action="store_true")
            b.add_argument("--allow-data-loss", action="store_true")
    cl = sub.add_parser("cleanup", help="Remove only a recognized temporary app from an interrupted attempt")
    cl.add_argument("--workdir", type=Path, required=True)
    cl.add_argument("--yes", action="store_true")
    cl.add_argument("--allow-data-loss", action="store_true")
    v = sub.add_parser("verify", help="Check original app and temporary ID inventory after App Store reinstall")
    v.add_argument("--udid", required=True)
    v.add_argument("--app-id", required=True)
    v.add_argument("--conflict-id", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            doctor(args)
        elif args.command == "diagnose":
            found = conflicts(args.log.read_text(errors="replace"))
            if args.app_id:
                valid_bundle(args.app_id)
                found = [x for x in found if x.startswith(args.app_id + ".")]
            print(json.dumps({"conflict_ids": found, "repair_verified": False}, indent=2))
            return 0 if found else 2
        elif args.command == "capture":
            capture(args)
        elif args.command == "build":
            work, app, _ = build(args)
            print(f"Built: {app}\nNo device mutation performed. Local files: {work}")
        elif args.command == "repair":
            repair(args)
        elif args.command == "cleanup":
            work = args.workdir.resolve()
            state = json.loads((work / "state.json").read_text())
            if state.get("tool") != MARKER:
                raise FixerError("Unrecognized state file")
            valid_bundle(state["conflict_id"])
            if state["conflict_id"].lower().startswith("com.apple."):
                raise FixerError("System targets are excluded")
            confirmed(args, state)
            remove_cleanup(work, state)
            print("Temporary app is absent. Reinstallation of original app remains a separate step.")
        elif args.command == "verify":
            valid_bundle(args.app_id)
            valid_bundle(args.conflict_id)
            require_device(args.udid)
            original = apps(args.udid, args.app_id)
            temporary = apps(args.udid, args.conflict_id)
            present = bool(original and original[0].get("Path") and not original[0].get("IsPlaceholder"))
            print(json.dumps({"original_installed": present, "temporary_app_absent": not temporary,
                              "version": original[0].get("CFBundleShortVersionString") if original else None,
                              "next": "Open the original app on the phone to confirm it works."}, indent=2))
            return 0 if present and not temporary else 2
        return 0
    except (FixerError, OSError, ValueError, KeyError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
