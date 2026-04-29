# Open Source Checklist

Use this checklist before changing the repository visibility to public.

## Safe to publish from the current tree

The intended public source tree contains:

- firmware source under `src/`
- Windows helper source under `helper/`
- documentation under `README.md`, `USER_MANUAL.md`, `docs/`, and `SECURITY.md`
- current OTA snapshot under `releases/latest/`
- templates such as `src/secrets.example.h`

The intended public source tree must not contain:

- real iFlytek credentials
- WiFi passwords
- GitHub tokens
- local helper config or logs
- diagnostic zip files
- PlatformIO or PyInstaller build caches

Run the repeatable check:

```powershell
python helper\open_source_check.py
```

The script fails on suspicious tokens in currently tracked text files. It also
prints warnings for local ignored files and known historical artifact paths.

## Do not publish a folder zip

Do not zip the whole working directory. The local folder can include ignored
files such as `src/secrets.h`, `helper/config.json`, helper logs, diagnostics,
`.pio/`, `helper/dist/`, and local memory files.

Publish from Git-tracked files, or create a clean export from the current commit.

## History caveat

Making an existing private repository public also exposes deleted files in Git
history. This matters even when `git status` is clean and the latest tree looks
safe.

For a clean public launch, choose one:

- create a new public repository from the current tracked files
- rewrite the existing repository history to remove old binaries, upload logs,
  and local reference files before changing visibility

Do not rewrite history while collaborators may have unpushed work unless they
are ready to re-clone or manually recover their branches.

## Release assets

GitHub Release assets become public when the repository is public. Confirm that
release assets are intended for public download:

- `firmware.bin`
- `manifest.json`
- `helper.json`
- `StickS3ClaudeCodexHelper.exe`

Firmware can contain public OTA URLs and protocol labels. It must not contain
real service credentials. The helper exe should be treated as a release binary,
not as source history.
