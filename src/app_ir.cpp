#include "app.h"
#include <driver/rmt.h>

static const uint16_t IR_TX_PIN = 46;
static const rmt_channel_t IR_TX_CH = RMT_CHANNEL_2;

struct IrKey {
  const char* name;
  uint32_t code;
};

static const IrKey kTvKeys[] = {
  {"电源",   0x57E3E817},
  {"开机",   0x57E316E9},
  {"关机",   0x57E318E7},
  {"音量+",  0x57E3F00F},
  {"音量-",  0x57E308F7},
  {"静音",   0x57E304FB},
  {"主页",   0x57E3C03F},
  {"确认",   0x57E354AB},
  {"上",     0x57E39867},
  {"下",     0x57E3CC33},
  {"左",     0x57E37887},
  {"右",     0x57E3B44B},
  {"返回",   0x57E36699},
  {"HDMI1",  0x57E321DE},
  {"HDMI2",  0x57E322DD},
  {"HDMI3",  0x57E323DC},
};

static const int N_KEYS = sizeof(kTvKeys) / sizeof(kTvKeys[0]);

static const char* kModes[] = {
  "NEC 原码",
  "NEC 反位",
  "NEC 反字节",
  "NEC 字节反位",
};

static const int N_MODES = sizeof(kModes) / sizeof(kModes[0]);

static bool s_rmt_tx_ready = false;
static int s_cursor = 0;
static int s_mode = 0;

static uint32_t reverseBits32(uint32_t v) {
  uint32_t r = 0;
  for (int i = 0; i < 32; i++) {
    r <<= 1;
    r |= (v >> i) & 0x1;
  }
  return r;
}

static uint8_t reverseBits8(uint8_t v) {
  v = (v & 0xF0) >> 4 | (v & 0x0F) << 4;
  v = (v & 0xCC) >> 2 | (v & 0x33) << 2;
  v = (v & 0xAA) >> 1 | (v & 0x55) << 1;
  return v;
}

static uint32_t reverseBytes32(uint32_t v) {
  return ((v & 0x000000FFUL) << 24) |
         ((v & 0x0000FF00UL) << 8)  |
         ((v & 0x00FF0000UL) >> 8)  |
         ((v & 0xFF000000UL) >> 24);
}

static uint32_t reverseBitsInBytes(uint32_t v) {
  uint32_t out = 0;
  out |= (uint32_t)reverseBits8((v >> 0)  & 0xFF) << 0;
  out |= (uint32_t)reverseBits8((v >> 8)  & 0xFF) << 8;
  out |= (uint32_t)reverseBits8((v >> 16) & 0xFF) << 16;
  out |= (uint32_t)reverseBits8((v >> 24) & 0xFF) << 24;
  return out;
}

static uint32_t encodeForMode(uint32_t code, int mode) {
  switch (mode) {
    case 1: return reverseBits32(code);
    case 2: return reverseBytes32(code);
    case 3: return reverseBitsInBytes(code);
    default: return code;
  }
}

static bool setupRmtTx() {
  if (s_rmt_tx_ready) return true;

  rmt_config_t tx = RMT_DEFAULT_CONFIG_TX((gpio_num_t)IR_TX_PIN, IR_TX_CH);
  tx.clk_div = 80;  // 1 us ticks.
  tx.mem_block_num = 2;
  tx.tx_config.carrier_en = true;
  tx.tx_config.carrier_freq_hz = 38000;
  tx.tx_config.carrier_duty_percent = 33;
  tx.tx_config.carrier_level = RMT_CARRIER_LEVEL_HIGH;
  tx.tx_config.idle_level = RMT_IDLE_LEVEL_LOW;
  tx.tx_config.idle_output_en = true;

  esp_err_t err = rmt_config(&tx);
  if (err != ESP_OK) {
    stick_log("error", String("ir tx config failed err=") + (int)err);
    return false;
  }

  err = rmt_driver_install(IR_TX_CH, 0, 0);
  if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
    stick_log("error", String("ir tx install failed err=") + (int)err);
    return false;
  }

  s_rmt_tx_ready = true;
  return true;
}

static void fillItem(rmt_item32_t& item, uint16_t mark, uint16_t space) {
  item.level0 = 1;
  item.duration0 = mark;
  item.level1 = 0;
  item.duration1 = space;
}

