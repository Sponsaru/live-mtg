# -*- coding: utf-8 -*-
"""確定済み会議データをSlide Worker正典のA4会議ペーパーへ変換する。

内容は削らず、読みやすい単位でページを増やす。AIによる要約・創作は挟まない。
出力名は既存API互換のため minutes-deck.html のまま維持する。
"""

import difflib
import html
import json
import math
import os
import re
import shutil
import sys


SDIR = os.environ.get("SDIR", "")
if not SDIR or not os.path.isdir(SDIR):
    sys.exit("SDIR未指定")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LANG = os.environ.get("LIVE_MTG_LANGUAGE", "ja")
TITLE = os.environ.get("TITLE", "会議" if LANG != "en" else "Meeting")
VARIANT = os.environ.get("MINUTES_VARIANT", "compact")
MAP_SCREENSHOTS = {
    "radial": os.environ.get("MINUTES_MAP_RADIAL", ""),
    "relation": os.environ.get("MINUTES_MAP_RELATION", ""),
}


def tr(ja, en):
    return en if LANG == "en" else ja


def load_json(name):
    try:
        with open(os.path.join(SDIR, name), encoding="utf-8") as source:
            return json.load(source)
    except Exception:
        return {}


def load_text(name):
    try:
        with open(os.path.join(SDIR, name), encoding="utf-8") as source:
            return source.read()
    except Exception:
        return ""


data = load_json("final.json") or load_json("data.json")
live = load_json("data.json")
flow = load_json("meeting-flow.json")
meta = load_json("meta.json")
if not data:
    sys.exit("議事データがありません")


def esc(value):
    return html.escape(str(value or ""), quote=True)


def item_text(value):
    if value is None:
        return ""
    if isinstance(value, list):
        return " ・ ".join(filter(None, (item_text(x) for x in value)))
    if isinstance(value, dict):
        first = next((str(value[k]).strip() for k in
                      ("label", "issue", "title", "what", "text", "q")
                      if str(value.get(k) or "").strip()), "")
        detail = next((str(value[k]).strip() for k in
                       ("detail", "description", "answer")
                       if str(value.get(k) or "").strip() and str(value[k]).strip() != first), "")
        return (first + " — " + detail) if first and detail else (first or detail)
    return str(value).strip()


def text_list(values):
    return [text for text in (item_text(value) for value in (values or [])) if text]


def normalized(value):
    value = item_text(value).lower()
    return re.sub(r"[\s\u3000、。・，,.：:；;（）()「」『』\[\]【】！？!?ー―—\-]+", "", value)


def is_covered(value, candidates):
    needle = normalized(value)
    if not needle:
        return True
    for candidate in candidates:
        hay = normalized(candidate)
        if not hay:
            continue
        shorter, longer = sorted((needle, hay), key=len)
        if len(shorter) >= 12 and shorter in longer:
            return True
        if difflib.SequenceMatcher(None, needle, hay).ratio() >= .62:
            return True
    return False


