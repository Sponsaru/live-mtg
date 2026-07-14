#!/usr/bin/env python3
"""Prevent customer branding and emoji UI from returning to distributed builds."""

from pathlib import Path


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

print("Neutral product branding OK")
