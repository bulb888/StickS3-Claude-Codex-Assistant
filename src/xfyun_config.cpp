#include "xfyun_config.h"
#include <Preferences.h>

static String trimCopy(const String& s) {
  String out = s;
  out.trim();
  return out;
}

bool xfyun_load_credentials(String& appid, String& api_key, String& api_secret) {
  Preferences p;
  p.begin("xfyun", true);
  appid      = trimCopy(p.getString("appid",  ""));
  api_key    = trimCopy(p.getString("key",    ""));
  api_secret = trimCopy(p.getString("secret", ""));
  p.end();
  if (appid.length() > 0 && api_key.length() > 0 && api_secret.length() > 0) {
    return true;
  }

  String default_appid = trimCopy(String(XFYUN_DEFAULT_APPID));
  String default_key = trimCopy(String(XFYUN_DEFAULT_API_KEY));
  String default_secret = trimCopy(String(XFYUN_DEFAULT_API_SECRET));
  if (default_appid.length() > 0 && default_key.length() > 0 && default_secret.length() > 0) {
    appid = default_appid;
    api_key = default_key;
    api_secret = default_secret;
    return true;
  }

  return false;
}

void xfyun_save_credentials(const char* appid, const char* api_key, const char* api_secret) {
  Preferences p;
  p.begin("xfyun", false);
  p.putString("appid",  trimCopy(String(appid ? appid : "")));
  p.putString("key",    trimCopy(String(api_key ? api_key : "")));
  p.putString("secret", trimCopy(String(api_secret ? api_secret : "")));
  p.end();
}

void xfyun_clear_credentials() {
  Preferences p;
  p.begin("xfyun", false);
  p.clear();
  p.end();
}

static String stripToken(String s) {
  s.trim();
  while (s.length() > 0 && (s[0] == '"' || s[0] == '\'' || s[0] == '`')) s.remove(0, 1);
  while (s.length() > 0) {
    char c = s[s.length() - 1];
    if (c == '"' || c == '\'' || c == '`' || c == ',' || c == ';') s.remove(s.length() - 1);
    else break;
  }
  s.trim();
  return s;
}

static bool readNamedValue(const String& blob, const char* const* names, size_t name_count, String& out) {
  String lower = blob;
  lower.toLowerCase();
  for (size_t i = 0; i < name_count; i++) {
    int key_pos = lower.indexOf(names[i]);
    if (key_pos < 0) continue;

    int sep = -1;
    for (int p = key_pos + strlen(names[i]); p < (int)blob.length(); p++) {
      char c = blob[p];
      if (c == '=' || c == ':') { sep = p; break; }
      if (c == '\n' || c == '\r') break;
    }
    if (sep < 0) continue;

    int start = sep + 1;
    while (start < (int)blob.length() && isspace((unsigned char)blob[start])) start++;
    int end = start;
    while (end < (int)blob.length()) {
      char c = blob[end];
      if (isspace((unsigned char)c) || c == ',' || c == ';') break;
      end++;
    }
    out = stripToken(blob.substring(start, end));
    return out.length() > 0;
  }
  return false;
}

bool xfyun_parse_credentials_blob(const String& blob, String& appid, String& api_key, String& api_secret) {
  String text = trimCopy(blob);
  if (!text.length()) return false;

  const char* appid_names[] = {"appid", "app_id", "app id"};
  const char* key_names[] = {"apikey", "api_key", "api key"};
  const char* secret_names[] = {"apisecret", "api_secret", "api secret"};
  bool named = readNamedValue(text, appid_names, 3, appid)
            & readNamedValue(text, key_names, 3, api_key)
            & readNamedValue(text, secret_names, 3, api_secret);
  if (named) {
    appid = stripToken(appid);
    api_key = stripToken(api_key);
    api_secret = stripToken(api_secret);
    return appid.length() > 0 && api_key.length() > 0 && api_secret.length() > 0;
  }

  String tokens[8];
  int n = 0;
  String cur;
  for (unsigned i = 0; i <= text.length(); i++) {
    char c = (i < text.length()) ? text[i] : ' ';
    if (isalnum((unsigned char)c) || c == '-' || c == '_') {
      cur += c;
    } else if (cur.length()) {
      if (cur.length() >= 8 && n < 8) tokens[n++] = cur;
      cur = "";
    }
  }

  appid = "";
  api_key = "";
  api_secret = "";
  for (int i = 0; i < n; i++) {
    if (!appid.length() && tokens[i].length() == 8) {
      appid = tokens[i];
    } else if (tokens[i].length() == 32) {
      if (!api_key.length()) api_key = tokens[i];
      else if (!api_secret.length()) api_secret = tokens[i];
    }
  }

  return appid.length() > 0 && api_key.length() > 0 && api_secret.length() > 0;
}
