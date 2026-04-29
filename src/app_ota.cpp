#include "app.h"
#include "remote_ota_config.h"
#include <WiFi.h>
#include <ArduinoOTA.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <ArduinoJson.h>
#include <Update.h>
#include <mbedtls/sha256.h>

// Dedicated OTA upgrade mode. Before beginning OTA we shut down every heavy
// consumer (audio, mic, speaker) so the upload doesn't collide with other
// tasks writing/reading Flash at the same time — which was the root cause of
// the PANIC crashes when OTA ran as a background task alongside the radio /
// voice-kb / WebSocket stacks.

static volatile int  s_ota_progress = -1;  // 0..100 during upload, -1 idle, -2 done, -3 err
static volatile int  s_ota_err      = 0;
// ArduinoOTA.handle() blocks for the full ~40s upload, so the main loop can't
// refresh the screen during transfer. We draw from inside the onProgress
// callback instead — that's the only code path running mid-transfer. IP is
// stashed at file scope so the callback has access.
static String s_ota_ip;

struct RemoteManifest {
  String version;
  String url;
  String sha256;
  String notes;
  uint32_t size = 0;
};

static void drawOTAScreen(const char* ip) {
  g_canvas.fillScreen(CLR_BG);
  draw_title("本地 OTA 升级");

  // Big claude mascot in the middle for vibe.
  draw_claude_mascot(SCR_W / 2, 48, 14, CLR_ACCENT);

  g_canvas.setFont(&fonts::efontCN_16);
  g_canvas.setTextDatum(middle_center);

  if (s_ota_progress == -3) {
    g_canvas.setTextColor(CLR_BAD, CLR_BG);
    g_canvas.drawString("升级失败", SCR_W / 2, 76);
    g_canvas.setFont(&fonts::efontCN_12);
    char buf[32]; snprintf(buf, sizeof(buf), "错误码 %d", s_ota_err);
    g_canvas.drawString(buf, SCR_W / 2, 94);
  } else if (s_ota_progress == -2) {
    g_canvas.setTextColor(CLR_GOOD, CLR_BG);
    g_canvas.drawString("升级成功，重启中", SCR_W / 2, 76);
  } else if (s_ota_progress >= 0) {
    g_canvas.setTextColor(CLR_WARN, CLR_BG);
    char buf[16]; snprintf(buf, sizeof(buf), "升级中 %d%%", s_ota_progress);
    g_canvas.drawString(buf, SCR_W / 2, 76);
    // Progress bar
    int pbx = 20, pby = 96, pbw = SCR_W - 40, pbh = 8;
    g_canvas.drawRoundRect(pbx - 1, pby - 1, pbw + 2, pbh + 2, 2, CLR_DIM);
    int fill = pbw * s_ota_progress / 100;
    g_canvas.fillRoundRect(pbx, pby, fill, pbh, 2, CLR_ACCENT);
  } else {
    g_canvas.setTextColor(CLR_TEXT, CLR_BG);
    g_canvas.drawString("等待上传", SCR_W / 2, 76);
    g_canvas.setFont(&fonts::efontCN_12);
    g_canvas.setTextColor(CLR_DIM, CLR_BG);
    char buf[48]; snprintf(buf, sizeof(buf), "IP %s", ip);
    g_canvas.drawString(buf, SCR_W / 2, 96);
    g_canvas.drawString("长按 B 退出", SCR_W / 2, SCR_H - 12);
  }
  g_canvas.setTextDatum(top_left);
  push_frame();
}

