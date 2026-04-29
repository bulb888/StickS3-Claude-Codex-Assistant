#include "app.h"
#include "remote_ota_config.h"
#include "xfyun_config.h"
#include <WiFi.h>
#include <Preferences.h>
#include <esp_heap_caps.h>
#include <esp_ota_ops.h>
#include <time.h>

static int  s_volume = 6;           // 0..10
static int  s_brightness = 7;       // 1..10
static bool s_auto_rotate = false;
static int  s_screen_timeout = 60;  // seconds, 0 = never

int  get_volume()             { return s_volume; }
int  get_brightness()         { return s_brightness; }
bool get_auto_rotate()        { return s_auto_rotate; }
int  get_screen_timeout_sec() { return s_screen_timeout; }

void apply_volume()     { if (!g_radio_owns_i2s) M5.Speaker.setVolume(s_volume * 25); }
void apply_brightness() {
  // Drive our own LEDC channel (set up in main.cpp at 20 kHz) instead of
  // M5.Display.setBrightness so backlight frequency stays anti-flicker.
  ledcWrite(3, s_brightness * 25);
}

void load_settings() {
  Preferences p;
  p.begin("sys", true);
  s_volume         = p.getInt("vol", 6);
  s_brightness     = p.getInt("bri", 7);
  s_auto_rotate    = p.getBool("rot", false);
  s_screen_timeout = p.getInt("sto", 60);
  p.end();
  if (s_volume < 0) s_volume = 0;
  if (s_volume > 10) s_volume = 10;
  if (s_brightness < 1) s_brightness = 1;
  if (s_brightness > 10) s_brightness = 10;
  if (s_screen_timeout < 0) s_screen_timeout = 0;
  apply_volume();
  apply_brightness();
}

static void save_settings() {
  Preferences p;
  p.begin("sys", false);
  p.putInt ("vol", s_volume);
  p.putInt ("bri", s_brightness);
  p.putBool("rot", s_auto_rotate);
  p.putInt ("sto", s_screen_timeout);
  p.end();
}

// Auto-rotate: flip between rotation 3 (default landscape) and rotation 1
// (180° flipped landscape) based on gravity along accel X axis.
void maybe_auto_rotate() {
  if (!s_auto_rotate) return;
  static uint32_t last = 0;
  static uint8_t  cur_rot = 3;
  if (millis() - last < 250) return;
  last = millis();

  M5.Imu.update();
  auto d = M5.Imu.getImuData();
  float ax = d.accel.x;
  float ay = d.accel.y;

  // Pick rotation based on which axis gravity mostly pulls along.
  // Threshold 0.35g — forgiving so a modest tilt triggers the flip.
  uint8_t wanted = cur_rot;
  if (fabsf(ax) > fabsf(ay)) {
    if (ax >  0.35f) wanted = 1;
    else if (ax < -0.35f) wanted = 3;
  } else {
    if (ay >  0.35f) wanted = 1;
    else if (ay < -0.35f) wanted = 3;
  }

  if (wanted != cur_rot) {
    // No stick_log here — it POSTs synchronously and the 600ms timeout
    // starved the radio I2S buffer, producing an audible click/skip when
    // the user tilted the device while music was playing.
    cur_rot = wanted;
    M5.Display.setRotation(cur_rot);
  }
}

enum SettingRow {
  ROW_VOLUME = 0,
  ROW_BRIGHTNESS,
  ROW_ROTATE,
  ROW_TIMEOUT,
  ROW_WIFI,
  ROW_REMOTE_OTA,
  ROW_DIAGNOSTICS,
  ROW_CLEAR_NET,
  N_ROWS
};

static int s_cursor = 0;
static const int PAGE_SIZE = 4;
static const int N_PAGES = (N_ROWS + PAGE_SIZE - 1) / PAGE_SIZE;

static const char* const SETTINGS_LABELS[N_ROWS] = {
  "音量", "亮度", "自动旋转", "自动熄屏",
  "WiFi 配网", "远程 OTA", "系统诊断", "清除配网"
};

static const int TIMEOUT_STEPS[] = { 0, 15, 30, 60, 120, 300 };
static const int N_TIMEOUT_STEPS = sizeof(TIMEOUT_STEPS) / sizeof(TIMEOUT_STEPS[0]);

static int timeoutStepIndex() {
  for (int i = 0; i < N_TIMEOUT_STEPS; i++) if (TIMEOUT_STEPS[i] == s_screen_timeout) return i;
  return 3;  // default to 60s
}

