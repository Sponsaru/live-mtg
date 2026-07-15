#!/usr/bin/env python3
"""Prevent customer branding and emoji UI from returning to distributed builds."""

from pathlib import Path
import tempfile
import sys


ROOT = Path(__file__).resolve().parents[1]
UI_FILES = ("index.html", "slides-template.html")
FORBIDDEN_BRANDING = (
    "毎日興業",
    "mainichi",
    "sponsaru",
    "#00A0E9",
    "#0079b3",
    "#15233f",
    "#f15a24",
    "data:image",
)
FORBIDDEN_EMOJI = "🎯⚠🔄🔎📖💾📁🖥🧠📚🔍▶■●📋🧩🧭🧾🗑💬👤💡🛡❓🎙📦🖨"


for filename in UI_FILES:
    text = (ROOT / filename).read_text(encoding="utf-8")
    found_branding = [token for token in FORBIDDEN_BRANDING if token.lower() in text.lower()]
    found_emoji = sorted({char for char in FORBIDDEN_EMOJI if char in text})
    assert not found_branding, f"{filename}: customer branding found: {found_branding}"
    assert not found_emoji, f"{filename}: emoji UI found: {found_emoji}"

server = (ROOT / "server.py").read_text(encoding="utf-8")
assert "neutral_generated_html" in server, "legacy generated decks must be neutralized when served"
assert 'content:"LiveMTG"' in server, "legacy customer logo must be replaced with product wordmark"

sys.path.insert(0, str(ROOT))
import server as app  # noqa: E402

legacy = '''<html><head><style>
.slide::after{background:url("data:image/png;base64,OLDLOGO") no-repeat left center}
.legacy-logo{background-image:url('data:image/png;base64,ANOTHERLOGO')}
/* sponsaru テーマ */
body[data-theme="sponsaru"] .slide::after {content:"sponsaru."}
</style></head><body data-theme="mainichi">
<div class="cover-meta">2026.07.15 ｜ 毎日興業 経営会議 議事サマリ</div></body></html>'''
with tempfile.NamedTemporaryFile("w", suffix=".html", encoding="utf-8", delete=False) as f:
    f.write(legacy)
    legacy_path = f.name
try:
    cleaned = app.neutral_generated_html(legacy_path, persist=True)
    persisted = Path(legacy_path).read_text(encoding="utf-8")
finally:
    Path(legacy_path).unlink(missing_ok=True)
for output in (cleaned, persisted):
    assert "data:image" not in output
    assert 'content:"sponsaru."' not in output
    assert "毎日興業 経営会議 議事サマリ" not in output
    assert 'data-theme="neutral"' in output
    assert 'id="livemtg-neutral-identity"' in output
    assert 'id="livemtg-back"' in output
    assert 'id="livemtg-back-style"' in output

print("Neutral product branding OK")
