#!/usr/bin/env python3
"""Legacy data must remain visible after upgrading the npm CLI."""

import os
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def doctor_home(fake_home: Path, language="en") -> str:
    # Node's os.homedir() reads HOME on Unix and USERPROFILE on Windows.
    env = dict(os.environ, HOME=str(fake_home), USERPROFILE=str(fake_home))
    if language:
        env["LIVE_MTG_LANGUAGE"] = language
    else:
        env.pop("LIVE_MTG_LANGUAGE", None)
    env.pop("LIVE_MTG_HOME", None)
    result = subprocess.run(
        ["node", str(ROOT / "cli" / "live-mtg.mjs"), "doctor"],
        env=env, capture_output=True, text=True, timeout=20,
    )
    return result.stdout + result.stderr


with tempfile.TemporaryDirectory(prefix="live-mtg-home-test-") as tmp:
    base = Path(tmp)
    legacy_meeting = base / "mtg-live" / "meetings" / "legacy-meeting"
    legacy_meeting.mkdir(parents=True)
    (legacy_meeting / "meta.json").write_text("{}", encoding="utf-8")
    output = doctor_home(base)
    assert f"Data: {base / 'mtg-live'}" in output, output
    legacy_default = doctor_home(base, language=None)
    assert "言語: 日本語" in legacy_default, legacy_default

with tempfile.TemporaryDirectory(prefix="live-mtg-home-test-") as tmp:
    base = Path(tmp)
    modern_meeting = base / ".live-mtg" / "meetings" / "modern-meeting"
    legacy_meeting = base / "mtg-live" / "meetings" / "legacy-meeting"
    modern_meeting.mkdir(parents=True)
    legacy_meeting.mkdir(parents=True)
    (modern_meeting / "meta.json").write_text("{}", encoding="utf-8")
    (legacy_meeting / "meta.json").write_text("{}", encoding="utf-8")
    output = doctor_home(base)
    assert f"Data: {base / '.live-mtg'}" in output, output

print("Legacy home compatibility OK")
