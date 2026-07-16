#!/usr/bin/env python3
"""Persistent local pyannote worker for LiveMTG.

JSON lines are read from stdin and written to stdout. The Hugging Face token is
loaded from the OS credential store inside this process; it is never accepted in
the request, command line, meeting data, or logs.
"""

import getpass
from array import array
import json
import os
import platform
import subprocess
import sys
import wave
import warnings
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
    # We provide an in-memory waveform and intentionally do not use pyannote's
    # optional torchcodec decoder.  Hide its import-time compatibility warning.
    warnings.filterwarnings("ignore", category=UserWarning, module=r"pyannote\.audio\.core\.io")
    import torch
    from pyannote.audio import Pipeline

    pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-community-1", token=token)
    if pipeline is None:
        raise RuntimeError("Could not load pyannote speaker diarization model")
    if torch.backends.mps.is_available():
        pipeline.to(torch.device("mps"))
    return pipeline


def wav_tensor(path):
    """Read the PCM WAV produced by LiveMTG without torchcodec/FFmpeg bindings."""
    import torch
    with wave.open(path, "rb") as reader:
        channels, width, rate = reader.getnchannels(), reader.getsampwidth(), reader.getframerate()
        frames = reader.readframes(reader.getnframes())
    if width == 1:
        values = [(value - 128) / 128.0 for value in frames]
    elif width in (2, 4):
        kind, scale = ("h", 32768.0) if width == 2 else ("i", 2147483648.0)
        samples = array(kind); samples.frombytes(frames)
        if sys.byteorder != "little": samples.byteswap()
        values = [value / scale for value in samples]
    else:
        raise RuntimeError("Unsupported WAV sample width: %d" % width)
    waveform = torch.tensor(values, dtype=torch.float32).reshape(-1, channels).transpose(0, 1).contiguous()
    if channels > 1:
        waveform = waveform.mean(dim=0, keepdim=True)
    return {"waveform": waveform, "sample_rate": rate}


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
    # pyannote 4 delegates file decoding to torchcodec.  Homebrew FFmpeg and the
    # bundled torch/torchcodec versions can diverge, so pass decoded PCM memory.
    output = pipeline(wav_tensor(wav), **kwargs)
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
