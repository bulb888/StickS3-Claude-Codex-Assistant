#include "app.h"
#include "remote_ota_config.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"

// PC helper endpoint used by stick_log(). Voice apps update the same NVS keys
// after UDP discovery, so logs follow the helper when its IP changes.
static const char* LOG_PC_IP_DEFAULT = "";
static const int   LOG_PC_PORT_DEFAULT = 8765;

M5Canvas g_canvas(&M5.Display);

// Set true while the radio app owns I2S through ESP32-audioI2S. During that
// window, shared speaker helpers become no-ops to prevent I2S conflicts.
bool g_radio_owns_i2s = false;

// ---- screen saver ---------------------------------------------------------
// Apps call screen_saver_tick() in their main loops. It detects button
// activity, keeps a timer, and dims the backlight to 0 after idle.
static uint32_t s_last_activity_ms = 0;
static bool     s_screen_off       = false;

void screen_saver_kick() {
  if (s_screen_off) {
    apply_brightness();   // restore whatever the user configured
    s_screen_off = false;
  }
  s_last_activity_ms = millis();
}

bool screen_saver_tick() {
  static bool s_swallow_until_release = false;

  if (s_swallow_until_release) {
    if (M5.BtnA.isPressed() || M5.BtnB.isPressed()) {
      screen_saver_kick();
      return true;
    }
    s_swallow_until_release = false;
  }

  // Any button state = activity. `isPressed` covers held buttons too.
  if (M5.BtnA.wasPressed() || M5.BtnB.wasPressed() ||
      M5.BtnA.isPressed()  || M5.BtnB.isPressed()) {
    bool woke = s_screen_off;
    screen_saver_kick();
    if (woke) {
      s_swallow_until_release = true;
      return true;
    }
    return false;
  }
  int timeout_sec = get_screen_timeout_sec();
  if (timeout_sec <= 0) return false;  // 0 means "never sleep".
  uint32_t idle = millis() - s_last_activity_ms;
  if (!s_screen_off && idle > (uint32_t)timeout_sec * 1000UL) {
    ledcWrite(3, 0);          // backlight off
    s_screen_off = true;
  }
  return false;
}

// ---- OTA (WiFi firmware upload) ------------------------------------------
// Runs in a background FreeRTOS task so we don't need to pepper every app
// loop with ArduinoOTA.handle() calls.
// (OTA used to live here as a background task — moved to app_ota.cpp.)

// --- persistent log bridge to PC helper (/log endpoint) ---
//
// stick_log() must never block the caller — the old synchronous version's
// 600ms HTTP timeout added ~1.2 seconds of lag to every app enter/exit when
// the helper was off. Now we push a fixed-size message into a FreeRTOS
// queue and a background task drains it at its own pace. If the helper is
// unreachable the HTTP worker absorbs the timeout without the caller noticing.
// Queue overflow (rare): we drop the oldest pending entry to make room.
struct StickLogMsg {
  char level[8];
  char text[160];
};
static QueueHandle_t s_log_queue = nullptr;
// Cached "helper is alive" flag: if several POSTs in a row have timed out
// we stop trying until a short cooldown elapses. Prevents the worker from
// spending all its time on 600ms failures that mean "helper is off".
static uint32_t s_log_next_try_ms = 0;
static int      s_log_fail_streak = 0;

static void loadLogEndpoint(String& ip, int& port) {
  ip = LOG_PC_IP_DEFAULT;
  port = LOG_PC_PORT_DEFAULT;
  Preferences p;
  if (p.begin("pc", true)) {
    ip = p.getString("ip", ip);
    port = p.getInt("port", port);
    p.end();
  }
}

static void stickLogWorkerTask(void* arg) {
  StickLogMsg m;
  for (;;) {
    if (xQueueReceive(s_log_queue, &m, portMAX_DELAY) != pdPASS) continue;
    if (WiFi.status() != WL_CONNECTED) continue;       // drop
    if (millis() < s_log_next_try_ms) continue;        // backoff active

    HTTPClient http;
    http.setTimeout(600);
    String ip;
    int port;
    loadLogEndpoint(ip, port);
    if (ip.length() == 0) continue;
    String url = String("http://") + ip + ":" + port + "/log";
    if (!http.begin(url)) { continue; }
    http.addHeader("Content-Type", "application/json");
    JsonDocument doc;
    doc["level"] = m.level;
    doc["text"]  = m.text;
    String body;
    serializeJson(doc, body);
    int code = http.POST(body);
    http.end();
    if (code == 200) {
      s_log_fail_streak = 0;
    } else {
      if (++s_log_fail_streak >= 3) {
        // Helper likely off — wait 15s before trying again. Pending
        // messages in the queue get dropped during this window.
        s_log_next_try_ms = millis() + 15000;
        s_log_fail_streak = 0;
        StickLogMsg drop;
        while (xQueueReceive(s_log_queue, &drop, 0) == pdPASS) {}
      }
    }
  }
}

