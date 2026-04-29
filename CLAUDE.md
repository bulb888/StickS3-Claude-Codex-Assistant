# StickS3 Claude Codex 小秘书 — 开发备忘

M5Stack StickS3（ESP32-S3-PICO-1-N8R8，8MB Flash + 8MB PSRAM，1.14" LCD 135x240）上的 Claude / Codex 语音小秘书，也包含仪表盘、红外、电台和 OTA。用 PlatformIO + Arduino 框架开发。

## 工程结构

```
.\ 
├── platformio.ini                    # 板子和库依赖 + OTA 配置
├── partitions_8MB_huge.csv           # 双 OTA 分区（app0/app1 各 3.5MB + spiffs 0.95MB）
├── .claude/settings.json             # Claude Code 钩子（Pre/Post/Stop/UserPrompt → hook_notify.py）
├── src/
│   ├── app.h                         # 共享常量 + 函数原型 + g_radio_owns_i2s
│   ├── main.cpp                      # setup()/loop()、draw_title（含 WiFi 信号格 + 电量）、屏保、异步 stick_log
│   ├── menu.cpp                      # 主菜单（当前 7 项，顺序见下）
│   ├── wifi_mgr.cpp                  # WiFiManager 配网（城市码+B站UID+讯飞三项）+ 首次设置摘要 + NTP
│   ├── app_clock.cpp                 # 仪表盘：NTP 时钟 + 天气 + B 站粉丝（NVS 可配置）
│   ├── app_ir.cpp                    # 红外学习/回放（4 个槽位，NVS 存储）
│   ├── app_radio.cpp                 # 网络电台（22 台，ESP32-audioI2S）— 见"电台 I2S 接力"
│   ├── app_voicekb.cpp               # Claude 小秘书（讯飞 STT + PC 助手 + 活动日志）
│   ├── app_codex.cpp                 # Codex 小秘书（讯飞 STT + PC 助手 + Codex Hooks 状态）
│   ├── voice_assistant_common.cpp    # 两个小秘书共享的 HMAC/Base64/URL/UTF-8 分行工具
│   ├── app_ota.cpp                   # OTA 升级模式（停音频/关麦克风/ArduinoOTA + 进度条）
│   └── app_settings.cpp              # 音量/亮度/自动旋转/屏幕超时/WiFi/远程 OTA/系统诊断
├── helper/
│   ├── type_server.py                # Windows 托盘助手（粘贴 + /status + /log + UDP 发现）
│   ├── hook_notify.py                # Claude Code 钩子脚本 → POST /status
│   ├── prepare_release.py            # 编译 release 环境并生成 firmware.bin + manifest.json
│   ├── publish_release.py            # 使用本机 GitHub 凭据创建/更新 GitHub Release
│   ├── stick_log.txt                 # 板子和助手的事件日志（调试用）
│   └── dist/                         # PyInstaller 输出目录（exe 建议放 GitHub Release）
└── downloads/                        # PlatformIO 工具链离线包（可忽略）
```

## 主菜单（顺序固定）

1. **Claude 小秘书** — 语音输入到 PC + 实时显示 Claude 活动
2. **Codex 小秘书** — 语音输入到 Codex + 实时显示 Codex 状态
3. **仪表盘** — 时钟 / 天气 / B 站粉丝（城市码和 UID 进 WiFi 配网页填）
4. **红外遥控** — 学习 / 回放
5. **网络电台** — 在线收音机（22 台）
6. **OTA 升级** — 无线烧录固件
7. **设置** — 音量 / 亮度 / 屏幕超时 / 自动旋转 / WiFi 配网 / 远程 OTA / 系统诊断；设置页按 4 项分页显示

## 构建和烧录

PlatformIO 通过 `python -m platformio` 调用（没装 `pio` 快捷命令）。**不要设 `HTTPS_PROXY`**，用户 TUN 模式已经全局代理，再设反而变慢。

```bash
# 只编译
python -m platformio run -d .

# 发布版编译（关闭核心调试日志）
python -m platformio run -d . -e m5stack-sticks3-release

# 编译 + 上传（使用 platformio.ini 当前 upload_port）
python -m platformio run -d . -t upload

# 串口上传（只有首次烧录或 OTA 不可用时使用）
python -m platformio run -d . -t upload --upload-port COM6

# 串口监视
python -m platformio device monitor -d .
```

