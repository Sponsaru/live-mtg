#!/usr/bin/env python3
"""Finished recordings enter the normal durable ASR and meeting-flow pipeline."""

import io
import json
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

with tempfile.TemporaryDirectory(prefix="live-mtg-audio-import-") as tmp:
    root = Path(tmp)
    os.environ.update({
        "RUN": str(root),
        "MEETINGS_DIR": str(root / "meetings"),
        "DRIVE_SYNC_DIR": str(root / "drive"),
        "PROFILE_MD": str(root / "profile.md"),
        "PLAYBOOK_DIR": str(root / "playbooks"),
    })
    import server

    sid = server.new_session("終了済み会議")
    payload = (b"finished-recording" * 8192) + b"tail"
    queue_path, original_path = server.save_imported_audio_stream(
        sid, io.BytesIO(payload), len(payload), "customer-meeting.m4a")
    assert Path(queue_path).read_bytes() == payload
    assert Path(original_path).read_bytes() == payload
    assert Path(queue_path).name.startswith("inc_import_customer-meeting_")
    assert Path(queue_path).suffix == ".webm", "imported audio must enter the existing ASR/finalize pipeline"

    try:
        server.save_imported_audio_stream(sid, io.BytesIO(payload[:-1]), len(payload), "broken.wav")
    except ValueError as error:
        assert "interrupted" in str(error)
    else:
        raise AssertionError("truncated uploads must never be accepted")
    assert not list((root / "wav" / sid).glob("*broken*"))
    assert not list((root / "meetings" / sid / "audio").glob("*broken*"))

    queued = []
    server.enqueue_chunk = lambda meeting_id, path: queued.append((meeting_id, path))
    handler = object.__new__(server.H)
    route_payload = b"route-recording-payload"
    handler.path = "/api/import-audio?title=Imported%20meeting&filename=recording.m4a"
    handler.headers = {"Content-Length": str(len(route_payload)), "Content-Type": "audio/mp4"}
    handler.rfile = io.BytesIO(route_payload)
    handler._origin_allowed = lambda: True
    response = {}
    handler._send = lambda code, body, ctype="application/json; charset=utf-8", headers=None: response.update(
        code=code, body=body, ctype=ctype)
    handler.do_POST()
    result = json.loads(response["body"])
    assert response["code"] == 200 and result["ok"]
    assert result["sid"] == server.current_id and queued[0][0] == result["sid"]
    assert Path(queued[0][1]).read_bytes() == route_payload
    assert result["state"]["current"]["title"] == "Imported meeting"

html = (ROOT / "index.html").read_text(encoding="utf-8")
source = (ROOT / "server.py").read_text(encoding="utf-8")
assert 'id="reviewimportfile"' in html and 'accept="audio/*' in html
assert 'id="mimportaudio"' in html and "uploadFinishedRecording(file)" in html and "/api/import-audio?title=" in html
assert 'if p == "/api/import-audio"' in source and "save_imported_audio_stream" in source
assert "enqueue_chunk(sid, queue_path)" in source, "imports must trigger transcript and meeting-flow analysis"

print("Finished audio import is durable and enters the normal analysis pipeline")
