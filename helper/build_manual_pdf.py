#!/usr/bin/env python3
"""Render USER_MANUAL.md → styled HTML → PDF via Chrome headless."""

import os
import sys
import subprocess
import markdown

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MD = os.path.join(ROOT, "USER_MANUAL.md")
HTML = os.path.join(ROOT, "USER_MANUAL.html")
PDF = os.path.join(ROOT, "USER_MANUAL.pdf")

STYLE = """
@page { size: A4; margin: 18mm 16mm; }
html { font-family: -apple-system, 'PingFang SC', 'Microsoft YaHei', 'Segoe UI', sans-serif; }
body { color: #24292f; line-height: 1.65; font-size: 11pt; max-width: 100%; margin: 0; }
h1 { font-size: 22pt; color: #0e1419; border-bottom: 2px solid #E8976F; padding-bottom: 6px; margin-top: 0; }
h2 { font-size: 15pt; color: #0e1419; border-bottom: 1px solid #ddd; padding-bottom: 4px; margin-top: 22px; page-break-after: avoid; }
h3 { font-size: 12.5pt; color: #0e1419; margin-top: 18px; page-break-after: avoid; }
p, li { font-size: 11pt; }
code { font-family: 'Consolas', 'Menlo', monospace; background: #f2f4f6; padding: 1px 4px; border-radius: 3px; font-size: 10pt; color: #c92a2a; }
pre { background: #f6f8fa; padding: 10px 14px; border-left: 3px solid #E8976F; border-radius: 4px; overflow-x: auto; font-size: 9.5pt; }
pre code { background: none; padding: 0; color: #24292f; }
strong { color: #0e1419; }
table { border-collapse: collapse; width: 100%; margin: 10px 0; page-break-inside: avoid; }
th, td { border: 1px solid #ddd; padding: 6px 10px; text-align: left; font-size: 10.5pt; }
th { background: #E8976F; color: white; font-weight: 600; }
tr:nth-child(even) { background: #fafafa; }
ul, ol { padding-left: 22px; }
li { margin: 3px 0; }
hr { border: none; border-top: 1px dashed #ccc; margin: 18px 0; }
blockquote { border-left: 3px solid #E8976F; background: #fff7f2; padding: 6px 14px; margin: 10px 0; color: #555; }
a { color: #0969da; text-decoration: none; }
"""

with open(MD, encoding="utf-8") as f:
    md_text = f.read()

html_body = markdown.markdown(md_text, extensions=["fenced_code", "tables", "sane_lists"])
html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>StickS3 Claude Codex 小秘书使用说明</title>
<style>{STYLE}</style>
</head>
<body>
{html_body}
</body>
</html>"""

with open(HTML, "w", encoding="utf-8") as f:
    f.write(html_doc)
print(f"wrote HTML: {HTML}")

# Find Chrome
candidates = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]
chrome = next((c for c in candidates if os.path.exists(c)), None)
if not chrome:
    print("ERROR: Chrome/Edge not found at expected paths.")
    sys.exit(1)

url = "file:///" + HTML.replace("\\", "/")
cmd = [
    chrome,
    "--headless=new",
    "--disable-gpu",
    "--no-sandbox",
    "--no-pdf-header-footer",
    f"--print-to-pdf={PDF}",
    url,
]
print("running:", " ".join(f'"{c}"' if " " in c else c for c in cmd))
r = subprocess.run(
    cmd,
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
    timeout=60,
)
if r.returncode != 0:
    print("chrome stderr:", (r.stderr or "")[-500:])
    sys.exit(r.returncode)
print(f"wrote PDF:  {PDF}")
print(f"size:       {os.path.getsize(PDF):,} bytes")