**COM 端口坑**：板子每次烧录后重启会把 USB 设备重新枚举，接下来几次烧录很可能找不到 COM6。**失败后让用户拔 USB 重插**，再重试。用 `powershell -Command "[System.IO.Ports.SerialPort]::GetPortNames()"` 查当前可用端口。**OTA 是绕开 COM 端口坑的正道**，板子开机已经有固件的情况下优先用 OTA。

## OTA 无线烧录（默认方式）

分区表 `partitions_8MB_huge.csv` 是双槽布局：`ota_0` + `ota_1` 各 3.5MB，升级时写入备用槽、切 `otadata`，下次启动用新固件。首次必须串口烧录一次才能生效。

```bash
# 板子进 "OTA 升级" App（屏幕会显示 IP，如 192.168.31.x）
# 在 PC 上：
python -m platformio run -d . -t upload --upload-port <板子IP>
# 端口改成 IP 会自动走 espota (UDP)，~65 秒跑完，板子自动重启
```

`platformio.ini` 当前默认走 `upload_protocol = espota`，所以正常开发上传直接跑 `pio run -t upload` 或 `python -m platformio run -d . -t upload`。mDNS 不可靠，直接用 OTA 页面显示的 IP 最稳；如果设备 IP 变了，先改 `platformio.ini` 的 `upload_port` 或在命令里覆盖。OTA 模式会 `M5.Speaker.end() + M5.Mic.end()`，避免 I2S/Flash 抢资源导致 PANIC。曾经踩过的坑：分区表缺 `ota_1` → OTA 启动直接 panic；OTA 回调里调用 `stick_log`（HTTP）→ 升级中途 WiFi 栈卡死。

## 远程 OTA（设置里触发）

入口在 **设置 → 远程 OTA**。`app_remote_ota_run()` 读取 `REMOTE_OTA_MANIFEST_URL`，下载 JSON manifest，字段为 `version` / `url` / `sha256` / `size` / 可选 `notes`。版本等于 `APP_VERSION` 时提示已是最新版；否则先显示更新说明，再用 HTTPS 下载 `firmware.bin`，按 `size` 写入 `Update` OTA 分区，并计算 SHA256 校验。默认 `REMOTE_OTA_MANIFEST_URL` 由 `release.json` 的 GitHub 仓库地址同步到 `src/remote_ota_config.h`；私有源或测试源可在本机 `src/secrets.h` 或 build flags 里覆盖。当前 HTTPS 用 `WiFiClientSecure::setInsecure()`，依赖 SHA256 做固件完整性校验。发布包可用 `python helper\prepare_release.py --repo OWNER/REPO --notes "..."` 生成到 `dist/release/`；一键发布用 `python helper\publish_release.py --repo OWNER/REPO --notes "..." --sync-latest`，脚本走本机 Git Credential Manager，不依赖 `gh` token。

## 关键依赖版本

- `espressif32@6.12.0`（平台）
- `ESP32-audioI2S@3.0.0`（pinned — 新版用 C++20 `<span>`，GCC 8.4 编译不过）
- `WiFiManager ^2.0.17`，`ArduinoJson ^7.0.4`，`IRremoteESP8266 ^2.8.6`，`arduinoFFT ^2.0.2`
- `WebSockets ^2.4.1`（讯飞 STT 用）

## 已解决的硬件/软件坑

