#pragma once

#if __has_include("secrets.h")
#include "secrets.h"
#endif

// Keep firmware version in this tracked file only. Local secrets.h may carry
// private URLs/keys, but must not change the version used by OTA comparison.
#ifdef APP_VERSION
#undef APP_VERSION
#endif
#define APP_VERSION "0.2.4"

#ifndef REMOTE_OTA_MANIFEST_URL
#define REMOTE_OTA_MANIFEST_URL "https://github.com/bulb888/StickS3-Claude-Codex-Assistant/releases/latest/download/manifest.json"
#endif
