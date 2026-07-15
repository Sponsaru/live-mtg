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

    sid = "20260715-150834"
    meeting = Path(server.sdir(sid)); meeting.mkdir(parents=True)
    meta = {"id": sid, "title": "田部井さんとのAX相談", "goal": "田部井さんのAIへの理解度を上げる",
            "project_dir": "", "created": "2026-07-15 15:08"}
    (meeting / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    (meeting / "data.json").write_text(server.EMPTY_DATA, encoding="utf-8")
    original_structured = server._claude_structured
    server._claude_structured = lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("AI must not be called"))

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

    server._claude_structured = original_structured
    server._claude_explore = lambda *_a, **_k: "not-json"
    server._ai_text = lambda *_a, **_k: '{"reply":"ok","brief":"saved","board":{}}'
    assert server._claude_structured("prompt", {}, timeout=1)["reply"] == "ok"

print("Explicit meeting identity bypasses fragile AI JSON and updates all meeting context")
