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
    assert "English meeting" in server._asr_hint("English meeting")
    assert "IMPORTANT LANGUAGE RULE" in server._localized_prompt("test")
    assert json.loads(server.EMPTY_DATA)["summary"] == "Press Start recording in the header to begin."

    session_id = server.new_session("English planning meeting", language="en")
    meta = server.read_meta(session_id)
    assert meta["language"] == "en"
    assert server.desktop_health()["language"] == "en"
    assert server.desktop_health()["version"] != "0.1.0-beta.1"

    assert server.set_language("ja") is True
    config = json.loads((pathlib.Path(runtime) / "config.json").read_text(encoding="utf-8"))
    assert config["language"] == "ja"

print("English server flow OK")
