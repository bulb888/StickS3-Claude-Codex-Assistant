#include "app.h"
#include <WiFi.h>
#include <Preferences.h>
#include <cstring>
#include "Audio.h"
#include "driver/i2s.h"

// ============================================================================
// 网络电台 — 简化版重写
//
// 设计原则（对应社区 M5Stack web radio 的成熟做法）：
//   1. 进入电台时把 I2S 从 M5.Speaker 交给 ESP32-audioI2S，退出时再交回。
//      电台播放期间按键 beep 由 g_radio_owns_i2s 这个全局 flag gate 掉。
//   2. Audio 实例是一个堆上的单例，第一次进 radio app 时创建，**从不 delete**
//      （库的析构函数在长 TCP 会话后会阻塞 client.stop() 超过 task WDT → panic）。
//   3. 切台 = stopSong() + 短 delay + connecttohost()。没有 fade、没有 debounce、
//      没有 state machine。用户快速切台的防冲击由"上一步 stopSong 的内部 IO 等待"
//      自然限速。
//   4. 流死亡 = 屏幕提示"按 B 重试"，用户自己按 B 触发。watchdog 不做自动 recreate
//      （之前的自动重试曾导致无限递归 + POWERON 崩）。
//   5. 退出 = stopSong()。仅此而已，不触碰 I2S、不 delete、不 reinit speaker。
// ============================================================================

// StickS3 I2S pins — these are the OFFICIAL M5Unified config for the
// ES8311 codec on this board (M5Unified.cpp case board_M5StickS3). The
// data-out pin was previously wrong (16 instead of 14), which is why
// samples never reached the codec and we spent the whole day chasing
// phantom "noise / no sound / stalled stream" bugs.
static const int I2S_MCLK = 18;
static const int I2S_BCLK = 17;
static const int I2S_LRC  = 15;   // ws / LRCLK
static const int I2S_DOUT = 14;   // data out → codec DIN

struct Station { const char* name; const char* url; };

static const Station kStations[] = {
  {"杭州交通918",    "http://lhttp.qingting.fm/live/4915/64k.mp3"},
  {"杭州音乐968",    "http://lhttp.qingting.fm/live/4864/64k.mp3"},
  {"杭州西湖之声",   "http://lhttp.qingting.fm/live/4865/64k.mp3"},
  {"央广中国之声",   "http://lhttp.qingting.fm/live/4970/64k.mp3"},
  {"央广经济之声",   "http://lhttp.qingting.fm/live/4972/64k.mp3"},
  {"Radio Paradise", "http://stream.radioparadise.com/mp3-128"},
  {"RP Rock",        "http://stream.radioparadise.com/rock-128"},
  {"RP Mellow",      "http://stream.radioparadise.com/mellow-128"},
  {"SomaFM Groove",  "http://ice1.somafm.com/groovesalad-128-mp3"},
  {"SomaFM Lush",    "http://ice1.somafm.com/lush-128-mp3"},
  {"SomaFM Drone",   "http://ice1.somafm.com/dronezone-128-mp3"},
  {"SomaFM Indie",   "http://ice1.somafm.com/indiepop-128-mp3"},
  {"BBC World",      "http://stream.live.vc.bbcmedia.co.uk/bbc_world_service"},
  {"France Inter",   "http://icecast.radiofrance.fr/franceinter-midfi.mp3"},
  {"FIP 法国",       "http://icecast.radiofrance.fr/fip-midfi.mp3"},
  {"Deutschland",    "http://st01.dlf.de/dlf/01/128/mp3/stream.mp3"},
  {"Swiss Classic",  "http://stream.srg-ssr.ch/m/rsc_de/mp3_128"},
  {"Swiss Jazz",     "http://stream.srg-ssr.ch/m/rsj/mp3_128"},
  {"Classic FM UK",  "http://media-ice.musicradio.com/ClassicFMMP3"},
  {"Smooth Radio",   "http://media-ice.musicradio.com/SmoothUKMP3"},
  {"KEXP Seattle",   "http://live-mp3-128.kexp.org"},
  {"Nightride FM",   "http://stream.nightride.fm/nightride.mp3"},
};
static const int N_STATIONS = sizeof(kStations) / sizeof(kStations[0]);

static Audio*   s_audio = nullptr;
static int      s_idx = 0;
static bool     s_playing = false;
static String   s_stream_title;
static String   s_err_msg;
static uint32_t s_err_t = 0;

