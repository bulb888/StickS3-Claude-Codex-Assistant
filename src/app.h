#pragma once
#include <M5Unified.h>

constexpr int SCR_W = 240;
constexpr int SCR_H = 135;

// Refined palette: muted cool navy bg, warm Claude accent, softer status tones.
constexpr uint32_t CLR_BG     = 0x0E1419u;   // near-black with blue undertone
constexpr uint32_t CLR_CARD   = 0x1B232Bu;   // slightly lighter surface for cards
constexpr uint32_t CLR_ACCENT = 0xE8976Fu;   // Claude warm orange
constexpr uint32_t CLR_ACCENT2= 0x33D9D0u;   // muted cyan
constexpr uint32_t CLR_TEXT   = 0xE8EAEDu;   // soft white
constexpr uint32_t CLR_DIM    = 0x6E7A87u;   // cool grey
constexpr uint32_t CLR_WARN   = 0xF5B870u;   // soft amber
constexpr uint32_t CLR_BAD    = 0xF07170u;   // soft red
constexpr uint32_t CLR_GOOD   = 0x7EE787u;   // soft green

constexpr uint32_t LONG_PRESS_MS = 900;

// Shared offscreen canvas — draw here, then pushSprite to eliminate flicker.
extern M5Canvas g_canvas;

// App entries
int  menu_run();
void app_clock_run();
void app_ir_run();
void app_radio_run();
void app_settings_run();
void app_voicekb_run();
void app_codex_run();
void app_ota_run();
void app_remote_ota_run();

// Draw the Claude "sparkle" mascot centered at (cx,cy) with given radius & color.
void draw_claude_mascot(int cx, int cy, int radius, uint32_t color);

// System settings (volume, brightness, auto-rotate, screen timeout) — NVS-backed.
void load_settings();
int  get_volume();
int  get_brightness();
bool get_auto_rotate();
int  get_screen_timeout_sec();
void apply_volume();
void apply_brightness();

// Screen saver — call tick() in app loops; kick() on manual wake (e.g., boot/app enter).
bool screen_saver_tick();
void screen_saver_kick();

// Call periodically from app loops that want the screen to follow device orientation.
// Cheap (rate-limited internally); safe to call every iteration.
void maybe_auto_rotate();

// Log to the PC helper's /log endpoint (appends a line to helper/stick_log.txt).
// Silently no-ops if WiFi down or helper unreachable. Short timeout — never blocks long.
void stick_log(const char* level, const char* msg);
void stick_log(const char* level, const String& msg);

// WiFi
void wifi_setup(bool force_portal);
void wifi_clear_saved_config();
bool wifi_is_connected();
const char* wifi_ssid();

// Drawing helpers — they draw on g_canvas; caller is responsible for pushSprite.
void draw_title(const char* title);
void draw_status_bar();
void push_frame();   // shortcut for g_canvas.pushSprite(0,0)

void beep_ok();
void beep_bad();

// True while the radio app owns I2S0 through ESP32-audioI2S. Shared speaker
// helpers gate themselves on this flag so M5.Speaker does not fight Audio.
extern bool g_radio_owns_i2s;