def clean_markdown_inline(value):
    value = str(value or "").strip()
    value = re.sub(r"!\[([^]]*)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"(\*\*|__|`)", "", value)
    return re.sub(r"\s+", " ", value).strip()


def parse_learnings(markdown):
    rows, section = [], ""
    for raw in str(markdown or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        heading = re.match(r"^#{1,6}\s+(.+)$", line)
        if heading:
            title = clean_markdown_inline(heading.group(1))
            if title.startswith(tr("学びと次の一手", "Learnings")):
                continue
            section = title
            continue
        bullet = re.match(r"^(?:[-*+]\s+|\d+[.)]\s+)(.+)$", line)
        text = clean_markdown_inline(bullet.group(1) if bullet else line)
        if text:
            rows.append((section, text))
    insights, moves = [], []
    for section, text in rows:
        if re.search(r"次の一手|次回|アクション|next\s*(?:step|move|action)",
                     section + " " + text[:18], re.I):
            body = re.sub(r"^(?:次の一手|次回までに|アクション)\s*[：:]\s*", "", text)
            split = [clean_markdown_inline(x) for x in re.split(r"[①②③④⑤⑥⑦⑧⑨⑩]", body)]
            moves.extend([x for x in split if x] or [body])
        else:
            insights.append(text)
    return insights, moves


STATUS_LABELS = {
    "not_started": tr("未着手", "Not started"), "discussing": tr("議論中", "In discussion"),
    "discussed": tr("話し終わり", "Discussed"), "deferred": tr("持ち越し", "Deferred"),
    "not_applicable": tr("合意対象外", "No agreement needed"), "pending": tr("未合意", "Pending"),
    "agreed": tr("合意済み", "Agreed"), "rejected": tr("見送り", "Rejected"),
}


def status_label(value):
    return STATUS_LABELS.get(str(value or ""), item_text(value) or tr("未設定", "Unset"))


def flow_target():
    return item_text(flow.get("target") if isinstance(flow, dict) else None)


def agenda_result(agenda, key):
    result = agenda.get("result") if isinstance(agenda.get("result"), dict) else {}
    return result.get(key) or []


def agenda_summary(agenda):
    result = agenda.get("result") if isinstance(agenda.get("result"), dict) else {}
    return item_text(result.get("summary") or agenda.get("summary"))


def action_fields(value, agenda_title=""):
    who = what = due = ""
    if isinstance(value, dict):
        who = item_text(value.get("who"))
        what = item_text(value.get("what") or value.get("text"))
        due = item_text(value.get("due"))
    else:
        what = item_text(value)
    if not who and "：" in what:
        maybe_who, maybe_what = what.split("：", 1)
        if 0 < len(maybe_who.strip()) <= 18:
            who, what = maybe_who.strip(), maybe_what.strip()
    return {"who": who or tr("担当未定", "TBD"), "what": what,
            "due": due or tr("期限未定", "Due TBD"), "agenda": agenda_title}


def dedupe_actions(actions):
    result = []
    for action in actions:
        if not action.get("what"):
            continue
        same = next((old for old in result if is_covered(action["what"], [old["what"]])), None)
        if same:
            if same["who"] == tr("担当未定", "TBD") and action["who"] != same["who"]:
                same["who"] = action["who"]
            if same["due"] == tr("期限未定", "Due TBD") and action["due"] != same["due"]:
                same["due"] = action["due"]
            continue
        result.append(action)
    return result


def split_text(value, limit=180):
    """文字を縮めず、内容を保ったまま自然な区切りで分割する。"""
    text = item_text(value)
    parts = []
    while len(text) > limit:
        cuts = [text.rfind(mark, 0, limit + 1) for mark in "。！？；;、,"]
        cut = max(cuts)
        cut = limit if cut < limit * .55 else cut + 1
        parts.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        parts.append(text)
    return parts


def paginate(rows, capacity=10):
    """長さに応じてページを増やす。rowsの順序と全文は保持する。"""
    weighted = [(row, max(1, math.ceil(len(row.get("text", "")) / 85))) for row in rows]
    if not weighted:
        return []
    page_count = max(1, math.ceil(sum(weight for _, weight in weighted) / capacity))
    target = math.ceil(sum(weight for _, weight in weighted) / page_count)
    pages, current, used = [], [], 0
    for row, weight in weighted:
        if current and used + weight > target and len(pages) < page_count - 1:
            pages.append(current)
            current, used = [], 0
        current.append(row)
        used += weight
    if current:
        pages.append(current)
    return pages


def header(title, purpose, chip=None, right=""):
    chip = chip or tr("報告", "Report")
    created = item_text(meta.get("created"))
    meta_left = (tr("議事録", "Meeting minutes") + ((" ｜ " + created) if created else ""))
    return ('<div class="blk blk-hd"><div class="meta"><span>%s</span><span class="r">%s</span></div>'
            '<h1>%s</h1><div class="ask"><span class="chip report">%s</span><p>%s</p>'
            '<span class="doc-logo"></span></div></div>'
            % (esc(meta_left), esc(right), esc(title), esc(chip), esc(purpose)))


def source_note(text=None):
    return '<div class="blk src">%s</div>' % esc(
        text or tr("出典：確定済み清書・進行ボード・会議中の解析結果", "Source: finalized meeting records"))


def sheet(title, purpose, blocks, chip=None, right="", source=None, cls=""):
    return '<div class="sheet%s">%s%s%s</div>' % (
        (" " + cls) if cls else "", header(title, purpose, chip, right), "".join(blocks), source_note(source))


def summary_block(number, title, text, sub=""):
    sub_html = '<p class="sub">%s</p>' % esc(sub) if sub else ""
    return ('<div class="blk sec"><h2>%s. %s</h2><div class="sum"><p>%s</p>%s</div></div>'
            % (number, esc(title), esc(text), sub_html))


def list_block(number, title, rows):
    items = []
    for index, row in enumerate(rows, 1):
        label = ('<span class="label">%s</span>' % esc(row.get("label"))) if row.get("label") else ""
        items.append('<li><span class="n">(%d)</span><span>%s%s</span></li>'
                     % (index, label, esc(row.get("text"))))
    return ('<div class="blk sec"><h2>%s. %s</h2><ul class="paper-list">%s</ul></div>'
            % (number, esc(title), "".join(items)))


def agenda_rows(agenda):
    rows = []
    if agenda_summary(agenda):
        rows.append({"label": tr("ここまでの要点", "Summary"), "text": agenda_summary(agenda)})
    for key, label in (("answers", tr("回答されたこと", "Answered")),
                       ("decisions", tr("決まったこと", "Decided")),
                       ("actions", tr("次の行動", "Next action")),
                       ("unresolved", tr("未解決・要確認", "Open item"))):
        for value in agenda_result(agenda, key):
            if key == "actions":
                action = action_fields(value, item_text(agenda.get("title")))
                text = "%s ｜ %s ｜ %s" % (action["what"], action["who"], action["due"])
            else:
                text = item_text(value)
            for part_index, part in enumerate(split_text(text)):
                rows.append({"label": label + (tr("（続き）", " (continued)") if part_index else ""),
                             "text": part})
    return rows


def radial_positions(count):
    presets = {
        1: [(50, 17)],
        2: [(27, 23), (73, 77)],
        3: [(50, 14), (18, 75), (82, 75)],
        4: [(27, 17), (73, 17), (20, 78), (80, 78)],
        5: [(50, 12), (17, 38), (27, 82), (73, 82), (83, 38)],
        6: [(50, 11), (17, 32), (17, 73), (50, 88), (83, 73), (83, 32)],
    }
    return presets.get(count, presets[6])


def radial_sheet(agenda_chunk, page_index, page_total):
    positions = radial_positions(len(agenda_chunk))
    lines, nodes = [], []
    for (x, y), agenda in zip(positions, agenda_chunk):
        lines.append('<line x1="50" y1="50" x2="%s" y2="%s" vector-effect="non-scaling-stroke"/>' % (x, y))
        result = agenda.get("result") if isinstance(agenda.get("result"), dict) else {}
        count = sum(len(result.get(key) or []) for key in ("answers", "decisions", "actions", "unresolved"))
        nodes.append('<div class="radial-node" style="left:%s%%;top:%s%%">%s<small>%s</small></div>'
                     % (x, y, esc(item_text(agenda.get("title"))),
                        esc(tr("整理済み %d件", "%d organized items") % count)))
    center = flow_target() or item_text(data.get("summary")) or TITLE
    blocks = ['<div class="blk sec" style="display:flex;flex:1;min-height:0;flex-direction:column">'
              '<h2>1. %s</h2><div class="map-canvas"><svg class="radial-lines" viewBox="0 0 100 100" preserveAspectRatio="none">%s</svg>'
              '<div class="radial-center">%s</div>%s</div></div>'
              % (esc(tr("議題と着地点の全体像", "Agenda and outcome overview")), "".join(lines),
                 esc(center), "".join(nodes))]
    suffix = (" %d/%d" % (page_index, page_total)) if page_total > 1 else ""
    return sheet(tr("放射マップ", "Radial map") + suffix,
                 tr("着地点を中心に、会議で扱った議題を俯瞰", "Meeting topics around the desired outcome"),
                 blocks, source=tr("出典：進行ボードの議題・着地点", "Source: meeting agenda board"), cls="map-sheet")


def conversation_radial_sheet(type_chunk, page_index, page_total):
    """画像撮影が使えない詳細版でも、放射マップの意味を会話タイプで統一する。"""
    positions = radial_positions(len(type_chunk))
    lines, nodes = [], []
    for (x, y), row in zip(positions, type_chunk):
        lines.append('<line x1="50" y1="50" x2="%s" y2="%s" vector-effect="non-scaling-stroke"/>' % (x, y))
        topics = [item_text(topic.get("label") if isinstance(topic, dict) else topic)
                  for topic in (row.get("topics") or []) if item_text(topic.get("label") if isinstance(topic, dict) else topic)]
        nodes.append('<div class="radial-node" style="left:%s%%;top:%s%%">%s %d%%<small>%s</small></div>' %
                     (x, y, esc(item_text(row.get("type"))), int(row.get("share") or 0),
                      esc("・".join(topics[:2]) or tr("具体話題を整理", "Topics organized"))))
    blocks = ['<div class="blk sec" style="display:flex;flex:1;min-height:0;flex-direction:column">'
              '<h2>1. %s</h2><div class="map-canvas"><svg class="radial-lines" viewBox="0 0 100 100" preserveAspectRatio="none">%s</svg>'
              '<div class="radial-center">%s</div>%s</div></div>' %
              (esc(tr("会話タイプ別の構成", "Conversation mix by type")), "".join(lines),
               esc(tr("会話の構成", "Conversation mix")), "".join(nodes))]
    suffix = (" %d/%d" % (page_index, page_total)) if page_total > 1 else ""
    return sheet(tr("放射マップ", "Radial map") + suffix,
                 tr("会議で何に会話量を使ったかを俯瞰", "How the meeting's conversation was distributed"),
                 blocks, source=tr("出典：清書後の会話タイプ分析", "Source: finalized conversation-type analysis"), cls="map-sheet")


def parse_relations(diagram):
    diagram = str(diagram or "")
    nodes = {}
    for node_id, label in re.findall(r"\b([A-Za-z][\w-]*)\s*\[([^\]]+)\]", diagram):
        nodes[node_id] = re.sub(r"\s+", " ", label).strip()
    normalized_diagram = re.sub(r"\b([A-Za-z][\w-]*)\s*\[[^\]]+\]", r"\1", diagram)
    relations = []
    edge_re = re.compile(r"\b([A-Za-z][\w-]*)\s*(-->|-\.->)\s*(?:\|([^|]+)\|\s*)?([A-Za-z][\w-]*)\b")
    for source, arrow, label, target in edge_re.findall(normalized_diagram):
        relations.append({"source": nodes.get(source, source), "label": label.strip() or
                          (tr("懸念・条件", "Concern / condition") if arrow == "-.->" else tr("つながり", "Leads to")),
                          "target": nodes.get(target, target)})
    return relations


def relation_sheets(relations):
    if not relations:
        relations = [{"source": flow_target() or TITLE, "label": tr("話し合った", "Discussed"),
                      "target": item_text(agenda.get("title"))} for agenda in agendas]
    rows = [{"text": "%s %s %s" % (r["source"], r["label"], r["target"]), **r} for r in relations]
    pages = paginate(rows, 8)
    output = []
    for page_index, page in enumerate(pages, 1):
        rel_html = "".join(
            '<div class="relation-row"><div class="relation-node">%s</div>'
            '<div class="relation-arrow">%s</div><div class="relation-node">%s</div></div>'
            % (esc(row["source"]), esc(row["label"]), esc(row["target"])) for row in page)
        blocks = ['<div class="blk sec"><h2>1. %s</h2><div class="relation-list">%s</div></div>'
                  % (esc(tr("発言から生まれた因果・判断の流れ", "Causal and decision flow")), rel_html)]
        suffix = (" %d/%d" % (page_index, len(pages))) if len(pages) > 1 else ""
        output.append(sheet(tr("会話の関係", "Conversation relationships") + suffix,
                            tr("課題・解決策・判断がどうつながったか", "How issues, options and decisions connected"),
                            blocks, source=tr("出典：確定済み清書の会話関係図", "Source: finalized relationship map"),
                            cls="map-sheet"))
    return output


def lead_sentences(value, count=1):
    """1〜4ページ版は文章を途中で切らず、完結した先頭文だけを採る。"""
    text = re.sub(r"\s+", " ", item_text(value)).strip()
    if not text:
        return ""
    sentences = [part.strip() for part in re.split(r"(?<=[。！？!?])\s*", text) if part.strip()]
    return "".join(sentences[:count]) if sentences else text


def numbered_list(values, limit=3):
    rows = [lead_sentences(value) for value in values[:limit]]
    return '<ul class="nl">%s</ul>' % "".join(
        '<li><span class="n">(%d)</span><span>%s</span></li>' % (index, esc(value))
        for index, value in enumerate(rows, 1) if value)


def conclusion_block(number, values, context=""):
    """結論が複数ある会議は、冒頭で最大4件を独立して読める形にする。"""
    conclusions = []
    for value in values:
        text = lead_sentences(value, 1)
        if text and text not in conclusions:
            conclusions.append(text)
    conclusions = conclusions[:4]
    context = lead_sentences(context, 2)
    if not conclusions:
        conclusions = [context] if context else [tr("確定した結論はありません", "No finalized outcome")]
    if len(conclusions) == 1:
        return summary_block(number, tr("結論", "Outcomes"), conclusions[0],
                             context if context and context != conclusions[0] else "")
    context_html = '<p class="sub">%s</p>' % esc(context) if context and context not in conclusions else ""
    return '<div class="blk sec"><h2>%s. %s</h2><div class="sum">%s%s</div></div>' % (
        number, esc(tr("結論", "Outcomes")), numbered_list(conclusions, 4), context_html)


def compact_overview_sheet():
    decisions = text_list(data.get("decisions"))
    if not decisions:
        decisions = [item_text(value) for agenda in agendas for value in agenda_result(agenda, "decisions")]
    opens = text_list(data.get("open"))
    if not opens:
        opens = [item_text(value) for agenda in agendas for value in agenda_result(agenda, "unresolved")]
    context = lead_sentences(summary, 2)
    decision_rows = "".join(
        '<tr><td>%s</td><td><span class="st-chip ok">%s</span></td></tr>' %
        (esc(lead_sentences(value)), esc(tr("合意・確認", "Agreed / confirmed")))
        for value in decisions[:5])
    action_rows = "".join(
        '<tr><td>%s</td><td>%s</td><td>%s</td></tr>' %
        (esc(lead_sentences(action["what"])), esc(action["who"]), esc(action["due"]))
        for action in all_actions[:4])
    blocks = [
        conclusion_block(1, decisions, context),
        '<div class="blk sec"><h2>2. %s</h2><div class="kpis n4">%s</div>'
        '<div class="kfoot">%s</div></div>' % (
            esc(tr("会議の到達点", "Meeting results")),
            "".join('<div class="k"><div class="v">%d</div><div class="l">%s</div></div>' % row
                    for row in ((len(agendas), esc(tr("議題", "Agendas"))),
                                (len(decisions), esc(tr("合意・確認", "Agreements"))),
                                (len(all_actions), esc(tr("次の行動", "Actions"))),
                                (len(opens), esc(tr("要確認", "Open items"))))),
            esc(tr("確定済み清書・進行ボードを集計", "Based on finalized meeting records"))),
        '<div class="blk sec"><h2>3. %s</h2><table><thead><tr><th>%s</th><th style="width:14rem">%s</th>'
        '</tr></thead><tbody>%s</tbody></table></div>' %
        (esc(tr("合意・確認したこと", "Agreements")), esc(tr("内容", "Item")),
         esc(tr("状態", "Status")), decision_rows),
        '<div class="blk sec"><h2>4. %s</h2><table><thead><tr><th>%s</th><th style="width:14rem">%s</th>'
        '<th style="width:12rem">%s</th></tr></thead><tbody>%s</tbody></table></div>' %
        (esc(tr("次の行動", "Next actions")), esc(tr("何を", "What")), esc(tr("誰が", "Who")),
         esc(tr("いつまでに", "Due")), action_rows),
    ]
    return sheet(TITLE + tr("｜会議成果", " | Meeting outcomes"),
                 tr("結論・合意事項・次の行動を共有", "Share outcomes, agreements and actions"),
                 blocks, right=tr("会議成果レポート", "Meeting outcome report"),
                 source=tr("出典：確定済み清書・進行ボード。全回答・全根拠は詳細議事録に収録", "Source: finalized records; full evidence is in detailed minutes"))


def compact_learning_sheet():
    opens = text_list(data.get("open"))
    if not opens:
        opens = [item_text(value) for agenda in agendas for value in agenda_result(agenda, "unresolved")]
    issues = [value for value in learning_insights
              if re.search(r"見落とし|詰め|未確定|不足|弱い|取り逃", value)]
    gains = [value for value in learning_insights if value not in issues]
    if not issues:
        issues = opens
    moves = learning_moves or [action["what"] for action in all_actions]
    facts = text_list(data.get("points"))
    headline = lead_sentences(gains[0] if gains else summary, 1)
    blocks = [
        summary_block(1, tr("学びの要旨", "Key learning"), headline),
        '<div class="blk cols2"><div class="sec"><h2>2. %s</h2>%s</div>'
        '<div class="sec"><h2>3. %s</h2>%s</div></div>' %
        (esc(tr("得られた示唆", "What worked")), numbered_list(gains, 3),
         esc(tr("見落とし・残った論点", "Gaps and open questions")), numbered_list(issues, 3)),
        '<div class="blk sec"><h2>4. %s</h2><div class="askbox"><ul>%s</ul></div></div>' %
        (esc(tr("次の一手", "Next moves")), "".join(
            '<li><span class="n">(%d)</span><span>%s</span></li>' % (index, esc(lead_sentences(value)))
            for index, value in enumerate(moves[:4], 1))),
        '<div class="blk sec"><h2>5. %s</h2>%s</div>' %
        (esc(tr("判断を支える事実", "Facts supporting the assessment")), numbered_list(facts, 4)),
    ]
    return sheet(tr("学びと次の一手", "Learnings and next moves"),
                 tr("会議を次の提案・導入行動につなげる", "Turn the meeting into better follow-up"),
                 blocks, right=tr("振り返り", "Review"),
                 source=tr("出典：会議後の学びレポート・未解決事項", "Source: learnings report and open items"))


def compact_agenda_sheet():
    agenda_rows = "".join(
        '<tr><td>%s</td><td><span class="st-chip %s">%s</span></td><td>%s</td></tr>' %
        (esc(item_text(agenda.get("title"))),
         "ok" if agenda.get("resolutionStatus") == "agreed" else "wt",
         esc(status_label(agenda.get("resolutionStatus"))), esc(lead_sentences(agenda_summary(agenda))))
        for agenda in agendas[:6])
    answers = []
    unresolved = []
    for agenda in agendas:
        title = item_text(agenda.get("title"))
        answers.extend("%s：%s" % (title, item_text(value)) for value in agenda_result(agenda, "answers"))
        unresolved.extend("%s：%s" % (title, item_text(value)) for value in agenda_result(agenda, "unresolved"))
    blocks = [
        '<div class="blk sec"><h2>1. %s</h2><table><thead><tr><th>%s</th><th style="width:13rem">%s</th>'
        '<th>%s</th></tr></thead><tbody>%s</tbody></table></div>' %
        (esc(tr("議題ごとの到達点", "Agenda outcomes")), esc(tr("議題", "Agenda")),
         esc(tr("合意状態", "Agreement")), esc(tr("要点", "Outcome")), agenda_rows),
        '<div class="blk cols2"><div class="sec"><h2>2. %s</h2>%s</div>'
        '<div class="sec"><h2>3. %s</h2>%s</div></div>' %
        (esc(tr("確認できたこと", "Confirmed points")), numbered_list(answers, 7),
         esc(tr("議題ごとの未解決", "Open points by agenda")), numbered_list(unresolved, 7)),
    ]
    return sheet(tr("議題ごとの記録", "Records by agenda"),
                 tr("各議題の到達点・確認事項・未解決を整理", "Review outcomes, findings and open points"),
                 blocks, right=tr("議題詳細", "Agenda details"),
                 source=tr("出典：進行ボード・確定済み清書。全回答は詳細議事録に収録", "Source: meeting board and finalized minutes"))


def compact_map_images_sheet():
    figures = []
    for key, title in (("radial", tr("放射マップ", "Radial map")),
                       ("relation", tr("会話の関係", "Conversation relationships"))):
        if not MAP_SCREENSHOTS.get(key) or not os.path.isfile(MAP_SCREENSHOTS[key]):
            continue
        filename = "minutes-map-%s.png" % key
        figures.append('<div class="blk sec"><h2>%d. %s</h2><div class="fig fit minutes-map-shot minutes-map-%s">'
                       '<img src="%s" alt="%s"></div><p class="figcap">%s</p></div>' %
                       (len(figures) + 1, esc(title), esc(key), esc(filename), esc(title),
                        esc(tr("会議終了時点のマップ表示を画像として保存", "Captured from the finalized map view"))))
    if not figures:
        return ""
    return sheet(tr("会議の全体図", "Meeting overview maps"),
                 tr("議題の広がりと、発言から判断までの関係を俯瞰", "Review topic scope and decision flow"),
                 figures, right=tr("図版", "Figures"),
                 source=tr("出典：放射マップ・会話の関係の確定時スクリーンショット", "Source: finalized map screenshots"))


agendas = sorted(
    [agenda for agenda in (flow.get("agendas") or [])
     if isinstance(agenda, dict) and item_text(agenda.get("title"))],
    key=lambda agenda: (agenda.get("order", 9999), item_text(agenda.get("title"))),
)
if not agendas:
    agendas = [{"id": "legacy-%d" % index, "order": index, "title": title,
                "status": "discussed", "resolutionStatus": "not_applicable", "result": {}}
               for index, title in enumerate(text_list(data.get("agenda")), 1)]

flow_actions = [action_fields(value, item_text(agenda.get("title"))) for agenda in agendas
                for value in agenda_result(agenda, "actions")]
all_actions = dedupe_actions(flow_actions + [action_fields(value) for value in (data.get("todos") or [])])
learning_insights, learning_moves = parse_learnings(load_text("learnings.md"))
summary = item_text(data.get("summary"))

sheets = []
decision_count = sum(len(agenda_result(agenda, "decisions")) for agenda in agendas)
unresolved_count = sum(len(agenda_result(agenda, "unresolved")) for agenda in agendas)
summary_blocks = []
paper_decisions = text_list(data.get("decisions"))
if not paper_decisions:
    paper_decisions = [item_text(value) for agenda in agendas for value in agenda_result(agenda, "decisions")]
summary_blocks.append(conclusion_block(1, paper_decisions,
                                      summary or tr("確定済みの要旨はありません", "No finalized summary")))
kpis = [(len(agendas), tr("議題", "Agendas")), (decision_count, tr("決定", "Decisions")),
        (len(all_actions), tr("次の行動", "Actions")), (unresolved_count, tr("未解決", "Open items"))]
summary_blocks.append('<div class="blk sec"><h2>2. %s</h2><div class="kpis n4">%s</div></div>' % (
    esc(tr("全体像", "Overview")), "".join(
        '<div class="k"><div class="v">%d</div><div class="l">%s</div></div>' % (value, esc(label))
        for value, label in kpis)))
conversation_map = data.get("conversationMap") if isinstance(data.get("conversationMap"), dict) else {}
conversation_types = [row for row in (conversation_map.get("types") or [])
                      if isinstance(row, dict) and item_text(row.get("type")) and int(row.get("share") or 0) > 0]
if conversation_types:
    type_pages = [conversation_types[index:index + 6] for index in range(0, len(conversation_types), 6)]
    for page_index, type_chunk in enumerate(type_pages, 1):
        sheets.append(conversation_radial_sheet(type_chunk, page_index, len(type_pages)))
elif agendas:
    overview_rows = []
    for agenda in agendas[:5]:
        overview_rows.append({"label": status_label(agenda.get("status")),
                              "text": "%s ｜ %s" % (item_text(agenda.get("title")),
                                                     status_label(agenda.get("resolutionStatus")))})
    summary_blocks.append(list_block(3, tr("議題", "Agendas"), overview_rows))
sheets.append(sheet(TITLE, flow_target() or tr("会議結果を確認する", "Review the meeting outcome"),
                    summary_blocks, right=tr("確定済み議事録", "Finalized minutes")))

if len(agendas) > 5:
    remaining = [{"label": status_label(agenda.get("status")),
                  "text": "%s ｜ %s" % (item_text(agenda.get("title")),
                                         status_label(agenda.get("resolutionStatus")))}
                 for agenda in agendas[5:]]
    for page_index, page in enumerate(paginate(remaining, 12), 1):
        sheets.append(sheet(tr("議題の全体像（続き）", "Agenda overview (continued)"),
                            tr("会議で扱った議題をすべて確認", "Review every agenda item"),
                            [list_block(1, tr("議題", "Agendas"), page)]))

learning_rows = ([{"label": tr("客観的な示唆", "Objective insight"), "text": text}
                  for text in learning_insights] +
                 [{"label": tr("次の一手", "Next move"), "text": text} for text in learning_moves])
for page_index, page in enumerate(paginate(learning_rows, 10), 1):
    suffix = (" %d/%d" % (page_index, len(paginate(learning_rows, 10)))) if len(paginate(learning_rows, 10)) > 1 else ""
    sheets.append(sheet(tr("学びと次の一手", "Learnings and next moves") + suffix,
                        tr("会議の事実を、次回の改善と行動へつなげる", "Turn meeting facts into improvement"),
                        [list_block(1, tr("客観的な示唆と実践", "Insights and practice"), page)]))

for agenda_index, agenda in enumerate(agendas, 1):
    rows = agenda_rows(agenda)
    if not rows:
        rows = [{"label": tr("現在の結果", "Current result"),
                 "text": tr("まだ整理された内容はありません", "No organized results yet")}]
    pages = paginate(rows, 10)
    for page_index, page in enumerate(pages, 1):
        suffix = (" %d/%d" % (page_index, len(pages))) if len(pages) > 1 else ""
        status = "%s ｜ %s" % (status_label(agenda.get("status")),
                               status_label(agenda.get("resolutionStatus")))
        sheets.append(sheet(item_text(agenda.get("title")) + suffix,
                            tr("議題 %d/%d", "Agenda %d/%d") % (agenda_index, len(agendas)),
                            [list_block(1, tr("話したことと結果", "Discussion and outcome"), page)],
                            right=status))

for page_index in range(0, len(all_actions), 6):
    chunk = all_actions[page_index:page_index + 6]
    rows = "".join('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' %
                   (esc(action["what"]), esc(action["who"]), esc(action["due"]), esc(action["agenda"]))
                   for action in chunk)
    table = ('<div class="blk sec"><h2>1. %s</h2><table><thead><tr><th>%s</th><th>%s</th><th>%s</th><th>%s</th>'
             '</tr></thead><tbody>%s</tbody></table></div>' %
             (esc(tr("実行項目", "Action items")), esc(tr("何を", "What")), esc(tr("誰が", "Who")),
              esc(tr("いつまでに", "Due")), esc(tr("議題", "Agenda")), rows))
    suffix = (" %d/%d" % (page_index // 6 + 1, math.ceil(len(all_actions) / 6))) if len(all_actions) > 6 else ""
    sheets.append(sheet(tr("次の行動", "Next actions") + suffix,
                        tr("担当・期限・対象議題を確認", "Confirm owners, due dates and agenda"), [table]))

covered = []
for agenda in agendas:
    covered.extend([item_text(agenda.get("title")), agenda_summary(agenda)])
    for key in ("answers", "decisions", "actions", "unresolved"):
        covered.extend(text_list(agenda_result(agenda, key)))
supplement_rows = []
for label, values in ((tr("補足の決定事項", "Additional decisions"), text_list(data.get("decisions"))),
                      (tr("補足の主要論点", "Additional key points"), text_list(data.get("points"))),
                      (tr("補足の未解決・要確認", "Additional open items"), text_list(data.get("open")))):
    for value in values:
        if not is_covered(value, covered):
            for part in split_text(value):
                supplement_rows.append({"label": label, "text": part})
for page_index, page in enumerate(paginate(supplement_rows, 10), 1):
    pages = paginate(supplement_rows, 10)
    suffix = (" %d/%d" % (page_index, len(pages))) if len(pages) > 1 else ""
    sheets.append(sheet(tr("補足資料", "Appendix") + suffix,
                        tr("清書で追加確認された内容", "Additional finalized details"),
                        [list_block(1, tr("補足", "Additional details"), page)]))

if agendas:
    agenda_pages = [agendas[index:index + 6] for index in range(0, len(agendas), 6)]
    for page_index, agenda_chunk in enumerate(agenda_pages, 1):
        sheets.append(radial_sheet(agenda_chunk, page_index, len(agenda_pages)))

diagram = item_text(data.get("diagram")) or item_text(live.get("diagram"))
relations = parse_relations(diagram)
sheets.extend(relation_sheets(relations))

# 通常表示は3ページの要約版。詳細版は同じ生成器を MINUTES_VARIANT=full で呼び、
# 全回答・全根拠・全関係を落とさず別PDFに保存する。
if VARIANT != "full":
    compact_sheets = [compact_overview_sheet()]
    if len(learning_insights) + len(learning_moves) >= 2:
        compact_sheets.append(compact_learning_sheet())
    organized_count = sum(len(agenda_rows(agenda)) for agenda in agendas)
    if len(agendas) >= 2 or organized_count >= 4:
        compact_sheets.append(compact_agenda_sheet())
    map_sheet = compact_map_images_sheet()
    if map_sheet and len(compact_sheets) < 4:
        compact_sheets.append(map_sheet)
    sheets = compact_sheets[:4]

out = os.path.abspath(os.environ.get("MINUTES_OUT") or os.path.join(SDIR, "minutes-deck.html"))
out_dir = os.path.dirname(out)
os.makedirs(out_dir, exist_ok=True)
logo_source = os.path.join(SCRIPT_DIR, "brand-logo.png")
logo_target = os.path.join(out_dir, "brand-logo.png")
if os.path.isfile(logo_source) and os.path.abspath(logo_source) != os.path.abspath(logo_target):
    shutil.copy2(logo_source, logo_target)
for map_key, map_source in MAP_SCREENSHOTS.items():
    if map_source and os.path.isfile(map_source):
        map_target = os.path.join(out_dir, "minutes-map-%s.png" % map_key)
        if os.path.abspath(map_source) != os.path.abspath(map_target):
            shutil.copy2(map_source, map_target)

template_path = os.path.join(SCRIPT_DIR, "minutes-paper-template.html")
if not os.path.isfile(template_path):
    sys.exit("minutes-paper-template.html がありません。scripts/sync-slide-work.py を実行してください")
template = open(template_path, encoding="utf-8").read()
doc = template.replace("{{TITLE}}", esc(TITLE + tr(" ｜ 議事録", " | Minutes")))
doc = doc.replace("{{SHEETS}}", "\n".join(sheets))
with open(out, "w", encoding="utf-8") as target:
    target.write(doc)
print("generated:", out, "/ canonical A4 meeting paper / pages:", len(sheets))