static const uint16_t RADIO_CONNECT_TIMEOUT_MS = 3000;
static const uint16_t RADIO_CONNECT_TIMEOUT_SSL_MS = 3500;
static const uint32_t RADIO_SLOW_CONNECT_MS = 2500;
static uint32_t s_last_audio_info_log_ms = 0;

static bool radioAudioInfoIsImportant(const char* info) {
  if (!info || !*info) return false;
  return strstr(info, "Connect to")
      || strstr(info, "connect to")
      || strstr(info, "established")
      || strstr(info, "redirect")
      || strstr(info, "timeout")
      || strstr(info, "failed")
      || strstr(info, "Request")
      || strstr(info, "HTTP/1.1");
}

// Audio lib callbacks (library requires these to be defined globally even if unused)
void audio_info(const char* info) {
  if (!radioAudioInfoIsImportant(info)) return;
  uint32_t now = millis();
  if (now - s_last_audio_info_log_ms < 250) return;
  s_last_audio_info_log_ms = now;
  const char* level = (strstr(info, "timeout") || strstr(info, "failed")) ? "warn" : "debug";
  stick_log(level, String("radio audio: ") + info);
}
void audio_showstation    (const char*)      {}
void audio_showstreamtitle(const char* info) { s_stream_title = info; }
void audio_bitrate        (const char*)      {}
void audio_commercial     (const char*)      {}
void audio_icyurl         (const char*)      {}
void audio_lasthost       (const char*)      {}

// ============ manually re-power the StickS3 codec after M5.Speaker.end ============
// M5.Speaker.end() invokes _speaker_enabled_cb_sticks3(enabled=false) which
// turns OFF the PMIC's speaker-amp rail (py32pmic @ 0x6E, reg 0x11 bit 3)
// AND powers down the ES8311 DAC. Audio library never touches the codec
// and has no idea, so samples go out on I2S → arrive at a dead codec →
// no sound. We replicate the ENABLE half of M5Unified's callback to wake
// the codec back up before handing off to Audio lib.
// Source: M5Unified.cpp:454 _speaker_enabled_cb_sticks3().
static void restoreStickS3CodecPower() {
  // 1) PMIC: turn the speaker-amp power rail back on.
  M5.In_I2C.bitOn(0x6E, 0x11, 0b00001000, 100000);
  delay(10);
  // 2) ES8311: run the same DAC/HP-driver power-on register writes.
  static const uint8_t seq[][2] = {
    {0x00, 0x80},  // reset / CSM power on
    {0x01, 0xB5},  // clock manager — MCLK = BCLK
    {0x02, 0x18},  // clock manager — MULT_PRE = 3
    {0x0D, 0x01},  // system — power up analog circuitry
    {0x12, 0x00},  // system — power up DAC
    {0x13, 0x10},  // system — enable HP drive output
    {0x32, 0xBF},  // DAC volume 0 dB
    {0x37, 0x08},  // DAC equalizer bypass
  };
  for (size_t i = 0; i < sizeof(seq) / sizeof(seq[0]); i++) {
    M5.In_I2C.writeRegister8(0x18, seq[i][0], seq[i][1], 100000);
  }
  delay(20);
}

// ============ station persistence (NVS) ============
static void saveIdx() {
  Preferences p;
  if (!p.begin("radio", false)) return;
  p.putInt("idx", s_idx);
  p.end();
}
static void loadIdx() {
  Preferences p;
  if (!p.begin("radio", true)) return;
  int v = p.getInt("idx", 0);
  p.end();
  if (v < 0 || v >= N_STATIONS) v = 0;
  s_idx = v;
}

// ============ UI ============
static void drawSplash(const char* msg) {
  g_canvas.fillScreen(CLR_BG);
  draw_title("网络电台");
  g_canvas.setFont(&fonts::efontCN_16);
  g_canvas.setTextColor(CLR_WARN, CLR_BG);
  g_canvas.setTextDatum(middle_center);
  g_canvas.drawString(msg, SCR_W / 2, SCR_H / 2 - 10);
  g_canvas.setFont(&fonts::efontCN_14);
  g_canvas.setTextColor(CLR_TEXT, CLR_BG);
  g_canvas.drawString(kStations[s_idx].name, SCR_W / 2, SCR_H / 2 + 14);
  g_canvas.setTextDatum(top_left);
  push_frame();
}

