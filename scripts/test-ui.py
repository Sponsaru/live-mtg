#!/usr/bin/env python3
"""Guard meeting-critical UI against accidental text truncation."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
html = (ROOT / "index.html").read_text(encoding="utf-8")
server = (ROOT / "server.py").read_text(encoding="utf-8")
cli = (ROOT / "cli" / "live-mtg.mjs").read_text(encoding="utf-8")
slides = (ROOT / "slides-template.html").read_text(encoding="utf-8")

assert "guideBrief(x.q||" not in html, "suggested questions must be rendered in full"
assert "guideBrief(raw,42)" not in html, "question intent must be rendered in full"
assert 'class="nextq"' in html, "suggested question text wrapper is missing"
assert 'class="nextintenttext"' in html, "question intent text wrapper is missing"
assert ".nextq,.nextintenttext" in html and "overflow-wrap:anywhere" in html
assert "/api/recording-heartbeat" in html and "/api/recording-heartbeat" in server
assert "serverRecording && capturing" in html, "stop button must reflect this tab's recorder"
assert "hardMs:15000" in html and "reachedHardLimit" in html, "audio must upload within 15 seconds"
assert "/api/desktop-health" in html and "/api/ai-check" in html
assert 'id="livemtg-back"' in slides and 'href="/"' in slides
assert "if (!setupComplete()) await onboard" in cli, "first launch must run onboarding"

print("Meeting-critical UI state and navigation OK")
