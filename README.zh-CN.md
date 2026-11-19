# iOS App Conflict Fixer

在 Mac 上诊断和定点处理 iPhone 的 **App 扩展标识冲突**。常见经历是：之前用巨魔安装过 App，升级系统或移除后，App Store 下载完成却安装失败，图标回到云朵状态。

**实验版本 0.1.0。** 手工修复原理已在同一台 iPhone 12 Pro Max / iOS 18.5 上修复 Shadowrocket 和 Outlook。打包后的 CLI 已通过自动测试和本机构建检查，尚未进行更多设备验证。云朵图标本身不能证明是这个问题。

## 工作方式

仅处理日志明确出现的错误：

```text
MIInstallerErrorDomain Code=81 / BundleIDConflictWithOtherIdentifier
App with identifier org.example.mail.NotificationExtension is already installed,
so we can't install app extension with that same identifier.
```

用冲突扩展的精确标识构建一个空的开发签名应用，让 iOS 安装服务识别实际容器，再完整卸载，最后从 App Store 重装正式应用。空应用不运行清理代码，也不需要打开。

处理可能删除该扩展的本地数据，原 App 可能需要重新登录或导入配置。需要用户自己的开发证书、钥匙串私钥和覆盖当前设备及目标标识的有效描述文件。本项目不提供签名，不自动登录 Apple 账号或申请描述文件。

## 安装和使用

需要 macOS、Python 3.10+、完整 Xcode，以及连接并解锁、已信任本机的 iPhone。当前构建目标是 arm64 / iOS 18.0+，已有成功案例为 iOS 18.5。

```sh
brew install libimobiledevice ideviceinstaller
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
ios-conflict-fixer doctor
```

采集时在手机重试安装：

```sh
ios-conflict-fixer capture --udid YOUR_DEVICE_ID --output local/install.log --seconds 120
ios-conflict-fixer diagnose local/install.log --app-id org.example.mail
```

确认精确冲突标识后执行：

```sh
ios-conflict-fixer repair \
  --udid YOUR_DEVICE_ID \
  --app-id org.example.mail \
  --conflict-id org.example.mail.NotificationExtension \
  --log local/install.log \
  --profile /path/to/your.mobileprovision \
  --identity YOUR_CERTIFICATE_SHA1 \
  --workdir local/repair-01
```

签名指纹用 `security find-identity -v -p codesigning` 在本机查看。`build` 接受相同参数，只生成并验证空应用，不改变手机。每次选择新的工作目录；`--developer-dir` 可指定 Xcode 路径。

修复会展示目标并要求输入精确扩展标识。非交互执行必须同时指定 `--yes --allow-data-loss`。工具拒绝系统标识、跨应用标识、没有明确日志证据的标识及已安装的独立应用。主应用仍安装时也会停止，请先确认其状态和数据保护方式，不要仅为通过检查而删除正常应用。

从 App Store 重装后验证：

```sh
ios-conflict-fixer verify --udid YOUR_DEVICE_ID \
  --app-id org.example.mail --conflict-id org.example.mail.NotificationExtension
```

还需要在手机上打开正式应用验证。安装清单不能证明登录、通知、VPN 等功能都正常。如果出现另一个扩展冲突，先采集新日志再单独处理。

中断时保留工作目录，重新连接同一设备后执行：

```sh
ios-conflict-fixer cleanup --workdir local/repair-01
```

仅清理能识别为本工具创建的临时应用。安装失败不会盲目执行卸载。工具不执行抹机、升级、重启、越狱或关闭安全保护。

## 隐私和验证

- 无遥测、服务器或上传功能；Apple 工具仍可能按系统机制进行签名验证。
- 日志、状态文件、签名和设备信息仅留本机，不应提交到 GitHub。
- 报告问题只提供机型、系统版本、App 名称、工具版本和脱敏错误。不要提交完整日志、`doctor` 输出、证书、私钥、UDID 或 `state.json`。
- 退出码：0 表示命令成功；1 表示检查/执行失败；2 表示未发现目标冲突或验证不通过；130 表示取消。临时卸载成功不等于正式应用已修复。
- 自动测试：`PYTHONPATH=src python3 -m unittest discover -s tests -v`，不操作真实手机。项目不使用 GitHub Actions。

详细机制和案例见 [原理说明](docs/how-it-works.md)，完整命令说明见 [English README](README.md)。原始源码采用 MIT 许可证，外部依赖遵循其各自许可证。
