# -*- coding: utf-8 -*-
"""ライブAIプロンプトのゴールデン評価（ローカル実行専用・claude CLIが必要）。

目的: プロンプトを変更した時に「出力の質が黙って壊れる」のを防ぐ回帰テスト。
      2026-07-16の実障害（confirmが未配線のまま一度も発火していなかった）の再発防止として新設。
CIでは動かない（claudeログインが無い）ため npm test には含めない。実行方法:
    python3 scripts/eval-prompts.py           # 全ケース
    python3 scripts/eval-prompts.py fast      # 即時レーンのみ

観点はスキーマ妥当性＋意味的な発火条件（曖昧な人名→confirmが出る／明瞭→出ない等）。
LLM出力は揺れるため、各ケース2回実行して1回でも通ればPASS（フレーキー緩和）。
"""
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("srv", os.path.join(ROOT, "server.py"))
srv = importlib.util.module_from_spec(spec)
sys.modules["srv"] = srv
spec.loader.exec_module(srv)

if not shutil.which("claude"):
    sys.exit("claude CLIが見つかりません（このテストはローカル専用です）")

INDEX_EMPTY = json.dumps({"summary": "", "arc": "", "agenda": [], "open": [],
                          "decisions": [], "guide_questions": [], "relations": []}, ensure_ascii=False)


def ask(prompt, timeout=90):
    r = subprocess.run(["claude", "-p", "--model", "haiku"], input=prompt,
                       capture_output=True, text=True, timeout=timeout)
    out = re.sub(r"^```json\s*|^```\s*|```\s*$", "", r.stdout or "", flags=re.M).strip()
    m = re.search(r"\{.*\}", out, re.S)
    return json.loads(m.group(0)) if m else None


def fast_prompt(delta, index=INDEX_EMPTY, goal="", mtype=""):
    parts = []
    if mtype:
        parts.append("【会議の用途】" + mtype)
    if goal:
        parts.append("【会議の目標】\n" + goal)
    return srv.LIVE_PATCH_PROMPT.format(title="評価用会議", bg="\n".join(parts), index=index, delta=delta)


CASES = []


def case(name, group):
    def deco(fn):
        CASES.append((name, group, fn))
        return fn
    return deco


@case("fast: JSONスキーマと必須キー", "fast")
def _(run):
    d = run(fast_prompt("では次回は金曜15時で。議事録は私がまとめます。"))
    assert isinstance(d, dict) and "summary" in d, "summaryが無い"


@case("fast: 曖昧な人名 → confirmが発火する", "fast")
def _(run):
    d = run(fast_prompt("なかたさん？たなかさん？まあその方が先方の担当で。見積もりは来週フィックスで。"))
    c = d.get("confirm") or {}
    assert str(c.get("point") or "").strip(), "曖昧な人名なのにconfirmが空"


@case("fast: 明瞭な発話 → confirmは出ない", "fast")
def _(run):
    d = run(fast_prompt("では次回の定例は金曜の15時からにしましょう。了解です。議事録は私がまとめて共有します。"))
    c = d.get("confirm") or {}
    assert not str(c.get("point") or "").strip(), "明瞭な発話でconfirmが出た: %r" % c


@case("fast: 合意 → decisionが入る", "fast")
def _(run):
    d = run(fast_prompt("では次回は金曜15時で確定にしましょう。はい、確定で。"))
    assert str(d.get("decision") or "").strip(), "明確な合意なのにdecisionが空"


@case("fast: 目標なしでも質問が出る（雑談以外）", "fast")
def _(run):
    d = run(fast_prompt("新サービスの価格をどうするかが今日の本題です。原価はだいたい月3万円くらい。"))
    qs = (d or {}).get("questions") or []
    assert any(str(q.get("q") or "").strip() for q in qs), "前進余地のある会話なのにquestionsが空"


@case("list: ToDo拾い（リストレーン）", "fast")
def _(run):
    prompt = srv.ACTIVE_LIST_PROMPT.format(title="評価用会議",
        index='{"agenda":[],"points":[],"decisions":[],"todos":[],"open":[]}',
        delta="じゃあ見積書のドラフトは佐藤さんが金曜までに作ってください。承知しました。")
    d = run(prompt)
    todos = (d or {}).get("todos_add") or []
    assert any("佐藤" in str(t.get("who") or "") for t in todos if isinstance(t, dict)), "明示のToDoを拾えていない: %r" % todos


RICH_DELTA = """毎日興業の案件はまず30万円で入って、実績を作って月単価100万に上げる。それを10社やれば1000万規模になる。
一方でラクハブはシステムとして売るから、1対1のコンサルと1対Nのアプリの中間の位置づけだよね。
そうそう。ただ個社コンサルを10社並行でやるのは労力が大きいのが懸念で、そこはAIエージェントでどこまで自動化できるか次第。
アプリ型に寄せられれば低労力・高単価になる。じゃあまず毎日興業で実績化を進めて、その事例を横展開しよう。"""


@case("diagram: 詳細レーン → ストーリーライン図", "diagram")
def _(run):
    obj = {"mindmap": []}
    prompt = srv.DETAIL_PATCH_PROMPT.format(title="評価用会議", delta=RICH_DELTA, bg="",
                                            index=srv._detail_index(obj))
    d = run(prompt, timeout=120)
    dg = str((d or {}).get("diagram") or "")
    assert "flowchart" in dg, "flowchartでない: %r" % dg[:80]
    edges = dg.count("-->") + dg.count("-.->")
    labeled = dg.count("-->|") + dg.count("-.->|")
    assert edges >= 4, "エッジが少なすぎる（流れになっていない）: %d本" % edges
    assert labeled == edges, "ラベル無しエッジがある（論旨が再生できない）: %d/%d" % (labeled, edges)
    assert dg.count("[") >= 6, "ノードが少なすぎる: %s" % dg.count("[")


