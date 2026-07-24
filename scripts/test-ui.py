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
assert "hardMs:10000" in html and "reachedHardLimit" in html, "audio must upload within 10 seconds"
assert all(token in html for token in ("indexedDB.open(CAPTURE_DB", "persistPendingChunk", "restorePendingChunks",
                                        "livemtg_capture_intent", 'id="capturerecoverbg"', "capturerecoverresume")), \
    "browser restarts must restore uploaded chunks and explicitly resume interrupted recording"
assert "function itemText(value)" in html and "esc(itemText(x))" in html, "structured AI items must never render as [object Object]"
assert "^\\[object Object\\]$" in html and 're.fullmatch(r"\\[object Object\\]"' in server
assert "def _live_list_text(value):" in server and "old_items = list(filter(None" in server, "structured list values must be normalized before persistence"
assert "capture_heartbeat > 45" in server, "heartbeat expiry must exceed the 15-second audio chunk interval"
assert '"detailing": bool(current_id)' in server and "s.detailing?" in html
assert "/api/desktop-health" in html and "/api/ai-check" in html
assert all(token in html for token in ('id="authblockbg"', 'id="authlogin"',
                                       "if(!readOnlyMode)pollAiAuth()", "api('/api/ai-auth'"))
assert 'p == "/api/ai-auth"' in server and "def ai_auth_status" in server, \
    "AI sign-out must be checked every 10 seconds through a dedicated lightweight endpoint"
assert "while(true)" in html and "chunkUploadTail" in html and "active='+active" in html, \
    "audio chunks must remain queued and ordered across a server restart"
assert "browser_active" in server and "recording = True" in server, \
    "an active browser chunk must restore server recording state"
assert 'await api(\'/api/health\')' in html, "recording must use the lightweight server check"
assert "if(!health||!health.ok)" not in html, "AI/ASR diagnostics must not block raw recording"
assert 'p == "/api/health"' in server and "def service_health():" in server
assert 'id="livemtg-back"' in slides and 'href="/"' in slides
assert "if (!setupComplete()) await onboard" in cli, "first launch must run onboarding"
assert "URLがありません。http:// または https:// で始まるURLを貼り付けてください。" in html
url_validation = html.index("const validationError=validateStrategyMessage(message)")
strategy_thinking = html.index("id=\"sthinking\"")
assert url_validation < strategy_thinking, "missing prep URL must be rejected before showing AI as busy"
assert "aria-describedby=\"sinputhint sinputerror\"" in html and 'id="sinputerror" role="alert"' in html
assert "/api/strategy-progress?sid=" in html and "STRATEGY_PROGRESS_LABELS" in html
assert "strategy-progress-time" in html and "Date.now()-startedAt" in html, \
    "prep chat must show the actual server stage and elapsed seconds"
assert "選択フォルダの資料を調べながら考え中…" not in html, "fixed fake progress copy must not return"
assert "def _strategy_progress_update" in server and '"web_research"' in server
assert "assist_verify(question)" in server and "【URL調査結果:" in server, \
    "URL prep requests must perform real web research before generation"
assert "STRATEGY_FAKE_SUCCESS" in server and "return False, msg" in server
assert "Claude Codeにログインされていません" in server, \
    "prep AI failures must be shown honestly instead of persisted as success"
assert all(token in html for token in ('id="aiprovider"', 'id="aimodelprofile"',
                                       'id="aimodelsummary"', 'id="ailoginalert"', 'id="ailogin"'))
assert all(token in html for token in ("renderCodexModelSummary", "codexProfile:select.value",
                                       "api('/api/ai-login'", "h.aiInstalled&&!h.aiLoggedIn")), \
    "diagnostics must expose model routing and a visible sign-in recovery flow"
assert all(token in html for token in ('#diagbg .modal{box-sizing:border-box',
                                       'width:min(620px,calc(100vw - 32px))',
                                       '.diag-asr{display:grid',
                                       '.diag-asr select{display:block;width:100%;max-width:100%',
                                       '@media(max-width:520px)',
                                       '.diag-model-summary{grid-template-columns:1fr}')), \
    "diagnostics must not overflow narrow or zoomed viewports"