- **屏幕频闪**：M5GFX 默认背光 PWM ~1kHz 有可见频闪。`main.cpp` 里 `ledcSetup(3, 20000, 8) + ledcAttachPin(38, 3)` 强制 20kHz，`apply_brightness()` 直接 `ledcWrite(3, ...)`。
- **全屏刷新闪烁**：改用 `M5Canvas g_canvas`（PSRAM 离屏画布），每帧先画到画布再 `pushSprite(0,0)`。
- **音频库 C++20 依赖**：`schreibfaul1/ESP32-audioI2S` 新版引入 `<span>`，pin 到 `3.0.0` 避开。
- **StickS3 的 I2S 数据引脚是 GPIO 14，不是 16**：ES8311 codec 的 DIN 接在 14。M5Unified `_speaker_enabled_cb_sticks3` 里的 `spk_cfg.pin_data_out = GPIO_NUM_14` 是官方定义。用 16 能编译、`isRunning()` 返回 true、但声音**压根送不到 codec**，表现是"电流声或静默"。今天花了半天才定位。**参考 Audio 库示例时别抄他们的 16，StickS3 是 14**。
- **`M5.Speaker.end()` 会悄悄把 codec + 功放电源全关**：它的 `_cb_set_enabled(false)` 会 `bitOff(py32pmic @ 0x6E, reg 0x11, bit 3)` 关掉扬声器功放电轨，再把 ES8311 DAC 断电。Audio 库接管 I2S 后**毫不知情**，只负责写样本，codec 没电不工作 = 没声音。`app_radio.cpp` 的 `restoreStickS3CodecPower()` 手动按 M5Unified enable 序列（`bitOn 0x6E/0x11/bit3` + ES8311 的 8 个寄存器写）重新上电。**这也是之前电台"随机哑掉 / 只有电流声"的真正根因**，所有 fade / debounce / recreate 都是治标。
- **Audio 库析构函数会阻塞 task WDT 触发 panic**：`~Audio()` 里 `client.stop()` / `clientsecure.stop()` 等 TCP FIN 握手，**长会话积累后能阻塞 > 5 秒**，FreeRTOS Task WDT 触发 → 整板 PANIC。退出电台时**故意不 delete**，直接 `s_audio = nullptr` 泄漏 10KB，避开 dtor。30 次进出之后还有余量。
- **电台切台**：只需 `stopSong() + delay(100) + connecttohost()`，不要任何 fade / debounce / state machine——那些都是被假 bug 骗出来的。连上后 watchdog 检测 `isRunning()`，死流提示用户按 B 重试即可。
- **电台 I2S 接力（两头都要手动操心 codec）**：见下面"电台 I2S 接力"一节。
- **语音键盘蓝牙方案 fail**：T-vK/ESP32-BLE-Keyboard + NimBLE 在 S3 上连接不稳；改用 **HTTP POST 到 PC 助手** 方案。
- **讯飞 STT 要 GMT 时间**：签名需要当前时间，WiFi 一连上就 `configTzTime`。
- **PC 助手原先 pyautogui/pyperclip 不稳**：多次迭代后改用 **Win32 剪贴板 API + SendInput Ctrl+V**。记得对所有 API 显式设 `argtypes`（64 位 HANDLE 不能用默认 c_int，会 OverflowError）。
- **剪贴板 `GlobalAlloc` 泄漏**：`set_clipboard_text` 失败路径（`GlobalLock`/`SetClipboardData` 返 false）要 `GlobalFree(hmem)`，成功时置 null 让 finally 别 free。
- **`stick_log` 异步化**：同步 HTTP POST（600ms 超时）在助手关着时每次 `stick_log` 阻塞主循环 → 进 App 卡 1-2 秒。改成 FreeRTOS 队列 + 后台 worker（栈 8KB，HTTPClient + mbedTLS 要这么大），连续 3 次失败进入 15 秒冷却。
- **PyInstaller frozen 路径陷阱**：`__file__` 在 exe 运行时指向 `_MEIxxx` 临时目录，exe 退出就删。`type_server.py` 的 `_THIS_DIR` 要 `getattr(sys, "frozen") ? dirname(sys.executable) : dirname(__file__)`，否则 `config.json` 和 `stick_log.txt` 都写到看不见的地方。

## 电台 I2S 接力（M5.Speaker ↔ Audio lib）

两个库都想独占 I2S0，硬件上只有一条到 ES8311 codec 的路。架构上：
- **非电台时**：M5.Speaker 占用 I2S，负责开机提示音、按键 beep、设置里的音量提示音
- **电台运行中**：Audio 库占用 I2S，`g_radio_owns_i2s = true` 把 `beep_ok` / `apply_volume` 静默掉

**进电台（握手）**：
```cpp
M5.Speaker.end();              // 停 spk_task、卸 I2S 驱动、通过回调关 codec
delay(300);
i2s_driver_uninstall(I2S_NUM_0);  // 兜底
restoreStickS3CodecPower();    // 手动 I2C 写 py32pmic + ES8311，重新上电
s_audio = new Audio();         // Audio 库的构造函数内部 i2s_driver_install
s_audio->setPinout(17, 15, 14, 18);  // BCLK/LRC/DOUT/MCLK
g_radio_owns_i2s = true;
```

**退电台（交还）**：
```cpp
stopPlay();                    // Audio 停流
i2s_driver_uninstall(I2S_NUM_0);  // 把 Audio 的 I2S 驱动卸掉
s_audio = nullptr;             // 故意不 delete（dtor panic）
g_radio_owns_i2s = false;
M5.Speaker.begin();            // 装自己的 I2S 驱动，通过回调 codec 上电
apply_volume();
```

