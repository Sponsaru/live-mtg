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

print("Long-running jobs are cancellable and live corrections are prioritized")