assert all(token in server for token in ("CODEX_PROFILE_PRESETS", '"gpt-5.6-terra"',
                                          '"gpt-5.6-sol"', "model_reasoning_effort", "start_ai_login"))
assert '"fast": {"model": "gpt-5.6-sol", "effort": "low"}' in server, \
    "subagent/background lane must remain GPT-5.6 Sol at low effort"
assert 'CODEX_QUALITY_MODEL' in server and 'CODEX_QUALITY_EFFORT' in server
assert "running.version === pkg.version" in cli, "dashboard must replace an outdated server"
assert 'await fetchJson("/api/state"' in cli, "CLI must detect a legacy server occupying the port"
assert "hasMeetings(defaultHome)" in cli and "hasMeetings(legacyHome)" in cli
assert '(autoLegacyHome ? "ja" : detectedLanguage())' in cli
assert "<key>LIVE_MTG_HOME</key>" in cli, "daemon must preserve the selected data home"
assert "if (hadMacDaemon || hadWindowsDaemon)" in cli and "installDaemon();" in cli, \
    "legacy Mac and Windows daemon definitions must be replaced permanently"
daemon_check = cli.index("const hadMacDaemon")
windows_daemon_check = cli.index("const hadWindowsDaemon")
server_return = cli.index("if (currentServer && currentDaemon)")
assert daemon_check < server_return and windows_daemon_check < server_return, \
    "daemon migration must be checked before the same-version early return"
assert 'plist.includes(fileURLToPath(import.meta.url))' in cli
assert 'id="statuspill"' in html and 'id="statuscluster"' not in html and 'id="stjob"' not in html, "header must show only the fixed-width status pill"
assert "PILL_LABELS" in html and "min-width:106px" in html, "status pill must be fixed-width with 4 states only"
assert "flex-wrap:nowrap" in html, "header polling must not move the controls"
assert "body:not(.slidemode){overflow-x:hidden}" in html, "header controls must not horizontally shift the meeting page"
assert "tr('最終解析','Last analysis')" in html, "last-analysis detail must live in the pill popover"
assert "$('st')" not in html and "'lastupdate'" not in html, "removed flickering header text must not come back"
assert 'id="slog"' in html and 'class="prep-quick"' in html and 'id="copilotbubble"' not in html, \
    "prep chat must be embedded in the prep phase, not a floating bubble/modal"
assert all(token in html for token in ("prep-chat-panel", "会議前の壁打ち", "AIとの壁打ち",
                                        "brief-form", "AIが整理した準備", "prep-input-hint")), \
    "prep must present a large chat workspace and a clearly structured meeting brief"
assert all(token in html for token in ('class="prep-accept" data-prep-question-accept',
                                        'class="prep-accept primary" data-prep-agenda-accept',
                                        "flowButtonIcon('check')", "この質問を採用", "この議題を採用")), \
    "prep AI drafts must use clear icon-led accept controls instead of native or borrowed buttons"
assert all(token in html for token in ("Pre-meeting thinking", "Think with AI", "Meeting blueprint",
                                        "AI-organized preparation")), \
    "the redesigned prep workspace must remain usable in English"
assert "livemtg_mindmap_mode" in html and "captureMindmapUi" in html and "restoreMindmapUi" in html, \
    "live mind-map refreshes must preserve the active tab, expanded nodes, and scroll position"
assert all(token in html for token in (
    "MAP_ZOOM_MIN=0.15", "MAP_ZOOM_MAX=6", "MAP_ZOOM_SENSITIVITY=0.003",
    "MAP_PAN_X=1600", "MAP_PAN_Y=1200", "padding:1200px 1600px",
    "function requestMapCenter(mode=mindmapMode)", "activeMapTarget(mode)",
    "stage.scrollLeft+=tr.left+tr.width/2", "stage.scrollTop+=tr.top+tr.height/2",
    "cancelMapCenter();down=true", "e.preventDefault();cancelMapCenter()"
)), "live maps must support responsive wide-range zoom and generous four-way panning"
assert "setViewTab(b.dataset.vt,true)" in html, "the 4 semantic views must keep driving list/map switching and center newly opened content"
assert "mindmapMode='tree'" in html and "mindmapDefaultVersion='tree-v1'" in html, \
    "live map state must keep tree as the stored default mode"
