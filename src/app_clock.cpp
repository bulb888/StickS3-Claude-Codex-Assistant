#include "app.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <time.h>

static const char* NTP1 = "ntp.aliyun.com";
static const char* NTP2 = "ntp.ntsc.ac.cn";

// Loaded from NVS namespace "site" (set via WiFiManager portal).
// Empty defaults keep personal location / account IDs out of open-source builds.
static String s_city_code = "";
static String s_bili_uid  = "";
static String s_city_name = "";   // filled from weather API response

struct Weather {
  bool ok = false;
  int temp = 0;
  String type;
  uint32_t fetched_ms = 0;
};

struct Bili {
  bool ok = false;
  long follower = 0;
  uint32_t fetched_ms = 0;
};

static Weather s_weather;
static Bili s_bili;

static void loadSiteConfig() {
  Preferences p;
  p.begin("site", true);
  String city = p.getString("city", "");
  String uid  = p.getString("uid",  "");
  p.end();

  if (city != s_city_code) {
    s_weather = Weather();
    s_city_name = "";
  }
  if (uid != s_bili_uid) {
    s_bili = Bili();
  }
  s_city_code = city;
  s_bili_uid = uid;
}

static bool fetchWeather() {
  if (WiFi.status() != WL_CONNECTED) return false;
  if (s_city_code.length() == 0) {
    s_weather = Weather();
    s_city_name = "";
    return false;
  }
  HTTPClient http;
  http.setTimeout(6000);
  String url = String("http://t.weather.itboy.net/api/weather/city/") + s_city_code;
  if (!http.begin(url)) return false;
  int code = http.GET();
  if (code != 200) { http.end(); return false; }
  String body = http.getString();
  http.end();
  JsonDocument doc;
  if (deserializeJson(doc, body)) return false;
  JsonObject data = doc["data"];
  if (data.isNull()) return false;
  s_weather.temp = data["wendu"].as<int>();
  JsonArray fc = data["forecast"];
  if (!fc.isNull() && fc.size() > 0) s_weather.type = fc[0]["type"] | "";
  // City name from the API response — strip trailing "市" (3 bytes UTF-8).
  String cn = doc["cityInfo"]["city"] | "";
  if (cn.endsWith("市")) cn.remove(cn.length() - 3);
  if (cn.length() > 0) s_city_name = cn;
  s_weather.ok = true;
  s_weather.fetched_ms = millis();
  return true;
}

static bool fetchBili() {
  if (WiFi.status() != WL_CONNECTED) return false;
  if (s_bili_uid.length() == 0) {
    s_bili = Bili();
    return false;
  }
  WiFiClientSecure client;
  client.setInsecure();
  HTTPClient http;
  http.setTimeout(6000);
  String url = String("https://api.bilibili.com/x/relation/stat?vmid=") + s_bili_uid;
  if (!http.begin(client, url)) return false;
  http.addHeader("User-Agent", "Mozilla/5.0 (StickS3)");
  int code = http.GET();
  if (code != 200) { http.end(); return false; }
  String body = http.getString();
  http.end();
  JsonDocument doc;
  if (deserializeJson(doc, body)) return false;
  if (doc["code"].as<int>() != 0) return false;
  s_bili.follower = doc["data"]["follower"] | 0L;
  s_bili.ok = true;
  s_bili.fetched_ms = millis();
  return true;
}

static void syncTime() {
  if (WiFi.status() != WL_CONNECTED) return;
  configTzTime("CST-8", NTP1, NTP2, "pool.ntp.org");
}

static const char* weekdayCN(int wday) {
  static const char* names[] = { "周日", "周一", "周二", "周三", "周四", "周五", "周六" };
  return names[wday % 7];
}

