# 开发者发布说明

## 本地验证

```powershell
python helper\open_source_check.py
python -m unittest discover -s tests
python -m platformio run -d .
python -m platformio run -d . -e m5stack-sticks3-release
```

开发环境保留 `CORE_DEBUG_LEVEL=3`，发布环境 `m5stack-sticks3-release` 使用 `CORE_DEBUG_LEVEL=0`。

## 生成 Release 文件

```powershell
python helper\prepare_release.py --repo OWNER/REPO --notes "本次更新说明"
```

输出目录：

```text
dist/release/
  firmware.bin
  manifest.json
  helper.json
  StickS3ClaudeCodexHelper.exe   # 如果 helper/dist 下存在，会自动复制
```

如果需要把当前固件快照放进仓库树，可以同步到：

```text
releases/latest/
  firmware.bin
  manifest.json
  helper.json
```

`manifest.json` 字段：

```json
{
  "version": "1.0.0",
  "url": "https://github.com/OWNER/REPO/releases/latest/download/firmware.bin",
  "sha256": "firmware.bin 的 sha256",
  "size": 2957445,
  "notes": "本次更新说明"
}
```

## 一键发布到 GitHub

```powershell
python helper\publish_release.py --repo OWNER/REPO --notes "本次更新说明" --sync-latest
```

这个脚本会：

1. 编译 `m5stack-sticks3-release`
2. 生成 `dist/release/firmware.bin` 和 `manifest.json`
3. 可选同步 `releases/latest/`
4. 通过本机 Git Credential Manager 创建或更新 GitHub Release
5. 替换同名 assets：`firmware.bin`、`manifest.json`，如果存在也会上传 `StickS3ClaudeCodexHelper.exe`

脚本不会读取 `gh` CLI token，也不会打印或保存 GitHub 凭据。请先确认本机 `git push` 能正常使用 GitHub 凭据。

发布脚本默认会做发布前体检：版本同步检查、Python 单元测试、语法检查、必要时 release 固件构建、helper exe 存在性检查、tag 指向检查、上传后 asset 尺寸校验。

## 远程 OTA

固件通过 `REMOTE_OTA_MANIFEST_URL` 读取 manifest。用户在 **设置 → 远程 OTA** 检查更新时，版本不同才会下载 `firmware.bin`；下载后按 `size` 和 `sha256` 校验，再写入 OTA 分区。

`notes` 是可选字段，建议保持在 30 个中文字符以内，板子会在发现新版本后短暂显示。

OTA 检查、下载、写入和校验失败会写入 PC 助手的 `stick_log.txt`，用于定位 GitHub asset、网络或校验问题。

PC 助手里的 **检查助手更新** 读取 GitHub latest Release API，查找 `StickS3ClaudeCodexHelper.exe` 和 `helper.json`。`helper.json` 记录助手 exe 的版本、大小、SHA256 和下载地址，助手下载后会先校验 SHA256，再替换本机 exe。它不检查固件 manifest，避免和板子里的远程 OTA 混淆。

## 版本号

版本源是仓库根目录的 `release.json`。修改版本后运行：

```powershell
python helper\version_sync.py
```

脚本会同步 `src/remote_ota_config.h` 和 `helper/version_info.py`。`src/secrets.h` 只放私有密钥和 `REMOTE_OTA_MANIFEST_URL`，不要定义 `APP_VERSION`。