void stick_log(const char* level, const char* msg) {
  if (!s_log_queue || !msg || !*msg) return;
  StickLogMsg m;
  strncpy(m.level, level ? level : "info", sizeof(m.level) - 1);
  m.level[sizeof(m.level) - 1] = 0;
  strncpy(m.text, msg, sizeof(m.text) - 1);
  m.text[sizeof(m.text) - 1] = 0;
  // Non-blocking enqueue. On overflow, drop oldest and retry once.
  if (xQueueSend(s_log_queue, &m, 0) != pdPASS) {
    StickLogMsg dummy;
    xQueueReceive(s_log_queue, &dummy, 0);
    xQueueSend(s_log_queue, &m, 0);
  }
}
void stick_log(const char* level, const String& msg) {
  stick_log(level, msg.c_str());
}

static const char* resetReasonStr(esp_reset_reason_t r) {
  switch (r) {
    case ESP_RST_POWERON:   return "POWERON";
    case ESP_RST_EXT:       return "EXT";
    case ESP_RST_SW:        return "SW";
    case ESP_RST_PANIC:     return "PANIC";
    case ESP_RST_INT_WDT:   return "INT_WDT";
    case ESP_RST_TASK_WDT:  return "TASK_WDT";
    case ESP_RST_WDT:       return "WDT";
    case ESP_RST_DEEPSLEEP: return "DEEPSLEEP";
    case ESP_RST_BROWNOUT:  return "BROWNOUT";
    case ESP_RST_SDIO:      return "SDIO";
    default:                return "UNKNOWN";
  }
}

static void drawBatteryIcon(int x, int y, int pct, bool charging) {
  // 22 x 10 battery shape with a 2 x 4 terminal nub on the right.
  const int W = 22, H = 10;
  // Outline
  g_canvas.drawRoundRect(x, y, W, H, 2, CLR_DIM);
  g_canvas.fillRect(x + W, y + 3, 2, H - 6, CLR_DIM);
  // Fill
  int fill_w = (W - 4) * pct / 100;
  if (fill_w < 0) fill_w = 0;
  if (fill_w > W - 4) fill_w = W - 4;
  uint32_t fc = charging ? CLR_GOOD : (pct <= 15 ? CLR_BAD : (pct <= 30 ? CLR_WARN : CLR_TEXT));
  if (fill_w > 0) g_canvas.fillRect(x + 2, y + 2, fill_w, H - 4, fc);
  // Lightning bolt overlay when charging
  if (charging) {
    int bx = x + W / 2 - 3;
    int by = y + 1;
    g_canvas.fillTriangle(bx + 4, by + 0, bx + 0, by + 4, bx + 3, by + 4, 0xFFD000u);
    g_canvas.fillTriangle(bx + 3, by + 4, bx + 6, by + 4, bx + 2, by + 8, 0xFFD000u);
  }
}

// Draw a compact 3-bar WiFi signal strength indicator. Baseline at y+12.
// Takes ~11 px wide. 0 bars = disconnected (draws small X instead).
static void drawWifiBars(int x, int y, int bars, bool connected, uint32_t color) {
  const int bar_w = 3;
  const int gap = 1;
  const int heights[3] = {3, 6, 9};
  const int baseline = y + 12;
  if (!connected) {
    // 11 px X mark in the same footprint
    g_canvas.drawLine(x, baseline - 6, x + 9, baseline, CLR_DIM);
    g_canvas.drawLine(x, baseline,     x + 9, baseline - 6, CLR_DIM);
    return;
  }
  for (int i = 0; i < 3; i++) {
    int bx = x + i * (bar_w + gap);
    int by = baseline - heights[i];
    uint32_t c = (i < bars) ? color : CLR_DIM;
    g_canvas.fillRect(bx, by, bar_w, heights[i], c);
  }
}