assert all(token in html for token in (
    'data-vt="list"', 'data-vt="tree"', 'data-vt="radial"', 'data-vt="relation"',
    'class="radial-map"', "radial=['mindmap'"
)) and 'data-vt="timeline"' not in html and 'id="full"' not in html, \
    "the visualization sheet must expose four semantic views without timeline/transcript UI"
assert "Mermaid</button>" not in html and "Mermaid</button>" not in mindmap, "the Mermaid tech name must not appear in UI labels"
assert all(token in slides for token in (
    "data-generated-map", "data-generated-view", "livemtg_generated_map_mode", "defaultVersion='radial-v1'", "mode='radial'"
)), "generated map must default to the radial view and preserve the selected tab"
assert 'str(data.get("diagram") or "").strip()' in mindmap, "generated Mermaid must preserve line breaks"
assert 'radial_lines = ["mindmap"' in mindmap and 'data-generated-map="radial"' in mindmap, \
    "generated output must contain the separate radial mind map"
assert 'grid-template-columns:minmax(0,1fr) minmax(410px,500px)' in html, "prep layout must keep chat dominant with a readable brief sidebar"
assert 'id="siderail" hidden aria-hidden="true"' in html and 'id="railtoggle" title="支援レールを開閉" hidden' in html, \
    "the former permanent support rail must no longer occupy the live layout"
assert all(token in html for token in (
    'id="flowdashboard"', 'id="flowtarget"', 'id="flowagendas"', 'id="flowfollow"',
    'data-open-view="list"', 'data-open-view="tree"', 'data-open-view="radial"',
    'data-open-view="relation"'
)) and 'data-open-view="timeline"' not in html, \
    "the live main area must expose outcome, agenda board, and four semantic visualization entrances"
assert all(token in html for token in (
    "new URLSearchParams(location.search).get('readonlySid')",
    "document.body.classList.add('readonly-mode')",
    "applyMeetingData(record.summary||{})", "adoptFlow(record.flow||{},false)",
    "if(readOnlyMode){toast(", "/?readonlySid="
)), "past meetings must reuse the complete main UI while all mutations remain read-only"
assert all(token in html for token in (
    'class="flow-board-title"', 'class="flow-board-actions"', 'class="flow-support"',
    'grid-template-columns:repeat(4,minmax(0,1fr))',
    '.flow-target-edit,.flow-add,.flow-support,.flow-follow,.flow-actions button,.flow-suggestion-actions button,.inspector-actions button,.visual-sheet-tools button',
    'border-radius:999px;background:#e9e9ed',
    'id="flowaddform" hidden novalidate', 'id="flowaddinput" maxlength="300"',
    "$('flowaddform').onsubmit=async", 'flowAgendaComposing', "e.key==='Escape'"
)) and 'class="view-fresh"' not in html, \
    "meeting-flow controls must follow the existing neutral segmented and compact-button UI"
