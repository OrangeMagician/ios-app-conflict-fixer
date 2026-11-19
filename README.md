# iOS App Conflict Fixer

A local macOS CLI for diagnosing **extension bundle identifier conflicts** that prevent App Store reinstallation after TrollStore use or an iOS update.

[中文说明](README.zh-CN.md) · [Mechanism and cases](docs/how-it-works.md)

**Experimental v0.1.0.** The underlying manual procedure restored Shadowrocket and Outlook on one iPhone 12 Pro Max running iOS 18.5. The packaged CLI has automated safety tests and a local build check; broader device compatibility has not been established. A cloud icon alone does not identify this fault.

## Scope

Only explicit errors like this qualify:

```text
MIInstallerErrorDomain Code=81 / BundleIDConflictWithOtherIdentifier
App with identifier org.example.mail.NotificationExtension is already installed,
so we can't install app extension with that same identifier.
```

The tool builds an empty development-signed app with the exact conflicting extension ID, installs it, verifies its container, then uninstalls it through iOS installation services. You reinstall the original app from the App Store afterward.

**This may remove local data associated with the extension.** Signing in again or restoring configuration may be necessary. It requires your own development certificate/private key in the Mac keychain and a valid provisioning profile covering your device and the exact ID. This project provides no signing files and does not implement automatic Apple-account login or provisioning.

## Requirements and setup

- macOS, Python 3.10+, full Xcode with iPhoneOS SDK.
- Connected, unlocked iPhone that trusts this Mac.
- Current helper target: arm64, iOS 18.0+. Documented manual success: iOS 18.5 only.
- `libimobiledevice`, `ideviceinstaller` with 1.2.x command syntax.
- Your own matching iOS development signing setup.

```sh
brew install libimobiledevice ideviceinstaller
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
ios-conflict-fixer doctor
```

`doctor` shows device IDs and signing identities locally. Redact this output before sharing. `--developer-dir` selects another Xcode location for build/repair.

## Diagnose

Start capture, then reproduce the failed installation on your phone:

```sh
ios-conflict-fixer capture --udid YOUR_DEVICE_ID --output local/install.log --seconds 120
ios-conflict-fixer diagnose local/install.log --app-id org.example.mail
```

Ctrl-C stops capture. Logs remain local and may contain personal information. No matching conflicts returns exit code 2. The PTY capture avoids delayed output caused by stdout buffering. Use recent logs from the selected device; do not guess extension IDs.

## Build or repair

Find your certificate fingerprint with `security find-identity -v -p codesigning`. Select an existing matching development profile from Xcode. Wildcard profiles work only when they cover the exact target and device.

```sh
ios-conflict-fixer build \
  --udid YOUR_DEVICE_ID \
  --app-id org.example.mail \
  --conflict-id org.example.mail.NotificationExtension \
  --log local/install.log \
  --profile /path/to/your.mobileprovision \
  --identity YOUR_CERTIFICATE_SHA1 \
  --workdir local/build-preview
```

`build` verifies expiry, device/certificate membership, ID coverage, and code signature. It performs no device mutation.

To install and remove the temporary app, use **`repair` instead of `build`**, with a fresh `--workdir local/repair-01`. Review the exact target and type its ID when prompted. Noninteractive use requires both `--yes --allow-data-loss`.

Repair refuses Apple system IDs, IDs outside the specified parent app, missing log evidence, existing standalone apps with the target ID, and a currently installed parent app. Do not remove a working parent app solely to bypass this check; inspect its state and protect its data first.

Reinstall the original app from the App Store, then:

```sh
ios-conflict-fixer verify --udid YOUR_DEVICE_ID \
  --app-id org.example.mail --conflict-id org.example.mail.NotificationExtension
```

Open the original app yourself. Inventory cannot prove that login, notifications, VPN, or every function works. If another extension conflict appears, capture fresh evidence and handle that extension separately. Temporary app removal is not yet a confirmed repair.

## Interrupted attempts

Keep the work directory and its private `state.json`. Reconnect/unlock the same device and run:

```sh
ios-conflict-fixer cleanup --workdir local/repair-01
```

Cleanup removes only an app recognized as the temporary helper by name and path, and verifies disappearance. Failed installation does not trigger blind removal. This tool never erases, upgrades, reboots, jailbreaks, or disables device security.

## Development

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Tests use synthetic profiles and mocked devices, and never touch a phone. No GitHub Actions workflows are used.

## Privacy and contributions

No telemetry, backend, or upload command. It invokes local Apple/libimobiledevice tools; Apple platform services may perform their normal signing validation. Never commit raw logs, UDIDs, account details, signing material, or populated work directories. `.gitignore` assists but does not replace reviewing commits.

For issues share only app name, device model, iOS version, tool version, and a redacted conflict error. Do not paste complete `doctor` output or `state.json`. Report successes and failures without claiming universal compatibility.

Original code: MIT license. External tools retain their own licenses; separately installed `ideviceinstaller` is GPL-2.0. This repository distributes no vendor app binaries, certificates, or private keys.

References: [TrollStore issue #752](https://github.com/opa334/TrollStore/issues/752), [Apple TN2415](https://developer.apple.com/library/archive/technotes/tn2415/), [Apple TN3125](https://developer.apple.com/documentation/technotes/tn3125-inside-code-signing-provisioning-profiles), [ideviceinstaller](https://github.com/libimobiledevice/ideviceinstaller).
