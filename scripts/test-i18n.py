import importlib.util
import json
import os
import pathlib
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]


with tempfile.TemporaryDirectory(prefix="live-mtg-i18n-") as runtime:
    os.environ.update({
        "RUN": runtime,
        "MEETINGS_DIR": str(pathlib.Path(runtime) / "meetings"),
        "DRIVE_SYNC_DIR": str(pathlib.Path(runtime) / "drive"),
        "LIVE_MTG_LANGUAGE": "en",
    })
    spec = importlib.util.spec_from_file_location("live_mtg_server_i18n", ROOT / "server.py")
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)

    assert server.LANGUAGE == "en"
    assert server._asr_language() == "en"
    assert "spoken audio faithfully" in server._asr_hint("English meeting")
    assert "English planning meeting" not in server._asr_hint("English meeting")
    assert "IMPORTANT LANGUAGE RULE" in server._localized_prompt("test")
    assert json.loads(server.EMPTY_DATA)["summary"] == "Press Start recording in the header to begin."

    session_id = server.new_session("English planning meeting", language="en")
    meta = server.read_meta(session_id)
    assert meta["language"] == "en"
    assert server.desktop_health()["language"] == "en"
    assert server.desktop_health()["version"] != "0.1.0-beta.1"

    # 10-second browser monitoring uses a dedicated cached auth check, so
    # multiple tabs do not spawn one CLI process each.
    original_which, original_run = server.shutil.which, server.subprocess.run
    auth_calls = []
    class AuthResult:
        returncode = 1
        stdout = "signed out"
        stderr = ""
    server.shutil.which = lambda name: "/tmp/" + name
    server.subprocess.run = lambda *args, **kwargs: (auth_calls.append(args[0]) or AuthResult())
    server._ai_auth_cache.update({"at": 0.0, "provider": "", "value": None})
    try:
        first = server.ai_auth_status(); second = server.ai_auth_status()
    finally:
        server.shutil.which, server.subprocess.run = original_which, original_run
    assert first["aiInstalled"] is True and first["aiLoggedIn"] is False
    assert second == first and len(auth_calls) == 1

    assert server.set_language("ja") is True
    config = json.loads((pathlib.Path(runtime) / "config.json").read_text(encoding="utf-8"))
    assert config["language"] == "ja"

print("English server flow OK")
