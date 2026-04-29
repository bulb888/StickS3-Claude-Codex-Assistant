#include "voice_assistant_common.h"

#include <mbedtls/base64.h>
#include <mbedtls/md.h>
#include "esp_heap_caps.h"
#include <time.h>

namespace voice_assistant {

int16_t* sharedPcmBuffer() {
  static int16_t* s_pcm = nullptr;
  if (!s_pcm) {
    s_pcm = (int16_t*)heap_caps_malloc(
        VOICE_MAX_SAMPLES * sizeof(int16_t),
        MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  }
  return s_pcm;
}

String wsPayloadPreview(const uint8_t* payload, size_t length) {
  String out;
  size_t n = length > 80 ? 80 : length;
  out.reserve(n);
  for (size_t i = 0; i < n; i++) {
    char c = (char)payload[i];
    out += (c >= 32 && c <= 126) ? c : '.';
  }
  return out.length() ? out : String("unknown");
}

String hmac_sha256_b64(const String& data, const String& key) {
  unsigned char hmac[32];
  mbedtls_md_context_t ctx;
  mbedtls_md_init(&ctx);
  mbedtls_md_setup(&ctx, mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), 1);
  mbedtls_md_hmac_starts(&ctx, (const unsigned char*)key.c_str(), key.length());
  mbedtls_md_hmac_update(&ctx, (const unsigned char*)data.c_str(), data.length());
  mbedtls_md_hmac_finish(&ctx, hmac);
  mbedtls_md_free(&ctx);
  char out[64];
  size_t out_len = 0;
  mbedtls_base64_encode((unsigned char*)out, sizeof(out), &out_len, hmac, 32);
  return String(out, out_len);
}

String base64_encode_str(const String& data) {
  size_t cap = ((data.length() + 2) / 3) * 4 + 4;
  char* buf = (char*)malloc(cap);
  if (!buf) return String();
  size_t out_len = 0;
  mbedtls_base64_encode((unsigned char*)buf, cap, &out_len,
                        (const unsigned char*)data.c_str(), data.length());
  String r(buf, out_len);
  free(buf);
  return r;
}

String base64_encode_bytes(const uint8_t* data, size_t len) {
  size_t cap = ((len + 2) / 3) * 4 + 4;
  char* buf = (char*)malloc(cap);
  if (!buf) return String();
  size_t out_len = 0;
  mbedtls_base64_encode((unsigned char*)buf, cap, &out_len, data, len);
  String r(buf, out_len);
  free(buf);
  return r;
}

String url_encode(const String& s) {
  String r;
  r.reserve(s.length() * 2);
  for (unsigned i = 0; i < s.length(); i++) {
    char c = s[i];
    if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
        (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.' || c == '~') {
      r += c;
    } else {
      char b[4];
      snprintf(b, sizeof(b), "%%%02X", (unsigned char)c);
      r += b;
    }
  }
  return r;
}

String http_date() {
  time_t now = time(nullptr);
  struct tm gmt;
  gmtime_r(&now, &gmt);
  char buf[64];
  strftime(buf, sizeof(buf), "%a, %d %b %Y %H:%M:%S GMT", &gmt);
  return String(buf);
}

int wrapLine(const String& src, size_t max_bytes, String out[2]) {
  if (src.length() == 0) { out[0] = ""; return 0; }
  if (src.length() <= max_bytes) { out[0] = src; return 1; }

  size_t cut = max_bytes;
  while (cut > 0 && (((uint8_t)src.charAt(cut)) & 0xC0) == 0x80) cut--;
  out[0] = src.substring(0, cut);

  String rest = src.substring(cut);
  if (rest.length() <= max_bytes) {
    out[1] = rest;
    return 2;
  }

  cut = max_bytes;
  while (cut > 0 && (((uint8_t)rest.charAt(cut)) & 0xC0) == 0x80) cut--;
  out[1] = rest.substring(0, cut) + "...";
  return 2;
}

String peelMarker(const String& raw, char& out_kind) {
  out_kind = 0;
  if (raw.length() >= 2 && (uint8_t)raw.charAt(0) == 0x01) {
    out_kind = raw.charAt(1);
    return raw.substring(2);
  }
  return raw;
}

}  // namespace voice_assistant