每次进电台会泄漏约 10KB（老 Audio 实例）。300KB 堆可以扛 30 次进出，日常使用足够，重启清零。

## 语音键盘链路（当前方案）

```
板子麦克风采样 PCM (16kHz 16bit)
  → 讯飞 WebSocket STT（HMAC-SHA256 签名 URL）
  → 识别文字
  → HTTP POST http://<PC IP>:8765/type
  → Python 助手 type_server.py
  → Win32 SetClipboardData (UTF-16LE CF_UNICODETEXT)
  → SendInput Ctrl+V（1 次 4 个按键事件）
  → delay 200ms
  → SendInput VK_RETURN
```

PC 助手是 **Windows 托盘程序**，常驻运行两种启动方式：
- 开发期：`python helper/type_server.py`
- 打包后：双击 `helper/dist/StickS3ClaudeCodexHelper.exe`（单文件 exe 建议作为 GitHub Release 资产发布；`config.json` 和 `stick_log.txt` 也写在 `dist/` 下，不进临时目录）

托盘右键菜单可打开配置对话框（tkinter），里面能改端口、开关 UDP 发现、设置开机自启（通过 Startup 文件夹放快捷方式）、编辑纠错表、检查并安装助手更新、导出诊断包。配置存 `helper/config.json`，带 `config_version` 迁移；旧的可编辑更新地址字段会自动清掉。助手更新检查读取 GitHub latest Release API 并查找 `StickS3ClaudeCodexHelper.exe` + `helper.json`，下载后按 SHA256 校验再替换 exe；固件更新仍只在板子 **设置 → 远程 OTA** 里做。

托盘右键还提供四个输入目标绑定入口：`绑定 Claude 输入目标`、`绑定 Codex 输入目标`、`绑定 Claude 输入框位置`、`绑定 Codex 输入框位置`。窗口绑定会在 3 秒倒计时后抓当前前台窗口；输入框位置绑定会抓鼠标点位，并保存窗口尺寸、相对比例、右侧/底部距离，窗口缩放后会尽量按底部输入框位置重算。绑定成功/失败、清除绑定都会发托盘通知并短暂改托盘标题。当前方案用于 VS Code 内 Codex/Claude 输入框未直接获得焦点时的粘贴稳定性；不要改回单纯“跟随焦点”。

**PC IP 不再硬编码在固件里**。板子通过 UDP 广播做 **自动发现**：
- 板子发 `STICK?` 到 UDP 端口 8766
- 助手回 `STICK! <ip>:<port>`
- 板子把结果存 NVS（`pc.ip` / `pc.port`），下次开机先试缓存再广播
所以 PC IP 变了通常不用重烧，最多进语音助手 App 触发一次重发现。

常见误识别替换表在 `type_server.py` 的 `CORRECTIONS` 里（如 `克拉克 → Claude`，`cloud code → Claude Code`），托盘里改完保存即可生效，不用重烧板子。

**讯飞 STT 凭据不再硬编码在固件里**。WiFiManager 配网页提供"一键粘贴三项"和单独的 `APPID`、`APISecret`、`APIKey` 输入框，保存到 NVS namespace `xfyun`，键名分别是 `appid` / `secret` / `key`。`xfyun_load_credentials()` 优先读取 NVS；只有 NVS 三项不完整时才使用可选 `src/secrets.h` 默认值。仓库只提供 `src/secrets.example.h` 模板，真实 `src/secrets.h` 已加入 `.gitignore`，开源时不要提交。版本源是 `release.json`，用 `python helper/version_sync.py` 同步 `APP_VERSION` 和助手版本；不要在 `src/secrets.h` 里定义 `APP_VERSION`。缺任何一项会提示 `请先配讯飞 API`；讯飞握手 `HTTP 401` 会提示 `讯飞鉴权失败 401`，通常是 `APISecret` / `APIKey` 填反或不是同一个 IAT 应用。

## Claude 活动实时显示

板子的"Claude 语音助手" App 左栏滚动显示 Claude Code 正在做什么。链路：

```
Claude Code (钩子 PreToolUse / PostToolUse / Stop / UserPromptSubmit)
  → hook_notify.py 读 stdin JSON
  → POST http://127.0.0.1:8765/status  (加 marker \x01U / \x01C 区分用户/回复)
  → type_server.py 维护环形缓冲（每条带单调 seq）
  → 板子 GET /status 轮询，diff seq 拉新事件
  → 动画队列按 500ms 节奏揭示，带 UTF-8 安全截断 + 2 行换行
```