@case("diagram: ライブ関係ペア → 論理ラベル", "diagram")
def _(run):
    d = run(fast_prompt("30万円で10社取れば300万。その実績で月単価100万に上げて、合計1000万規模を狙う。"))
    r = (d or {}).get("relation") or {}
    t = str(r.get("type") or "")
    assert str(r.get("from") or "").strip() and t.strip(), "relationが空: %r" % r
    assert len(t) >= 4 and t != "関連", "typeが論理を語っていない: %r" % t


@case("mode: ブレスト会話 → kind=話す の提案", "mode")
def _(run):
    d = run(fast_prompt("じゃあ新サービスの名前と売り方、自由に出していこうか。とりあえずサブスクはありだよね。あとは代理店経由も面白いかも。他には何があるかな。",
                        mtype="ブレスト"))
    qs = (d or {}).get("questions") or []
    assert any(q.get("kind") == "話す" for q in qs), "ブレストなのに話す提案が無い: %r" % qs


@case("mode: 商談ヒアリング → kind=聞く の質問", "mode")
def _(run):
    d = run(fast_prompt("弊社の在庫管理はエクセルでやっていて、月末の棚卸しに3日かかるんですよ。担当は2名です。",
                        mtype="商談", goal="課題を聞き出して提案につなげる"))
    qs = (d or {}).get("questions") or []
    assert qs and all(q.get("kind") != "話す" for q in qs), "商談なのに話す提案が混ざった: %r" % qs


@case("fast: 行き詰まり発話 → stuckが立つ", "counsel")
def _(run):
    d = run(fast_prompt("うーん、どっちの案も決め手がないんだよなあ。さっきから同じ話をぐるぐるしてる気がする。難しいね。どうしようか。"))
    assert str((d or {}).get("stuck") or "").strip(), "明確な停滞なのにstuckが無い"


@case("fast: 普通の進行 → stuckは立たない", "counsel")
def _(run):
    d = run(fast_prompt("では次回は金曜15時で確定にしましょう。はい、確定で。議事録は私がまとめます。"))
    assert not str((d or {}).get("stuck") or "").strip(), "順調な進行でstuckが立った: %r" % d.get("stuck")


@case("counsel: 2案＋推し＋根拠の構造", "counsel")
def _(run):
    prompt = srv.COUNSEL_PROMPT.format(stuck="単価設定の決め手がない", bg="【背景】過去案件では30万円開始→実績後に100万円へ引き上げた成功例がある",
                                       arc="新サービスの価格設定を議論中。低価格案と高価格案で膠着", delta="30万だと安すぎる気もするし、100万だと最初は売れない気もする。うーん。")
    d = run(prompt, timeout=120)
    assert str((d or {}).get("situation") or "").strip(), "situationが無い"
    opts = (d or {}).get("options") or []
    assert len(opts) == 2, "2案でない: %d案" % len(opts)
    assert str((d or {}).get("pick") or "").strip() and str((d or {}).get("reason") or "").strip(), "推し/根拠が無い"


@case("vet: 一般語は棄却・固有名詞は通過", "vet")
def _(run):
    terms = "- 「エージェント」（発話: AIエージェントの導入支援というところ）\n- 「ワットライン」（発話: 会社に対してワットラインされたりする）"
    d = run(srv.CONFIRM_VET_PROMPT.format(terms=terms))
    keep = [str(k.get("term") or "") for k in ((d or {}).get("keep") or [])]
    assert not any("エージェント" in t for t in keep), "一般語が通過した: %r" % keep
    assert any("ワットライン" in t for t in keep), "聞き間違い疑いが棄却された: %r" % keep


@case("fast: 質問は複数候補で返せる", "fast")
def _(run):
    d = run(fast_prompt("新サービスの価格の議論です。原価は月3万円。競合Aは月15万円で提供。うちは機能面で優位があります。", goal="価格戦略を決める"))
    qs = (d or {}).get("questions") or []
    assert len(qs) >= 1 and all(str(q.get("q") or "").strip() for q in qs), "質問候補が返らない: %r" % qs


@case("retro: 訂正 → 置換ペア抽出", "retro")
def _(run):
    out = ask(srv.RETRO_PROMPT.format(note="訂正：担当は田中さんではなく中田さんです"))
    pairs = (out or {}).get("replacements") or []
    assert any("田中" in str(p.get("from") or "") and "中田" in str(p.get("to") or "") for p in pairs), \
        "田中→中田系の置換を抽出できない: %r" % pairs


@case("retro: 補足（訂正でない）→ 空配列", "retro")
def _(run):
    out = ask(srv.RETRO_PROMPT.format(note="補足：先方は予算に慎重な社風です"))
    assert not ((out or {}).get("replacements") or []), "補足なのに置換ペアが出た"


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    passed = failed = 0
    for name, group, fn in CASES:
        if only and group != only:
            continue
        ok, err = False, ""
        for _try in range(2):   # LLM揺れ対策：2回中1回通ればPASS
            try:
                fn(ask)
                ok = True
                break
            except AssertionError as e:
                err = str(e)
            except Exception as e:
                err = repr(e)
        print("%s %s" % ("PASS" if ok else "FAIL", name) + ("" if ok else "  ← " + err))
        passed += ok
        failed += (not ok)
    print("---\n%d passed, %d failed" % (passed, failed))
    sys.exit(1 if failed else 0)


main()