void draw_title(const char* title) {
  // Subtle accent stripe at top + thin accent underline, rather than a full
  // bright bar. Easier on the eyes, room for real icons.
  g_canvas.fillRect(0, 0, SCR_W, 22, CLR_CARD);
  g_canvas.fillRect(0, 21, SCR_W, 1, CLR_ACCENT);

  // Small Claude sparkle as app icon on the left.
  draw_claude_mascot(11, 11, 6, CLR_ACCENT);

  g_canvas.setTextColor(CLR_TEXT, CLR_CARD);
  g_canvas.setFont(&fonts::efontCN_16);
  g_canvas.setTextDatum(middle_left);
  g_canvas.drawString(title, 22, 11);

  int pct = (int)M5.Power.getBatteryLevel();
  if (pct < 0) pct = 0;
  if (pct > 100) pct = 100;
  bool charging = (int)M5.Power.isCharging() >= 1;

  // Battery percentage text + icon in top-right
  char bat[8];
  snprintf(bat, sizeof(bat), "%d%%", pct);
  g_canvas.setFont(&fonts::efontCN_12);
  g_canvas.setTextColor(CLR_DIM, CLR_CARD);
  g_canvas.setTextDatum(middle_right);
  g_canvas.drawString(bat, SCR_W - 28, 12);
  drawBatteryIcon(SCR_W - 26, 6, pct, charging);

  // WiFi signal-strength indicator to the left of the battery group.
  // RSSI thresholds: ≥-60 full, ≥-72 good, ≥-85 weak.
  bool wifi_on = (WiFi.status() == WL_CONNECTED);
  int bars = 0;
  uint32_t wifi_color = CLR_GOOD;
  if (wifi_on) {
    int rssi = WiFi.RSSI();
    if      (rssi >= -60) { bars = 3; wifi_color = CLR_GOOD;   }
    else if (rssi >= -72) { bars = 2; wifi_color = CLR_GOOD;   }
    else if (rssi >= -85) { bars = 1; wifi_color = CLR_WARN;   }
    else                  { bars = 1; wifi_color = CLR_BAD;    }
  }
  drawWifiBars(SCR_W - 68, 4, bars, wifi_on, wifi_color);

  g_canvas.setTextDatum(top_left);
}

// WiFi status is now integrated into draw_title() at the top. This function
// is kept for backward compatibility with apps that still call it — the
// bottom strip is just not drawn anymore.
void draw_status_bar() { /* no-op — WiFi moved to title bar */ }

void push_frame() { g_canvas.pushSprite(0, 0); }

void beep_ok()  { if (!g_radio_owns_i2s) M5.Speaker.tone(1800, 60); }
void beep_bad() { if (!g_radio_owns_i2s) M5.Speaker.tone(400, 150); }

static void drawBootAnimation() {
  const int frames = 22;
  const int line_x = 48;
  const int line_y = 104;
  const int line_w = SCR_W - line_x * 2;

  for (int f = 0; f < frames; ++f) {
    M5.update();
    g_canvas.fillScreen(CLR_BG);

    int pulse = f % 8;
    int star_r = 12 + (pulse <= 4 ? pulse : 8 - pulse);
    draw_claude_mascot(72, 48, star_r, CLR_ACCENT);

    int box_x = 146;
    int box_y = 33;
    int box_w = 44;
    int box_h = 32;
    g_canvas.fillRoundRect(box_x, box_y, box_w, box_h, 4, CLR_CARD);
    g_canvas.drawRoundRect(box_x, box_y, box_w, box_h, 4, CLR_ACCENT2);
    g_canvas.fillCircle(box_x + 7, box_y + 6, 2, CLR_BAD);
    g_canvas.fillCircle(box_x + 14, box_y + 6, 2, CLR_WARN);
    g_canvas.fillCircle(box_x + 21, box_y + 6, 2, CLR_GOOD);
    g_canvas.drawFastHLine(box_x + 4, box_y + 12, box_w - 8, CLR_DIM);
    g_canvas.setFont(&fonts::Font2);
    g_canvas.setTextDatum(middle_center);
    g_canvas.setTextColor(CLR_ACCENT2, CLR_CARD);
    g_canvas.drawString(">_", box_x + box_w / 2, box_y + 23);

    g_canvas.drawLine(92, 48, 142, 48, CLR_DIM);
    for (int i = 0; i < 3; ++i) {
      int dot_x = 94 + ((f * 8 + i * 18) % 46);
      g_canvas.fillCircle(dot_x, 48, 2, i == 1 ? CLR_ACCENT2 : CLR_ACCENT);
    }

    g_canvas.setFont(&fonts::efontCN_16);
    g_canvas.setTextDatum(middle_center);
    g_canvas.setTextColor(CLR_TEXT, CLR_BG);
    g_canvas.drawString("小秘书启动中", SCR_W / 2, 82);

    g_canvas.setFont(&fonts::efontCN_12);
    g_canvas.setTextColor(CLR_DIM, CLR_BG);
    g_canvas.drawString("StickS3 Claude Codex", SCR_W / 2, 121);

    g_canvas.drawRoundRect(line_x - 1, line_y - 1, line_w + 2, 7, 2, CLR_DIM);
    int fill = line_w * (f + 1) / frames;
    g_canvas.fillRoundRect(line_x, line_y, fill, 5, 2, f < frames / 2 ? CLR_ACCENT : CLR_ACCENT2);

    g_canvas.setTextDatum(top_left);
    push_frame();

    if (f == 0) M5.Speaker.tone(900, 45);
    if (f == 12) M5.Speaker.tone(1500, 45);
    delay(45);
  }

  M5.Speaker.tone(2100, 70);
  delay(120);
}