钩子配置在 `.claude/settings.json`。`hook_notify.py` 有几个关键点：
- 用 `sys.stdin.buffer.read().decode("utf-8", errors="replace")` 读 JSON，**绝对不要**用默认文本模式（Windows 下是 GBK，JSON 中文会解码崩）
- `ProxyHandler({})` 强制 localhost 请求不走代理（用户开着 TUN 代理）
- Stop 钩子读 `transcript_path` 的最后一条 assistant 消息，即使解析失败也要发一条空 `\x01C`，否则板子右边的"运行中"标签不会回到"空闲"

板子上不同类型事件用颜色区分：用户输入青色、Claude 回复琥珀色、工具调用橙/灰色。`\x01C`（Claude 回复）揭示时会发两声短促提示音。

## Codex 活动实时显示

Codex 小秘书和 Claude 小秘书共用 PC 助手，但使用独立端点与绑定：

```
Codex Hooks (UserPromptSubmit / PreToolUse / PostToolUse / Stop)
  → hook_notify.py
  → POST http://127.0.0.1:8765/codex/status
  → type_server.py 维护 Codex 独立环形缓冲与 state
  → 板子 GET /codex/status
```

`/codex/status` 会显式返回 `state`：`thinking` / `running` / `idle`。板子端优先信这个字段，不再靠旧 JSONL watcher 推断。助手重启后 `latest_seq` 会回到 0，固件检测到 seq 回退会清空左侧历史；这是预期行为，用户已确认助手重启时不用保留旧历史。Codex 官方 hooks 需要在 `%USERPROFILE%\.codex\config.toml` 里开启 `[features] codex_hooks = true` 并配置四类 hook；详见 `memory/codex_hooks_status.md`。

## 按键约定（所有 App）

- **BtnA 短按**：主动作（菜单切换 / 录音 / 电台下一台 / 等）
- **BtnA 长按 ~0.9 秒**：逆向动作（电台里 = 上一台；其他 App 暂未用）
- **BtnB 短按**：确认 / 进入 / 播放暂停
- **BtnB 长按 ~0.9 秒**：返回上一层
- 菜单里按 B 进 App 时：`menu_run` 会把 B 按压吃干净（内部 `while(isPressed()) delay`）避免进入目标 App 时 B 的释放被误识别（之前栽过音量 +1、电台直接开播等坑）
- Claude 小秘书特殊：短按 B 在 2 秒冷静期后是"测试发送"（发 `hello from StickS3`）
- 屏保：默认 60 秒无操作自动熄屏，按任意键唤醒；OTA App 常开屏不熄灭

## 硬件特性（无法软件修复）

- **充电时喇叭吱啦声**：充电 IC 开关噪声耦合到功放电源，换电源头或用电池能改善
- **BLE A2DP 不支持**：ESP32-S3 只有 BLE，没蓝牙经典，**连不了蓝牙音箱**

## 标题栏布局（`draw_title()`）

所有 App 共享统一标题栏（22 px 高）：
```
[✦ Claude sparkle]  App 名称            [WiFi 信号 3 格]  XX%  [电池图标]
```
- WiFi 信号格子根据 `WiFi.RSSI()`：≥ -60 dBm 3 格绿 / ≥ -72 dBm 2 格绿 / ≥ -85 dBm 1 格黄 / < -85 dBm 1 格红 / 未连接 灰 X
- `draw_status_bar()` 现在是 no-op（底部不再占 16 px），App 底部提示文字可以放到 `SCR_H - 10` 附近

## 主菜单字体和布局注意

主菜单目前用 `efontCN_14` 绘制左侧应用名、`efontCN_12` 绘制右侧说明，行高 `16`、起始 `y = 24`。文字使用 `top_left/top_right` 定位，而不是 `middle_left/middle_right`，目的是绕开中文字体基线导致的上下笔画被裁切。选中高亮高度必须保留完整 `item_h`，不要再改回 `item_h - 2`。

不要把主菜单字体切到 `fonts::lv_font_simsun_14_cjk`：实机测试会出现大量方块字。当前字体仍有轻微字形观感问题，用户已接受“就这样”，后续除非明确要求，不要继续折腾字体。

## 内存系统参考

本地开发时如果有 Claude/Codex 的私有 memory 目录，可以作为上下文参考；这类文件不要随仓库发布。公开仓库里的准确信息以 `README.md`、`USER_MANUAL.md` 和本文件为准。