void app_ota_run() {
  // 1) Quiet down everything that might fight for WiFi / Flash / I2S.
  M5.Speaker.end();
  M5.Mic.end();
  // (Audio-lib streaming lives inside the radio app — not active here.)
  // (BLE lib no longer used after we moved to WiFi helper.)

  if (WiFi.status() != WL_CONNECTED) {
    g_canvas.fillScreen(CLR_BG);
    draw_title("本地 OTA 升级");
    g_canvas.setFont(&fonts::efontCN_16);
    g_canvas.setTextColor(CLR_BAD, CLR_BG);
    g_canvas.setTextDatum(middle_center);
    g_canvas.drawString("WiFi 未连接", SCR_W / 2, SCR_H / 2);
    g_canvas.setTextDatum(top_left);
    push_frame();
    delay(2000);
    M5.Speaker.begin();
    apply_volume();
    return;
  }

  String ip_str = WiFi.localIP().toString();

  // 2) Wire up OTA callbacks — draw from within them because handle()
  //    blocks for the full upload and the main loop can't repaint.
  //    Keep it screen-only: no HTTP/stick_log/etc (those crashed before).
  s_ota_ip = ip_str;
  static bool ota_configured = false;
  if (!ota_configured) {
    ArduinoOTA.setHostname("sticks3");
    ArduinoOTA.setMdnsEnabled(false);
    ArduinoOTA.onStart([]() {
      s_ota_progress = 0;
      drawOTAScreen(s_ota_ip.c_str());
    });
    ArduinoOTA.onEnd([]() {
      s_ota_progress = -2;
      drawOTAScreen(s_ota_ip.c_str());
    });
    ArduinoOTA.onProgress([](unsigned done, unsigned total) {
      if (total == 0) return;
      int p = (int)((uint64_t)done * 100 / total);
      if (p == s_ota_progress) return;
      s_ota_progress = p;
      // Rate-limit screen pushes so frequent small chunks don't starve the
      // Flash write path. Force a draw at 100% so the final frame lands.
      static uint32_t s_last_draw_ms = 0;
      uint32_t now = millis();
      if (p < 100 && now - s_last_draw_ms < 150) return;
      s_last_draw_ms = now;
      drawOTAScreen(s_ota_ip.c_str());
    });
    ArduinoOTA.onError([](ota_error_t e) {
      s_ota_err = (int)e;
      s_ota_progress = -3;
      drawOTAScreen(s_ota_ip.c_str());
    });
    ArduinoOTA.begin();
    ota_configured = true;
  } else {
    // Just re-begin in case previous session ended.
    ArduinoOTA.begin();
  }

  s_ota_progress = -1;  // reset state to idle
  drawOTAScreen(ip_str.c_str());

  bool longpress_sent = false;
  uint32_t last_draw = 0;

  while (true) {
    // Tight OTA loop — no other work here; that's the whole point.
    // Keep the screen ON for the whole OTA mode (progress must stay visible).
    screen_saver_kick();
    ArduinoOTA.handle();

    // UI refresh at most every 80ms to avoid starving OTA.
    if (millis() - last_draw > 80) {
      last_draw = millis();
      drawOTAScreen(ip_str.c_str());
    }

    if (s_ota_progress == -2) {   // success — board will reboot itself
      delay(1500);
      ESP.restart();
    }

    // Allow exit unless we're actively receiving.
    if (s_ota_progress < 0 || s_ota_progress == -3) {
      M5.update();
      if (M5.BtnB.pressedFor(LONG_PRESS_MS)) {
        if (!longpress_sent) longpress_sent = true;
      }
      if (longpress_sent && !M5.BtnB.isPressed()) {
        M5.Speaker.begin();
        apply_volume();
        return;
      }
    }
    delay(5);
  }
}

static void drawRemoteOTAScreen(const char* title, const char* detail, int progress) {
  g_canvas.fillScreen(CLR_BG);
  draw_title("远程 OTA");

  draw_claude_mascot(SCR_W / 2, 38, 11, CLR_ACCENT2);

  g_canvas.setTextDatum(middle_center);
  g_canvas.setFont(&fonts::efontCN_16);
  g_canvas.setTextColor(progress < -1 ? CLR_BAD : CLR_TEXT, CLR_BG);
  g_canvas.drawString(title, SCR_W / 2, 66);

  g_canvas.setFont(&fonts::efontCN_12);
  g_canvas.setTextColor(CLR_DIM, CLR_BG);
  int detail_y = 86;
  if (detail && *detail) {
    String d(detail);
    int nl = d.indexOf('\n');
    if (nl >= 0) {
      g_canvas.drawString(d.substring(0, nl), SCR_W / 2, detail_y);
      g_canvas.drawString(d.substring(nl + 1), SCR_W / 2, detail_y + 14);
    } else {
      g_canvas.drawString(d, SCR_W / 2, detail_y);
    }
  }

  if (progress >= 0) {
    int pbx = 20, pby = 108, pbw = SCR_W - 40, pbh = 8;
    g_canvas.drawRoundRect(pbx - 1, pby - 1, pbw + 2, pbh + 2, 2, CLR_DIM);
    int fill = pbw * progress / 100;
    g_canvas.fillRoundRect(pbx, pby, fill, pbh, 2, CLR_ACCENT2);
  } else {
    g_canvas.drawString("短按 B 开始  长按 B 退出", SCR_W / 2, SCR_H - 12);
  }

  g_canvas.setTextDatum(top_left);
  push_frame();
}

static void formatBytes(uint32_t bytes, char* out, size_t out_len) {
  if (bytes >= 1024UL * 1024UL) {
    float mb = (float)bytes / (1024.0f * 1024.0f);
    snprintf(out, out_len, "%.1f MB", mb);
  } else if (bytes >= 1024UL) {
    snprintf(out, out_len, "%lu KB", (unsigned long)(bytes / 1024UL));
  } else {
    snprintf(out, out_len, "%lu B", (unsigned long)bytes);
  }
}

