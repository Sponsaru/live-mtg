#!/usr/bin/env python3
"""Guard meeting-critical UI against accidental text truncation."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
html = (ROOT / "index.html").read_text(encoding="utf-8")
server = (ROOT / "server.py").read_text(encoding="utf-8")
cli = (ROOT / "cli" / "live-mtg.mjs").read_text(encoding="utf-8")
slides = (ROOT / "slides-template.html").read_text(encoding="utf-8")
mindmap = (ROOT / "make-mindmap.py").read_text(encoding="utf-8")

assert "guideBrief(x.q||" not in html, "suggested questions must be rendered in full"
assert "guideBrief(raw,42)" not in html, "question intent must be rendered in full"
assert 'class="nextq"' in html, "suggested question text wrapper is missing"
assert 'class="nextintenttext"' in html, "question intent text wrapper is missing"
assert ".nextq,.nextintenttext" in html and "overflow-wrap:anywhere" in html
assert "/api/recording-heartbeat" in html and "/api/recording-heartbeat" in server
assert "serverRecording && capturing" in html, "stop button must reflect this tab's recorder"
assert "hardMs:15000" in html and "reachedHardLimit" in html, "audio must upload within 15 seconds"
assert "function itemText(value)" in html and "esc(itemText(x))" in html, "structured AI items must never render as [object Object]"
assert "^\\[object Object\\]$" in html and 're.fullmatch(r"\\[object Object\\]"' in server
assert "def _live_list_text(value):" in server and "old_items = list(filter(None" in server, "structured list values must be normalized before persistence"
assert "capture_heartbeat > 45" in server, "heartbeat expiry must exceed the 15-second audio chunk interval"
assert '"detailing": bool(current_id)' in server and "s.detailing?" in html
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
assert 'id="statuscluster"' in html and 'id="stprovider"' not in html and 'id="stjob"' not in html, "header must only show meeting-critical status"
assert "flex-wrap:nowrap" in html and "flex:0 0 132px" in html, "header polling must not move the controls"
assert "body:not(.slidemode){overflow-x:hidden}" in html, "header controls must not horizontally shift the meeting page"
assert "grid-template-columns:58px 74px" in html, "header status must not leave empty space between labels"
assert "el.title=tr(`最終解析" in html, "relative analysis age should remain available without changing layout"
assert "（${ago}）`" not in html.split("el.textContent=tr(", 1)[1].split(";", 1)[0], "relative age must not be visible in the live header"
assert 'id="copilotbubble"' in html and 'class="copilot-body"' in html
assert "livemtg_mindmap_mode" in html and "captureMindmapUi" in html and "restoreMindmapUi" in html, \
    "live mind-map refreshes must preserve the active tab, expanded nodes, and scroll position"
assert "applyMindmapMode(btn.dataset.mapview)" in html, "mind-map tabs must persist across live updates"
assert "mindmapMode='tree'" in html and "mindmapDefaultVersion='tree-v1'" in html, \
    "live mind map must default to the hierarchical topic map"
assert all(token in html for token in (
    'data-mapview="tree"', 'data-mapview="radial"', 'data-mapview="relation"', 'data-mapview="timeline"',
    "radial=['mindmap'", 'class="radial-map"'
)), "live view must keep the hierarchical map and add a separate radial map"
assert all(token in slides for token in (
    "data-generated-map", "data-generated-view", "livemtg_generated_map_mode", "defaultVersion='topics-v1'", "mode='topics'"
)), "generated mind map must default to topics and preserve the selected tab"
assert 'str(data.get("diagram") or "").strip()' in mindmap, "generated Mermaid must preserve line breaks"
assert 'radial_lines = ["mindmap"' in mindmap and 'data-generated-map="radial"' in mindmap, \
    "generated output must contain the separate radial mind map"
assert 'width:min(1120px,calc(100vw - 48px))' in html and 'grid-template-columns:minmax(0,1.45fr)' in html
assert 'id="preprec"' in html and "if(capturing)doStop();else openRecordingSetup('prep')" in html and 'id="sreset"' not in html
assert all(token in html for token in ("speaker-review", "speaker-in", "speakerMap", "hftoken")), \
    "polishing must review anonymous speakers before assigning names"
assert all(token in server for token in ("prepare_diarization", "_speaker_payload", "_apply_speaker_map", 'shutil.which("whispermlx")')), \
    "server must support optional whispermlx diarization with deterministic confirmed mapping"
assert all(token in html for token in ('id="livespeakers"', "liveDiarization", "話者を識別中", 'id="hftoken_diag"')), \
    "live diarization and secure credential setup must remain visible"
assert all(token in server for token in ("_credential_set_hf_token", "live_diarization_worker", "_stable_live_speakers", "_origin_allowed")), \
    "server must keep secure credentials and parallel live diarization"
assert '"--hf_token"' not in server, "HF token must never be exposed through process arguments"
assert "'/api/chunk?kind='+encodeURIComponent(captureKind)" in html
assert '"prep-audio" if is_prep else "audio"' in server and 'prep-transcript.txt' in server
assert 'class="prep-summary"' in html and 'overflow-y:auto;overscroll-behavior:contain' in html
assert 'id="micrefresh"' in html and "getUserMedia({audio:true})" not in html and "addEventListener('devicechange'" in html
assert "$('micrefresh').onclick=fillMics" in html and "getUserMedia({audio:audioC})" in html
assert 'join(homedir(), ".local", "bin")' in cli and "mlx-whisper installation failed" in cli
assert "correctionRequest=api('/api/live-notes'" in html and "/api/live-notes" in server
assert "依頼者のライブ補足・訂正（文字起こしより優先）" in server
assert 'p == "/api/cancel"' in server and "toast-cancel" in html and "cancelCurrentOperation" in html
assert 'data-mapview="timeline"' in html and "timeline-map" in html and 'entry.setdefault("at", now[:5])' in server
assert 'join(homedir(), ".local", "bin")' in cli and 'const asrInstalled = hasMlx || hasCpp' in cli

print("Meeting-critical UI state and navigation OK")