assert all(token in html for token in (
    '.flow-eyebrow{font-size:16px', '.flow-view-label{margin-top:24px;font-size:16px',
    '.flow-view-buttons button{min-width:0;border:0;background:transparent;color:var(--sub);border-radius:9px;padding:12px 12px;font-family:inherit;font-size:16px;font-weight:780', '.flow-board-head h1{font-size:24px',
    'font-family:inherit;font-size:15px;font-weight:700',
    '.visual-sheet-head h2{margin:0;font-size:22px',
    '.visual-sheet .viewtabs button{font-size:16px'
)), "meeting-flow headings and controls must remain legible beside the large outcome text"
assert all(token in html for token in (
    '--viz-blue:#2167d5', '--viz-purple:#7440b8', '--viz-teal:#14786f',
    'class="card viz-agenda"', 'class="card viz-points"',
    'class="card viz-decisions"', 'class="card viz-todos"', 'class="card viz-open"',
    'tree-branch tone-${i%5}', "col.className='relflow tone-'+(flowIndex%5)",
    '#deck .tree-lines path.tone-0', '.relflow .mermaid svg .flowchart-link'
)), "summary blocks, map branches, and relation flows must share one color system"
assert all(token in html for token in (
    'if(tree.offsetParent===null||!tr.width||!tr.height)return;',
    'liveTreeResizeObserver=new ResizeObserver',
    "if(t==='tree')animateLiveTree();"
)), "topic-map connectors must redraw when the hidden visualization sheet becomes visible"
assert all(token not in html for token in (
    'border-left:5px solid var(--blue)', 'border-left:6px solid var(--blue)',
    'border-left:5px solid var(--viz-blue)', 'border-top:3px solid var(--block-color)',
    'border-top:3px solid var(--kc,var(--blue))',
    'box-shadow:inset 4px 0 0 var(--tree-color)'
)), "rounded cards must use full borders and color fills, never thick side bars that curve at their ends"
assert 'background:transparent;color:var(--sub);border-radius:9px;padding:12px 12px;font-family:inherit;font-size:16px;font-weight:780' in html and \
       '.visual-sheet .viewtabs button[data-vt]{color:var(--sub)}' in html, \
       "visualization controls must stay neutral while the outcome-card entrances remain bold"
assert all(token in html for token in (
    '.flow-agenda.status-discussed{border-color:#d5d6da}',
    "const FLOW_RESOLUTION={not_applicable:tr('合意対象外'",
    'class="flow-resolution ${esc(agenda.resolutionStatus',
    "tr('議論の状態','Discussion status')", "tr('合意の状態','Agreement status')",
    '.flow-summary-line{font-size:17px;font-weight:590;line-height:1.6',
    '-webkit-line-clamp:2',
    "const questionBlocks=(next.length||queued.length||history.length)?",
    "tr('これまでの質問','Question history')", 'class="flow-question-state ${esc(q.status',
    'class="flow-now"', "tr('いま話している議題','Current agenda')",
    'class="flow-result-section result-${key}"',
    'class="flow-result-list"', 'const resultGroups=(resultLeft||resultRight)?',
    'class="flow-result-column"',
    '.flow-result-section{--result-color:var(--viz-blue)',
    '.flow-result-section.result-decisions',
    '.flow-result-section.result-actions', '.flow-result-section.result-unresolved'
)), "agenda cards must hide empty question areas, wrap summaries, and separate results into legible semantic groups"
assert all(token in html for token in (
    'class="flow-question-intent"', "tr('意図','Intent')",
    'function flowAgendaSuggestionsHtml(agenda)',
    'class="flow-agenda-suggestion suggestion-${esc(s.type', 'data-agenda-suggestion-action="accept"',
    'data-agenda-suggestion-action="defer"', 'data-agenda-suggestion-action="dismiss"',
    "meetingFlowAction('suggestion.'+b.dataset.agendaSuggestionAction",
    "tr('この議題への提案','Suggestions for this agenda')",
    "tr('議題提案','Agenda suggestions')"
)) and 'data-suggestion-action="accept"' not in html, \
    "agenda questions must show their intent and agenda-specific suggestions must be actionable inline without opening details"
