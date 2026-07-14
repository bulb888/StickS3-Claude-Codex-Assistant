# Security Policy

## Private data

This project is designed to be built and published without bundled private
credentials.

Do not commit or publish:

- `src/secrets.h`
- `helper/config.json`
- `helper/stick_log.txt`
- `helper/dist/config.json`
- `helper/dist/stick_log.txt`
- `helper/diagnostics/`
- `helper/dist/diagnostics/`
- `.claude/settings.local.json`
- `.pio/`, `build/`, `dist/release/`, `downloads/`, `memory/`

The repository only includes `src/secrets.example.h` as a template. Users should
enter their own iFlytek IAT `APPID`, `APISecret`, and `APIKey` through the
StickS3 WiFi setup page, or keep local developer defaults in ignored
`src/secrets.h`.

## LAN trust model (PC helper)

The Windows helper listens on `0.0.0.0` so the StickS3 board can reach it over
WiFi. Its HTTP interface is **unauthenticated by design**:

- `POST /type` and `POST /codex/type` paste text and press Enter on the PC.
  Any device on the same LAN can call them — treat every network that the
  helper runs on as trusted (a home LAN behind NAT, not public WiFi).
- `POST /log`, `POST /status`, and the UDP discovery responder are similarly
  open to the local network.

Mitigations if your LAN is shared with untrusted devices:

- Run the helper only while you are using the voice assistant, and quit it
  from the tray afterwards.
- Restrict the helper's port (default 8765/TCP and 8766/UDP) to the board's
  IP with Windows Defender Firewall inbound rules.
- Disable UDP discovery in the tray config dialog once the board has cached
  the helper address (it re-discovers only when the cached address fails).

## Firmware and release assets

Release firmware may contain public strings such as the GitHub OTA manifest URL,
the setup AP name, protocol field names, and UI labels. It must not contain real
iFlytek credentials, WiFi passwords, GitHub tokens, or local helper config.

Before publishing a release, run:

```powershell
python helper\open_source_check.py
```

Then build release assets through:

```powershell
python helper\prepare_release.py --repo OWNER/REPO --notes "release notes"
```

or publish through:

```powershell
python helper\publish_release.py --repo OWNER/REPO --notes "release notes" --sync-latest
```

`publish_release.py` uses the local Git Credential Manager flow and does not
store GitHub credentials in this repository.

## Existing Git history

If an existing private repository is made public, its Git history becomes public
too. A clean current tree is not the same as a clean history.

For the safest public launch, prefer creating a fresh public repository from the
current tracked files, or rewrite history before making the existing repository
public. Historical generated artifacts such as old helper executables, upload
logs, and local reference files should not be part of a clean source history.

## Reporting

If you find a credential, private endpoint, or other sensitive data in a public
commit or release asset, remove the asset or repository visibility first, then
rotate the affected credential. Do not only delete the latest file from the
working tree; Git history and Release assets need separate cleanup.
