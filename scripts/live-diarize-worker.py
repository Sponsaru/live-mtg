#!/usr/bin/env python3
"""Persistent local pyannote worker for LiveMTG.

JSON lines are read from stdin and written to stdout. The Hugging Face token is
loaded from the OS credential store inside this process; it is never accepted in
the request, command line, meeting data, or logs.
"""

import getpass
import json
import os
import platform
import subprocess
import sys
from pathlib import Path


SERVICE = "live-mtg.huggingface"


def credential_token():
    override = os.environ.get("HF_TOKEN", "").strip()
    if override:
        return override
    if sys.platform == "darwin":
        result = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-a", getpass.getuser(),
             "-s", SERVICE, "-w"],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    if os.name == "nt":
        secret = Path(os.environ.get("LIVE_MTG_HOME", Path.home() / ".live-mtg")) / "hf-token.dpapi"
        if not secret.is_file():
            return ""
        script = (
            "$b=[IO.File]::ReadAllBytes($args[0]);"
            "$p=[Security.Cryptography.ProtectedData]::Unprotect($b,$null,[Security.Cryptography.DataProtectionScope]::CurrentUser);"
            "[Console]::Out.Write([Text.Encoding]::UTF8.GetString($p))"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script, str(secret)],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    return ""


def load_pipeline():
    token = credential_token()
    if not token:
        raise RuntimeError("Hugging Face credential is not configured")
    import torch
    from pyannote.audio import Pipeline

    pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-community-1", token=token)
    if pipeline is None:
        raise RuntimeError("Could not load pyannote speaker diarization model")
    if torch.backends.mps.is_available():
        pipeline.to(torch.device("mps"))
    return pipeline


def diarize(pipeline, request):
    wav = str(request.get("wav") or "")
    if not wav or not os.path.isfile(wav):
        raise RuntimeError("Audio file is missing")
    kwargs = {}
    minimum = int(request.get("minSpeakers") or 0)
    maximum = int(request.get("maxSpeakers") or 0)
    if minimum > 0:
        kwargs["min_speakers"] = minimum
    if maximum > 0:
        kwargs["max_speakers"] = maximum
    output = pipeline(wav, **kwargs)
    annotation = getattr(output, "speaker_diarization", output)
    turns = []
    for turn, _, speaker in annotation.itertracks(yield_label=True):
        turns.append({"speaker": str(speaker), "start": round(float(turn.start), 3),
                      "end": round(float(turn.end), 3)})
    return {"ok": True, "id": request.get("id"), "turns": turns}


def main():
    pipeline = None
    for line in sys.stdin:
        request = {}
        try:
            request = json.loads(line)
            if request.get("command") == "ping":
                response = {"ok": True, "id": request.get("id"), "platform": platform.system()}
            else:
                if pipeline is None:
                    pipeline = load_pipeline()
                response = diarize(pipeline, request)
        except Exception as error:
            response = {"ok": False, "id": request.get("id") if isinstance(request, dict) else None,
                        "error": str(error)[:500]}
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
