#!/usr/bin/env python3
"""Run the slide-writing AI with a bounded primary attempt and one fallback."""

import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile


def run_once(command, prompt, timeout, output_file=None):
    popen_kwargs = dict(
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **popen_kwargs)
    try:
        stdout, stderr = process.communicate(input=prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           capture_output=True, text=True)
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=2)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        return "", "timeout"
    if output_file:
        try:
            output = Path(output_file).read_text(encoding="utf-8").strip()
        except OSError:
            output = ""
    else:
        output = (stdout or "").strip()
    if process.returncode == 0 and output:
        return output, ""
    detail = (stderr or stdout or "AI output was empty").strip()
    return "", detail[-600:]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt")
    parser.add_argument("--provider", choices=("claude", "codex"), required=True)
    parser.add_argument("--fallback-provider", choices=("claude", "codex"))
    parser.add_argument("--model", required=True)
    parser.add_argument("--fallback-model", required=True)
    parser.add_argument("--effort", default="high")
    parser.add_argument("--fallback-effort", default="medium")
    parser.add_argument("--primary-timeout", type=int, default=210)
    parser.add_argument("--fallback-timeout", type=int, default=120)
    args = parser.parse_args()
    prompt = Path(args.prompt).read_text(encoding="utf-8")

    attempts = (
        (args.provider, args.model, args.effort, args.primary_timeout, "primary"),
        (args.fallback_provider or args.provider, args.fallback_model,
         args.fallback_effort, args.fallback_timeout, "fallback"),
    )
    failures = []
    for provider, model, effort, timeout, label in attempts:
        sys.stderr.write(
            f"[SLIDES] {label} provider={provider} model={model} "
            f"timeout={timeout}s prompt={len(prompt)} chars\n"
        )
        sys.stderr.flush()
        output_file = None
        if provider == "claude":
            command = ["claude", "-p", "--model", model]
        else:
            handle = tempfile.NamedTemporaryFile(prefix="live-mtg-slides-", suffix=".txt", delete=False)
            output_file = handle.name
            handle.close()
            command = [
                "codex", "exec", "--ephemeral", "--sandbox", "read-only",
                # 会議フォルダのAGENTS.mdや既存成果物を探索させず、渡した
                # 完結プロンプトへのHTML応答だけを得る。
                "--skip-git-repo-check", "-C", tempfile.gettempdir(),
                "--model", model, "-c", f'model_reasoning_effort="{effort}"',
                "--color", "never", "-o", output_file, "-",
            ]
        try:
            output, error = run_once(command, prompt, timeout, output_file)
        finally:
            if output_file:
                try:
                    os.remove(output_file)
                except OSError:
                    pass
        if output:
            if label == "fallback":
                sys.stderr.write(
                    f"[SLIDES] primary failed; fallback completed with {provider}\n"
                )
            sys.stdout.write(output)
            return 0
        failures.append(f"{label}: {error}")

    sys.stderr.write("[SLIDES] both attempts failed: %s\n" % " / ".join(failures))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