static void drawDiagLine(int y, const char* label, const String& value, uint32_t color = CLR_TEXT) {
  g_canvas.setFont(&fonts::efontCN_12);
  g_canvas.setTextDatum(top_left);
  g_canvas.setTextColor(CLR_DIM, CLR_BG);
  g_canvas.drawString(label, 8, y);
  g_canvas.setTextDatum(top_right);
  g_canvas.setTextColor(color, CLR_BG);
  g_canvas.setClipRect(74, y, SCR_W - 82, 14);
  g_canvas.drawString(value, SCR_W - 8, y);
  g_canvas.clearClipRect();
  g_canvas.setTextDatum(top_left);
}

static String kbString(uint32_t bytes) {
  return String(bytes / 1024UL) + "K";
}

static String cachedPcAddress() {
  Preferences p;
  p.begin("pc", true);
  String ip = p.getString("ip", "");
  int port = p.getInt("port", 0);
  p.end();
  if (ip.length() == 0 || port <= 0) return "未发现";
  return ip + ":" + port;
}

static void drawDiagnostics(int page) {
  g_canvas.fillScreen(CLR_BG);
  draw_title("系统诊断");
  g_canvas.setFont(&fonts::efontCN_12);
  g_canvas.setTextColor(CLR_DIM, CLR_BG);
  g_canvas.setTextDatum(middle_right);
  g_canvas.drawString(String(page + 1) + "/2", SCR_W - 74, 12);

  int y = 28;
  if (page == 0) {
    bool wifi = WiFi.status() == WL_CONNECTED;
    drawDiagLine(y, "WiFi", wifi ? WiFi.SSID() : "未连接", wifi ? CLR_GOOD : CLR_BAD); y += 15;
    drawDiagLine(y, "IP", wifi ? WiFi.localIP().toString() : "-", wifi ? CLR_TEXT : CLR_DIM); y += 15;
    drawDiagLine(y, "RSSI", wifi ? String(WiFi.RSSI()) + " dBm" : "-", wifi ? CLR_TEXT : CLR_DIM); y += 15;
    String pc = cachedPcAddress();
    drawDiagLine(y, "PC 助手", pc, pc == "未发现" ? CLR_WARN : CLR_GOOD); y += 15;
    String appid, key, secret;
    bool xf_ok = xfyun_load_credentials(appid, key, secret);
    drawDiagLine(y, "讯飞 API", xf_ok ? "已配置" : "缺失", xf_ok ? CLR_GOOD : CLR_WARN); y += 15;
    time_t now = time(nullptr);
    drawDiagLine(y, "NTP", now > 1700000000 ? "已同步" : "未同步", now > 1700000000 ? CLR_GOOD : CLR_WARN);
  } else {
    const esp_partition_t* part = esp_ota_get_running_partition();
    drawDiagLine(y, "版本", String(APP_VERSION), CLR_ACCENT); y += 15;
    drawDiagLine(y, "OTA 槽", part ? String(part->label) : "未知"); y += 15;
    drawDiagLine(y, "Heap", kbString(ESP.getFreeHeap())); y += 15;
    drawDiagLine(y, "PSRAM", kbString(ESP.getFreePsram())); y += 15;
    drawDiagLine(y, "亮度", String(s_brightness) + "/10"); y += 15;
    drawDiagLine(y, "音量", String(s_volume) + "/10");
  }

  g_canvas.setFont(&fonts::efontCN_12);
  g_canvas.setTextColor(CLR_DIM, CLR_BG);
  g_canvas.setTextDatum(bottom_center);
  g_canvas.drawString("A 下一页    B 刷新    长按 B 返回", SCR_W / 2, SCR_H - 4);
  g_canvas.setTextDatum(top_left);
  push_frame();
}

static void runDiagnostics() {
  int page = 0;
  bool longpress_sent = false;
  bool b_primed = false;
  drawDiagnostics(page);
  while (true) {
    M5.update();
    if (screen_saver_tick()) { delay(20); continue; }
    maybe_auto_rotate();

    if (!b_primed) {
      if (!M5.BtnB.isPressed()) b_primed = true;
      delay(20);
      continue;
    }

    if (M5.BtnB.pressedFor(LONG_PRESS_MS)) {
      if (!longpress_sent) { beep_ok(); longpress_sent = true; }
    }
    if (longpress_sent && !M5.BtnB.isPressed()) return;

    if (M5.BtnA.wasPressed() && !longpress_sent) {
      page = (page + 1) % 2;
      beep_ok();
      drawDiagnostics(page);
    }
    if (M5.BtnB.wasReleased() && !longpress_sent) {
      beep_ok();
      drawDiagnostics(page);
    }
    delay(20);
  }
}

