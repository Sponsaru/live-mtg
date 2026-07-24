#!/usr/bin/env python3
"""API handler smoke test for the meeting-flow server integration."""

import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


with tempfile.TemporaryDirectory(prefix="live-mtg-flow-http-") as root:
    os.environ["RUN"] = root
    os.environ["MEETINGS_DIR"] = os.path.join(root, "meetings")
    os.environ["DRIVE_SYNC_DIR"] = os.path.join(root, "drive")

    import server

    sid = server.new_session("統合テスト", goal="導入条件と次の行動を決める")
    # APIはsidを要求開始時に固定し、表示中会議に依存しないことも確認する。
    server.current_id = "another-meeting"

    def request(path, body=None):
        handler = object.__new__(server.H)
        handler.path = path
        handler.headers = {}
        handler._body_json = lambda: body or {}
        handler._send = lambda code, data, ctype="application/json; charset=utf-8": (
            code, json.loads(data) if isinstance(data, str) and data.startswith("{") else data)
        return handler.do_POST() if body is not None else handler.do_GET()

    status, loaded = request("/api/meeting-flow?sid=" + sid)
    assert status == 200 and loaded["ok"]
    assert loaded["flow"]["target"]["text"] == "導入条件と次の行動を決める"
    assert loaded["flow"]["target"]["origin"] == "user" and loaded["flow"]["target"]["locked"] is True
    revision = loaded["revision"]

    status, missing_url = request("/api/strategy", {
        "sid": sid, "message": "このURLを調べて、会議に反映してほしい：会社名だけ",
    })
    assert status == 400 and missing_url["msg"].startswith("URLがありません")
    assert not os.path.exists(server._strategy_path(sid)), "invalid URL request must not create strategy state"

    server._strategy_progress_update(sid, "progress-http-test", "reading_sources", {"sourceCount": 2})
    status, progress = request("/api/strategy-progress?sid=" + sid + "&jobId=progress-http-test")
    assert status == 200 and progress["stage"] == "reading_sources"
    assert progress["detail"]["sourceCount"] == 2 and progress["done"] is False

    status, model_settings = request("/api/settings", {"codexProfile": "quality"})
    assert status == 200 and model_settings["codexModels"]["profile"] == "quality"
    assert model_settings["codexModels"]["lanes"]["fast"]["model"] == "gpt-5.6-sol"
    assert model_settings["codexModels"]["lanes"]["fast"]["effort"] == "low"
    status, model_settings = request("/api/settings", {"codexProfile": "recommended"})
    assert status == 200 and model_settings["codexModels"]["lanes"]["assist"]["model"] == "gpt-5.6-terra"

    status, changed = request("/api/meeting-flow/action", {
        "sid": sid, "revision": revision, "action": "agenda.create",
        "payload": {"title": "料金体系を確定する"},
    })
    assert status == 200 and changed["revision"] == revision + 1
    assert changed["flow"]["agendas"][0]["title"] == "料金体系を確定する"

    status, conflict = request("/api/meeting-flow/action", {
        "sid": sid, "revision": revision, "action": "target.update",
        "payload": {"text": "古い更新は保存しない"},
    })
    assert status == 409 and conflict["conflict"] is True
    assert conflict["revision"] == changed["revision"]

    status, target_changed = request("/api/meeting-flow/action", {
        "sid": sid, "revision": changed["revision"], "action": "target.update",
        "payload": {"text": "新しい着地点"},
    })
    assert status == 200 and server.read_meta(sid)["goal"] == "新しい着地点"

    persisted = json.load(open(os.path.join(server.sdir(sid), "meeting-flow.json"), encoding="utf-8"))
    assert persisted["agendas"][0]["title"] == "料金体系を確定する"

    # 準備の壁打ちは既存AI応答1回の中でflow差分も返し、即時保存する。
    server.current_id = sid
    strategy_message = "議題に導入時期を入れたい"
    strategy_prompts = []

    def fake_strategy_ai(prompt, **_kwargs):
        strategy_prompts.append(prompt)
        return json.dumps({
            "reply": "導入時期を議題に反映します。", "brief": "導入時期を確認する。",
            "board": {"outcome": "", "counterpart": "", "hypotheses": [], "questions": [],
                      "risks": [], "avoid": [], "sources": []},
            "meetingFlow": {
                "target": {"text": "", "successCriteria": "", "explicit": False,
                           "evidence": {"start": 0, "end": 0, "text": ""}},
                "agendas": [{"clientKey": "a1", "title": "導入時期", "explicit": True,
                             "evidence": {"start": 0, "end": len(strategy_message),
                                          "text": strategy_message}}],
                "questions": [],
            },
        }, ensure_ascii=False)

    original_ai = server._ai_text
    server._ai_text = fake_strategy_ai
    try:
        ok, _result = server.strategy_chat(sid, strategy_message)
    finally:
        server._ai_text = original_ai
    assert ok and "meetingFlow" in strategy_prompts[0]
    prepared = server.FLOW_STORE.load(sid)
    prepared_agenda = next(a for a in prepared["agendas"] if a["title"] == "導入時期")
    assert prepared_agenda["approval"] == "accepted" and prepared_agenda["origin"] == "user"

    # ライブ進行ボードは即時質問とは別のAIレーンで、根拠span付きで反映する。
    transcript = "導入時期について議論を始めます"
    with open(os.path.join(server.sdir(sid), "transcript.txt"), "w", encoding="utf-8") as stream:
        stream.write(transcript)
    server._invalidate_transcript_consumers(sid)
    live_prompts = []

    def fake_live_ai(prompt, **_kwargs):
        live_prompts.append(prompt)
        if "進行ボードだけ" not in prompt:
            return json.dumps({"summary": "導入時期の議論を開始"}, ensure_ascii=False)
        return json.dumps({
            "currentAgendaId": prepared_agenda["id"],
            "currentAgendaEvidenceKeys": ["e1"],
            "evidence": [{"key": "e1", "deltaStart": 0, "deltaEnd": len(transcript),
                          "text": transcript, "speaker": "", "at": ""}],
            "agendaStatusUpdates": [{"agendaId": prepared_agenda["id"], "status": "discussing",
                                     "basis": "議論を開始", "evidenceKeys": ["e1"]}],
            "questionUpdates": [], "resultUpdates": [], "agendaProposals": [],
            "questionProposals": [], "suggestions": [],
        }, ensure_ascii=False)

    server._ai_text = fake_live_ai
    original_recording = server.recording
    server.recording = True
    try:
        assert server._claude_update(sid) is True
        assert server._flow_update(sid) is True
    finally:
        server.recording = original_recording
        server._ai_text = original_ai
    live = server.FLOW_STORE.load(sid)
    live_agenda = next(a for a in live["agendas"] if a["id"] == prepared_agenda["id"])
    assert live_agenda["status"] == "discussing" and live_agenda["current"] is True
    assert live["evidence"] and live["evidence"][-1]["text"] == transcript
    assert "進行ボード" not in live_prompts[0]
    assert "進行ボードだけ" in live_prompts[1]

    # 即時支援が落ちても、独立した進行ボードレーンは同じ発話を処理できる。
    second = "導入時期は来月で合意しました"
    with open(os.path.join(server.sdir(sid), "transcript.txt"), "a", encoding="utf-8") as stream:
        stream.write(second)
    server.analysis_coverage.pop(sid, None)
    server.flow_analysis_coverage.pop(sid, None)
    pathlib.Path(server.sdir(sid), ".analysis-coverage.json").unlink(missing_ok=True)
    pathlib.Path(server.sdir(sid), ".flow-analysis-coverage.json").unlink(missing_ok=True)

    def fail_fast_only(prompt, **_kwargs):
        if "進行ボードだけ" not in prompt:
            raise TimeoutError("fast lane down")
        return json.dumps({"currentAgendaId": prepared_agenda["id"], "evidence": [],
                           "agendaResolutionUpdates": [], "resultUpdates": []}, ensure_ascii=False)

    server._ai_text = fail_fast_only
    server.recording = True
    try:
        assert server._claude_update(sid) is False
        assert server._flow_update(sid) is True
    finally:
        server.recording = original_recording
        server._ai_text = original_ai

    server._add_flow_support_suggestion(sid, "unstuck", "2案を評価軸で比較する", "議論が平行線")
    supported = server.FLOW_STORE.load(sid)
    support = next(s for s in supported["suggestions"] if s["type"] == "unstuck")
    assert support["agendaId"] == prepared_agenda["id"]

    # 目標欄が未入力のまま用途・立場だけ保存しても、AI仮の「今日の着地点」を
    # 空文字で上書きロックしない。目標を実際に変えた時だけ正本へ同期する。
    # 予約済みの固定IDで旧データ移行を再現する。
    goal_sid = "goal-sync-regression"
    goal_dir = pathlib.Path(server.sdir(goal_sid))
    goal_dir.mkdir(parents=True)
    (goal_dir / "meta.json").write_text(
        json.dumps({"id": goal_sid, "title": "goal同期テスト"}, ensure_ascii=False), encoding="utf-8")
    (goal_dir / "data.json").write_text("{}", encoding="utf-8")
    server.FLOW_STORE.apply_ai_diff(goal_sid, {"targetUpdate": {"text": "AIが仮置きした着地点", "origin": "ai"}})
    status, res = request("/api/goal", {"sid": goal_sid, "goal": "", "mtype": "定例", "stance": ""})
    assert status == 200 and res["ok"]
    kept = server.FLOW_STORE.load(goal_sid)["target"]
    assert kept["text"] == "AIが仮置きした着地点" and not kept["locked"], \
        "saving mtype with an empty goal must not wipe or lock the AI-drafted target"
    status, res = request("/api/goal", {"sid": goal_sid, "goal": "本日の着地点を確定", "mtype": "定例", "stance": ""})
    assert status == 200 and res["ok"]
    synced = server.FLOW_STORE.load(goal_sid)["target"]
    assert synced["text"] == "本日の着地点を確定" and synced["locked"] and synced["origin"] == "user"

    # 旧会議にmeeting-flowの結果が無くても、保存済みの最終整理からメイン画面を復元する。
    legacy_sid = "legacy-final-flow"
    legacy_dir = pathlib.Path(server.sdir(legacy_sid)); legacy_dir.mkdir(parents=True)
    (legacy_dir / "meta.json").write_text(
        json.dumps({"id": legacy_sid, "title": "旧会議"}, ensure_ascii=False), encoding="utf-8")
    (legacy_dir / "data.json").write_text("{}", encoding="utf-8")
    (legacy_dir / "final.json").write_text(json.dumps({"meetingFlow": {"agendas": [{
        "title": "契約条件", "status": "completed", "summary": "条件を確定",
        "answers": ["年間契約"], "decisions": ["来月開始"],
        "actions": ["佐藤：契約書を送る"], "unresolved": []}]}},
        ensure_ascii=False), encoding="utf-8")
    status, restored = request("/api/meeting-flow?sid=" + legacy_sid)
    assert status == 200 and restored["rebuilding"] is False
    restored_agenda = restored["flow"]["agendas"][0]
    assert restored_agenda["title"] == "契約条件" and restored_agenda["status"] == "discussed"
    assert restored_agenda["resolutionStatus"] == "agreed"
    assert restored_agenda["result"]["decisions"][0]["text"] == "来月開始"

print("meeting flow integration tests passed")
