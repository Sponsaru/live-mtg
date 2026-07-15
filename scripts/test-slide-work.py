#!/usr/bin/env python3
"""Ensure completed decks use the vendored Slide Work design system."""

from pathlib import Path
import json


ROOT = Path(__file__).resolve().parents[1]
template = (ROOT / "slide-work-template.html").read_text(encoding="utf-8")
examples = (ROOT / "slide-work-pattern-examples.html").read_text(encoding="utf-8")
guide = (ROOT / "slide-work-guide.md").read_text(encoding="utf-8")
generator = (ROOT / "make-slides.sh").read_text(encoding="utf-8")
package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
packager = (ROOT / "scripts" / "build-npm-package.mjs").read_text(encoding="utf-8")

assert 'data-design-system="slide-work"' in template
assert 'html { scroll-snap-type: y mandatory; font-size: clamp(10px, 1vw, 28px); }' in template
assert '{{SLIDES}}' in template and 'id="livemtg-back"' in template
for pattern in ("P01", "P03", "P04", "P05", "P07", "P10", "P17", "P22", "P23", "P31", "P33", "P34", "P35", "P37", "P41", "P43", "P44", "P46", "P47"):
    assert f"{pattern}·" in examples, f"missing vendored pattern {pattern}"
assert "neutral / hybrid" in guide
assert 'TPL="$SCRIPT_DIR/slide-work-template.html"' in generator
assert 'EXAMPLES="$SCRIPT_DIR/slide-work-pattern-examples.html"' in generator
assert "never invents CSS" in generator
for name in ("slide-work-template.html", "slide-work-pattern-examples.html", "slide-work-guide.md"):
    assert name in package["files"], f"{name} missing from npm package"
    assert name in packager, f"{name} missing from staged npm package"
for text in (template, examples, guide):
    assert "毎日興業" not in text and "mainichi" not in text.lower()

print("Slide Work is the canonical completed-deck design")
