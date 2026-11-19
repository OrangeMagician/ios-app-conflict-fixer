# Mechanism and evidence

The observed failure occurs at installation, after purchase validation and download succeed. iOS reports an identifier occupied by an app when the new package needs it for an extension.

Historical TrollStore installation is a plausible source of residual state, consistent with public reports. We did not directly inspect the original container metadata and do not claim to identify which prior operation created it.

## Observed cases

Both were on one iPhone 12 Pro Max, iOS 18.5:

| Original app | Conflicting ID | Final verified version |
| --- | --- | --- |
| Shadowrocket | `com.liguangming.Shadowrocket.PacketTunnel` | 2.2.93 (3488) |
| Outlook | `com.microsoft.Office.Outlook.NotificationServiceExtension` | 5.2635.1 (55308512) |

The user confirmed each original app opened. No account details, device IDs, signing fingerprints, or raw logs are included.

## Why ordinary uninstall was insufficient

In the Shadowrocket case, direct uninstall reported Complete while lower-level logs said no bundle container was found and only unregistered a LaunchServices record. Ordinary reboot did not resolve the conflict.

A minimal app with the conflicting ID installed using a valid development signing setup. Inventory then showed an actual app and data container. Removing it succeeded, after which the original App Store app could install.

The CLI checks temporary installation completion and container presence, then verifies that the standalone helper disappears. Final App Store reinstallation and opening remain separate user steps. The automated CLI has not yet reproduced the entire repair on another broken device; its safety behavior is tested with mocks.

## Signing

The working combination used a matching development profile, certificate/private key, exact application identifier and keychain group, get-task-allow=true, and DER entitlements. An earlier signature was rejected with 0xe8008015; multiple factors changed before success, so no single entitlement is asserted to be the sole cause.

A profile must cover the exact conflicting ID and device. The tool does not change the ID to make signing pass. Free-account provisioning for arbitrary existing vendor extension IDs is unvalidated.

The helper's main function only returns zero; it is never launched. It requests no network-extension or app-group entitlements. Its exact keychain group follows the tested signing setup; no code accesses the keychain. The helper is removed immediately after verifying installation.

## Limits

Not a general App Store repair tool. Other installation codes, storage/network/account issues, or original-package signature problems need separate diagnosis. Current helper target is iOS 18.0+, with only iOS 18.5 documented. Logs can be stale: capture a fresh attempt where possible and verify the target device and scope.

## Sources

- [TrollStore issue #752](https://github.com/opa334/TrollStore/issues/752): similar extension conflict; its workaround depends on TrollStore and differs from this procedure.
- [Apple TN2415](https://developer.apple.com/library/archive/technotes/tn2415/): entitlement matching.
- [Apple TN3125](https://developer.apple.com/documentation/technotes/tn3125-inside-code-signing-provisioning-profiles): provisioning constraints.
- [ideviceinstaller](https://github.com/libimobiledevice/ideviceinstaller): installation and inventory interface.