static bool sendNec32(uint32_t code) {
  if (!setupRmtTx()) return false;

  rmt_item32_t items[34];
  fillItem(items[0], 9000, 4500);
  for (int bit = 0; bit < 32; bit++) {
    bool one = (code >> bit) & 0x1;
    fillItem(items[bit + 1], 560, one ? 1690 : 560);
  }
  fillItem(items[33], 560, 0);

  for (int i = 0; i < 3; i++) {
    esp_err_t err = rmt_write_items(IR_TX_CH, items, 34, true);
    if (err != ESP_OK) return false;
    rmt_wait_tx_done(IR_TX_CH, pdMS_TO_TICKS(1000));
    delay(70);
  }
  return true;
}

static void drawIrTool() {
  g_canvas.fillScreen(CLR_BG);
  draw_title("红外发射");

  const IrKey& key = kTvKeys[s_cursor];
  uint32_t tx_code = encodeForMode(key.code, s_mode);

  g_canvas.setTextDatum(middle_center);
  g_canvas.setFont(&fonts::efontCN_24);
  g_canvas.setTextColor(CLR_ACCENT, CLR_BG);
  g_canvas.drawString(key.name, SCR_W / 2, 42);

  g_canvas.setFont(&fonts::efontCN_14);
  g_canvas.setTextColor(CLR_TEXT, CLR_BG);
  g_canvas.drawString(kModes[s_mode], SCR_W / 2, 72);

  char buf[48];
  snprintf(buf, sizeof(buf), "%02d/%02d  0x%08lX",
           s_cursor + 1, N_KEYS, (unsigned long)tx_code);
  g_canvas.setTextColor(CLR_DIM, CLR_BG);
  g_canvas.drawString(buf, SCR_W / 2, 94);

  g_canvas.setFont(&fonts::efontCN_12);
  g_canvas.setTextColor(CLR_DIM, CLR_BG);
  g_canvas.drawString("A换键  长按A换模式  B发送", SCR_W / 2, 118);
  g_canvas.setTextDatum(top_left);
  push_frame();
}

static void drawSendResult(bool ok) {
  g_canvas.fillScreen(CLR_BG);
  g_canvas.setTextDatum(middle_center);
  g_canvas.setFont(&fonts::efontCN_24);
  g_canvas.setTextColor(ok ? CLR_GOOD : CLR_BAD, CLR_BG);
  g_canvas.drawString(ok ? "已发送" : "发送失败", SCR_W / 2, SCR_H / 2);
  g_canvas.setTextDatum(top_left);
  push_frame();
}

static void sendCurrentKey() {
  const IrKey& key = kTvKeys[s_cursor];
  uint32_t tx_code = encodeForMode(key.code, s_mode);
  M5.Power.setExtOutput(true);
  delay(20);
  bool ok = sendNec32(tx_code);

  stick_log(ok ? "info" : "error",
            String("ir send key=") + key.name
            + " mode=" + kModes[s_mode]
            + " code=0x" + String(tx_code, HEX));

  if (ok) beep_ok();
  else    beep_bad();
  drawSendResult(ok);
  delay(300);
}

void app_ir_run() {
  M5.Power.setExtOutput(true);
  s_cursor = 0;
  drawIrTool();

  bool long_a_sent = false;
  bool long_b_sent = false;

  while (true) {
    M5.update();
    if (screen_saver_tick()) { delay(20); continue; }
    maybe_auto_rotate();

    if (M5.BtnA.pressedFor(LONG_PRESS_MS)) {
      if (!long_a_sent) {
        long_a_sent = true;
        s_mode = (s_mode + 1) % N_MODES;
        beep_ok();
        drawIrTool();
      }
    }

    if (M5.BtnB.pressedFor(LONG_PRESS_MS)) {
      if (!long_b_sent) {
        long_b_sent = true;
        beep_ok();
      }
    }

    if (M5.BtnA.wasReleased()) {
      if (long_a_sent) {
        long_a_sent = false;
      } else {
        s_cursor = (s_cursor + 1) % N_KEYS;
        beep_ok();
        drawIrTool();
      }
    }

    if (M5.BtnB.wasReleased()) {
      if (long_b_sent) {
        M5.Power.setExtOutput(false);
        return;
      } else {
        sendCurrentKey();
        drawIrTool();
      }
    }

    delay(20);
  }
}