static void drawNoWiFi() {
  g_canvas.fillScreen(CLR_BG);
  draw_title("网络电台");
  g_canvas.setFont(&fonts::efontCN_16);
  g_canvas.setTextColor(CLR_BAD, CLR_BG);
  g_canvas.setTextDatum(middle_center);
  g_canvas.drawString("WiFi 未连接", SCR_W / 2, SCR_H / 2 - 10);
  g_canvas.setFont(&fonts::efontCN_14);
  g_canvas.setTextColor(CLR_DIM, CLR_BG);
  g_canvas.drawString("长按 B 返回", SCR_W / 2, SCR_H / 2 + 14);
  g_canvas.setTextDatum(top_left);
  push_frame();
}

static void drawUI() {
  g_canvas.fillScreen(CLR_BG);
  draw_title("网络电台");

  // Top-right: idx / total
  char hdr[16];
  snprintf(hdr, sizeof(hdr), "%d / %d", s_idx + 1, N_STATIONS);
  g_canvas.setFont(&fonts::efontCN_12);
  g_canvas.setTextColor(CLR_DIM, CLR_BG);
  g_canvas.setTextDatum(top_right);
  g_canvas.drawString(hdr, SCR_W - 4, 26);

  // Station name (large)
  g_canvas.setFont(&fonts::efontCN_24);
  g_canvas.setTextColor(s_playing ? CLR_ACCENT : CLR_TEXT, CLR_BG);
  g_canvas.setTextDatum(middle_center);
  g_canvas.drawString(kStations[s_idx].name, SCR_W / 2, 48);

  // Status
  g_canvas.setFont(&fonts::efontCN_14);
  g_canvas.setTextColor(s_playing ? CLR_GOOD : CLR_DIM, CLR_BG);
  g_canvas.drawString(s_playing ? "● 播放中" : "○ 已停止", SCR_W / 2, 72);

  // Stream title or error
  g_canvas.setFont(&fonts::efontCN_12);
  if (s_playing && s_stream_title.length() > 0) {
    String t = s_stream_title;
    if (t.length() > 30) t = t.substring(0, 30) + "...";
    g_canvas.setTextColor(CLR_WARN, CLR_BG);
    g_canvas.drawString(t, SCR_W / 2, 92);
  } else if (s_err_msg.length() > 0 && millis() - s_err_t < 6000) {
    g_canvas.setTextColor(CLR_BAD, CLR_BG);
    g_canvas.drawString(s_err_msg, SCR_W / 2, 92);
  }

  // Bottom hints
  g_canvas.setFont(&fonts::efontCN_12);
  g_canvas.setTextColor(CLR_DIM, CLR_BG);
  g_canvas.setTextDatum(middle_left);
  g_canvas.drawString("A 下台  长按A 上台", 4, SCR_H - 10);
  g_canvas.setTextDatum(middle_right);
  g_canvas.drawString("B 播放/停止", SCR_W - 4, SCR_H - 10);
  g_canvas.setTextDatum(top_left);
  draw_status_bar();
  push_frame();
}

// ============ play control ============
static void startPlay() {
  if (!s_audio) return;
  s_stream_title = "";
  s_err_msg = "";
  drawSplash("连接中...");

  // Fully stop any prior stream before the new connect. A short settle
  // gives the library's TCP/buffer a moment to wind down cleanly.
  s_audio->stopSong();
  delay(100);

  s_audio->setVolume(get_volume() * 2);   // refresh volume every connect

  uint32_t t0 = millis();
  stick_log("info", String("radio connect: ") + kStations[s_idx].name
                    + " rssi=" + WiFi.RSSI());

  bool ok = s_audio->connecttohost(kStations[s_idx].url);
  uint32_t dt = millis() - t0;

  if (ok) {
    s_playing = true;
    saveIdx();
    stick_log(dt > RADIO_SLOW_CONNECT_MS ? "warn" : "info",
              String("radio play: ") + kStations[s_idx].name
              + " connect=" + dt + "ms"
              + " rssi=" + WiFi.RSSI()
              + " heap=" + (ESP.getFreeHeap() / 1024) + "KB");
  } else {
    s_playing = false;
    s_err_msg = "连接失败 " + String(dt / 1000.0f, 1) + "s";
    s_err_t = millis();
    stick_log("warn", String("radio fail: ") + kStations[s_idx].name
                      + " connect=" + dt + "ms"
                      + " rssi=" + WiFi.RSSI()
                      + " heap=" + (ESP.getFreeHeap() / 1024) + "KB");
  }
}

static void stopPlay() {
  if (s_audio) s_audio->stopSong();
  s_playing = false;
  s_stream_title = "";
}

