#!/usr/bin/env python3
"""Regression checks for anonymous diarization and confirmed speaker mapping."""

import importlib.util
import os
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as tmp:
    os.environ.update({
        "RUN": tmp,
        "MEETINGS_DIR": os.path.join(tmp, "meetings"),
        "DRIVE_SYNC_DIR": os.path.join(tmp, "drive"),
        "PROFILE_MD": os.path.join(tmp, "profile.md"),
    })
    spec = importlib.util.spec_from_file_location("live_mtg_speaker_test", ROOT / "server.py")
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)

    sid = "speaker-test"
    os.makedirs(server.sdir(sid), exist_ok=True)
    server.write_meta(sid, {"title": "田部井社長との会議", "language": "ja"})
    Path(server.PROFILE_MD).write_text("名前：丹野健心\n", encoding="utf-8")

    hint = server._asr_hint(sid)
    assert "丹野健心" not in hint and "田部井" not in hint
    cleaned = server._clean("話者名は丹野健吾。\n話題は丹野健吾とのAX相談。\n実際の発言です。", sid)
    assert cleaned == "実際の発言です。"

    result = {"segments": [
        {"speaker": "SPEAKER_00", "start": 0, "end": 2, "text": "最初の発言"},
        {"speaker": "SPEAKER_00", "start": 3, "end": 5, "text": "続きの発言"},
        {"speaker": "SPEAKER_01", "start": 8, "end": 10, "text": "相手の返答"},
    ]}
    speakers, turns, transcript = server._speaker_payload(result)
    assert len(speakers) == 2 and len(turns) == 2
    assert "[SPEAKER_00] 最初の発言 続きの発言" in transcript

    mapped = server._apply_speaker_map({
        "speakers": ["SPEAKER_00", "SPEAKER_01"],
        "log": [{"who": "SPEAKER_00", "text": "SPEAKER_01へ質問"}],
    }, {"SPEAKER_00": "丹野健心", "SPEAKER_01": "田部井社長"})
    assert mapped["speakers"] == ["丹野健心", "田部井社長"]
    assert mapped["log"][0] == {"who": "丹野健心", "text": "田部井社長へ質問"}

    corrected = server._merge_live_patch(
        {"speakers": ["丹野健心", "丹野健一郎", {"name": "丹野健吾"}],
         "summary": "話者が丹野健吾と確定",
         "open": ["話者『丹野健一郎』と依頼主の同一性未確認", "金額は未確認"],
         "log": [{"who": "丹野健吾", "text": "実際の発言"}]},
        {"speakers_set": ["丹野健心", "田部井社長"]},
        "12:00",
    )
    assert corrected["speakers"] == ["丹野健心", "田部井社長"]
    assert corrected["summary"] == ""
    assert corrected["open"] == ["金額は未確認"]
    assert corrected["log"][0]["who"] == "不明"
    assert server._explicit_participants("参加者は丹野健心と田部井社長の2名のみです。誤認名は除外。") == ["丹野健心", "田部井社長"]
    persisted = server._merge_live_patch(
        corrected,
        {"speakers_add": ["丹野健吾"], "summary": "丹野健吾が説明した"},
        "12:01",
    )
    assert persisted["speakers"] == ["丹野健心", "田部井社長"]
    assert persisted["summary"] == ""

print("Anonymous speakers remain stable until user-confirmed mapping")
