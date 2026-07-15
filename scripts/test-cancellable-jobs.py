#!/usr/bin/env python3
"""Long-running generation must stop its real child process when cancelled."""

import os
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

with tempfile.TemporaryDirectory() as tmp:
    os.environ.update({
        "RUN": tmp,
        "MEETINGS_DIR": os.path.join(tmp, "meetings"),
        "DRIVE_SYNC_DIR": os.path.join(tmp, "meetings"),
        "PROFILE_MD": os.path.join(tmp, "profile.md"),
        "PLAYBOOK_DIR": os.path.join(tmp, "playbooks"),
    })
    import server

    result = []

    def work():
        try:
            with server.long_job_scope("test-session", "slides"):
                server._run([sys.executable, "-c", "import time; time.sleep(10)"], timeout=12)
        except server.JobCancelled:
            result.append("cancelled")

    thread = threading.Thread(target=work)
    thread.start()
    deadline = time.time() + 3
    while time.time() < deadline:
        with server.long_job_lock:
            process = server.long_jobs.get(("test-session", "slides"), {}).get("process")
        if process:
            break
        time.sleep(0.02)
    assert server.cancel_long_job("test-session", "slides")
    thread.join(4)
    assert not thread.is_alive(), "cancel must terminate the child process immediately"
    assert result == ["cancelled"]

    sid = server.new_session("Live note test")
    ok, notes = server.add_live_note(sid, "相手はA社ではなくB社。https://example.com")
    assert ok and notes[-1]["text"].startswith("相手はA社ではなくB社")
    transcript = open(os.path.join(server.sdir(sid), "transcript.txt"), encoding="utf-8").read()
    assert "依頼者のライブ補足・訂正（文字起こしより優先）" in transcript

    # 事前打ち合わせ録音は本会議の原本音声と分け、背景としてだけ反映する。
    prep_webm = os.path.join(server.WAVROOT, sid, "prep_1.webm")
    os.makedirs(os.path.dirname(prep_webm), exist_ok=True)
    open(prep_webm, "wb").write(b"fake-webm")

    class Result:
        returncode = 0

    def fake_run(cmd, **_kwargs):
        open(cmd[-1], "wb").write(b"fake-wav")
        return Result()

    server._run = fake_run
    server._mean_db = lambda _wav: -10
    server._overlap_wav = lambda _sid, wav, _kind="meeting": wav
    server._whisper = lambda _wav, _sid=None: "本番では価格の確認を優先しよう"
    server.queue_spoken_lookup = lambda *_args: None
    server.request_analysis = lambda *_args: None
    server.request_detail = lambda *_args: None
    server.process_chunk(sid, prep_webm)

    meeting_audio = os.path.join(server.sdir(sid), "audio", "prep_1.webm")
    prep_audio = os.path.join(server.sdir(sid), "prep-audio", "prep_1.webm")
    assert os.path.isfile(prep_audio) and not os.path.isfile(meeting_audio)
    assert "本番では価格" in open(os.path.join(server.sdir(sid), "prep-transcript.txt"), encoding="utf-8").read()
    transcript = open(os.path.join(server.sdir(sid), "transcript.txt"), encoding="utf-8").read()
    assert "事前打ち合わせの背景情報（本会議の発言・決定ではない）" in transcript

print("Long-running jobs are cancellable; live corrections and prep recordings are safely separated")