assert all(token in html for token in (
    'function maybeOpenNewGlobalFlowSuggestions()',
    'requestAnimationFrame(()=>openGlobalFlowInspector',
    'function markGlobalFlowSuggestionsSeen',
    'function flowGlobalActiveSuggestions()',
    'function flowGlobalSuggestions(){return flowItems(meetingFlow.suggestions).filter(s=>!s.agendaId).slice().reverse()}',
    "accepted:tr('採用済み','Accepted')", "dismissed:tr('不要','Dismissed')",
    'class="flow-support-state ${esc(s.status',
    "tr(`全体AI支援 ${globalSuggestions.length}件`",
    "tr('全体AI支援','Global AI support')"
)), "new global AI support must distinguish itself from agenda suggestions and proactively open the right drawer once"
assert all(token in html for token in (
    '.flow-item{font-size:18px', '.flow-question-intent{display:block;margin-top:7px',
    '.flow-suggestion-text{font-size:18px', '.flow-suggestion-actions button{min-height:44px',
    '.drawer.flow-inspector .dh h3{font-size:25px', '.inspector-row{display:flex;',
    'font-size:18px;font-weight:650', '.flow-question-intent b{display:inline-block;',
    'background:rgba(255,255,255,.72);border:1px solid #86a4c2',
    'font-size:14px;font-weight:600;margin-right:8px',
    '.flow-suggestion-kind{display:inline-flex;align-items:center;border:1px solid var(--support-color)',
    'class="inspector-section support-card support-${esc(s.type'
)), "live AI assistance must use glanceable type, strong semantic colors, and large tap targets"
assert all(token in html for token in (
    'function flowButtonIcon(name)', 'class="flow-button-icon"', 'aria-hidden="true" focusable="false"',
    "flowButtonIcon('check')", "flowButtonIcon('pause')", "flowButtonIcon('close')",
    "flowButtonIcon('plus')", "flowButtonIcon('edit')", "flowButtonIcon('eye')",
    '.flow-actions button,.flow-suggestion-actions button,.inspector-actions button{display:inline-flex'
)), "neutral agenda and AI-support actions must include consistent glanceable icons"
assert 'flow-result-overview' not in html and "tr('該当なし','None')" not in html, \
    "agenda results must not be wrapped in a redundant current-result card or render empty category cards"
assert 'id="who" hidden aria-hidden="true"' in html and "$('who').classList.remove('show')" in html and \
       "$('who').classList.toggle('show'" not in html, "the redundant participant bar must stay hidden"
assert "$('flowadd').onclick=async()=>{const title=prompt" not in html, \
    "adding an agenda must use the accessible custom inline form, never a browser prompt"
assert all(token in html for token in (
    'class="flow-agenda-editor flow-agenda-composer" hidden novalidate',
    'data-agenda-edit-cancel', 'agendaEditor.onsubmit=async'
)) and "title=prompt(tr('議題名を編集'" not in html, \
    "editing an agenda from its card must use the custom inline form, never a browser prompt"
assert all(token in html for token in (
    'class="flow-question-editor flow-agenda-composer" hidden novalidate',
    'data-question-add-cancel', 'questionEditor.onsubmit=async',
    "meetingFlowAction('question.create',{agendaId:id,text,status:'queued'})"
)) and "if(addQuestion)addQuestion.onclick=async()=>{const text=prompt" not in html, \
    "adding a question from an agenda card must use the custom inline form, never a browser prompt"
assert all(token in html for token in (
    'id="visualbg" aria-hidden="true"', 'role="dialog" aria-modal="true" aria-labelledby="visualtitle"',
    'id="visualgrab"', 'id="visualfull"', 'id="visualclose"',
    'function openVisualSheet(mode,origin)', 'function closeVisualSheet(restore=true)',
    "visualSheetOpen&&document.visibilityState==='visible'"
)), "visualizations must use an accessible, resizable bottom sheet and focus only the open view"
assert all(token in html for token in (
    'class="drawer flow-inspector"', 'aria-labelledby="drtitle" aria-hidden="true"',
    'function openFlowInspector(id,origin)', 'function openQuestionInspector(id,origin)',
    'width:clamp(560px,46vw,760px)', 'function mountInspectorEditor(anchor,',
    'class="inspector-input"', 'class="inspector-save"', 'class="inspector-cancel"'
)), "agenda/question detail must open in the normally closed right inspector"
assert all(token in html for token in (
    "meetingFlowAction('agenda.reorder',{agendaIds:ids})", 'data-move="up"',
    'data-move="down"', 'draggable="true"', 'aria-expanded="', 'class="flow-head-tools"',
    'flex-direction:column;align-items:flex-end'
)) and 'data-move="first"' not in html and 'data-move="last"' not in html and 'flow-chevron' not in html and 'flow-expand' not in html, \
    "agenda headers must put badges/expand/reorder on the right without redundant edge-jump or left-arrow controls"