static void drawUI() {
  g_canvas.fillScreen(CLR_BG);
  draw_title("设置");
  g_canvas.setFont(&fonts::efontCN_12);
  g_canvas.setTextColor(CLR_DIM, CLR_CARD);
  g_canvas.setTextDatum(middle_left);
  g_canvas.drawString(String("v") + APP_VERSION, 64, 12);

  int page = s_cursor / PAGE_SIZE;
  int first = page * PAGE_SIZE;
  int last = first + PAGE_SIZE;
  if (last > N_ROWS) last = N_ROWS;

  char page_buf[8];
  snprintf(page_buf, sizeof(page_buf), "%d/%d", page + 1, N_PAGES);
  g_canvas.setTextDatum(middle_right);
  g_canvas.drawString(page_buf, SCR_W - 74, 12);

  int row_h = 23;
  int start_y = 28;

  for (int i = first; i < last; i++) {
    int y = start_y + (i - first) * row_h;
    bool sel = (i == s_cursor);
    uint32_t label_color = sel ? CLR_ACCENT : CLR_TEXT;

    g_canvas.setFont(&fonts::efontCN_14);
    g_canvas.setTextColor(label_color, CLR_BG);
    g_canvas.setTextDatum(middle_left);
    g_canvas.drawString(SETTINGS_LABELS[i], 8, y + 8);

    if (i == ROW_VOLUME || i == ROW_BRIGHTNESS) {
      int v = (i == 0) ? s_volume : s_brightness;
      int maxv = 10;
      char vbuf[8]; snprintf(vbuf, sizeof(vbuf), "%d", v);
      g_canvas.setTextDatum(middle_right);
      g_canvas.drawString(vbuf, SCR_W - 8, y + 8);

      int bar_x = 8;
      int bar_y = y + 16;
      int bar_w = SCR_W - 16;
      int bar_h = 4;
      g_canvas.drawRoundRect(bar_x, bar_y, bar_w, bar_h, 2, CLR_DIM);
      int fill_w = (int)((float)v / maxv * (bar_w - 2));
      uint32_t bar_color = sel ? CLR_ACCENT : CLR_GOOD;
      if (fill_w > 0) g_canvas.fillRoundRect(bar_x + 1, bar_y + 1, fill_w, bar_h - 2, 2, bar_color);
    } else if (i == ROW_ROTATE) {
      g_canvas.setTextDatum(middle_right);
      const char* v = s_auto_rotate ? "开" : "关";
      g_canvas.setTextColor(s_auto_rotate ? CLR_GOOD : CLR_DIM, CLR_BG);
      g_canvas.drawString(v, SCR_W - 8, y + 8);
    } else if (i == ROW_TIMEOUT) {
      g_canvas.setTextDatum(middle_right);
      char buf[16];
      if (s_screen_timeout == 0) snprintf(buf, sizeof(buf), "关");
      else if (s_screen_timeout < 60) snprintf(buf, sizeof(buf), "%d 秒", s_screen_timeout);
      else snprintf(buf, sizeof(buf), "%d 分", s_screen_timeout / 60);
      g_canvas.setTextColor(s_screen_timeout == 0 ? CLR_DIM : CLR_GOOD, CLR_BG);
      g_canvas.drawString(buf, SCR_W - 8, y + 8);
    } else if (i == ROW_WIFI) {
      // WiFi 配网 — action row, B press triggers portal.
      g_canvas.setTextDatum(middle_right);
      g_canvas.setTextColor(sel ? CLR_ACCENT : CLR_DIM, CLR_BG);
      g_canvas.drawString("点 B 开始", SCR_W - 8, y + 8);
    } else if (i == ROW_REMOTE_OTA) {
      // Remote OTA — fetches a GitHub Release manifest if configured.
      g_canvas.setTextDatum(middle_right);
      g_canvas.setTextColor(sel ? CLR_ACCENT2 : CLR_DIM, CLR_BG);
      g_canvas.drawString("点 B 检查", SCR_W - 8, y + 8);
    } else if (i == ROW_DIAGNOSTICS) {
      g_canvas.setTextDatum(middle_right);
      g_canvas.setTextColor(sel ? CLR_ACCENT2 : CLR_DIM, CLR_BG);
      g_canvas.drawString("点 B 查看", SCR_W - 8, y + 8);
    } else {
      // 清除配网 — clears WiFi, site, and iFlytek credentials, then restarts.
      g_canvas.setTextDatum(middle_right);
      g_canvas.setTextColor(sel ? CLR_WARN : CLR_DIM, CLR_BG);
      g_canvas.drawString("点 B 重置", SCR_W - 8, y + 8);
    }
  }

  g_canvas.setFont(&fonts::efontCN_12);
  g_canvas.setTextColor(CLR_DIM, CLR_BG);
  g_canvas.setTextDatum(bottom_center);
  g_canvas.drawString("A 下一项    B 修改    长按 B 返回", SCR_W / 2, SCR_H - 4);

  g_canvas.setTextDatum(top_left);
  push_frame();
}

