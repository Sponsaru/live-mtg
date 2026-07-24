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


def load_embedding_inference():
    """Load the embedding model vendored inside the community diarization model."""
    token = credential_token()
    import torch
    from pyannote.audio import Inference, Model
    model = Model.from_pretrained(
        "pyannote/speaker-diarization-community-1", subfolder="embedding", token=token
    )
    device = torch.device("mps") if torch.backends.mps.is_available() else torch.device("cpu")
    model.to(device)
    return Inference(model, window="whole", device=device)


def voice_embedding(inference, audio):
    import numpy as np
    value = np.asarray(inference(audio), dtype=float).reshape(-1)
    norm = float(np.linalg.norm(value))
    if not norm:
        raise RuntimeError("Voice embedding was empty")
    return (value / norm).tolist()


def match_voice_profiles(inference, audio, turns, profiles):
    import numpy as np
    import torch
    waveform, rate = audio["waveform"], int(audio["sample_rate"])
    matches = {}
    for speaker in sorted({turn["speaker"] for turn in turns}):
        pieces = []
        for turn in turns:
            if turn["speaker"] != speaker:
                continue
            start, end = max(0, int(turn["start"] * rate)), min(waveform.shape[1], int(turn["end"] * rate))
            if end - start >= rate // 2:
                pieces.append(waveform[:, start:end])
        if not pieces:
            continue
        joined = torch.cat(pieces, dim=1)[:, : rate * 30]
        if joined.shape[1] < rate * 2:
            continue
        probe = np.asarray(voice_embedding(inference, {"waveform": joined, "sample_rate": rate}))
        scored = []
        for profile in profiles:
            enrolled = np.asarray(profile.get("embedding") or [], dtype=float)
            if enrolled.shape == probe.shape:
                scored.append((float(np.dot(probe, enrolled)), profile))
        if not scored:
            continue
        score, profile = max(scored, key=lambda item: item[0])
        # 誤認より「不明」を優先。十分似ている場合だけ本人名へ確定する。
        if score >= 0.68:
            matches[speaker] = {"name": str(profile.get("name") or "本人"),
                                "confidence": round(score, 3)}
    return matches


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


def diarize(pipeline, request, inference=None):
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
    audio = wav_tensor(wav)
    output = pipeline(audio, **kwargs)
    annotation = getattr(output, "speaker_diarization", output)
    turns = []
    for turn, _, speaker in annotation.itertracks(yield_label=True):
        turns.append({"speaker": str(speaker), "start": round(float(turn.start), 3),
                      "end": round(float(turn.end), 3)})
    profiles = request.get("voiceProfiles") if isinstance(request.get("voiceProfiles"), list) else []
    if profiles and inference is not None:
        matches = match_voice_profiles(inference, audio, turns, profiles)
        for turn in turns:
            match = matches.get(turn["speaker"])
            if match:
                turn["profileName"] = match["name"]
                turn["profileConfidence"] = match["confidence"]
    return {"ok": True, "id": request.get("id"), "turns": turns}


def main():
    pipeline = None
    inference = None
    for line in sys.stdin:
        request = {}
        try:
            request = json.loads(line)
            if request.get("command") == "ping":
                response = {"ok": True, "id": request.get("id"), "platform": platform.system()}
            elif request.get("command") == "enroll":
                if inference is None:
                    inference = load_embedding_inference()
                response = {"ok": True, "id": request.get("id"),
                            "embedding": voice_embedding(inference, wav_tensor(str(request.get("wav") or "")))}
            else:
                if pipeline is None:
                    pipeline = load_pipeline()
                profiles = request.get("voiceProfiles") if isinstance(request.get("voiceProfiles"), list) else []
                if profiles and inference is None:
                    inference = load_embedding_inference()
                response = diarize(pipeline, request, inference)
        except Exception as error:
            response = {"ok": False, "id": request.get("id") if isinstance(request, dict) else None,
                        "error": str(error)[:500]}
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
