#include "app.h"

static const char* const APP_NAMES[] = {
  "Claude 小秘书",
  "Codex 小秘书",
  "仪表盘",
  "红外遥控",
  "网络电台",
  "本地 OTA 升级",
  "设置",
};
static const char* const APP_DESC[] = {
  "语音输入到 PC",
  "语音输入 Codex",
  "时钟 天气 B站",
  "红外发射工具",
  "在线收音机",
  "无线刷机",
  "音量 / 亮度",
};
static const int N_APPS = sizeof(APP_NAMES) / sizeof(APP_NAMES[0]);

static int g_cursor = 0;

static void drawMenu() {
  g_canvas.fillScreen(CLR_BG);
  draw_title("StickS3");

  const int item_h = 16;
  const int start_y = 24;  // keep first selection clear of the title underline
  for (int i = 0; i < N_APPS; i++) {
    int y = start_y + i * item_h;
    bool sel = (i == g_cursor);
    if (sel) {
      g_canvas.fillRoundRect(4, y, SCR_W - 8, item_h, 3, CLR_ACCENT);
      g_canvas.setTextColor(CLR_BG);
    } else {
      g_canvas.setTextColor(CLR_TEXT);
    }
    g_canvas.setFont(&fonts::efontCN_14);
    g_canvas.setTextDatum(top_left);
    g_canvas.drawString(APP_NAMES[i], 10, y + 1);
    g_canvas.setFont(&fonts::efontCN_12);
    g_canvas.setTextDatum(top_right);
    g_canvas.drawString(APP_DESC[i], SCR_W - 10, y + 2);
  }
  g_canvas.setTextDatum(top_left);
  push_frame();
}

int menu_run() {
  drawMenu();
  uint32_t last_refresh = millis();

  while (true) {
    M5.update();
    if (screen_saver_tick()) { delay(20); continue; }
    maybe_auto_rotate();
    if (M5.BtnA.wasPressed()) {
      g_cursor = (g_cursor + 1) % N_APPS;
      beep_ok();
      drawMenu();
      last_refresh = millis();
    }
    if (M5.BtnB.wasPressed()) {
      beep_ok();
      // Absorb the full press-and-release before handing control to the
      // launched app, so its first-row BtnB logic doesn't fire on the same
      // press (e.g. settings +1 volume, IR enter-detail, radio start-play).
      while (M5.BtnB.isPressed()) { M5.update(); delay(10); }
      return g_cursor;
    }
    // Keep the title bar's battery / charge icon responsive to plug events.
    if (millis() - last_refresh > 1000) {
      last_refresh = millis();
      drawMenu();
    }
    delay(20);
  }
}