static void drawClockView() {
  g_canvas.fillScreen(CLR_BG);
  draw_title("仪表盘");

  time_t now = time(nullptr);
  struct tm tm_now;
  localtime_r(&now, &tm_now);
  bool time_ok = tm_now.tm_year > (2020 - 1900);

  char tbuf[16];
  if (time_ok) snprintf(tbuf, sizeof(tbuf), "%02d:%02d", tm_now.tm_hour, tm_now.tm_min);
  else         snprintf(tbuf, sizeof(tbuf), "--:--");
  g_canvas.setFont(&fonts::Font7);
  g_canvas.setTextColor(CLR_TEXT, CLR_BG);
  g_canvas.setTextDatum(middle_center);
  g_canvas.drawString(tbuf, SCR_W / 2, 50);

  char dbuf[48];
  if (time_ok) {
    snprintf(dbuf, sizeof(dbuf), "%d-%02d-%02d %s  :%02d",
             tm_now.tm_year + 1900, tm_now.tm_mon + 1, tm_now.tm_mday,
             weekdayCN(tm_now.tm_wday), tm_now.tm_sec);
  } else {
    snprintf(dbuf, sizeof(dbuf), "等待对时...");
  }
  g_canvas.setFont(&fonts::efontCN_14);
  g_canvas.setTextColor(CLR_DIM, CLR_BG);
  g_canvas.setTextDatum(middle_center);
  g_canvas.drawString(dbuf, SCR_W / 2, 84);

  g_canvas.setFont(&fonts::efontCN_14);
  g_canvas.setTextDatum(middle_left);
  if (s_weather.ok) {
    char wbuf[80];
    const char* city = s_city_name.length() > 0 ? s_city_name.c_str() : "当前";
    snprintf(wbuf, sizeof(wbuf), "%s %d℃ %s", city, s_weather.temp, s_weather.type.c_str());
    g_canvas.setTextColor(CLR_ACCENT, CLR_BG);
    g_canvas.drawString(wbuf, 6, 120);
  } else {
    g_canvas.setTextColor(CLR_DIM, CLR_BG);
    g_canvas.drawString(s_city_code.length() ? "天气加载..." : "天气未配置", 6, 120);
  }

  g_canvas.setTextDatum(middle_right);
  if (s_bili_uid.length() == 0) {
    // User cleared the UID field — just don't show anything on that side.
  } else if (s_bili.ok) {
    char bbuf[32];
    snprintf(bbuf, sizeof(bbuf), "粉丝 %ld", s_bili.follower);
    g_canvas.setTextColor(CLR_WARN, CLR_BG);
    g_canvas.drawString(bbuf, SCR_W - 6, 120);
  } else {
    g_canvas.setTextColor(CLR_DIM, CLR_BG);
    g_canvas.drawString("B站加载...", SCR_W - 6, 120);
  }
  g_canvas.setTextDatum(top_left);
  draw_status_bar();
  push_frame();
}

void app_clock_run() {
  loadSiteConfig();
  syncTime();
  fetchWeather();
  fetchBili();

  uint32_t last_redraw = 0;
  uint32_t last_weather = millis();
  uint32_t last_bili = millis();
  bool longpress_sent = false;

  while (true) {
    M5.update();
    if (screen_saver_tick()) { delay(30); continue; }
    maybe_auto_rotate();
    if (M5.BtnB.pressedFor(LONG_PRESS_MS)) {
      if (!longpress_sent) { beep_ok(); longpress_sent = true; }
    }
    if (longpress_sent && !M5.BtnB.isPressed()) return;
    if (M5.BtnA.wasPressed()) {
      fetchWeather();
      fetchBili();
      beep_ok();
      last_redraw = 0;
    }
    if (millis() - last_redraw > 500) {
      last_redraw = millis();
      drawClockView();
    }
    if (millis() - last_weather > 10UL * 60UL * 1000UL) {
      last_weather = millis();
      fetchWeather();
    }
    if (millis() - last_bili > 2UL * 60UL * 1000UL) {
      last_bili = millis();
      fetchBili();
    }
    delay(30);
  }
}
