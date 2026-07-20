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

    # whisperがinitial-promptを崩して吐く漏れ（記号違い・1〜2字の揺れ）も曖昧一致で除去し、
    # 語彙が被るだけの実発話は残す（2026-07-20 時系列にヒント文が混入する報告への対応）
    leak = server._clean("話者名やメタデータを創作しない！\n"                          # 記号付き
                         "話者名やメタデータを作成しない\n"                            # 創作→作成
                         "話者名やメタデータを創作しない。話者名やメタデータを創作しない。\n"  # 2連結
                         "文字起こしない。\n"                                          # 文字起こしする→ない
                         "忠実に文字起こしする\n"                                      # ヒント前半の断片
                         "メタデータの設計を来週見直そう\n"                            # 実発話（語彙被り）
                         "文字起こしを確認したい\n"                                    # 実発話（語彙被り）
                         "話者分離の精度を上げたい", sid)
    for bad in ("創作しない", "作成しない", "文字起こしない", "忠実に文字起こし"):
        assert bad not in leak, (bad, leak)
    assert "メタデータの設計を来週見直そう" in leak and "話者分離の精度を上げたい" in leak \
        and "文字起こしを確認したい" in leak, leak

    # YouTube字幕由来の定型ハルシネーション（音楽マーカー・動画句）も行全体一致なら除去し、
    # 語彙が被る実発話は残す（2026-07-20 「音楽」「この動画を見てみましょう」混入報告への対応）
    hallu = server._clean("次回は金曜15時でお願いします\n音楽\n私はこの動画を見てみましょう\n"
                          "[拍手]\n♪\nチャンネル登録お願いします\n"
                          "音楽が好きなメンバーが多い\nチャンネル登録数を来月分析する", sid)
    lines = hallu.split("\n")
    assert "音楽" not in lines and "♪" not in lines and "[拍手]" not in lines, hallu
    assert "私はこの動画を見てみましょう" not in lines and "チャンネル登録お願いします" not in lines, hallu
    assert "次回は金曜15時でお願いします" in lines and "音楽が好きなメンバーが多い" in lines \
        and "チャンネル登録数を来月分析する" in lines, hallu

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
    assert server._explicit_rejected_speakers("丹野健一郎・丹野健吾・野沢栄一は文字起こし由来の誤認名です。") == ["丹野健一郎", "丹野健吾", "野沢栄一"]
    assert server._explicit_rejected_speakers("丹野健一郎・野沢栄一は参加者ではなく、文字起こしが作った誤認名です。") == ["丹野健一郎", "野沢栄一"]
    persisted = server._merge_live_patch(
        corrected,
        {"speakers_add": ["丹野健吾"], "summary": "丹野健吾が説明した"},
        "12:01",
    )
    assert persisted["speakers"] == ["丹野健心", "田部井社長"]
    assert persisted["summary"] == ""
    historical = server._enforce_confirmed_speakers({
        "speakers": ["丹野健心", "田部井社長"],
        "summary": "丹野健吾と確定",
        "open": ["丹野健一郎との同一性未確認"],
        "log": [{"who": "野沢栄一", "text": "発言"}],
    }, ["丹野健心", "田部井社長"], ["丹野健一郎", "丹野健吾", "野沢栄一"])
    assert historical["summary"] == "" and historical["open"] == []
    assert historical["log"][0]["who"] == "不明"

    first_speakers, first_turns = server._stable_live_speakers(sid, [
        {"speaker": "raw_a", "start": 0, "end": 4},
        {"speaker": "raw_b", "start": 4, "end": 8},
    ])
    Path(server._live_diarization_path(sid)).write_text(__import__("json").dumps({
        "turns": first_turns, "speakers": first_speakers,
    }), encoding="utf-8")
    # Backend labels may swap on a full re-run; time overlap must preserve UI labels.
    second_speakers, second_turns = server._stable_live_speakers(sid, [
        {"speaker": "backend_y", "start": 0, "end": 4},
        {"speaker": "backend_x", "start": 4, "end": 8},
    ])
    assert second_turns[0]["speaker"] == "SPEAKER_00"
    assert second_turns[1]["speaker"] == "SPEAKER_01"
    assert [x["id"] for x in second_speakers] == ["SPEAKER_00", "SPEAKER_01"]
    # 重なりの無い新規声を、その場にいない既存話者IDへ誤帰属しない。
    Path(server._live_diarization_path(sid)).write_text(__import__("json").dumps({
        "turns": first_turns, "speakers": first_speakers,
    }), encoding="utf-8")
    _, unseen_turns = server._stable_live_speakers(sid, [
        {"speaker": "new_voice", "start": 100, "end": 104},
    ])
    assert unseen_turns[0]["speaker"] == "SPEAKER_02"

    # ライブは全音声ではなく直近90秒＋境界1チャンクだけを処理する。
    audio_dir = Path(server.sdir(sid)) / "audio"; audio_dir.mkdir()
    audio_files = []
    for i in range(5):
        path = audio_dir / ("inc_%02d.webm" % i); path.write_bytes(b"audio"); audio_files.append(str(path))
    original_duration, original_concat = server._audio_duration, server._concat_audio_files
    selected = []
    server._audio_duration = lambda _path: 30.0
    server._concat_audio_files = lambda files, _sid, _stem: (selected.extend(files) or ("window.wav", "window.txt"))
    try:
        wav, _listf, start, spans, through = server._rolling_diarization_audio(sid, 90)
    finally:
        server._audio_duration, server._concat_audio_files = original_duration, original_concat
    assert wav == "window.wav" and start == 30 and through == 150
    assert [Path(x).name for x in selected] == ["inc_01.webm", "inc_02.webm", "inc_03.webm", "inc_04.webm"]
    assert [x["name"] for x in spans] == [Path(x).name for x in selected]

    # 一つのチャンクに二人が拮抗しているときは誤って1人に帰属しない。
    span = {"start": 0, "end": 10}
    assert server._dominant_speaker(span, [
        {"speaker": "SPEAKER_00", "start": 0, "end": 7},
        {"speaker": "SPEAKER_01", "start": 7, "end": 10},
    ])[0] == "SPEAKER_00"
    assert server._dominant_speaker(span, [
        {"speaker": "SPEAKER_00", "start": 0, "end": 5},
        {"speaker": "SPEAKER_01", "start": 5, "end": 10},
    ])[0] == ""

    # 既に判明した音声チャンクは、文字起こし受領時から暂定話者付きで表示する。
    Path(server._live_diarization_path(sid)).write_text(__import__("json").dumps({
        "audioSpeakers": {"inc_04.webm": "SPEAKER_00"}, "turns": [], "speakers": [],
    }), encoding="utf-8")
    (Path(server.sdir(sid)) / "data.json").write_text(server.EMPTY_DATA, encoding="utf-8")
    server._write_live_receipt(sid, "最新の発話", 20, "inc_04.webm")
    live_data = __import__("json").loads((Path(server.sdir(sid)) / "data.json").read_text(encoding="utf-8"))
    assert live_data["liveReceipt"]["speaker"] == "SPEAKER_00"
    assert live_data["timeline"][-1]["who"] == "話者A"

worker_source = (ROOT / "scripts" / "live-diarize-worker.py").read_text(encoding="utf-8")
assert "def wav_tensor" in worker_source and "pipeline(wav_tensor(wav)" in worker_source
assert "pyannote\\.audio\\.core\\.io" in worker_source

print("Anonymous speakers remain stable until user-confirmed mapping")
