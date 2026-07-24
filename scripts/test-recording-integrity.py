#!/usr/bin/env python3
"""Recording transitions must preserve every accepted audio chunk."""

import json
import io
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def route_block(source, route, next_route):
    start = source.index('if p == "%s":' % route)
    end = source.index('if p == "%s":' % next_route, start)
    return source[start:end]


source = (ROOT / "server.py").read_text(encoding="utf-8")
html = (ROOT / "index.html").read_text(encoding="utf-8")

# These transitions may stop accepting new audio, but must not destroy audio
# that the server has already accepted.
for route, next_route in (("/api/stop", "/api/new"),
                          ("/api/new", "/api/switch"),
                          ("/api/switch", "/api/delete")):
    assert "clear_queue(" not in route_block(source, route, next_route), \
        "%s must drain accepted chunks instead of deleting them" % route

# The recorder's final onstop chunk must finish uploading before the server is
# told to stop. Merely invoking stopCapture() before /api/stop is racy because
# MediaRecorder.onstop and fetch are asynchronous.
do_stop_start = html.index("function doStop()")
do_stop = html[do_stop_start:html.index("async function fillMics", do_stop_start)]
assert "await stopCapture()" in do_stop, "doStop must await the recorder's final upload"
assert do_stop.index("await stopCapture()") < do_stop.index("api('/api/stop'"), \
    "the final audio upload must complete before /api/stop"

new_handler = html[html.index("$('mok').onclick"):html.index("// フォルダ選択", html.index("$('mok').onclick"))]
switch_handler = html[html.index("$('sess').onchange"):html.index("// 会議切替のカスタム", html.index("$('sess').onchange"))]
assert "await stopCapture()" in new_handler and new_handler.index("await stopCapture()") < new_handler.index("/api/new"), \
    "new transition must await the final recorder upload"
assert "if(capturing||mediaRec)" in switch_handler and "openMeetingRecordReadOnly(nextId)" in switch_handler, \
    "recording-time meeting selection must open a read-only record instead of stopping capture"
recording_guard = switch_handler[:switch_handler.index("await withCaptureTransition")]
assert "stopCapture" not in recording_guard and "/api/switch" not in recording_guard, \
    "recording-time history viewing must not stop capture or switch the server meeting"
assert "?sid='+encodeURIComponent(sessionId)" in html, \
    "every audio upload must stay bound to its recording-start meeting"
assert "/?readonlySid=" in html and "/api/meeting-record?sid=" in html, \
    "recording-time history must reuse the main UI in sid-scoped read-only mode"
assert "document.body.classList.add('readonly-mode')" in html and "if(readOnlyMode){toast(" in html, \
    "historical meetings must expose the main UI without allowing mutations"
assert "p == \"/api/meeting-record\"" in source and "\"readOnly\": True" in source, \
    "server must expose a read-only historical meeting endpoint"


