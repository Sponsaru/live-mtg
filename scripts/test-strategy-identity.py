#!/usr/bin/env python3
"""Explicit meeting identity corrections must work without an AI JSON round trip."""

import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

with tempfile.TemporaryDirectory(prefix="live-mtg-strategy-") as tmp:
    root = Path(tmp)
    os.environ.update({
        "RUN": str(root),
        "MEETINGS_DIR": str(root / "meetings"),
        "DRIVE_SYNC_DIR": str(root / "meetings"),
        "PROFILE_MD": str(root / "profile.md"),
        "PLAYBOOK_DIR": str(root / "playbooks"),
    })
    import server

    original_codex_profile = server.CODEX_PROFILE
    original_codex_model = server.CODEX_MODEL
    server.CODEX_MODEL = ""
    server.CODEX_PROFILE = "recommended"
    assert server._codex_route("haiku") == {
        "model": "gpt-5.6-sol", "effort": "low", "lane": "fast"}
    assert server._codex_route("sonnet") == {
        "model": "gpt-5.6-terra", "effort": "medium", "lane": "assist"}
    assert server._codex_route("opus") == {
        "model": "gpt-5.6-sol", "effort": "high", "lane": "quality"}
    assert server._codex_route("sonnet", background=True)["lane"] == "fast"
    assert server._codex_route("sonnet", background=True)["model"] == "gpt-5.6-sol"
    assert server.set_codex_profile("speed")
    assert server._codex_route("sonnet")["effort"] == "low"
    assert not server.set_codex_profile("unknown")
    server.CODEX_PROFILE = original_codex_profile
    server.CODEX_MODEL = original_codex_model

    sid = "20260715-150834"
    meeting = Path(server.sdir(sid)); meeting.mkdir(parents=True)
    meta = {"id": sid, "title": "田部井さんとのAX相談", "goal": "田部井さんのAIへの理解度を上げる",
            "project_dir": "", "created": "2026-07-15 15:08"}
    (meeting / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    (meeting / "data.json").write_text(server.EMPTY_DATA, encoding="utf-8")
    original_structured = server._claude_structured
    server._claude_structured = lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("AI must not be called"))

    ok, missing_url = server.strategy_chat(
        sid, "このURLを調べて、会議に必要な要点を反映してほしい：高商ロジテックの織田社長")
    assert not ok and missing_url.startswith("URLがありません")
    assert not (meeting / "strategy.json").exists(), "missing URL must be rejected before saving or calling AI"
    assert server._strategy_url_error("このURLを調べて：https://example.com/company") == ""

    cleaned = server._strategy_clean_state({
        "messages": [
            {"role": "user", "text": server.STRATEGY_IMPORTED_PREFIX + "company.md）の全文です。\n" + "x" * 5000},
            {"role": "assistant", "text": server.STRATEGY_FAKE_SUCCESS},
            {"role": "user", "text": "会長と息子にも興味を持ってほしい"},
            {"role": "assistant", "text": server.STRATEGY_FAKE_SUCCESS},
        ],
        "brief": "【依頼主の追加メモ】\n" + server.STRATEGY_IMPORTED_PREFIX + "company.md）" + "x" * 5000,
        "board": {},
    })
    assert all(x["text"] != server.STRATEGY_FAKE_SUCCESS for x in cleaned["messages"])
    assert len(cleaned["messages"][0]["text"]) < 400 and "会長と息子" in cleaned["brief"]
    assert "内容は反映していません" in server._strategy_failure_message(TimeoutError("slow"))

    ok, result = server.strategy_chat(sid, "田部井社長とのミーティングだよ")
    assert ok
    assert "反映しました" in result["reply"] and "回答形式" not in result["reply"]
    updated = json.loads((meeting / "meta.json").read_text(encoding="utf-8"))
    assert updated["title"] == "田部井社長とのAX相談"
    assert updated["goal"] == "田部井社長のAIへの理解度を上げる"
    assert updated["counterpart"] == "田部井社長"
    strategy = json.loads((meeting / "strategy.json").read_text(encoding="utf-8"))
    assert strategy["board"]["counterpart"] == "田部井社長とのミーティング"
    data = json.loads((meeting / "data.json").read_text(encoding="utf-8"))
    assert data["preparation"]["counterpart"] == "田部井社長とのミーティング"

    ok, repeated = server.strategy_chat(sid, "田部井社長とのミーティングだよ")
    assert ok and len(repeated["messages"]) == 2, "retry must replace the fallback reply instead of duplicating chat"

    # URL付き依頼は表示だけでなく実際のWeb調査結果を最終生成へ渡し、進捗も完了する。
    original_ai_text = server._ai_text
    original_assist_verify = server.assist_verify
    researched = []
    prompts = []
    server.assist_verify = lambda q: (researched.append(q) or True, "URLで確認した事実。参照: https://example.com/company")
    server._ai_text = lambda prompt, **_kwargs: (prompts.append(prompt) or json.dumps({
        "reply": "URLの内容を会議準備へ反映しました。", "brief": "URLで確認した事実。",
        "board": {"outcome": "", "counterpart": "", "hypotheses": [], "questions": [],
                  "risks": [], "avoid": [], "sources": []}
    }, ensure_ascii=False))
    job_id = "url-research-test"
    ok, web_result = server.strategy_chat(
        sid, "このURLを調べて会議に反映して：https://example.com/company", job_id)
    assert ok and researched and prompts
    assert "URL調査結果" in prompts[0] and "URLで確認した事実" in prompts[0]
    progress = server._strategy_progress_get(sid, job_id)
    assert progress["stage"] == "done" and progress["done"] is True
    server.assist_verify = original_assist_verify
    server._ai_text = original_ai_text

    server._claude_structured = original_structured
    server._claude_explore = lambda *_a, **_k: "not-json"
    server._ai_text = lambda *_a, **_k: '{"reply":"ok","brief":"saved","board":{}}'
    assert server._claude_structured("prompt", {}, timeout=1)["reply"] == "ok"

print("Explicit meeting identity bypasses fragile AI JSON and updates all meeting context")
