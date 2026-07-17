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
assert 'class="ri-q"' in html and 'class="ri-body"' in html, "rail cards must show question and intent/answer in full"
assert "overflow-wrap:anywhere" in html
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
assert 'id="statuspill"' in html and 'id="statuscluster"' not in html and 'id="stjob"' not in html, "header must show only the fixed-width status pill"
assert "PILL_LABELS" in html and "min-width:106px" in html, "status pill must be fixed-width with 4 states only"
assert "flex-wrap:nowrap" in html, "header polling must not move the controls"
assert "body:not(.slidemode){overflow-x:hidden}" in html, "header controls must not horizontally shift the meeting page"
assert "tr('最終解析','Last analysis')" in html, "last-analysis detail must live in the pill popover"
assert "$('st')" not in html and "'lastupdate'" not in html, "removed flickering header text must not come back"
assert 'id="slog"' in html and 'class="prep-quick"' in html and 'id="copilotbubble"' not in html, \
    "prep chat must be embedded in the prep phase, not a floating bubble/modal"
assert "livemtg_mindmap_mode" in html and "captureMindmapUi" in html and "restoreMindmapUi" in html, \
    "live mind-map refreshes must preserve the active tab, expanded nodes, and scroll position"
assert "setViewTab(b.dataset.vt)" in html, "the 5 flat view tabs must drive list/map switching"
assert "mindmapMode='tree'" in html and "mindmapDefaultVersion='tree-v1'" in html, \
    "live map state must keep tree as the stored default mode"
assert all(token in html for token in (
    'data-vt="list"', 'data-vt="tree"', 'data-vt="radial"', 'data-vt="relation"', 'data-vt="timeline"',
    'class="radial-map"', "radial=['mindmap'"
)), "live view must expose all five views as flat tabs"
assert "Mermaid</button>" not in html and "Mermaid</button>" not in mindmap, "the Mermaid tech name must not appear in UI labels"
assert all(token in slides for token in (
    "data-generated-map", "data-generated-view", "livemtg_generated_map_mode", "defaultVersion='radial-v1'", "mode='radial'"
)), "generated map must default to the radial view and preserve the selected tab"
assert 'str(data.get("diagram") or "").strip()' in mindmap, "generated Mermaid must preserve line breaks"
assert 'radial_lines = ["mindmap"' in mindmap and 'data-generated-map="radial"' in mindmap, \
    "generated output must contain the separate radial mind map"
assert 'grid-template-columns:minmax(0,1fr) minmax(360px,520px)' in html, "prep layout must keep chat dominant with the brief sidebar"
assert 'class="railtabs"' in html and 'id="rail-ask"' in html and 'id="rail-goal"' in html, "live rail must keep its two tabs"
assert 'min-height:calc(100dvh - 58px)' in html, "live rail color must always reach the viewport bottom"
assert 'renderConfirm' in html and '"confirm"' in server, "live interpretation checks (confirm) must stay wired"
assert 'id="importfile"' in html and '/api/import_notes' in server, "prep notes import must stay wired"
assert 'id="preprec"' in html and "if(capturing)doStop();else openRecordingSetup('prep')" in html and 'id="sreset"' not in html
assert all(token in html for token in ("speaker-review", "speaker-in", "speakerMap", "hftoken")), \
    "polishing must review anonymous speakers before assigning names"
assert all(token in server for token in ("prepare_diarization", "_speaker_payload", "_apply_speaker_map", 'shutil.which("whispermlx")')), \
    "server must support optional whispermlx diarization with deterministic confirmed mapping"
assert all(token in html for token in ('id="livespeakers"', "liveDiarization", "話者を識別中", 'id="hftoken_diag"')), \
    "live diarization and secure credential setup must remain visible"
assert all(token in html for token in ('id="recordhealth"', "captureProof.mic", "captureProof.server", "captureProof.transcript")), \
    "recording must visibly prove microphone, server persistence, and transcription"
assert all(token in server for token in ("_write_live_receipt", "detail_deferred", "timeout=15", "_cancel_background_ai")), \
    "live transcription receipt must not wait for slow background analysis"
assert all(token in server for token in ("_credential_set_hf_token", "live_diarization_worker", "_stable_live_speakers", "_origin_allowed")), \
    "server must keep secure credentials and parallel live diarization"
assert '"--hf_token"' not in server, "HF token must never be exposed through process arguments"
assert "'/api/chunk?kind='+encodeURIComponent(captureKind)" in html
assert '"prep-audio" if is_prep else "audio"' in server and 'prep-transcript.txt' in server
assert 'id="sboard"' in html and 'overflow-y:auto;overscroll-behavior:contain' in html
assert 'id="micrefresh"' in html and "getUserMedia({audio:true})" not in html and "addEventListener('devicechange'" in html
assert "$('micrefresh').onclick=fillMics" in html and "getUserMedia({audio:audioC})" in html
assert 'join(homedir(), ".local", "bin")' in cli and "mlx-whisper installation failed" in cli
assert "correctionRequest=api('/api/live-notes'" in html and "/api/live-notes" in server
assert "依頼者のライブ補足・訂正（文字起こしより優先）" in server
assert 'p == "/api/cancel"' in server and "toast-cancel" in html and "cancelCurrentOperation" in html
assert 'data-vt="timeline"' in html and "timeline-map" in html and 'entry.setdefault("at", now[:5])' in server
assert 'join(homedir(), ".local", "bin")' in cli and 'const asrInstalled = hasMlx || hasCpp' in cli

print("Meeting-critical UI state and navigation OK")