assert all(token not in html for token in (
    "const text=prompt(tr('追加する結果を入力'", "const text=prompt(tr('結果を編集'",
    "const text=prompt(tr('質問を編集'", "const answer=prompt(tr('回答を入力してください'"
)), "agenda and question detail editing must use the custom inline editor instead of browser prompts"
assert all(token in html for token in (
    "meetingFlowAction('question.create'", "meetingFlowAction('question.update'",
    "meetingFlowAction('question.accept'", "meetingFlowAction('question.dismiss'",
    "meetingFlowAction('result.update'", 'data-result-summary', 'data-flow-edit-agenda'
)), "agenda questions and results must remain manually editable and lockable"
assert all(token in html for token in (
    "'/api/meeting-flow?sid='", "'/api/meeting-flow/action'", "e.status===409",
    "rebaseFlowPayload(action,payload,next)", "fixedSid=currentSessionId", "if(sid!==currentSessionId)return false"
)), "flow UI must use the revisioned API and safely rebase once after a 409 conflict"
assert all(token in html for token in (
    'let meetingFlowRequest=null', 'if(meetingFlowRequest)return meetingFlowRequest',
    "meetingFlowRequest=null;meetingFlowSig=''",
    'async function pollMeetingContent(){await Promise.all([pollData(),pollMeetingFlow()])}',
    "$('mcancel').onclick = async ()=>{ startupPending=false;", 'await pollMeetingContent()',
    'if(recording){ startupPending=false; await pollMeetingContent(); return; }'
)), "the existing agenda board must load immediately with data.json after startup and session transitions"
assert all(token in html for token in (
    'class="modal-import-audio" id="mimportaudio"',
    '#mbg .row button{flex:1;white-space:nowrap;justify-content:center;text-align:center}',
    '終了済みの録音ファイルから作る'
)), "finished-recording import must stay separate from the two-button new-meeting action row"
assert ".flow-visual-list{display:none}" in html and \
       ".visual-sheet>.visual-scroll>.summary,.visual-sheet>.visual-scroll>.grid{display:none!important}" not in html, \
    "the Organize view must keep showing the complete legacy data.json summary"
assert "const r=await api('/api/goal',{sid,goal:text" in html, \
    "the outcome editor must save through the backward-compatible goal API"
assert '汎用的な「結論を確定」' not in html and '>結論を確定<' not in html, \
    "the ambiguous generic decision confirmation control must not return"
assert 'renderConfirm' in html and '"confirm"' in server, "live interpretation checks (confirm) must stay wired"
assert 'id="importfile"' in html and '/api/import_notes' in server, "prep notes import must stay wired"
assert 'id="preprec"' in html and "if(capturing)doStop();else openRecordingSetup('prep')" in html and 'id="sreset"' not in html
assert all(token in html for token in ("speaker-review", "speaker-in", "speakerMap", "hftoken")), \
    "polishing must review anonymous speakers before assigning names"
assert "hasSavedAnswer?answers[id]:(x.guess||'')" in html and "const resolved=hasSavedAnswer&&" in html, \
    "AI guesses must be editable from the textarea while low-confidence prefills remain reviewable"
assert "proposedStatus=suggested?" in html and "?'keep':'replace'" in html and "data-provisional" in html, \
    "AI spelling guesses must preselect keep/replace and remain editable"
assert all(token in html for token in ("candidateAnswers", "sourceSignature:finalizeSourceSignature",
                                        "status==='pending'", "corr-from", "corr-to")), \
    "polishing must require explicit term decisions and allow manual from/to corrections"
assert all(token in html for token in ("reviewfilter", "reviewsort", "applyFinalizeReviewFilters",
                                        "確信度が低い順", "自動入力済み", "data-confidence")), \
    "pre-finalize review must default to unresolved items and support confidence sorting/filtering"
assert all(token in html for token in ("flowExpanded=new Set([currentId])", "flowPendingFocusId=currentId",
                                        "focusPendingFlowAgenda()", "scrollIntoView({behavior,block:'center'})")), \
    "a newly current agenda must exclusively open and scroll into view"
assert all(token in html for token in ("review-shell", "review-polish", "review-minutes", "review-learn",
                                        "1. 会話を高精度に清書", "2. 学びと次の一手", "3. 共有用資料を作る")), \
    "the review phase must use a large, ordered, live-readable workflow"