static String sha256Hex(const uint8_t hash[32]) {
  static const char* hex = "0123456789abcdef";
  char out[65];
  for (int i = 0; i < 32; i++) {
    out[i * 2] = hex[(hash[i] >> 4) & 0x0F];
    out[i * 2 + 1] = hex[hash[i] & 0x0F];
  }
  out[64] = 0;
  return String(out);
}

static bool fetchRemoteManifest(RemoteManifest& m, String& err) {
  String manifest_url = REMOTE_OTA_MANIFEST_URL;
  if (manifest_url.length() == 0) {
    err = "未配置 manifest";
    return false;
  }

  WiFiClientSecure client;
  client.setInsecure();
  HTTPClient http;
  http.setTimeout(8000);
  http.setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS);
  if (!http.begin(client, manifest_url)) {
    err = "manifest 打开失败";
    return false;
  }

  int code = http.GET();
  if (code != HTTP_CODE_OK) {
    err = String("manifest HTTP ") + code;
    http.end();
    return false;
  }

  String body = http.getString();
  http.end();

  JsonDocument doc;
  DeserializationError json_err = deserializeJson(doc, body);
  if (json_err) {
    err = String("manifest JSON ") + json_err.c_str();
    return false;
  }

  m.version = String((const char*)(doc["version"] | ""));
  m.url     = String((const char*)(doc["url"] | ""));
  m.sha256  = String((const char*)(doc["sha256"] | ""));
  m.notes   = String((const char*)(doc["notes"] | ""));
  m.size    = doc["size"] | 0;
  m.sha256.toLowerCase();
  m.notes.replace("\r", "");

  if (m.url.length() == 0) {
    err = "manifest 缺 url";
    return false;
  }
  if (m.size == 0) {
    err = "manifest 缺 size";
    return false;
  }
  if (m.sha256.length() > 0 && m.sha256.length() != 64) {
    err = "sha256 长度错误";
    return false;
  }
  return true;
}

static bool downloadAndApplyRemoteOTA(const RemoteManifest& m, String& err) {
  WiFiClientSecure client;
  client.setInsecure();
  HTTPClient http;
  http.setTimeout(12000);
  http.setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS);
  if (!http.begin(client, m.url)) {
    err = "固件打开失败";
    return false;
  }

  int code = http.GET();
  if (code != HTTP_CODE_OK) {
    err = String("固件 HTTP ") + code;
    http.end();
    return false;
  }

  int content_len = http.getSize();
  if (content_len > 0 && (uint32_t)content_len != m.size) {
    err = String("固件大小不匹配 ") + content_len + "/" + m.size;
    http.end();
    return false;
  }

  if (!Update.begin(m.size)) {
    err = String("OTA 开始失败 ") + Update.getError();
    http.end();
    return false;
  }

  mbedtls_sha256_context sha_ctx;
  mbedtls_sha256_init(&sha_ctx);
  mbedtls_sha256_starts_ret(&sha_ctx, 0);

  WiFiClient* stream = http.getStreamPtr();
  uint8_t buf[2048];
  uint32_t written = 0;
  int last_progress = -1;
  uint32_t last_data_ms = millis();

  while (written < m.size) {
    screen_saver_kick();
    int avail = stream->available();
    if (avail <= 0) {
      if (!http.connected() && written >= m.size) break;
      if (millis() - last_data_ms > 15000) {
        err = String("下载超时 ") + written + "/" + m.size;
        Update.abort();
        http.end();
        mbedtls_sha256_free(&sha_ctx);
        return false;
      }
      delay(10);
      continue;
    }

    size_t want = (size_t)avail;
    if (want > sizeof(buf)) want = sizeof(buf);
    if (want > m.size - written) want = m.size - written;

    int n = stream->readBytes(buf, want);
    if (n <= 0) continue;
    last_data_ms = millis();

    size_t w = Update.write(buf, n);
    if (w != (size_t)n) {
      err = String("写入失败 ") + Update.getError();
      Update.abort();
      http.end();
      mbedtls_sha256_free(&sha_ctx);
      return false;
    }

    mbedtls_sha256_update_ret(&sha_ctx, buf, n);
    written += n;

    int progress = (int)((uint64_t)written * 100 / m.size);
    if (progress != last_progress) {
      last_progress = progress;
      char detail[40];
      snprintf(detail, sizeof(detail), "%lu / %lu KB",
               (unsigned long)(written / 1024),
               (unsigned long)(m.size / 1024));
      drawRemoteOTAScreen("下载固件", detail, progress);
    }
    delay(1);
  }

  http.end();

  uint8_t hash[32];
  mbedtls_sha256_finish_ret(&sha_ctx, hash);
  mbedtls_sha256_free(&sha_ctx);

  if (written != m.size) {
    err = String("下载不完整 ") + written + "/" + m.size;
    Update.abort();
    return false;
  }

  if (m.sha256.length() == 64) {
    String actual = sha256Hex(hash);
    if (!actual.equalsIgnoreCase(m.sha256)) {
      err = String("SHA256 校验失败 ") + actual.substring(0, 8);
      Update.abort();
      return false;
    }
  }

  if (!Update.end(true)) {
    err = String("OTA 结束失败 ") + Update.getError();
    return false;
  }
  if (!Update.isFinished()) {
    err = "OTA 未完成";
    return false;
  }
  return true;
}