void app_settings_run() {
  drawUI();

  bool longpress_sent = false;
  // The B press that brought us into this app is still held down when we
  // start. If we don't wait for it to be released first, its release event
  // fires on the first row (volume) and bumps the value by one. Prime here.
  bool b_primed = false;
  while (true) {
    M5.update();
    if (screen_saver_tick()) { delay(20); continue; }
    maybe_auto_rotate();

    if (!b_primed) {
      if (!M5.BtnB.isPressed()) b_primed = true;
      delay(20);
      continue;
    }

    if (M5.BtnB.pressedFor(LONG_PRESS_MS)) {
      if (!longpress_sent) { beep_ok(); longpress_sent = true; }
    }
    if (longpress_sent && !M5.BtnB.isPressed()) {
      save_settings();
      return;
    }

    if (M5.BtnA.wasPressed() && !longpress_sent) {
      s_cursor = (s_cursor + 1) % N_ROWS;
      beep_ok();
      drawUI();
    }

    if (M5.BtnB.wasReleased() && !longpress_sent) {
      if (s_cursor == ROW_VOLUME) {
        s_volume = (s_volume + 1) % 11;
        apply_volume();
      } else if (s_cursor == ROW_BRIGHTNESS) {
        s_brightness = (s_brightness + 1);
        if (s_brightness > 10) s_brightness = 1;
        apply_brightness();
      } else if (s_cursor == ROW_ROTATE) {
        s_auto_rotate = !s_auto_rotate;
        if (!s_auto_rotate) M5.Display.setRotation(3);
      } else if (s_cursor == ROW_TIMEOUT) {
        int idx = (timeoutStepIndex() + 1) % N_TIMEOUT_STEPS;
        s_screen_timeout = TIMEOUT_STEPS[idx];
        screen_saver_kick();   // reset idle timer so change takes effect cleanly
      } else if (s_cursor == ROW_WIFI) {
        // WiFi 重新配网 — save current settings first, then hand the screen
        // over to WiFiManager. When it returns (connected or timed out) we
        // redraw our UI and continue.
        save_settings();
        beep_ok();
        stick_log("info", "settings: forcing WiFi config portal");
        wifi_setup(true);
        screen_saver_kick();
        drawUI();
        continue;
      } else if (s_cursor == ROW_REMOTE_OTA) {
        save_settings();
        beep_ok();
        stick_log("info", "settings: remote OTA");
        app_remote_ota_run();
        screen_saver_kick();
        drawUI();
        continue;
      } else if (s_cursor == ROW_DIAGNOSTICS) {
        save_settings();
        beep_ok();
        runDiagnostics();
        screen_saver_kick();
        drawUI();
        continue;
      } else {
        save_settings();
        beep_bad();
        g_canvas.fillScreen(CLR_BG);
        draw_title("清除配网");
        g_canvas.setFont(&fonts::efontCN_16);
        g_canvas.setTextColor(CLR_WARN, CLR_BG);
        g_canvas.setCursor(8, 34); g_canvas.print("正在清除...");
        g_canvas.setFont(&fonts::efontCN_14);
        g_canvas.setTextColor(CLR_DIM, CLR_BG);
        g_canvas.setCursor(8, 62); g_canvas.print("WiFi 和讯飞配置");
        g_canvas.setCursor(8, 82); g_canvas.print("将回到首次配网");
        push_frame();
        wifi_clear_saved_config();
        delay(900);
        ESP.restart();
      }
      save_settings();
      beep_ok();
      drawUI();
    }

    delay(20);
  }
}