// ============ app entry ============
void app_radio_run() {
  loadIdx();

  // WiFi check
  if (WiFi.status() != WL_CONNECTED) {
    drawNoWiFi();
    bool b_long = false;
    while (true) {
      M5.update();
      if (screen_saver_tick()) { delay(20); continue; }
      if (M5.BtnB.pressedFor(LONG_PRESS_MS)) {
        if (!b_long) { beep_ok(); b_long = true; }
      }
      if (b_long && !M5.BtnB.isPressed()) return;
      delay(20);
    }
  }

  // Hand I2S + codec over from M5.Speaker to Audio library. Done on every
  // radio entry (not just the first) because we give I2S back to M5.Speaker
  // on exit so menu beeps / volume adjust tones keep working between
  // radio sessions. Cost: old Audio instance leaks each entry (~10KB;
  // we can't safely call its dtor — TCP close blocks WDT → panic).
  if (!g_radio_owns_i2s) {
    drawSplash("初始化音频...");
    M5.Speaker.end();
    delay(300);
    i2s_driver_uninstall(I2S_NUM_0);
    delay(100);

    // Wake the codec + amp back up (M5.Speaker.end() just turned them off).
    // Without this the Audio lib pushes samples to a silent codec.
    restoreStickS3CodecPower();

    s_audio = new Audio();   // abandon previous pointer (intentional leak)
    if (!s_audio) {
      drawSplash("内存不足");
      delay(2000);
      return;
    }
    s_audio->setPinout(I2S_BCLK, I2S_LRC, I2S_DOUT, I2S_MCLK);
    s_audio->setConnectionTimeout(RADIO_CONNECT_TIMEOUT_MS, RADIO_CONNECT_TIMEOUT_SSL_MS);
    g_radio_owns_i2s = true;
    stick_log("info", String("radio: I2S takeover  heap=")
                      + (ESP.getFreeHeap() / 1024) + "KB");
    delay(100);
  }

  // Auto-play the saved station
  s_playing = false;
  startPlay();
  drawUI();

  bool b_long = false;
  bool a_long = false;
  uint32_t last_ui_t = millis();

  while (true) {
    M5.update();
    if (screen_saver_tick()) { delay(20); continue; }
    maybe_auto_rotate();

    // Long-press B → exit
    if (M5.BtnB.pressedFor(LONG_PRESS_MS)) {
      if (!b_long) { beep_ok(); b_long = true; }
    }
    if (b_long && !M5.BtnB.isPressed()) {
      saveIdx();
      stopPlay();
      delay(200);
      // Hand I2S back to M5.Speaker so menu/app button beeps + volume
      // tones work again. Audio instance is abandoned (not deleted — its
      // dtor blocks on TCP close and trips WDT). M5.Speaker.begin will
      // install its own I2S driver AND re-enable the codec via its
      // callback, so no manual codec writes needed here.
      i2s_driver_uninstall(I2S_NUM_0);
      delay(50);
      s_audio = nullptr;
      g_radio_owns_i2s = false;
      M5.Speaker.begin();
      apply_volume();
      stick_log("info", "radio exit: I2S returned to speaker");
      return;
    }

    // Long-press A → previous station (flagged; actual switch happens on release)
    if (M5.BtnA.pressedFor(LONG_PRESS_MS)) {
      if (!a_long) { beep_ok(); a_long = true; }
    }

    // A release: long = previous, short = next
    if (M5.BtnA.wasReleased() && !b_long) {
      if (a_long) {
        s_idx = (s_idx - 1 + N_STATIONS) % N_STATIONS;
        a_long = false;
      } else {
        s_idx = (s_idx + 1) % N_STATIONS;
        beep_ok();
      }
      startPlay();
      drawUI();
      last_ui_t = millis();
    }

    // Short-press B → play/stop toggle
    if (M5.BtnB.wasReleased() && !b_long) {
      beep_ok();
      if (s_playing) stopPlay();
      else           startPlay();
      drawUI();
      last_ui_t = millis();
    }

    // Feed the audio loop ONLY while actively playing — critical for
    // ESP32-audioI2S to push samples.
    if (s_playing && s_audio) s_audio->loop();

    // Periodic UI + stream watchdog (every 500ms)
    if (millis() - last_ui_t > 500) {
      last_ui_t = millis();
      if (s_playing && s_audio && !s_audio->isRunning()) {
        // Stream went dead. Surface an error + wait for user to press B.
        s_playing = false;
        s_err_msg = "流中断 按 B 重试";
        s_err_t = millis();
        stick_log("warn", String("radio died: ") + kStations[s_idx].name);
      }
      drawUI();
    }

    // Yield. vTaskDelay(1) is the canonical pattern for ESP32-audioI2S.
    vTaskDelay(1);
  }
}