assert html.index('class="phasecard review-learn"') < html.index('class="phasecard review-minutes"'), \
    "learnings must be prepared before exporting the minutes PDF that includes them"
assert all(token not in html for token in ('id="rvbtn-learn-slides"', 'id="rvbtn-learn-pdf"', "api('/api/learn_slides'")), \
    "learnings must remain a readable report instead of offering a separate slide/PDF workflow"
assert "bl.style.display=s.hasLearn?'none':''" in html and "lv.style.display=s.hasLearn?'':'none'" in html, \
    "completed learnings must offer view only, without a regenerate action"
assert all(token in server for token in ("minutes_pdf_is_current", '"learnings.md"',
                                          '"hasMinutesPdf": bool(current_id) and minutes_pdf_is_current(current_id)')), \
    "a minutes PDF must become stale when the learning report changes"
assert all(token in server for token in ("_map_figure_capture_html", "livemtg-map-figure-capture",
                                          'figure=1', 'q.get("figure")')), \
    "minutes map screenshots must capture the diagrams alone instead of the surrounding slide chrome"
png_capture = server[server.index("def _html_to_png"):server.index("def _map_figure_capture_html")]
assert all(token in png_capture for token in ("subprocess.Popen", "size == last_size",
                                               "_kill_process_tree(proc)")), \
    "PNG capture must accept a completed file without waiting for Chrome to exit naturally"
minutes_export = server[server.index("def export_minutes_pdf"):server.index("def minutes_pdf_is_current")]
assert all(token in minutes_export for token in ('missing_maps', '"radial"', '"relation"',
                                                  "図版撮影に失敗")), \
    "minutes export must fail visibly instead of silently dropping its final map page"
assert all(token in html for token in ("After the meeting", "Polish the conversation", "Create a shareable output",
                                        "Learnings and next steps")), \
    "the redesigned review workflow must remain usable in English"
assert all(token in html for token in ('id="rvformat-paper"', 'id="rvformat-slides"',
                                        'id="rvactions-paper"', 'id="rvactions-slides"',
                                        "selectReviewOutputFormat", "livemtg_output_format")), \
    "review outputs must offer an explicit meeting-paper versus presentation-slides choice"
assert all(token in server for token in ("deck_is_current", '"hasDeck": bool(current_id) and deck_is_current(current_id)',
                                          "_minutes_map_screenshots(sid")), \
    "presentation slides must become stale with meeting updates and receive diagram-only map assets"
map_capture = server[server.index("def _minutes_map_screenshots"):server.index("def export_minutes_pdf")]
assert all(token in map_capture for token in ("_ensure_mindmap_artifact", '"/slides.html?view=%s&figure=1&sid=%s"')) \
    and "make-map-slide.py" not in map_capture, \
    "deck figures must screenshot the actual saved relationship/radial view without rebuilding another diagram"
assert all(token in server for token in ("start_background_long_job", '"jobStatus": job_status',
                                          'timeout=1260')), \
    "Claude slide generation must run beyond the HTTP request and expose pollable state"
assert all(token in html for token in ("Claudeでスライドを生成中です", "jobStatus&&state.jobStatus.deck",
                                        "25*60*1000")), \
    "the slide UI must follow the background Claude job instead of timing out its request"
assert all(token in server for token in ('"hasDeckPdf"', 'p == "/deck.pdf"', '"slides.pdf"',
                                          "local_html=os.path.join(sdir(sid), \"slides.html\")")), \
    "presentation slide generation must also create and serve a PDF artifact"
assert all(token in html for token in ('id="rvbtn-deck-pdf"', "'/deck.pdf?ts='")), \
    "the review UI must expose the generated presentation PDF"
assert all(token in server for token in ("PREP_AUTO_CONFIDENCE", "PREP_AUTO_REPLACE_CONFIDENCE", "PREP_MACHINE_REVIEW_LIMIT",
                                          "_prepare_prep_review", '"auto": True')), \
    "high-confidence review answers must be prefilled while machine fallback noise stays bounded"
assert all(token in server for token in ("prepare_diarization", "_speaker_payload", "_apply_speaker_map", 'shutil.which("whispermlx")')), \
    "server must support optional whispermlx diarization with deterministic confirmed mapping"
