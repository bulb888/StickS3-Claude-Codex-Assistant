#pragma once

#include <Arduino.h>

#if __has_include("secrets.h")
#include "secrets.h"
#endif

#ifndef XFYUN_DEFAULT_APPID
#define XFYUN_DEFAULT_APPID ""
#endif

#ifndef XFYUN_DEFAULT_API_KEY
#define XFYUN_DEFAULT_API_KEY ""
#endif

#ifndef XFYUN_DEFAULT_API_SECRET
#define XFYUN_DEFAULT_API_SECRET ""
#endif

bool xfyun_load_credentials(String& appid, String& api_key, String& api_secret);
void xfyun_save_credentials(const char* appid, const char* api_key, const char* api_secret);
void xfyun_clear_credentials();
bool xfyun_parse_credentials_blob(const String& blob, String& appid, String& api_key, String& api_secret);