with tempfile.TemporaryDirectory(prefix="live-mtg-recording-integrity-") as tmp:
    root = Path(tmp)
    os.environ.update({
        "RUN": str(root),
        "MEETINGS_DIR": str(root / "meetings"),
        "DRIVE_SYNC_DIR": str(root / "drive"),
        "PROFILE_MD": str(root / "profile.md"),
        "PLAYBOOK_DIR": str(root / "playbooks"),
    })

    import server

    # Avoid unrelated asynchronous Drive work while exercising the HTTP
    # transition endpoints in isolation.
    server.sync_to_drive = lambda *_args, **_kwargs: None
    def post(path, body=b"", content_type="application/json"):
        if isinstance(body, dict):
            body = json.dumps(body).encode("utf-8")
        # Invoke the real request handler without binding a TCP socket. This
        # keeps the test runnable in network-restricted CI sandboxes while still
        # exercising route parsing, request-body handling and queue mutation.
        handler = object.__new__(server.H)
        handler.path = path
        handler.headers = {"Content-Length": str(len(body)),
                           "Content-Type": content_type}
        handler.rfile = io.BytesIO(body)
        response = {}
        def capture(code, payload, ctype="application/json; charset=utf-8"):
            response.update(code=code, body=payload, ctype=ctype)
            return None
        handler._send = capture
        handler.do_POST()
        payload = response.get("body", "{}")
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        return json.loads(payload)

    def get(path):
        handler = object.__new__(server.H)
        handler.path = path
        handler.headers = {}
        response = {}
        def capture(code, payload, ctype="application/json; charset=utf-8"):
            response.update(code=code, body=payload, ctype=ctype)
            return None
        handler._send = capture
        handler.do_GET()
        payload = response.get("body", "{}")
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        return response.get("code"), json.loads(payload)

    def drain_queue():
        while True:
            try:
                item = server.chunk_q.get_nowait()
            except queue.Empty:
                return
            server.chunk_q.task_done()
            server._chunk_finished(item[0], sync=False)

    def assert_transition_preserves_queue(route, body=None):
        drain_queue()
        owner = server.current_id
        pending = root / (route.strip("/").replace("/", "-") + ".webm")
        pending.write_bytes(b"accepted-before-transition")
        expected = (owner, str(pending))
        server.chunk_q.put(expected)
        post(route, body or {})
        actual = server.chunk_q.get_nowait()
        server.chunk_q.task_done()
        assert actual == expected, "%s removed or replaced an accepted chunk" % route
        assert pending.exists(), "%s deleted accepted audio from disk" % route

    try:
        first = server.new_session("Recording integrity A")
        second = server.new_session("Recording integrity B")
        assert first != second and second.startswith(first + "-"), \
            "same-second sessions must receive distinct, sortable IDs"

        # 並行作成もフォルダ予約で原子化し、一件も上書きしない。
        parallel_ids = []
        parallel_lock = threading.Lock()
        def create_parallel(index):
            sid = server.new_session("Parallel %d" % index)
            with parallel_lock:
                parallel_ids.append(sid)
        workers = [threading.Thread(target=create_parallel, args=(index,)) for index in range(12)]
        for worker in workers: worker.start()
        for worker in workers: worker.join()
        assert len(parallel_ids) == 12 and len(set(parallel_ids)) == 12, \
            "parallel session creation must never reuse an ID"
        assert all(Path(server.sdir(sid), "meta.json").is_file() for sid in parallel_ids), \
            "every reserved session ID must own an independent metadata file"
        server.current_id = first

        # 録音中に過去の議事録を開いても、録音対象と録音状態は不変。
        server.recording = True
        server.capture_heartbeat = time.time()
        code, historical = get("/api/meeting-record?sid=" + second)
        assert code == 200 and historical.get("ok") is True and historical.get("readOnly") is True
        assert historical.get("meta", {}).get("id") == second
        assert server.current_id == first, "read-only history viewing switched the recording meeting"
        assert server.recording is True, "read-only history viewing stopped recording"

        # Chunk IDs carry capture epoch + a monotonic sequence. UUID suffixes
        # provide idempotency only and must never determine meeting chronology.
        order_dir = root / "order"
        order_dir.mkdir()
        ordered_names = [
            "inc_1700000000000-00000002-z.webm",
            "inc_1700000000000-00000001-a.webm",
            "inc_old-random-uuid.webm",
        ]
        order_paths = [order_dir / name for name in ordered_names]
        for path in order_paths:
            path.write_bytes(path.name.encode("utf-8"))
        os.utime(order_paths[2], (1700000000.5, 1700000000.5))
        assert [path.name for path in sorted(order_paths, key=server._audio_sort_key)] == [
            ordered_names[1], ordered_names[0], ordered_names[2]
        ], "audio chronology must use capture sequence and legacy UUID mtime fallback"

        # Restart recovery must enqueue pending work in the same chronological
        # order used by full-audio finalization and rolling diarization.
        drain_queue()
        recovery_dir = Path(server.WAVROOT) / first
        recovery_dir.mkdir(parents=True, exist_ok=True)
        recovery_paths = [recovery_dir / name for name in ordered_names]
        for path in recovery_paths:
            path.write_bytes(b"pending")
        os.utime(recovery_paths[2], (1700000000.5, 1700000000.5))
        server.recover_pending_chunks()
        recovered_names = []
        while not server.chunk_q.empty():
            recovered_sid, recovered_path = server.chunk_q.get_nowait()
            server.chunk_q.task_done()
            server._chunk_finished(recovered_sid, sync=False)
            recovered_names.append(Path(recovered_path).name)
        assert recovered_names == [ordered_names[1], ordered_names[0], ordered_names[2]], \
            "restart recovery reordered recorded speech"
        for path in recovery_paths:
            path.unlink(missing_ok=True)

        # API acceptance itself is the durability boundary: before any worker
        # runs, the original payload must already exist under the meeting's
        # audio directory.
        payload = b"original-webm-payload"
        # Bind the upload to its recording-start sid even if the visible meeting
        # changes before a delayed fetch reaches the server.
        server.current_id = second
        server.recording = False
        chunk_url = "/api/chunk?kind=meeting&sid=" + first + "&chunk=chunk-a"
        result = post(chunk_url, payload, "application/octet-stream")
        assert result.get("ok") is True
        retried = post(chunk_url, payload, "application/octet-stream")
        assert retried.get("ok") is True and retried.get("duplicate") is True, \
            "retrying the same chunk id must be idempotent"
        durable = list((Path(server.sdir(first)) / "audio").glob("*.webm"))
        assert len(durable) == 1, "/api/chunk retry must keep exactly one durable original"
        assert any(path.read_bytes() == payload for path in durable), \
            "durable meeting audio must exactly match the accepted request body"
        assert not list((Path(server.sdir(second)) / "audio").glob("*.webm")), \
            "a delayed chunk must not leak into the newly visible meeting"
        assert server.recording is False, "a delayed final chunk must not restart recording state"

        # During an actual browser capture, active=1 is proof that a restarted
        # server should restore recording immediately from the arriving chunk.
        server.current_id = first
        active_result = post("/api/chunk?kind=meeting&sid=" + first +
                             "&chunk=chunk-active&active=1", b"active-webm", "application/octet-stream")
        assert active_result.get("ok") is True and server.recording is True
        assert server.capture_heartbeat > 0, "active chunk must restore the server heartbeat after restart"

        queued_sid, queued_path = server.chunk_q.get_nowait()
        server.chunk_q.task_done()
        assert queued_sid == first
        active_sid, active_path = server.chunk_q.get_nowait()
        server.chunk_q.task_done()
        assert active_sid == first and Path(active_path).name.endswith("chunk-active.webm")
        assert server.chunk_q.empty(), "the same chunk id must be enqueued only once"
        original = Path(server.sdir(first)) / "audio" / Path(queued_path).name
        original_hash = original.read_bytes()

        class Result:
            returncode = 0

        def fake_run(cmd, **_kwargs):
            Path(cmd[-1]).write_bytes(b"fake-wav")
            return Result()

        server._run = fake_run
        server._mean_db = lambda _wav: -10
        server._overlap_wav = lambda _sid, wav, _kind="meeting": wav
        server._whisper = lambda _wav, _sid=None: "保存完全性の確認"
        server.queue_spoken_lookup = lambda *_args: None
        server.request_analysis = lambda *_args: None
        server.request_active_view_update = lambda *_args, **_kwargs: None
        server.request_detail = lambda *_args: None
        server.request_live_diarization = lambda *_args: None
        server.process_chunk(first, queued_path)
        server._chunk_finished(first, sync=False)
        assert original.read_bytes() == original_hash, \
            "ASR processing must never overwrite the durable original"

        # A stalled ASR process must be bounded. Live chunks use the short
        # timeout while imported recordings retain enough time for long audio.
        captured_timeouts = []
        def capture_asr_run(_cmd, **kwargs):
            captured_timeouts.append(kwargs.get("timeout"))
            return Result()
        server._run = capture_asr_run
        server._whisper_mlx_once(str(root / "inc_1700000000000-00000003-x.wav"), first)
        server._whisper_cpp(str(root / "prep_1700000000000-00000004-y.wav"), first)
        assert captured_timeouts == [server.ASR_LIVE_TIMEOUT, server.ASR_LIVE_TIMEOUT], \
            "every live ASR backend must enforce the short timeout"
        assert server._asr_timeout(str(root / "inc_import_recording.wav")) == server.ASR_IMPORT_TIMEOUT, \
            "completed recording imports must use the longer ASR timeout"

        # Timeout/error cleanup must preserve the queue copy, then requeue it
        # behind later audio instead of permanently blocking the worker.
        failed_webm = root / "inc_1700000000000-00000005-timeout.webm"
        failed_webm.write_bytes(b"retry-me")
        def decode_then_timeout(cmd, **_kwargs):
            if cmd and cmd[0] == "ffmpeg":
                Path(cmd[-1]).write_bytes(b"fake-wav")
            return Result()
        server._run = decode_then_timeout
        server._mean_db = lambda _wav: -10
        server._overlap_wav = lambda _sid, wav, _kind="meeting": wav
        server._whisper = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired("mlx_whisper", server.ASR_LIVE_TIMEOUT))
        try:
            server.process_chunk(first, str(failed_webm))
            raise AssertionError("ASR timeout must propagate to the queue worker")
        except subprocess.TimeoutExpired:
            pass
        assert failed_webm.exists(), "ASR timeout must preserve the retryable queue audio"
        drain_queue()
        assert server._retry_failed_chunk(first, str(failed_webm),
                                          subprocess.TimeoutExpired("mlx_whisper", 1)) is True
        retry_sid, retry_path = server.chunk_q.get_nowait()
        server.chunk_q.task_done(); server._chunk_finished(retry_sid, sync=False)
        assert (retry_sid, retry_path) == (first, str(failed_webm)), \
            "failed audio must return at the end of the ASR queue"
        server.chunk_retry_counts.pop(str(failed_webm), None)
        failed_webm.unlink(missing_ok=True)

        server.current_id = first
        server.recording = True
        assert_transition_preserves_queue("/api/stop")

        server.current_id = first
        assert_transition_preserves_queue("/api/new", {"title": "Recording integrity C"})

        server.current_id = first
        assert_transition_preserves_queue("/api/switch", {"id": second})

        # Deleting B must not discard A's pending ASR work.
        drain_queue()
        server.current_id = second
        pending_a = root / "pending-a.webm"
        pending_a.write_bytes(b"meeting-a")
        expected_a = (first, str(pending_a))
        server.enqueue_chunk(*expected_a)
        deleted = post("/api/delete", {"id": second})
        assert deleted.get("ok") is True
        actual_a = server.chunk_q.get_nowait(); server.chunk_q.task_done()
        server._chunk_finished(first, sync=False)
        assert actual_a == expected_a and pending_a.exists(), \
            "deleting one meeting must preserve another meeting's pending chunk"
    finally:
        drain_queue()


print("Recording stop/new/switch preserve accepted chunks and durable original audio")