void app_remote_ota_run() {
  bool b_primed = false;
  bool longpress_sent = false;

  if (WiFi.status() != WL_CONNECTED) {
    stick_log("warn", "remote ota: wifi not connected");
    drawRemoteOTAScreen("WiFi 未连接", "", -2);
    delay(1800);
    return;
  }

  String manifest_url = REMOTE_OTA_MANIFEST_URL;
  if (manifest_url.length() == 0) {
    stick_log("warn", "remote ota: manifest not configured");
    drawRemoteOTAScreen("远程 OTA 未配置", "请设置 manifest URL", -2);
    delay(2200);
    return;
  }

  char detail[64];
  snprintf(detail, sizeof(detail), "当前版本 %s", APP_VERSION);
  drawRemoteOTAScreen("检查远程更新", detail, -1);

  while (true) {
    M5.update();
    screen_saver_kick();

    if (!b_primed) {
      if (!M5.BtnB.isPressed()) b_primed = true;
      delay(20);
      continue;
    }

    if (M5.BtnB.pressedFor(LONG_PRESS_MS)) {
      if (!longpress_sent) { longpress_sent = true; beep_ok(); }
    }
    if (longpress_sent && !M5.BtnB.isPressed()) return;

    if (M5.BtnB.wasReleased() && !longpress_sent) {
      M5.Speaker.end();
      M5.Mic.end();

      RemoteManifest manifest;
      String err;
      drawRemoteOTAScreen("读取 manifest", "", 0);
      stick_log("info", String("remote ota: checking ") + manifest_url);
      if (!fetchRemoteManifest(manifest, err)) {
        stick_log("warn", String("remote ota: check failed: ") + err);
        drawRemoteOTAScreen("检查失败", err.c_str(), -2);
        delay(2200);
        M5.Speaker.begin();
        apply_volume();
        return;
      }

      if (manifest.version.length() > 0 && manifest.version == APP_VERSION) {
        char same[64];
        snprintf(same, sizeof(same), "当前/最新 %s", manifest.version.c_str());
        stick_log("info", String("remote ota: already latest ") + manifest.version);
        drawRemoteOTAScreen("已经是最新版", same, -1);
        delay(1800);
        M5.Speaker.begin();
        apply_volume();
        return;
      }

      char size_buf[20];
      formatBytes(manifest.size, size_buf, sizeof(size_buf));
      char found[80];
      snprintf(found, sizeof(found), "当前 %s  最新 %s\n固件大小 %s", APP_VERSION,
               manifest.version.length() ? manifest.version.c_str() : "new",
               size_buf);
      stick_log("info", String("remote ota: found ")
                       + (manifest.version.length() ? manifest.version : "new")
                       + " size=" + String(manifest.size));
      drawRemoteOTAScreen("发现新版本", found, -1);
      delay(900);

      if (manifest.notes.length() > 0) {
        String notes = manifest.notes;
        if (notes.length() > 60) notes = notes.substring(0, 60) + "...";
        drawRemoteOTAScreen("更新说明", notes.c_str(), -1);
        delay(1600);
      }

      char ver[64];
      snprintf(ver, sizeof(ver), "%s -> %s", APP_VERSION,
               manifest.version.length() ? manifest.version.c_str() : "new");
      drawRemoteOTAScreen("准备升级", ver, 0);
      delay(500);

      if (!downloadAndApplyRemoteOTA(manifest, err)) {
        stick_log("error", String("remote ota: upgrade failed: ") + err);
        drawRemoteOTAScreen("升级失败", err.c_str(), -2);
        delay(2600);
        M5.Speaker.begin();
        apply_volume();
        return;
      }

      drawRemoteOTAScreen("升级成功", "正在重启", 100);
      stick_log("info", String("remote ota: upgrade ok ") + APP_VERSION + " -> "
                         + (manifest.version.length() ? manifest.version : "new"));
      delay(1200);
      ESP.restart();
    }

    delay(20);
  }
}
