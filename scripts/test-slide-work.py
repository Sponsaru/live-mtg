#!/usr/bin/env python3
"""Ensure completed decks use the vendored Slide Work design system."""

from pathlib import Path
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
template = (ROOT / "slide-work-template.html").read_text(encoding="utf-8")
paper_template = (ROOT / "minutes-paper-template.html").read_text(encoding="utf-8")
examples = (ROOT / "slide-work-pattern-examples.html").read_text(encoding="utf-8")
guide = (ROOT / "slide-work-guide.md").read_text(encoding="utf-8")
generator = (ROOT / "make-slides.sh").read_text(encoding="utf-8")
runner = (ROOT / "scripts" / "run-slide-ai.py").read_text(encoding="utf-8")
package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
packager = (ROOT / "scripts" / "build-npm-package.mjs").read_text(encoding="utf-8")

assert 'data-design-system="slide-worker-canonical"' in template
assert "scroll-snap-type: y mandatory" in template
assert "font-size: clamp(7px, min(1vw, calc(1vh * 16 / 9)), 28px)" in template
assert '{{SLIDES}}' in template and 'id="livemtg-back"' in template
assert '<!-- slide-worker-browser-editor:begin -->' in template
assert 'data-theme="live-mtg"' in template and 'id="livemtg-brand-theme"' in template
assert all(token in template for token in ('url("slide-bg.jpg")', 'url("brand-logo.png")',
                                            "--accent: #0071e3"))
assert 'data-design-system="slide-worker-doc-canonical"' in paper_template
assert '@page { size: 210mm 297mm; margin: 0; }' in paper_template
assert 'id="livemtg-document-theme"' in paper_template
assert 'url("brand-logo.png")' in paper_template and '--bg-image: none' in paper_template
assert '{{SHEETS}}' in paper_template and '<!-- slide-worker-browser-editor:begin -->' in paper_template
for pattern in ("P01", "P03", "P04", "P05", "P07", "P10", "P17", "P22", "P23", "P31", "P33", "P34", "P35", "P37", "P41", "P43", "P44", "P46", "P47"):
    assert f"{pattern}·" in examples, f"missing vendored pattern {pattern}"
assert "informative / standard" in guide
assert 'TPL="$SCRIPT_DIR/slide-work-template.html"' in generator
assert 'EXAMPLES="$SCRIPT_DIR/slide-work-pattern-examples.html"' in generator
assert "never invents CSS" in generator
assert all(token in generator for token in ("meeting-flow.json", "learnings.md",
                                             "minutes-map-radial.png", "minutes-map-relation.png",
                                             "6〜10枚", "学びと次の一手を2枚目"))
assert "transcript_excerpt" in generator and "has_structured_source" in generator
assert "run-slide-ai.py" in generator and "CLAUDE_SLIDE_FALLBACK_MODEL" in generator
assert "--primary-timeout 900 --fallback-timeout 300" in generator
assert "AIが図版指示を1枚だけ落としても" in generator and "map_defs" in generator
assert all(token in runner for token in ("primary-timeout", "fallback-timeout", "subprocess.TimeoutExpired"))

# 本命モデルが固まっても、短い上限のあとにフォールバックが出力を返す。
runner_path = ROOT / "scripts" / "run-slide-ai.py"
spec = importlib.util.spec_from_file_location("run_slide_ai", runner_path)
runner_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner_module)
with tempfile.TemporaryDirectory(prefix="live-mtg-slide-runner-") as raw:
    prompt_path = Path(raw) / "prompt.txt"
    prompt_path.write_text("compact meeting source", encoding="utf-8")
    calls = []
    class FakeProcess:
        returncode = 0
        pid = 12345
        def __init__(self, command, **kwargs):
            self.command = command
            self.timed_out = False
        def communicate(self, input=None, timeout=None):
            if self.timed_out:
                return "", ""
            calls.append(self.command)
            if len(calls) == 1:
                self.timed_out = True
                raise subprocess.TimeoutExpired(self.command, timeout)
            return "<div class=\"slide\">fallback</div>", ""
        def wait(self, timeout=None): self.returncode = -15
        def kill(self): self.returncode = -9
    def fake_killpg(pid, sig): return None
    def fake_popen(command, **kwargs):
        return FakeProcess(command, **kwargs)
    original_popen = runner_module.subprocess.Popen
    original_killpg = runner_module.os.killpg
    original_argv = runner_module.sys.argv
    runner_module.subprocess.Popen = fake_popen
    runner_module.os.killpg = fake_killpg
    runner_module.sys.argv = ["run-slide-ai.py", str(prompt_path), "--provider", "claude",
                              "--model", "opus", "--fallback-model", "sonnet",
                              "--primary-timeout", "1", "--fallback-timeout", "2"]
    stdout, stderr = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            assert runner_module.main() == 0
    finally:
        runner_module.subprocess.Popen = original_popen
        runner_module.os.killpg = original_killpg
        runner_module.sys.argv = original_argv
    assert len(calls) == 2 and calls[0][-1] == "opus" and calls[1][-1] == "sonnet"
    assert "fallback" in stdout.getvalue() and "fallback completed" in stderr.getvalue()
for name in ("slide-work-template.html", "minutes-paper-template.html",
             "slide-work-pattern-examples.html", "slide-work-guide.md",
             "slide-bg.jpg", "brand-logo.png", "scripts/run-slide-ai.py"):
    assert name in package["files"], f"{name} missing from npm package"
    assert name in packager, f"{name} missing from staged npm package"
assert "毎日興業" not in template and "mainichi" not in template.lower()

# AI由来の記号を含む会話関係も、紙面用に安全な引用付きMermaidへ再構成する。
with tempfile.TemporaryDirectory(prefix="live-mtg-map-slide-") as raw:
    folder = Path(raw)
    payload = {
        "diagram": "flowchart LR\n  subgraph 運用状況\n"
                   "    A[アプリ運用開始] -->|移行98〜99%完了| B[使いやすさ向上]\n"
                   "    C[料金帯] -.->|丹野1,3,6提案／先方3,5,7想定| B\n  end"
    }
    (folder / "data.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    env = dict(os.environ, SDIR=str(folder), TITLE="回帰会議", VIEW="relation", LIVE_MTG_LANGUAGE="ja")
    result = subprocess.run(["python3", str(ROOT / "make-map-slide.py")], env=env,
                            text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr or result.stdout
    relation = (folder / "map-slide-relation.html").read_text(encoding="utf-8")
    assert 'class="relation-grid"' in relation and relation.count('class="relation-item') == 2
    for expected in ("アプリ運用開始", "移行98〜99%完了", "使いやすさ向上",
                     "料金帯", "丹野1,3,6提案／先方3,5,7想定"):
        assert expected in relation
    assert '<div class="mermaid">' not in relation and "subgraph 運用状況" not in relation

print("Slide Work is the canonical completed-deck design")
