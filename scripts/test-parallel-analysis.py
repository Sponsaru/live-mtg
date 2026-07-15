#!/usr/bin/env python3
"""Fast/detail lanes may run concurrently, but their persisted results must never clobber each other."""

import json
import os
from pathlib import Path
import tempfile
import threading
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


with tempfile.TemporaryDirectory(prefix="live-mtg-parallel-") as tmp:
    root = Path(tmp)
    os.environ.update({
        "RUN": str(root),
        "MEETINGS_DIR": str(root / "meetings"),
        "DRIVE_SYNC_DIR": str(root / "meetings"),
        "PROFILE_MD": str(root / "profile.md"),
        "PLAYBOOK_DIR": str(root / "playbooks"),
    })

    import server

    sid = "20260715-120000"
    meeting = Path(server.sdir(sid))
    meeting.mkdir(parents=True)
    (meeting / "meta.json").write_text(json.dumps({"id": sid, "title": "Parallel"}), encoding="utf-8")
    (meeting / "data.json").write_text(server.EMPTY_DATA, encoding="utf-8")

    fast = {"summary": "fast result", "decisions_add": ["fast decision"]}
    detail = {"mindmap_add": [{"topic": "detail topic", "groups": []}], "lookups": []}
    barrier = threading.Barrier(3)

    def merge(patch):
        barrier.wait()
        server._merge_patch_to_disk(sid, patch, "12:00:00")

    threads = [threading.Thread(target=merge, args=(patch,)) for patch in (fast, detail)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()

    saved = json.loads((meeting / "data.json").read_text(encoding="utf-8"))
    assert saved["summary"] == "fast result"
    assert "fast decision" in saved["decisions"]
    assert any(x.get("topic") == "detail topic" for x in saved["mindmap"])

    # Prove the two AI lanes overlap in wall-clock time, not merely that two queues exist.
    (meeting / "transcript.txt").write_text("会議の新しい発言です。" * 30, encoding="utf-8")
    server.applied[sid] = 0
    server.detail_applied[sid] = 0

    def fake_ai(prompt, **_kwargs):
        time.sleep(0.4)
        if "最優先の即時レーン" in prompt:
            return json.dumps({"summary": "latest fast", "decisions_add": ["parallel decision"]}, ensure_ascii=False)
        return json.dumps({"mindmap_add": [{"topic": "parallel detail", "groups": []}], "lookups": []}, ensure_ascii=False)

    server._ai_text = fake_ai
    results = {}
    started = time.monotonic()
    fast_thread = threading.Thread(target=lambda: results.setdefault("fast", server._claude_update(sid)))
    detail_thread = threading.Thread(target=lambda: results.setdefault("detail", server._detail_update(sid)))
    fast_thread.start(); detail_thread.start()
    fast_thread.join(timeout=3); detail_thread.join(timeout=3)
    elapsed = time.monotonic() - started
    assert results == {"fast": True, "detail": True}, results
    assert elapsed < 0.7, f"AI lanes ran serially: {elapsed:.2f}s"

    saved = json.loads((meeting / "data.json").read_text(encoding="utf-8"))
    assert saved["summary"] == "latest fast"
    assert any(x.get("topic") == "parallel detail" for x in saved["mindmap"])

    server.request_analysis(sid)
    server.request_analysis(sid)
    assert server.analysis_q.qsize() == 1
    server.request_detail(sid)
    server.request_detail(sid)
    assert server.detail_q.qsize() == 1

source = Path(server.__file__).read_text(encoding="utf-8")
assert "これは最優先の即時レーンです" in server.LIVE_PATCH_PROMPT
assert "mindmap_add" not in server.LIVE_PATCH_PROMPT
assert "mindmap_add" in server.DETAIL_PATCH_PROMPT
assert "threading.Thread(target=detail_worker" in source
assert "with background_ai_lock:" in source
assert "len(transcript) - off > 2700" in source and "off + 900" in source
assert "end - 3000" in source

print("Parallel fast/detail analysis preserves both results")
