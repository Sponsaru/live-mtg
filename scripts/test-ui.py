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
assert "function itemText(value)" in html and "esc(itemText(x))" in html, "structured AI items must never render as [object Object]"
assert "def _live_list_text(value):" in server and "old_items = [_live_list_text" in server, "structured list values must be normalized before persistence"
assert "capture_heartbeat > 45" in server, "heartbeat expiry must exceed the 15-second audio chunk interval"
assert '"detailing": bool(current_id)' in server and "s.detailing ?" in html
assert "/api/desktop-health" in html and "/api/ai-check" in html
assert 'await api(\'/api/health\')' in html, "recording must use the lightweight server check"
assert "if(!health||!health.ok)" not in html, "AI/ASR diagnostics must not block raw recording"
assert 'p == "/api/health"' in server and "def service_health():" in server
assert 'id="livemtg-back"' in slides and 'href="/"' in slides
assert "if (!setupComplete()) await onboard" in cli, "first launch must run onboarding"
assert "running.version === pkg.version" in cli, "dashboard must replace an outdated server"
assert 'await fetchJson("/api/state"' in cli, "CLI must detect a legacy server occupying the port"
assert "hasMeetings(defaultHome)" in cli and "hasMeetings(legacyHome)" in cli
assert '(autoLegacyHome ? "ja" : detectedLanguage())' in cli
assert "<key>LIVE_MTG_HOME</key>" in cli, "daemon must preserve the selected data home"
assert "if (hadMacDaemon)" in cli and "installDaemon();" in cli, "legacy daemon must be replaced permanently"
daemon_check = cli.index("const hadMacDaemon")
server_return = cli.index("if (currentServer && (!hadMacDaemon || currentMacDaemon))")
assert daemon_check < server_return, "plist migration must be checked before the same-version early return"
assert 'plist.includes(fileURLToPath(import.meta.url))' in cli

print("Meeting-critical UI state and navigation OK")
