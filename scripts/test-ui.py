#!/usr/bin/env python3
"""Guard meeting-critical UI against accidental text truncation."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
html = (ROOT / "index.html").read_text(encoding="utf-8")

assert "guideBrief(x.q||" not in html, "suggested questions must be rendered in full"
assert "guideBrief(raw,42)" not in html, "question intent must be rendered in full"
assert 'class="nextq"' in html, "suggested question text wrapper is missing"
assert 'class="nextintenttext"' in html, "question intent text wrapper is missing"
assert ".nextq,.nextintenttext" in html and "overflow-wrap:anywhere" in html

print("Meeting-critical text remains fully visible")