assert all(token in html for token in ('id="livespeakers"', "liveDiarization", "話者を識別中", 'id="hftoken_diag"')), \
    "live diarization and secure credential setup must remain visible"
assert all(token in html for token in ('id="recordhealth"', "captureProof.mic", "captureProof.server", "captureProof.transcript")), \
    "recording must visibly prove microphone, server persistence, and transcription"
assert all(token in html for token in (
    "CAPTURE_NOTE_HOLD_MS=500", "captureNoteQueue.push(value)", "playCaptureNoteQueue()",
    "translateY(115%)", "translateY(-115%)", "class=\"rh-note-window\"",
    "CAPTURE_NOTE_DEDUPE_MS=8000"
)), "recording status messages must enter from below, remain visible, exit upward, and play sequentially"
assert all(token in server for token in ("_write_live_receipt", "detail_deferred", "timeout=15", "_cancel_background_ai")), \
    "live transcription receipt must not wait for slow background analysis"
assert all(token in server for token in ("_credential_set_hf_token", "live_diarization_worker", "_stable_live_speakers", "_origin_allowed")), \
    "server must keep secure credentials and parallel live diarization"
assert all(token in server for token in ("_enroll_voice_profile", 'p == "/api/profile-voice"',
                                          '"profileSpeakers"', "os.chmod(_voice_profile_path(), 0o600)",
                                          "_load_voice_profiles", "_remove_voice_profile")), \
    "voice enrollment must store a protected local embedding and feed realtime speaker identity"
assert all(token in html for token in ('id="profvoice"', 'id="profvoicestatus"',
                                        'id="prof_voice_name"', 'id="profvoicelist"',
                                        "new MediaRecorder", "'/api/profile-voice'",
                                        "参加者の声を登録")), \
    "prep/profile UI must provide named multi-person voice enrollment and individual removal"
assert '"--hf_token"' not in server, "HF token must never be exposed through process arguments"
assert "'/api/chunk?sid='+encodeURIComponent(sessionId)+'&kind='+encodeURIComponent(kind)" in html, \
    "audio chunks must remain bound to the meeting and capture kind active when recording started"
assert "captureMeetingChanged" in html and "別の画面で会議が切り替わりました" in html, \
    "a heartbeat 409 (meeting switched elsewhere) must be explained, not shown as server-down"
assert '"prep-audio" if is_prep else "audio"' in server and 'prep-transcript.txt' in server
assert 'id="sboard"' in html and 'overflow-y:auto;overscroll-behavior:contain' in html
assert 'id="micrefresh"' in html and "getUserMedia({audio:true})" not in html and "addEventListener('devicechange'" in html
assert "$('micrefresh').onclick=fillMics" in html and "getUserMedia({audio:audioC})" in html
assert 'join(homedir(), ".local", "bin")' in cli and "mlx-whisper installation failed" in cli
assert "correctionRequest=api('/api/live-notes'" not in html and "/api/live-notes" in server, \
    "prep chat must not trigger unrelated live-transcript analysis"
assert "依頼者のライブ補足・訂正（文字起こしより優先）" in server
assert 'p == "/api/cancel"' in server and "toast-cancel" in html and "cancelCurrentOperation" in html
assert 'data-vt="timeline"' not in html and "timeline-map" in html and 'entry.setdefault("at", now[:5])' in server, \
    "timeline data must remain internally available without a redundant UI tab"
assert "$('full').onclick" not in html and 'function loadTranscript()' not in html and '/api/transcript' in server, \
    "full transcript data must remain available to server workflows without a permanent UI entry"
assert all(token in html for token in ("jumpTimeline('top')", "jumpTimeline('bottom')",
                                        "timelinePage=rows.length", "map.offsetHeight", "timeline-jumps")), \
    "timeline must expose true top/bottom jumps, loading hidden earlier speech before jumping to the top"
assert 'join(homedir(), ".local", "bin")' in cli and 'const asrInstalled = hasMlx || hasCpp' in cli

print("Meeting-critical UI state and navigation OK")
