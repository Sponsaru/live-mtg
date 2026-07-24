#!/usr/bin/env python3
"""A4会議ペーパーが内容と2つの全体図を落とさず組み上がることを検証する。"""
import json
import os
import subprocess
import tempfile
import base64
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "make-minutes-deck.py"


def write_json(folder, name, value):
    (folder / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def generate(folder, variant="compact", filename="minutes-deck.html", maps=False):
    env = dict(os.environ, SDIR=str(folder), TITLE="回帰会議", LIVE_MTG_LANGUAGE="ja",
               MINUTES_VARIANT=variant, MINUTES_OUT=str(folder / filename))
    if maps:
        env.update(MINUTES_MAP_RADIAL=str(folder / "source-radial.png"),
                   MINUTES_MAP_RELATION=str(folder / "source-relation.png"))
    result = subprocess.run(["python3", str(GENERATOR)], env=env, text=True,
                            capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr or result.stdout
    return (folder / filename).read_text(encoding="utf-8")


with tempfile.TemporaryDirectory(prefix="live-mtg-minutes-") as raw:
    folder = Path(raw)
    final = {
        "summary": "会議全体の要旨",
        "speakers": ["山田", "佐藤"],
        "agenda": ["清書側の細かな話題"],
        "decisions": ["決定A", "清書だけの追加決定"],
        "todos": [{"who": "佐藤", "what": "資料を送る", "due": "金曜"}],
        "points": ["回答A", "清書だけの追加論点"],
        "open": ["未解決A", "清書だけの追加確認"],
        "diagram": "flowchart LR\n  A[現状を把握] -->|課題を特定| B[対応案を比較]\n  B -->|合意| C[試作する]",
    }
    flow = {
        "target": {"text": "結論を決める"},
        "agendas": [
            {"id": "b", "order": 2, "title": "第二議題", "status": "discussing",
             "resolutionStatus": "pending", "result": {"summary": {"text": "第二の要点"},
             "answers": [{"text": "回答B"}], "decisions": [], "actions": [], "unresolved": []}},
            {"id": "a", "order": 1, "title": "第一議題", "status": "discussed",
             "resolutionStatus": "agreed", "result": {"summary": {"text": "第一の要点"},
             "answers": [{"text": "回答A"}], "decisions": [{"text": "決定A"}],
             "actions": [{"text": "山田：試作する"}], "unresolved": [{"text": "未解決A"}]}},
        ],
    }
    write_json(folder, "final.json", final)
    write_json(folder, "data.json", final)
    write_json(folder, "meta.json", {"created": "2026-07-23"})
    write_json(folder, "meeting-flow.json", flow)
    (folder / "learnings.md").write_text(
        "# 学びと次の一手 — 回帰会議\n\n"
        "- **客観的な学びA**：相手が自分の言葉で成功像を語った。\n"
        "- **見落としB**：成功基準の数字を握れていない。\n"
        "- **客観的な学びC**：導入担当が明確になった。\n"
        "- **客観的な学びD**：判断材料が揃った。\n"
        "- **次の一手**：①成功KPIを合意 ②資料を共有\n", encoding="utf-8")
    pixel = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
    (folder / "source-radial.png").write_bytes(pixel)
    (folder / "source-relation.png").write_bytes(pixel)

    compact = generate(folder, maps=True)
    assert compact.count('class="sheet') == 4, "rich minutes with maps must use four pages"
    for expected in ("会議成果", "学びと次の一手", "議題ごとの記録", "会議の全体図", "詳細議事録に収録"):
        assert expected in compact, "compact minutes lost: %s" % expected
    assert 'src="minutes-map-radial.png"' in compact and 'src="minutes-map-relation.png"' in compact
    assert (folder / "minutes-map-radial.png").is_file() and (folder / "minutes-map-relation.png").is_file()
    assert "…（詳細版に全文）" not in compact, "compact report must not cut sentences midway"

    doc = generate(folder, "full", "minutes-detail.html")
    assert doc.index("第一議題") < doc.index("第二議題"), "agenda order must follow order field"
    for expected in ("結論を決める", "第一の要点", "第二の要点", "回答A", "回答B",
                     "決定A", "試作する", "未解決A", "資料を送る",
                     "清書だけの追加決定", "清書だけの追加論点", "清書だけの追加確認",
                     "客観的な学びA", "見落としB", "客観的な学びC", "客観的な学びD",
                     "成功KPIを合意", "資料を共有"):
        assert expected in doc, "missing minutes item: %s" % expected
    assert 'data-design-system="slide-worker-doc-canonical"' in doc
    assert '@page { size: 210mm 297mm; margin: 0; }' in doc
    for block in ('class="blk blk-hd"', 'class="sum"', 'class="kpis n4"',
                  'class="paper-list"', '<table>'):
        assert block in doc, "missing canonical A4 document block: %s" % block
    assert "result-card" not in doc and "overview-grid" not in doc and "mermaid" not in doc
    assert 'class="radial-center"' in doc and 'class="relation-row"' in doc, \
        "radial and relationship maps must be embedded in the minutes"
    for expected in ("現状を把握", "課題を特定", "対応案を比較", "合意"):
        assert expected in doc, "relationship map lost: %s" % expected
    assert '<!-- slide-worker-browser-editor:begin -->' in doc
    assert doc.count('class="sheet') >= 7, "content should paginate instead of being dropped"
    assert (folder / "brand-logo.png").is_file(), "brand logo was not copied"
    assert not (folder / "slide-bg.jpg").is_file(), "A4 paper must not use a background image"
    assert 'data-theme="live-mtg"' in doc and "--accent: #0071e3" in doc
    assert "合意済み" in doc and "議論中" in doc and "未合意" in doc
    assert doc.index("<h1>学びと次の一手") < doc.rindex("<h1>第一議題"), \
        "learnings must appear before agenda details"

    # meeting-flow.jsonが無い旧会議も、清書の全セクションを従来互換で出力できる。
    (folder / "meeting-flow.json").unlink()
    legacy = generate(folder, "full", "minutes-detail.html")
    for expected in ("清書側の細かな話題", "清書だけの追加決定", "資料を送る",
                     "清書だけの追加論点", "清書だけの追加確認"):
        assert expected in legacy, "legacy fallback lost: %s" % expected

    # 情報が少ない会議は、空の紙面を足さず1ページで完結する。
    sparse = folder / "sparse"
    sparse.mkdir()
    write_json(sparse, "final.json", {"summary": "結論のみを確認した。", "decisions": ["方針を確認した。"]})
    write_json(sparse, "meta.json", {"created": "2026-07-23"})
    sparse_doc = generate(sparse)
    assert sparse_doc.count('class="sheet') == 1, "small meetings must remain a one-page report"

print("minutes paper tests: OK")
