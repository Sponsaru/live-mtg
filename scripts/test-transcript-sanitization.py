#!/usr/bin/env python3
"""Whisper hallucinations must be removed without erasing real discussion."""

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


with tempfile.TemporaryDirectory(prefix="live-mtg-transcript-clean-") as tmp:
    root = Path(tmp)
    os.environ.update({
        "RUN": str(root),
        "MEETINGS_DIR": str(root / "meetings"),
        "DRIVE_SYNC_DIR": str(root / "drive"),
        "PROFILE_MD": str(root / "profile.md"),
        "PLAYBOOK_DIR": str(root / "playbooks"),
    })

    import server

    sid = "transcript-clean-test"
    meeting = Path(server.sdir(sid))
    meeting.mkdir(parents=True)
    server.write_meta(sid, {"id": sid, "title": "Transcript cleaning", "language": "ja"})
    (meeting / "data.json").write_text(server.EMPTY_DATA, encoding="utf-8")

    hallucinations = (
        "字幕を作成しています。",
        "字幕をご覧ください。",
        "日本語字幕をオンにしてご覧ください。",
        "話者名やメタデータは創作しない。",
    )
    real_speech = ("字幕制作の話をする", "メタデータ設計を見直す")

    # Each reported phrase is rejected as a complete utterance.
    for phrase in hallucinations:
        assert server._clean(phrase, sid) == "", "%r leaked into the transcript" % phrase
        inline = server._clean("実発話の前。" + phrase + "実発話の後。", sid)
        assert inline == "実発話の前。実発話の後。", \
            "inline hallucination was not removed safely: %r" % inline

    # The same phrases must be removed when interleaved with real lines, while
    # legitimate discussion sharing words such as "subtitles" and "metadata"
    # remains untouched.
    mixed_lines = [real_speech[0], hallucinations[0], hallucinations[1],
                   real_speech[1], hallucinations[2], hallucinations[3]]
    mixed = server._clean("\n".join(mixed_lines), sid).splitlines()
    assert mixed == list(real_speech), mixed

    # Existing timeline entries are cleaned in place when the versioned
    # timeline migration runs. A partly useful entry keeps its metadata and its
    # cleaned text; an entry containing only hallucinations disappears.
    timeline = [
        {"at": "09:00", "who": "話者A", "text": hallucinations[0] + "議題Aへ進みます。"},
        {"at": "09:01", "who": "話者B", "text": "\n".join(hallucinations)},
        {"at": "09:02", "who": "話者A", "text": real_speech[0]},
        {"at": "09:03", "who": "話者B", "text": real_speech[1]},
    ]
    data = json.loads(server.EMPTY_DATA)
    data.update({"timeline": timeline, "_tlCleanVer": 2})
    (meeting / "data.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    server._write_live_receipt(sid, "新しい実発話", 100, "inc_new.webm")
    saved = json.loads((meeting / "data.json").read_text(encoding="utf-8"))
    texts = [entry.get("text") for entry in saved.get("timeline", [])]
    assert texts == ["議題Aへ進みます。", real_speech[0], real_speech[1], "新しい実発話"], texts
    assert saved.get("timeline", [])[0].get("at") == "09:00"
    assert saved.get("timeline", [])[0].get("who") == "話者A"
    assert saved.get("_tlCleanVer") == server.TL_CLEAN_VER

    # A pre-existing transcript is migrated atomically, with an exact backup
    # and a generation rotation so every transcript consumer reprocesses the
    # changed offsets. The version marker makes subsequent calls idempotent.
    original = (hallucinations[0] + "\n" + real_speech[0] + "\n" +
                hallucinations[1] + "\n" + hallucinations[2] + "\n" +
                real_speech[1] + "\n" + hallucinations[3] + "\n")
    transcript_path = meeting / "transcript.txt"
    transcript_path.write_text(original, encoding="utf-8")
    old_generation = server._transcript_generation(sid)

    replaced_targets = []
    original_replace = server.os.replace
    def tracked_replace(source, target):
        replaced_targets.append(os.fspath(target))
        return original_replace(source, target)
    server.os.replace = tracked_replace
    try:
        server._migrate_transcript_cleaning(sid)
    finally:
        server.os.replace = original_replace

    cleaned = transcript_path.read_text(encoding="utf-8")
    assert cleaned.strip().splitlines() == list(real_speech), cleaned
    assert os.fspath(transcript_path) in replaced_targets, \
        "transcript migration must publish through atomic os.replace"

    backup = meeting / ("transcript.txt.pre-clean-v%d.bak" % server.TL_CLEAN_VER)
    marker = meeting / ".transcript-clean.json"
    assert backup.read_text(encoding="utf-8") == original, "migration backup is not the exact original"
    marker_data = json.loads(marker.read_text(encoding="utf-8"))
    assert marker_data.get("version") == server.TL_CLEAN_VER
    new_generation = (meeting / ".transcript-generation").read_text(encoding="utf-8").strip()
    assert new_generation and new_generation != old_generation, \
        "non-append transcript cleaning must rotate the analysis generation"
    assert not list(meeting.glob("*.tmp*")), "atomic migration left temporary files behind"

    snapshot = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in (transcript_path, backup, marker, meeting / ".transcript-generation")
    }
    server._migrate_transcript_cleaning(sid)
    second = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in (transcript_path, backup, marker, meeting / ".transcript-generation")
    }
    assert second == snapshot, "second migration call was not idempotent"

    # Migration's read/replace transaction and every transcript append share a
    # lock. Pause migration after its snapshot read, then let ASR and a live
    # note attempt to append; neither accepted line may be overwritten by the
    # older cleaned snapshot.
    def concurrent_session(name):
        concurrent_sid = "transcript-clean-" + name
        concurrent = Path(server.sdir(concurrent_sid))
        concurrent.mkdir(parents=True)
        server.write_meta(concurrent_sid, {"id": concurrent_sid, "title": name, "language": "ja"})
        (concurrent / "data.json").write_text(server.EMPTY_DATA, encoding="utf-8")
        (concurrent / "transcript.txt").write_text(
            hallucinations[0] + "\n移行前の実発話\n", encoding="utf-8"
        )
        return concurrent_sid, concurrent

    def run_during_paused_migration(concurrent_sid, concurrent, append, expected):
        snapshot_read = threading.Event()
        publish_allowed = threading.Event()
        original_clean_stored = server._clean_stored_transcript

        def paused_clean(value, selected_sid=None):
            result = original_clean_stored(value, selected_sid)
            snapshot_read.set()
            assert publish_allowed.wait(3), "test did not release transcript migration"
            return result

        server._clean_stored_transcript = paused_clean
        migration = threading.Thread(target=server._migrate_transcript_cleaning, args=(concurrent_sid,))
        writer = threading.Thread(target=append)
        try:
            migration.start()
            assert snapshot_read.wait(3), "migration did not reach its read/replace boundary"
            writer.start()
            time.sleep(.05)
            assert writer.is_alive(), "transcript append did not wait for migration's shared lock"
            publish_allowed.set()
            migration.join(3); writer.join(3)
            assert not migration.is_alive() and not writer.is_alive(), "transcript migration deadlocked"
        finally:
            publish_allowed.set()
            server._clean_stored_transcript = original_clean_stored
        final_text = (concurrent / "transcript.txt").read_text(encoding="utf-8")
        assert "移行前の実発話" in final_text and expected in final_text, final_text
        assert hallucinations[0] not in final_text

    asr_sid, asr_meeting = concurrent_session("asr-race")
    asr_input = root / "asr-race.webm"
    asr_input.write_bytes(b"audio")
    original_helpers = {
        name: getattr(server, name) for name in (
            "_run", "_mean_db", "_overlap_wav", "_whisper", "queue_spoken_lookup",
            "request_analysis", "request_active_view_update", "request_detail",
            "request_live_diarization",
        )
    }

    class Result:
        returncode = 0

    def fake_run(command, **_kwargs):
        Path(command[-1]).write_bytes(b"wav")
        return Result()

    server._run = fake_run
    server._mean_db = lambda _wav: -10
    server._overlap_wav = lambda _sid, wav, _kind="meeting": wav
    server._whisper = lambda _wav, _sid=None: "並行ASR追記"
    server.queue_spoken_lookup = lambda *_args: None
    server.request_analysis = lambda *_args: None
    server.request_active_view_update = lambda *_args, **_kwargs: None
    server.request_detail = lambda *_args: None
    server.request_live_diarization = lambda *_args: None
    try:
        run_during_paused_migration(
            asr_sid, asr_meeting,
            lambda: server.process_chunk(asr_sid, os.fspath(asr_input)), "並行ASR追記"
        )
    finally:
        for name, value in original_helpers.items():
            setattr(server, name, value)

    note_sid, note_meeting = concurrent_session("note-race")
    original_retro = server._retro_apply
    original_request_analysis = server.request_analysis
    original_request_detail = server.request_detail
    server._retro_apply = lambda *_args: None
    server.request_analysis = lambda *_args: None
    server.request_detail = lambda *_args: None
    try:
        run_during_paused_migration(
            note_sid, note_meeting,
            lambda: server.add_live_note(note_sid, "並行ライブ補足"), "並行ライブ補足"
        )
    finally:
        server._retro_apply = original_retro
        server.request_analysis = original_request_analysis
        server.request_detail = original_request_detail


print("Transcript and timeline cleaning removes reported hallucinations safely and idempotently")
