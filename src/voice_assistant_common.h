#pragma once

#include <Arduino.h>

namespace voice_assistant {

constexpr uint32_t VOICE_SAMPLE_RATE = 16000;
constexpr size_t VOICE_MAX_SAMPLES = VOICE_SAMPLE_RATE * 30;  // 30 seconds

int16_t* sharedPcmBuffer();
String wsPayloadPreview(const uint8_t* payload, size_t length);
String hmac_sha256_b64(const String& data, const String& key);
String base64_encode_str(const String& data);
String base64_encode_bytes(const uint8_t* data, size_t len);
String url_encode(const String& s);
String http_date();
int wrapLine(const String& src, size_t max_bytes, String out[2]);
String peelMarker(const String& raw, char& out_kind);

}  // namespace voice_assistant