// Claude "sparkle" logo — 4-point star with diagonal accents.
void draw_claude_mascot(int cx, int cy, int radius, uint32_t color) {
  int r = radius;
  int d = (int)(radius * 0.55f);
  // Main 4 petals (N/S/E/W)
  g_canvas.fillTriangle(cx - r, cy, cx, cy - 3, cx, cy + 3, color);
  g_canvas.fillTriangle(cx + r, cy, cx, cy - 3, cx, cy + 3, color);
  g_canvas.fillTriangle(cx, cy - r, cx - 3, cy, cx + 3, cy, color);
  g_canvas.fillTriangle(cx, cy + r, cx - 3, cy, cx + 3, cy, color);
  // Shorter 4 diagonal petals (NE/NW/SE/SW)
  g_canvas.fillTriangle(cx - d, cy - d, cx - 1, cy - 1, cx + 1, cy + 1, color);
  g_canvas.fillTriangle(cx + d, cy + d, cx - 1, cy - 1, cx + 1, cy + 1, color);
  g_canvas.fillTriangle(cx - d, cy + d, cx + 1, cy - 1, cx - 1, cy + 1, color);
  g_canvas.fillTriangle(cx + d, cy - d, cx + 1, cy - 1, cx - 1, cy + 1, color);
  // Center dot
  g_canvas.fillCircle(cx, cy, 3, color);
}

void setup() {
  auto cfg = M5.config();
  M5.begin(cfg);
  M5.Display.setRotation(3);

  // Override backlight PWM to 20 kHz to eliminate the visible flicker from
  // M5GFX's default ~1 kHz LEDC frequency. Must run before load_settings()
  // which writes the duty via ledcWrite.
  ledcSetup(3, 20000, 8);
  ledcAttachPin(38, 3);  // G38 = LCD BL on StickS3

  load_settings();  // restores volume & brightness from NVS (defaults 6 / 7)

  // Offscreen canvas (PSRAM)
  g_canvas.setColorDepth(16);
  g_canvas.setPsram(true);
  g_canvas.createSprite(SCR_W, SCR_H);

  drawBootAnimation();

  // Spin up the async stick_log worker before WiFi so the very first log
  // line (the boot message) is already queue-backed and doesn't block.
  s_log_queue = xQueueCreate(12, sizeof(StickLogMsg));
  if (s_log_queue) {
    // 8 KB stack — HTTPClient + mbedTLS + ArduinoJson together blow past
    // 4 KB, and when several log lines land in the queue at once (e.g. the
    // 5-message burst on radio app exit) the worker's consecutive POSTs
    // used to overflow and PANIC the whole system ~4s after the burst.
    xTaskCreate(stickLogWorkerTask, "sticklog", 8192, nullptr, 1, nullptr);
  }

  wifi_setup(false);

  // OTA no longer runs in a background task — that conflicts with audio /
  // WebSocket / BLE tasks during flash writes and panics. Instead, OTA is a
  // dedicated app (app_ota_run) the user enters from the menu; that app
  // shuts down heavy consumers first, then services OTA in a tight loop.
  s_last_activity_ms = millis();

  // Log the boot event (including the reason for the previous reset — crucial
  // for debugging reboots). Done AFTER WiFi is connected so the POST lands.
  esp_reset_reason_t rr = esp_reset_reason();
  String boot_msg = String("boot  reason=") + resetReasonStr(rr)
                  + "  version=" + APP_VERSION
                  + "  free=" + String(ESP.getFreeHeap() / 1024) + "KB"
                  + "  psram=" + String(ESP.getFreePsram() / 1024) + "KB";
  stick_log(rr == ESP_RST_POWERON || rr == ESP_RST_SW ? "info" : "warn", boot_msg);
}

void loop() {
  int pick = menu_run();
  static const char* const names[] = {
    "voicekb", "codex", "clock", "ir", "radio", "ota", "settings"
  };
  if (pick >= 0 && pick < (int)(sizeof(names) / sizeof(names[0]))) {
    stick_log("info", String("enter ") + names[pick]);
  }
  switch (pick) {
    case 0: app_voicekb_run(); break;
    case 1: app_codex_run();   break;
    case 2: app_clock_run();   break;
    case 3: app_ir_run();      break;
    case 4: app_radio_run();   break;
    case 5: app_ota_run();     break;
    case 6: app_settings_run(); break;
  }
  if (pick >= 0 && pick < (int)(sizeof(names) / sizeof(names[0]))) {
    stick_log("info", String("exit ") + names[pick]);
  }
}
