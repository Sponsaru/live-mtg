"""Persistence and validated mutations for LiveMTG's meeting progress board.

This module deliberately has no dependency on ``server.py``.  Callers pass the
meetings root explicitly, which keeps meeting ids fixed for the lifetime of an
operation and makes the data layer straightforward to test.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import threading
import time
import uuid
from difflib import SequenceMatcher


SCHEMA_VERSION = 1
AGENDA_STATUSES = {"not_started", "discussing", "discussed", "deferred"}
RESOLUTION_STATUSES = {"not_applicable", "pending", "agreed", "rejected"}
QUESTION_STATUSES = {"next", "queued", "asked", "answered", "deferred", "dismissed"}
APPROVALS = {"draft", "accepted", "dismissed"}
RESULT_SECTIONS = {"answers", "decisions", "actions", "unresolved"}
SUGGESTION_TYPES = {"agenda_proposal", "question_proposal", "research", "unstuck", "warning"}
_SID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_NON_AGENDA_TITLE_RE = re.compile(
    r"(?:会話|会議|文字起こし| transcript).{0,12}(?:本文|原本).{0,12}(?:取り込み|提供|共有|不足|なし)|"
    r"(?:本文|原本).{0,12}(?:提供|共有).{0,12}(?:必要|待ち|要)|"
    r"(?:transcript|source).{0,18}(?:required|missing|needed)", re.I,
)
_AGENDA_AI_TRANSITIONS = {
    "not_started": {"not_started", "discussing", "deferred"},
    "discussing": {"discussing", "discussed", "deferred"},
    "discussed": {"discussed", "discussing", "deferred"},
    "deferred": {"deferred", "discussing"},
}
_QUESTION_AI_TRANSITIONS = {
    "next": {"next", "queued", "asked", "answered", "deferred"},
    "queued": {"next", "queued", "asked", "answered", "deferred"},
    "asked": {"asked", "answered", "deferred"},
    "answered": {"answered"}, "deferred": {"deferred"}, "dismissed": {"dismissed"},
}


class FlowError(Exception):
    """Base class for errors safe to expose through the local API."""

    def __init__(self, message):
        self.message = str(message)
        super().__init__(self.message)


class ValidationError(FlowError):
    pass


class RevisionConflict(FlowError):
    def __init__(self, current):
        self.current = copy.deepcopy(current)
        super().__init__("別の更新が先に保存されました。最新状態を再取得してください")


def _now():
    return int(time.time())


def _id(prefix):
    return "%s-%s" % (prefix, uuid.uuid4().hex)


def _stable_id(prefix, *parts):
    raw = "\u241f".join(str(x or "") for x in parts)
    return "%s-%s" % (prefix, uuid.uuid5(uuid.NAMESPACE_URL, raw).hex)


def _text(value, maximum, field="内容", required=False):
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    if required and not value:
        raise ValidationError("%sを入力してください" % field)
    if len(value) > maximum:
        raise ValidationError("%sは%d文字以内にしてください" % (field, maximum))
    return value


def _strings(value, maximum=100, item_max=120):
    if not isinstance(value, list):
        return []
    out = []
    for item in value[:maximum]:
        text = _text(item, item_max)
        if text and text not in out:
            out.append(text)
    return out


def _integer(value, default=0):
    try: return int(value)
    except (TypeError, ValueError, OverflowError): return int(default)


def _summary_text(value, maximum=1000):
    """Turn legacy/final summary items into readable flow result text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return _text(value, maximum)
    if not isinstance(value, dict):
        return _text(value, maximum)
    what = _text(value.get("what"), maximum)
    if what:
        who = _text(value.get("who"), 120)
        due = _text(value.get("due"), 120)
        prefix = (who + "：") if who else ""
        suffix = ("（期限：%s）" % due) if due else ""
        return _text(prefix + what + suffix, maximum)
    for key in ("text", "title", "label", "issue", "question", "q", "name", "detail", "description"):
        text = _text(value.get(key), maximum)
        if text:
            return text
    return ""


def _summary_strings(value, maximum=1000):
    rows = value if isinstance(value, list) else []
    out = []
    for item in rows:
        text = _summary_text(item, maximum)
        if text and text not in out:
            out.append(text)
    return out


def _is_non_agenda_placeholder(title, detail=""):
    """True for model-authored requests for source material, not meeting topics."""
    title = _text(title, 300)
    combined = _text("%s %s" % (title, detail), 2400)
    if _NON_AGENDA_TITLE_RE.search(title):
        return True
    has_source = any(x in combined for x in ("文字起こし", "会話本文", "会議本文", "原本"))
    has_failure = any(x in combined for x in ("渡されておらず", "提供されてい", "ありません", "不足", "復元", "再構成でき"))
    return has_source and has_failure


def _default_resolution_status(title, result=None):
    """Conservatively infer whether an agenda expects an agreement."""
    result = result if isinstance(result, dict) else {}
    if result.get("decisions"):
        return "agreed"
    if re.search(r"(?:決め|確定|合意|選定|可否|方針|条件|進め方|対象|見送り|採否)", str(title or "")):
        return "pending"
    return "not_applicable"


def empty_flow(now=None):
    now = _now() if now is None else int(now)
    return {
        "version": SCHEMA_VERSION,
        "revision": 0,
        "transcriptGeneration": "",
        "transcriptCursor": 0,
        "target": {"text": "", "successCriteria": "", "origin": "user",
                   "locked": False, "updatedAt": now},
        "agendas": [],
        "questions": [],
        "suggestions": [],
        "evidence": [],
        "unclassifiedResults": {"answers": [], "decisions": [], "actions": [], "unresolved": []},
        "summaryHydration": {"mode": ""},
        "agendaOrderLocked": False,
        "updatedAt": now,
    }


def _normal_result_item(value, prefix="result"):
    if isinstance(value, str):
        value = {"text": value}
    if not isinstance(value, dict):
        return None
    text = _text(value.get("text"), 1000)
    if not text:
        return None
    return {"id": _text(value.get("id"), 100) or _id(prefix), "text": text,
            "origin": value.get("origin") if value.get("origin") in {"user", "ai", "migrated"} else "migrated",
            "locked": bool(value.get("locked")), "evidenceIds": _strings(value.get("evidenceIds"), 50, 100)}


def _normal_result(value):
    value = value if isinstance(value, dict) else {}
    out = {}
    for section in RESULT_SECTIONS:
        out[section] = [x for x in (_normal_result_item(v, "result-%s" % section[:-1])
                                    for v in (value.get(section) if isinstance(value.get(section), list) else [])) if x]
    summary = value.get("summary") if isinstance(value.get("summary"), dict) else {"text": value.get("summary", "")}
    out["summary"] = {"text": _text(summary.get("text"), 1000),
                      "origin": summary.get("origin") if summary.get("origin") in {"user", "ai", "migrated"} else "migrated",
                      "locked": bool(summary.get("locked")),
                      "evidenceIds": _strings(summary.get("evidenceIds"), 50, 100)}
    out["updatedAt"] = int(value.get("updatedAt") or 0)
    return out


def normalize_flow(value, now=None):
    """Return a bounded, internally consistent version-1 flow."""
    now = _now() if now is None else int(now)
    value = value if isinstance(value, dict) else {}
    out = empty_flow(now)
    out["revision"] = max(0, _integer(value.get("revision")))
    out["transcriptGeneration"] = _text(value.get("transcriptGeneration"), 160)
    out["transcriptCursor"] = max(0, _integer(value.get("transcriptCursor")))
    target = value.get("target") if isinstance(value.get("target"), dict) else {}
    out["target"] = {"text": _text(target.get("text"), 1000),
                     "successCriteria": _text(target.get("successCriteria"), 2000),
                     "origin": target.get("origin") if target.get("origin") in {"user", "ai", "migrated"} else "migrated",
                     "locked": bool(target.get("locked")), "updatedAt": _integer(target.get("updatedAt"))}

    agendas, agenda_ids = [], set()
    for pos, raw in enumerate((value.get("agendas") if isinstance(value.get("agendas"), list) else [])[:500]):
        if not isinstance(raw, dict):
            raw = {"title": raw}
        title = _text(raw.get("title"), 300)
        origin = raw.get("origin") if raw.get("origin") in {"user", "ai", "migrated"} else "migrated"
        result_hint = raw.get("result") if isinstance(raw.get("result"), dict) else {}
        detail_hint = "%s %s" % (raw.get("summary", ""), result_hint.get("summary", ""))
        if origin != "user" and _is_non_agenda_placeholder(title, detail_hint):
            continue
        aid = _text(raw.get("id"), 100)
        if not title or not aid or aid in agenda_ids:
            if not title: continue
            aid = _id("agenda")
        agenda_ids.add(aid)
        raw_status = "discussed" if raw.get("status") == "completed" else raw.get("status")
        normalized_result = _normal_result(raw.get("result"))
        resolution = raw.get("resolutionStatus")
        if resolution not in RESOLUTION_STATUSES:
            resolution = _default_resolution_status(title, normalized_result)
        agendas.append({"id": aid, "title": title, "order": _integer(raw.get("order"), (pos + 1) * 1000),
                        "status": raw_status if raw_status in AGENDA_STATUSES else "not_started",
                        "statusLocked": bool(raw.get("statusLocked")), "origin": origin,
                        "resolutionStatus": resolution,
                        "resolutionLocked": bool(raw.get("resolutionLocked")),
                        "resolutionBasis": _text(raw.get("resolutionBasis"), 1000),
                        "resolutionEvidenceIds": _strings(raw.get("resolutionEvidenceIds"), 50, 100),
                        "approval": raw.get("approval") if raw.get("approval") in APPROVALS else ("draft" if origin == "ai" else "accepted"),
                        "current": bool(raw.get("current")), "summary": _text(raw.get("summary"), 1000),
                        "evidenceIds": _strings(raw.get("evidenceIds"), 50, 100),
                        "statusBasis": _text(raw.get("statusBasis"), 1000),
                        "statusEvidenceIds": _strings(raw.get("statusEvidenceIds"), 50, 100),
                        "currentEvidenceIds": _strings(raw.get("currentEvidenceIds"), 50, 100),
                        "questionIds": [], "result": normalized_result})
    agendas.sort(key=lambda x: (x["order"], x["id"]))
    for pos, agenda in enumerate(agendas): agenda["order"] = (pos + 1) * 1000
    current = next((a for a in agendas if a["current"]), None)
    if current is None:
        current = next((a for a in agendas if a["status"] == "discussing"), None)
    for agenda in agendas:
        agenda["current"] = agenda is current
        if agenda is current:
            agenda["status"] = "discussing"
        elif agenda["status"] == "discussing":
            has_discussion = bool(agenda["statusEvidenceIds"] or agenda["currentEvidenceIds"]
                                  or agenda["result"]["summary"]["text"]
                                  or any(agenda["result"][key] for key in RESULT_SECTIONS))
            agenda["status"] = "discussed" if has_discussion else "not_started"
    out["agendas"] = agendas

    questions, question_ids = [], set()
    for pos, raw in enumerate((value.get("questions") if isinstance(value.get("questions"), list) else [])[:5000]):
        if not isinstance(raw, dict): raw = {"text": raw}
        text = _text(raw.get("text") or raw.get("q"), 500)
        qid = _text(raw.get("id"), 100)
        if not text: continue
        if not qid or qid in question_ids: qid = _id("question")
        question_ids.add(qid)
        agenda_id = _text(raw.get("agendaId"), 100) or None
        if agenda_id not in agenda_ids: agenda_id = None
        origin = raw.get("origin") if raw.get("origin") in {"user", "ai", "migrated"} else "migrated"
        questions.append({"id": qid, "agendaId": agenda_id,
                          "order": _integer(raw.get("order"), (pos + 1) * 1000), "text": text,
                          "status": raw.get("status") if raw.get("status") in QUESTION_STATUSES else "queued",
                          "origin": origin,
                          "approval": raw.get("approval") if raw.get("approval") in APPROVALS else ("draft" if origin == "ai" else "accepted"),
                          "reason": _text(raw.get("reason"), 1000), "answer": _text(raw.get("answer"), 2000),
                          "evidenceIds": _strings(raw.get("evidenceIds"), 50, 100),
                          "updatedAt": _integer(raw.get("updatedAt"))})
    questions.sort(key=lambda x: ((x["agendaId"] or ""), x["order"], x["id"]))
    for aid in [None] + [x["id"] for x in agendas]:
        group = [q for q in questions if q["agendaId"] == aid]
        for pos, q in enumerate(group): q["order"] = (pos + 1) * 1000
    qmap = {q["id"]: q for q in questions}
    for agenda in agendas:
        agenda["questionIds"] = [q["id"] for q in questions if q["agendaId"] == agenda["id"]]
    out["questions"] = questions

    hydration = value.get("summaryHydration") if isinstance(value.get("summaryHydration"), dict) else {}
    out["summaryHydration"] = {"mode": hydration.get("mode") if hydration.get("mode") in {"legacy", "per_agenda"} else ""}

    evidence, evidence_ids = [], set()
    for raw in (value.get("evidence") if isinstance(value.get("evidence"), list) else [])[:10000]:
        if not isinstance(raw, dict): continue
        eid = _text(raw.get("id"), 100) or _id("evidence")
        if eid in evidence_ids: continue
        start, end = _integer(raw.get("transcriptStart")), _integer(raw.get("transcriptEnd"))
        text = _text(raw.get("text"), 3000)
        if not text or end <= start: continue
        evidence_ids.add(eid)
        evidence.append({"id": eid, "at": _text(raw.get("at"), 30), "speaker": _text(raw.get("speaker"), 100),
                         "text": text, "transcriptStart": start, "transcriptEnd": end,
                         "transcriptGeneration": _text(raw.get("transcriptGeneration"), 160)})
    out["evidence"] = evidence
    # Broken or model-invented references must never become UI evidence links.
    for question in questions:
        question["evidenceIds"] = [x for x in question["evidenceIds"] if x in evidence_ids]
    for agenda in agendas:
        agenda["statusEvidenceIds"] = [x for x in agenda["statusEvidenceIds"] if x in evidence_ids]
        agenda["currentEvidenceIds"] = [x for x in agenda["currentEvidenceIds"] if x in evidence_ids]
        agenda["resolutionEvidenceIds"] = [x for x in agenda["resolutionEvidenceIds"] if x in evidence_ids]
        for section in RESULT_SECTIONS:
            for item in agenda["result"][section]:
                item["evidenceIds"] = [x for x in item["evidenceIds"] if x in evidence_ids]
        summary = agenda["result"]["summary"]
        summary["evidenceIds"] = [x for x in summary["evidenceIds"] if x in evidence_ids]

    suggestions = []
    suggestion_ids = set()
    for raw in (value.get("suggestions") if isinstance(value.get("suggestions"), list) else [])[:2000]:
        if not isinstance(raw, dict) or raw.get("type") not in SUGGESTION_TYPES: continue
        text = _text(raw.get("text") or raw.get("title"), 2000)
        if not text: continue
        sid = _text(raw.get("id"), 100) or _id("suggestion")
        if sid in suggestion_ids: sid = _id("suggestion")
        suggestion_ids.add(sid)
        suggestions.append({"id": sid, "type": raw["type"],
                            "text": text, "status": raw.get("status") if raw.get("status") in {"pending", "accepted", "deferred", "dismissed"} else "pending",
                            "agendaId": raw.get("agendaId") if raw.get("agendaId") in agenda_ids else None,
                            "targetId": raw.get("targetId") if raw.get("targetId") in (agenda_ids | question_ids) else None,
                            "reason": _text(raw.get("reason"), 1000), "payload": raw.get("payload") if isinstance(raw.get("payload"), dict) else {},
                            "evidenceIds": [x for x in _strings(raw.get("evidenceIds"), 50, 100) if x in evidence_ids], "fingerprint": _text(raw.get("fingerprint"), 160),
                            "updatedAt": _integer(raw.get("updatedAt"))})
    out["suggestions"] = suggestions
    unc = value.get("unclassifiedResults") if isinstance(value.get("unclassifiedResults"), dict) else {}
    out["unclassifiedResults"] = {s: [x for x in (_normal_result_item(v, "unclassified") for v in (unc.get(s) if isinstance(unc.get(s), list) else [])) if x]
                                      for s in RESULT_SECTIONS}
    out["agendaOrderLocked"] = bool(value.get("agendaOrderLocked"))
    out["updatedAt"] = _integer(value.get("updatedAt"), now)
    return out


class MeetingFlowStore:
    def __init__(self, meetings_dir):
        self.meetings_dir = os.path.realpath(os.fspath(meetings_dir))
        self._locks = {}
        self._locks_guard = threading.Lock()

    def _lock(self, sid):
        with self._locks_guard:
            return self._locks.setdefault(sid, threading.RLock())

    def _dir(self, sid):
        sid = str(sid or "")
        if not _SID_RE.fullmatch(sid) or sid in {".", ".."}:
            raise ValidationError("会議IDが不正です")
        path = os.path.realpath(os.path.join(self.meetings_dir, sid))
        if os.path.commonpath([self.meetings_dir, path]) != self.meetings_dir:
            raise ValidationError("会議IDが不正です")
        if not os.path.isdir(path):
            raise ValidationError("会議が見つかりません")
        return path

    @staticmethod
    def _read(path):
        try:
            with open(path, encoding="utf-8") as f: return json.load(f)
        except (OSError, ValueError): return {}

    @staticmethod
    def _read_flow(path):
        try:
            with open(path, encoding="utf-8") as f:
                value = json.load(f)
        except (OSError, ValueError) as exc:
            raise ValidationError("meeting-flow.jsonを読み取れません。原本は上書きせず保全しました") from exc
        if not isinstance(value, dict):
            raise ValidationError("meeting-flow.jsonの形式が不正です")
        try: version = int(value.get("version") or 1)
        except (TypeError, ValueError): raise ValidationError("meeting-flow.jsonのバージョンが不正です")
        if version > SCHEMA_VERSION:
            raise ValidationError("このmeeting-flow.jsonはより新しいLiveMTGで作成されています")
        return value

    @staticmethod
    def _atomic(path, value):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".meeting-flow-", suffix=".tmp", dir=os.path.dirname(path))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(value, f, ensure_ascii=False, indent=2)
                f.flush(); os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            try: os.unlink(tmp)
            except FileNotFoundError: pass

    def load(self, sid):
        directory = self._dir(sid)
        with self._lock(sid):
            path = os.path.join(directory, "meeting-flow.json")
            if not os.path.isfile(path):
                flow = self._migrate(directory)
                self._atomic(path, flow)
            else:
                raw = self._read_flow(path)
                flow = normalize_flow(raw)
                if flow != raw: self._atomic(path, flow)
            return copy.deepcopy(flow)

    def hydrate_summary(self, sid, summary, finalized=False):
        """Rebuild the main progress board from a historical/final summary.

        This is used for meetings created before ``meeting-flow.json`` existed
        and after polishing, where ``data.json``/``final.json`` is the more
        complete source. Explicit user edits remain authoritative.
        """
        directory = self._dir(sid)
        summary = summary if isinstance(summary, dict) else {}
        with self._lock(sid):
            current = self.load(sid)
            nested = summary.get("meetingFlow")
            if not isinstance(nested, dict):
                nested = summary.get("meeting_flow") if isinstance(summary.get("meeting_flow"), dict) else {}
            source_rows = nested.get("agendas") if isinstance(nested.get("agendas"), list) else []
            generated = []

            for raw in source_rows:
                if not isinstance(raw, dict):
                    raw = {"title": raw}
                title = _summary_text(raw.get("title"), 300)
                if not title:
                    continue
                result = raw.get("result") if isinstance(raw.get("result"), dict) else raw
                if _is_non_agenda_placeholder(title, _summary_text(result.get("summary"), 1000)):
                    continue
                generated.append({
                    "title": title,
                    "status": raw.get("status"),
                    "resolutionStatus": raw.get("resolutionStatus"),
                    "resolutionBasis": _summary_text(raw.get("resolutionBasis"), 1000),
                    "summary": _summary_text(result.get("summary"), 1000),
                    "answers": _summary_strings(result.get("answers") or result.get("points")),
                    "decisions": _summary_strings(result.get("decisions")),
                    "actions": _summary_strings(result.get("actions") or result.get("todos")),
                    "unresolved": _summary_strings(result.get("unresolved") or result.get("open")),
                })

            # Older final/data files do not contain per-agenda results.  Attach
            # the meeting-wide result to the first agenda so the main board is
            # immediately useful instead of displaying empty cards.
            if not generated:
                generated = [{"title": x} for x in _summary_strings(summary.get("agenda"), 300)
                             if not _is_non_agenda_placeholder(x)]
            overall = {
                "summary": _summary_text(summary.get("summary"), 1000),
                "answers": _summary_strings(summary.get("points")),
                "decisions": _summary_strings(summary.get("decisions")),
                "actions": _summary_strings(summary.get("todos")),
                "unresolved": _summary_strings(summary.get("open")),
            }
            legacy_multi = not source_rows and len(generated) > 1
            # A prior transcript rebuild is richer than an old final.json that
            # only has meeting-wide arrays.  Never replace it with legacy data.
            if legacy_multi and current.get("summaryHydration", {}).get("mode") == "per_agenda":
                return copy.deepcopy(current)
            if generated and any(overall.values()) and not source_rows and not legacy_multi:
                generated[0].update(overall)
            if not generated and any(overall.values()):
                generated = [{"title": "会議全体の整理", **overall}]
            if not generated:
                return copy.deepcopy(current)

            old_agendas = list(current.get("agendas") or [])
            used_old, id_map, agendas = set(), {}, []
            for pos, row in enumerate(generated):
                match = next((a for a in old_agendas if a["id"] not in used_old
                              and self._similar(row["title"], [a.get("title", "")])), None)
                if match:
                    used_old.add(match["id"]); aid = match["id"]; id_map[match["id"]] = aid
                else:
                    aid = _stable_id("agenda", sid, pos, row["title"])
                raw_status = "discussed" if row.get("status") == "completed" else row.get("status")
                status = raw_status if raw_status in AGENDA_STATUSES else ("discussed" if finalized else "not_started")
                if match and match.get("statusLocked"):
                    status = match.get("status", status)
                result = _normal_result({
                    "summary": {"text": row.get("summary", ""), "origin": "migrated"},
                    "answers": [{"id": _stable_id("result-answer", sid, row["title"], x), "text": x, "origin": "migrated"} for x in row.get("answers", [])],
                    "decisions": [{"id": _stable_id("result-decision", sid, row["title"], x), "text": x, "origin": "migrated"} for x in row.get("decisions", [])],
                    "actions": [{"id": _stable_id("result-action", sid, row["title"], x), "text": x, "origin": "migrated"} for x in row.get("actions", [])],
                    "unresolved": [{"id": _stable_id("result-unresolved", sid, row["title"], x), "text": x, "origin": "migrated"} for x in row.get("unresolved", [])],
                    "updatedAt": 0,
                })
                # A manually locked result is never discarded by a rebuild.
                if match:
                    old_result = match.get("result") if isinstance(match.get("result"), dict) else {}
                    old_summary = old_result.get("summary") if isinstance(old_result.get("summary"), dict) else {}
                    if old_summary.get("locked"):
                        result["summary"] = copy.deepcopy(old_summary)
                    for section in RESULT_SECTIONS:
                        locked = [copy.deepcopy(x) for x in old_result.get(section, [])
                                  if isinstance(x, dict) and x.get("locked")]
                        known = [x.get("text", "") for x in result[section]]
                        result[section].extend(x for x in locked if not self._similar(x.get("text", ""), known))
                resolution = row.get("resolutionStatus")
                if resolution not in RESOLUTION_STATUSES:
                    resolution = _default_resolution_status(row["title"], result)
                if match and match.get("resolutionLocked"):
                    resolution = match.get("resolutionStatus", resolution)
                agendas.append({
                    "id": aid, "title": row["title"], "order": (pos + 1) * 1000,
                    "status": status, "statusLocked": bool(match and match.get("statusLocked")),
                    "resolutionStatus": resolution,
                    "resolutionLocked": bool(match and match.get("resolutionLocked")),
                    "resolutionBasis": (match.get("resolutionBasis", "") if match and match.get("resolutionLocked")
                                        else row.get("resolutionBasis", "")),
                    "resolutionEvidenceIds": (copy.deepcopy(match.get("resolutionEvidenceIds", []))
                                              if match and match.get("resolutionLocked") else []),
                    "origin": "user" if match and match.get("origin") == "user" else "migrated",
                    "approval": "accepted", "current": False,
                    "summary": row.get("summary", ""), "evidenceIds": [], "statusBasis": "",
                    "statusEvidenceIds": [], "currentEvidenceIds": [], "questionIds": [], "result": result,
                })

            # Preserve unmatched agendas explicitly created by the user.
            for old in old_agendas:
                if old["id"] in used_old or old.get("origin") != "user":
                    continue
                copied = copy.deepcopy(old); copied["current"] = False
                copied["order"] = (len(agendas) + 1) * 1000
                agendas.append(copied); id_map[old["id"]] = copied["id"]

            questions = []
            for question in current.get("questions") or []:
                if question.get("origin") != "user" and question.get("approval") != "dismissed":
                    continue
                copied = copy.deepcopy(question)
                copied["agendaId"] = id_map.get(question.get("agendaId"))
                questions.append(copied)
            rebuilt = empty_flow()
            rebuilt.update({
                "revision": current.get("revision", 0),
                "transcriptGeneration": current.get("transcriptGeneration", ""),
                "transcriptCursor": current.get("transcriptCursor", 0),
                "target": copy.deepcopy(current.get("target") or rebuilt["target"]),
                "agendas": agendas, "questions": questions,
                "suggestions": [copy.deepcopy(x) for x in current.get("suggestions") or []
                                if x.get("status") == "dismissed"],
                "evidence": copy.deepcopy(current.get("evidence") or []),
                "summaryHydration": {"mode": "per_agenda" if source_rows else "legacy"},
                "agendaOrderLocked": current.get("agendaOrderLocked", False),
                "updatedAt": current.get("updatedAt", 0),
            })
            rebuilt = normalize_flow(rebuilt)
            if rebuilt == normalize_flow(current):
                return copy.deepcopy(current)
            return self._save_changed(directory, rebuilt)

    def _migrate(self, directory):
        now, flow = _now(), empty_flow()
        meta = self._read(os.path.join(directory, "meta.json"))
        strategy = self._read(os.path.join(directory, "strategy.json"))
        data = self._read(os.path.join(directory, "data.json"))
        board = strategy.get("board") if isinstance(strategy.get("board"), dict) else {}
        target = _text(board.get("outcome"), 1000) or _text(meta.get("goal"), 1000)
        if target:
            flow["target"].update({"text": target, "origin": "migrated", "locked": False, "updatedAt": now})
        for pos, q in enumerate(board.get("questions") if isinstance(board.get("questions"), list) else []):
            text = _text(q, 500)
            if text:
                flow["questions"].append({"id": _id("question"), "agendaId": None, "order": (pos + 1) * 1000,
                                          "text": text, "status": "queued", "origin": "migrated", "approval": "accepted",
                                          "reason": "事前準備から移行", "answer": "", "evidenceIds": [], "updatedAt": now})
        for pos, item in enumerate(data.get("agenda") if isinstance(data.get("agenda"), list) else []):
            title = _text(item.get("title") if isinstance(item, dict) else item, 300)
            if title:
                flow["agendas"].append({"id": _id("agenda"), "title": title, "order": (pos + 1) * 1000,
                                        "status": "not_started", "statusLocked": False, "origin": "ai", "approval": "draft",
                                        "resolutionStatus": _default_resolution_status(title), "resolutionLocked": False,
                                        "resolutionBasis": "", "resolutionEvidenceIds": [],
                                        "current": False, "summary": "", "questionIds": [], "result": _normal_result({})})
        mapping = {"decisions": "decisions", "todos": "actions", "open": "unresolved"}
        for old_key, section in mapping.items():
            for item in data.get(old_key) if isinstance(data.get(old_key), list) else []:
                text = _text(item.get("text") if isinstance(item, dict) else item, 1000)
                if not text: continue
                entry = _normal_result_item({"text": text, "origin": "migrated"}, "unclassified")
                # The legacy arrays have no agenda id.  Even a one-agenda
                # meeting is not sufficient evidence to invent a relationship.
                flow["unclassifiedResults"][section].append(entry)
        flow["revision"] = 1 if (target or flow["questions"] or flow["agendas"] or any(flow["unclassifiedResults"].values())) else 0
        flow["updatedAt"] = now
        return normalize_flow(flow, now)

    def _save_changed(self, directory, flow):
        flow = normalize_flow(flow)
        flow["revision"] += 1; flow["updatedAt"] = _now()
        self._atomic(os.path.join(directory, "meeting-flow.json"), flow)
        return copy.deepcopy(flow)

    def apply_action(self, sid, revision, action, payload):
        directory = self._dir(sid)
        payload = payload if isinstance(payload, dict) else {}
        with self._lock(sid):
            flow = self.load(sid)
            try: expected = int(revision)
            except (TypeError, ValueError): raise ValidationError("更新番号が不正です")
            if expected != flow["revision"]: raise RevisionConflict(flow)
            self._manual(flow, str(action or ""), payload)
            return self._save_changed(directory, flow)

    def close_current(self, sid):
        """Close the transient current topic without changing agreement state."""
        directory = self._dir(sid)
        with self._lock(sid):
            flow = self.load(sid)
            changed = False
            for agenda in flow["agendas"]:
                if agenda.get("current") or agenda.get("status") == "discussing":
                    agenda["current"] = False
                    agenda["currentEvidenceIds"] = []
                    if agenda.get("status") == "discussing":
                        agenda["status"] = "discussed"
                    changed = True
            return self._save_changed(directory, flow) if changed else flow

    @staticmethod
    def _find(rows, rid, label):
        found = next((x for x in rows if x.get("id") == rid), None)
        if not found: raise ValidationError("%sが見つかりません" % label)
        return found

    def _manual(self, flow, action, p):
        now = _now(); agendas, questions = flow["agendas"], flow["questions"]
        if action == "target.update":
            target = flow["target"]
            if "text" in p: target["text"] = _text(p.get("text"), 1000, "着地点")
            if "successCriteria" in p: target["successCriteria"] = _text(p.get("successCriteria"), 2000, "成功条件")
            target.update({"origin": "user", "locked": True, "updatedAt": now}); return
        if action == "agenda.create":
            title = _text(p.get("title"), 300, "議題", True)
            if self._similar(title, [a["title"] for a in agendas]): raise ValidationError("同じ内容の議題があります")
            agendas.append({"id": _id("agenda"), "title": title, "order": (len(agendas)+1)*1000,
                            "status": "not_started", "statusLocked": False, "origin": "user", "approval": "accepted",
                            "resolutionStatus": _default_resolution_status(title), "resolutionLocked": False,
                            "resolutionBasis": "", "resolutionEvidenceIds": [],
                            "current": False, "summary": "", "evidenceIds": [], "statusBasis": "",
                            "statusEvidenceIds": [], "currentEvidenceIds": [], "questionIds": [], "result": _normal_result({})}); return
        if action in {"agenda.update", "agenda.accept", "agenda.delete"}:
            agenda = self._find(agendas, p.get("agendaId") or p.get("id"), "議題")
            if action == "agenda.delete":
                if agenda.get("origin") == "ai" and agenda.get("approval") == "draft":
                    fp = self._fingerprint("agenda", agenda["title"], "")
                    flow["suggestions"].append({"id": _id("suggestion"), "type": "agenda_proposal",
                                                "text": agenda["title"], "status": "dismissed", "agendaId": None,
                                                "targetId": None, "reason": "", "payload": {}, "evidenceIds": [],
                                                "fingerprint": fp, "updatedAt": now})
                agendas.remove(agenda)
                for q in questions:
                    if q["agendaId"] == agenda["id"]: q["agendaId"] = None
                return
            if action == "agenda.accept": agenda["approval"] = "accepted"; return
            if "title" in p:
                title = _text(p.get("title"), 300, "議題", True)
                if self._similar(title, [a["title"] for a in agendas if a["id"] != agenda["id"]]):
                    raise ValidationError("同じ内容の議題があります")
                agenda["title"] = title; agenda["origin"] = "user"; agenda["approval"] = "accepted"
            if "status" in p:
                if p["status"] not in AGENDA_STATUSES: raise ValidationError("議題の状態が不正です")
                status = p["status"]
                if status == "discussing":
                    for other in agendas:
                        other["current"] = other is agenda
                        if other is not agenda and other["status"] == "discussing":
                            other["status"] = "discussed"
                    agenda["current"] = True
                else:
                    agenda["current"] = False
                agenda["status"] = status; agenda["statusLocked"] = True
            if "resolutionStatus" in p:
                if p["resolutionStatus"] not in RESOLUTION_STATUSES: raise ValidationError("合意の状態が不正です")
                agenda["resolutionStatus"] = p["resolutionStatus"]
                agenda["resolutionLocked"] = True
                agenda["resolutionBasis"] = "ユーザーが手動で設定"
                agenda["resolutionEvidenceIds"] = []
            if "current" in p:
                for a in agendas: a["current"] = False
                agenda["current"] = bool(p["current"])
            return
        if action == "agenda.reorder":
            ids = p.get("agendaIds")
            if (not isinstance(ids, list) or not all(isinstance(x, str) for x in ids)
                    or len(ids) != len(agendas) or len(ids) != len(set(ids))
                    or set(ids) != {a["id"] for a in agendas}):
                raise ValidationError("議題の並び順が不正です")
            by_id = {a["id"]: a for a in agendas}; flow["agendas"][:] = [by_id[x] for x in ids]
            for pos, a in enumerate(flow["agendas"]): a["order"] = (pos+1)*1000
            flow["agendaOrderLocked"] = True; return
        if action == "agenda.merge":
            ids = p.get("agendaIds")
            if (not isinstance(ids, list) or not all(isinstance(x, str) for x in ids)
                    or len(ids) < 2 or len(ids) != len(set(ids))):
                raise ValidationError("統合する議題を2件以上選んでください")
            selected = [self._find(agendas, x, "議題") for x in ids]
            target = selected[0]; target["title"] = _text(p.get("title") or target["title"], 300, "議題", True)
            target.update({"origin": "user", "approval": "accepted"})
            for source in selected[1:]:
                for q in questions:
                    if q["agendaId"] == source["id"]: q["agendaId"] = target["id"]
                for section in RESULT_SECTIONS:
                    known = {x["text"] for x in target["result"][section]}
                    target["result"][section].extend(x for x in source["result"][section] if x["text"] not in known)
                agendas.remove(source)
            return
        if action == "question.create":
            agenda_id = p.get("agendaId") or None
            if agenda_id is not None: self._find(agendas, agenda_id, "議題")
            text = _text(p.get("text"), 500, "質問", True)
            same = [q["text"] for q in questions if q["agendaId"] == agenda_id]
            if self._similar(text, same): raise ValidationError("同じ内容の質問があります")
            questions.append({"id": _id("question"), "agendaId": agenda_id, "order": 999999, "text": text,
                              "status": p.get("status") if p.get("status") in QUESTION_STATUSES else "queued",
                              "origin": "user", "approval": "accepted", "reason": _text(p.get("reason"), 1000),
                              "answer": "", "evidenceIds": [], "updatedAt": now}); return
        if action in {"question.update", "question.accept", "question.dismiss", "question.move"}:
            q = self._find(questions, p.get("questionId") or p.get("id"), "質問")
            if action == "question.accept": q["approval"] = "accepted"; return
            if action == "question.dismiss": q["status"] = "dismissed"; q["approval"] = "dismissed"; q["updatedAt"] = now; return
            if action == "question.move":
                aid = p.get("agendaId") or None
                if aid is not None: self._find(agendas, aid, "議題")
                q["agendaId"] = aid; q["updatedAt"] = now; return
            if "text" in p:
                text = _text(p.get("text"), 500, "質問", True)
                if self._similar(text, [other["text"] for other in questions
                                        if other["id"] != q["id"] and other["agendaId"] == q["agendaId"]]):
                    raise ValidationError("同じ内容の質問があります")
                q["text"] = text; q["origin"] = "user"; q["approval"] = "accepted"
            if "status" in p:
                if p["status"] not in QUESTION_STATUSES: raise ValidationError("質問の状態が不正です")
                q["status"] = p["status"]
            if "answer" in p: q["answer"] = _text(p.get("answer"), 2000, "回答")
            if "reason" in p: q["reason"] = _text(p.get("reason"), 1000, "理由")
            q["updatedAt"] = now; return
        if action == "question.reorder":
            aid = p.get("agendaId") or None
            if aid is not None: self._find(agendas, aid, "議題")
            ids = p.get("questionIds"); group = [q for q in questions if q["agendaId"] == aid]
            if (not isinstance(ids, list) or not all(isinstance(x, str) for x in ids)
                    or len(ids) != len(group) or len(ids) != len(set(ids))
                    or set(ids) != {q["id"] for q in group}):
                raise ValidationError("質問の並び順が不正です")
            order = {qid: (pos+1)*1000 for pos, qid in enumerate(ids)}
            for q in group: q["order"] = order[q["id"]]
            return
        if action == "result.update":
            agenda = self._find(agendas, p.get("agendaId"), "議題"); section = p.get("section") or p.get("kind")
            if section == "summary":
                agenda["result"]["summary"] = {"text": _text(p.get("text"), 1000, "結果"), "origin": "user",
                                                   "locked": True, "evidenceIds": _strings(p.get("evidenceIds"), 50, 100)}
            elif section in RESULT_SECTIONS:
                rows = agenda["result"][section]; item_id = p.get("itemId") or p.get("id")
                if p.get("remove"):
                    rows.remove(self._find(rows, item_id, "結果"))
                elif item_id:
                    item = self._find(rows, item_id, "結果"); item.update({"text": _text(p.get("text"), 1000, "結果", True), "origin": "user", "locked": True})
                else:
                    rows.append(_normal_result_item({"text": _text(p.get("text"), 1000, "結果", True), "origin": "user", "locked": True,
                                                     "evidenceIds": p.get("evidenceIds")}, "result"))
            else: raise ValidationError("結果の種類が不正です")
            agenda["result"]["updatedAt"] = now; return
        if action in {"suggestion.accept", "suggestion.defer", "suggestion.dismiss"}:
            suggestion = self._find(flow["suggestions"], p.get("suggestionId") or p.get("id"), "AI提案")
            if action == "suggestion.accept":
                payload = suggestion.get("payload") or {}
                if suggestion["type"] == "agenda_proposal" and not payload.get("title"):
                    raise ValidationError("この提案は自動採用できないため、議題を手動で編集してください")
                if suggestion["type"] == "question_proposal" and not payload.get("text"):
                    raise ValidationError("この提案は自動採用できないため、質問を手動で追加してください")
            suggestion["status"] = action.split(".")[1] + ("red" if action.endswith("defer") else "ed")
            if action == "suggestion.defer": suggestion["status"] = "deferred"
            if action == "suggestion.dismiss": suggestion["status"] = "dismissed"
            suggestion["updatedAt"] = now
            if action == "suggestion.accept": self._accept_suggestion(flow, suggestion)
            return
        raise ValidationError("未対応の操作です")

    def _accept_suggestion(self, flow, suggestion):
        payload = suggestion.get("payload") or {}
        if suggestion["type"] == "agenda_proposal" and payload.get("title"):
            self._manual(flow, "agenda.create", {"title": payload["title"]})
        elif suggestion["type"] == "question_proposal" and payload.get("text"):
            self._manual(flow, "question.create", {"agendaId": payload.get("agendaId") or suggestion.get("agendaId"),
                                                    "text": payload["text"], "reason": suggestion.get("reason", "")})

    @staticmethod
    def _similar(text, existing):
        key = re.sub(r"[\s\W_]+", "", text).lower()
        for old in existing:
            other = re.sub(r"[\s\W_]+", "", str(old)).lower()
            if key == other or (min(len(key), len(other)) >= 6 and SequenceMatcher(None, key, other).ratio() >= .88): return True
        return False

    def apply_ai_diff(self, sid, diff, transcript_text="", transcript_generation=None):
        directory = self._dir(sid); diff = diff if isinstance(diff, dict) else {}
        with self._lock(sid):
            flow = self.load(sid); before = copy.deepcopy(flow); now = _now()
            old_generation = flow["transcriptGeneration"]
            generation = _text(transcript_generation, 160) if transcript_generation is not None else old_generation
            valid_evidence = self._add_ai_evidence(flow, diff.get("evidence"), str(transcript_text or ""), generation)
            agenda_creates = diff.get("agendaCreates") if isinstance(diff.get("agendaCreates"), list) else diff.get("agendaProposals")
            question_creates = diff.get("questionCreates") if isinstance(diff.get("questionCreates"), list) else diff.get("questionProposals")
            self._add_ai_drafts(flow, agenda_creates, question_creates, now)
            agenda_map = {a["id"]: a for a in flow["agendas"]}; question_map = {q["id"]: q for q in flow["questions"]}
            target_update = diff.get("targetUpdate") if isinstance(diff.get("targetUpdate"), dict) else None
            if target_update:
                origin = target_update.get("origin") if target_update.get("origin") in {"user", "ai"} else "ai"
                # A verified explicit preparation utterance is a user edit.  An
                # inferred AI target may fill an empty/unlocked target only.
                if origin == "user" or not flow["target"]["locked"]:
                    text = _text(target_update.get("text"), 1000)
                    if text:
                        proposed = {"text": text,
                                    "successCriteria": _text(target_update.get("successCriteria"), 2000),
                                    "origin": origin, "locked": origin == "user" and bool(target_update.get("locked", True))}
                        if any(flow["target"].get(k) != v for k, v in proposed.items()):
                            flow["target"].update(proposed); flow["target"]["updatedAt"] = now
            for row in diff.get("agendaUpdates") if isinstance(diff.get("agendaUpdates"), list) else []:
                if not isinstance(row, dict): continue
                agenda = agenda_map.get(row.get("agendaId"))
                if not agenda: continue
                if row.get("approval") == "accepted": agenda["approval"] = "accepted"
                if row.get("origin") == "user": agenda["origin"] = "user"
            for row in diff.get("questionUpdates") if isinstance(diff.get("questionUpdates"), list) else []:
                if not isinstance(row, dict): continue
                q = question_map.get(row.get("questionId"))
                if q and row.get("approval") == "accepted":
                    q["approval"] = "accepted"
                    if row.get("origin") == "user": q["origin"] = "user"
            current = diff.get("currentAgendaId")
            current_evidence_ids = set(_strings(diff.get("currentAgendaEvidenceIds"), 50, 100))
            if current in agenda_map and bool(current_evidence_ids & valid_evidence):
                for a in flow["agendas"]:
                    selected = a["id"] == current
                    if selected and a.get("statusLocked") and a.get("status") in {"not_started", "deferred"}:
                        selected = False
                    if a.get("current") and not selected and a.get("status") == "discussing":
                        a["status"] = "discussed"
                    a["current"] = selected
                    a["currentEvidenceIds"] = list(current_evidence_ids) if selected else []
                    if selected:
                        a["status"] = "discussing"
            for row in diff.get("agendaStatusUpdates") if isinstance(diff.get("agendaStatusUpdates"), list) else []:
                if not isinstance(row, dict): continue
                agenda = agenda_map.get(row.get("agendaId")); status = row.get("status")
                refs = [x for x in _strings(row.get("evidenceIds"), 50, 100) if x in valid_evidence]
                old = agenda.get("status") if agenda else None
                if (agenda and not agenda["statusLocked"] and status in AGENDA_STATUSES
                        and status in _AGENDA_AI_TRANSITIONS.get(old, set()) and (status == old or refs)):
                    agenda["status"] = status
                    if refs:
                        agenda["statusBasis"] = _text(row.get("basis"), 1000)
                        agenda["statusEvidenceIds"] = refs
                    if status != "discussing":
                        agenda["current"] = False
            for row in diff.get("agendaResolutionUpdates") if isinstance(diff.get("agendaResolutionUpdates"), list) else []:
                if not isinstance(row, dict): continue
                agenda = agenda_map.get(row.get("agendaId")); status = row.get("status")
                refs = [x for x in _strings(row.get("evidenceIds"), 50, 100) if x in valid_evidence]
                if (not agenda or agenda.get("resolutionLocked") or status not in RESOLUTION_STATUSES or not refs):
                    continue
                agenda["resolutionStatus"] = status
                agenda["resolutionBasis"] = _text(row.get("basis"), 1000)
                agenda["resolutionEvidenceIds"] = refs
            for row in diff.get("questionUpdates") if isinstance(diff.get("questionUpdates"), list) else []:
                if not isinstance(row, dict): continue
                q = question_map.get(row.get("questionId")); status = row.get("status")
                evid = [x for x in _strings(row.get("evidenceIds"), 50, 100) if x in valid_evidence]
                if not q or status not in QUESTION_STATUSES or status not in _QUESTION_AI_TRANSITIONS.get(q["status"], set()): continue
                if status != q["status"] and not evid: continue
                new_answer = _text(row.get("answer"), 2000) if row.get("answer") and evid else q["answer"]
                new_evidence = list(dict.fromkeys(q["evidenceIds"] + evid)) if evid else q["evidenceIds"]
                if q["status"] != status or q["answer"] != new_answer or q["evidenceIds"] != new_evidence:
                    q["status"] = status; q["answer"] = new_answer; q["evidenceIds"] = new_evidence; q["updatedAt"] = now
            for row in diff.get("resultUpdates") if isinstance(diff.get("resultUpdates"), list) else []:
                if not isinstance(row, dict): continue
                agenda = agenda_map.get(row.get("agendaId")); section = row.get("section") or row.get("kind"); text = _text(row.get("text"), 1000)
                evid = [x for x in _strings(row.get("evidenceIds"), 50, 100) if x in valid_evidence]
                if not agenda or not text or section not in RESULT_SECTIONS | {"summary"}: continue
                if not evid: continue
                result_changed = False
                if section == "summary":
                    if not agenda["result"]["summary"]["locked"]:
                        proposed = {"text": text, "origin": "ai", "locked": False, "evidenceIds": evid}
                        if agenda["result"]["summary"] != proposed:
                            agenda["result"]["summary"] = proposed; result_changed = True
                else:
                    rows = agenda["result"][section]
                    if not self._similar(text, [x["text"] for x in rows]):
                        rows.append(_normal_result_item({"text": text, "origin": "ai", "evidenceIds": evid}, "result")); result_changed = True
                        if section == "decisions" and not agenda.get("resolutionLocked"):
                            agenda["resolutionStatus"] = "agreed"
                            agenda["resolutionBasis"] = text
                            agenda["resolutionEvidenceIds"] = evid
                if result_changed: agenda["result"]["updatedAt"] = now
            self._add_suggestions(flow, diff.get("suggestions"), now)
            if transcript_generation is not None:
                requested_cursor = _integer(diff.get("transcriptCursor"), len(str(transcript_text or "")))
                requested_cursor = max(0, min(len(str(transcript_text or "")), requested_cursor))
                flow["transcriptGeneration"] = generation
                flow["transcriptCursor"] = max(flow["transcriptCursor"], requested_cursor) if generation == old_generation else requested_cursor
            flow = normalize_flow(flow)
            if flow == normalize_flow(before): return copy.deepcopy(before)
            return self._save_changed(directory, flow)

    def _add_ai_evidence(self, flow, rows, transcript, generation):
        valid = {e["id"] for e in flow["evidence"] if e.get("transcriptGeneration", "") == generation}
        if not isinstance(rows, list): return valid
        for row in rows[:100]:
            if not isinstance(row, dict): continue
            try: start, end = int(row.get("transcriptStart")), int(row.get("transcriptEnd"))
            except (TypeError, ValueError): continue
            if start < 0 or end <= start or end > len(transcript): continue
            exact = transcript[start:end]
            supplied = re.sub(r"\s+", " ", str(row.get("text") or "")).strip()
            if supplied and re.sub(r"\s+", " ", exact).strip() != supplied: continue
            eid = _text(row.get("id"), 100) or _id("evidence")
            if eid in valid: continue
            flow["evidence"].append({"id": eid, "at": _text(row.get("at"), 30), "speaker": _text(row.get("speaker"), 100),
                                     "text": exact, "transcriptStart": start, "transcriptEnd": end,
                                     "transcriptGeneration": generation})
            valid.add(eid)
        return valid

    def _add_ai_drafts(self, flow, agenda_rows, question_rows, now):
        rejected = [s.get("fingerprint") for s in flow["suggestions"] if s.get("status") == "dismissed"]
        rejected_agendas = [s.get("text", "") for s in flow["suggestions"]
                            if s.get("status") == "dismissed" and s.get("type") == "agenda_proposal"]
        for row in agenda_rows if isinstance(agenda_rows, list) else []:
            if not isinstance(row, dict): continue
            title = _text(row.get("title"), 300)
            fingerprint = self._fingerprint("agenda", title, row.get("basis"))
            origin = row.get("origin") if row.get("origin") in {"user", "ai"} else "ai"
            if (not title or (origin != "user" and (fingerprint in rejected or self._similar(title, rejected_agendas)))
                    or self._similar(title, [a["title"] for a in flow["agendas"]])): continue
            aid = _text(row.get("id"), 100) or _id("agenda")
            if aid in {a["id"] for a in flow["agendas"]}: continue
            approval = row.get("approval") if row.get("approval") in APPROVALS else ("accepted" if origin == "user" else "draft")
            flow["agendas"].append({"id": aid, "title": title, "order": _integer(row.get("order"), 999999), "status": "not_started",
                                    "statusLocked": False, "origin": origin, "approval": approval, "current": False,
                                    "resolutionStatus": (row.get("resolutionStatus") if row.get("resolutionStatus") in RESOLUTION_STATUSES
                                                         else _default_resolution_status(title)),
                                    "resolutionLocked": False, "resolutionBasis": "", "resolutionEvidenceIds": [],
                                    "summary": "", "evidenceIds": _strings(row.get("evidenceIds"), 50, 100),
                                    "statusBasis": "", "statusEvidenceIds": [], "currentEvidenceIds": [],
                                    "questionIds": [], "result": _normal_result({})})
        agenda_ids = {a["id"] for a in flow["agendas"]}
        # 却下履歴：提案として却下されたもの＋不要化された仮質問。fingerprintは
        # kind/basisの揺れで一致しないため、本文の類似で照合する。
        dismissed_questions = ([s.get("text", "") for s in flow["suggestions"]
                                if s.get("status") == "dismissed" and s.get("type") == "question_proposal"]
                               + [q["text"] for q in flow["questions"] if q.get("approval") == "dismissed"])
        for row in question_rows if isinstance(question_rows, list) else []:
            if not isinstance(row, dict): continue
            text = _text(row.get("text") or row.get("q"), 500); aid = row.get("agendaId") if row.get("agendaId") in agenda_ids else None
            origin = row.get("origin") if row.get("origin") in {"user", "ai"} else "ai"
            fingerprint = self._fingerprint("question", text, row.get("basis"))
            # 却下済みと同内容のAI再提案は、紐づけ先の議題を変えても復活させない。
            # ユーザー明示分（origin=user）は却下履歴に妨げられない（議題側と同じ扱い）。
            if (not text
                    or (origin != "user" and (fingerprint in rejected
                                              or self._similar(text, dismissed_questions)))
                    or self._similar(text, [q["text"] for q in flow["questions"] if q["agendaId"] == aid])): continue
            qid = _text(row.get("id"), 100) or _id("question")
            if qid in {q["id"] for q in flow["questions"]}: continue
            approval = row.get("approval") if row.get("approval") in APPROVALS else ("accepted" if origin == "user" else "draft")
            flow["questions"].append({"id": qid, "agendaId": aid, "order": _integer(row.get("order"), 999999), "text": text, "status": "queued",
                                      "origin": origin, "approval": approval, "reason": _text(row.get("reason") or row.get("basis"), 1000),
                                      "answer": "", "evidenceIds": _strings(row.get("evidenceIds"), 50, 100), "updatedAt": now})

    def _add_suggestions(self, flow, rows, now):
        if not isinstance(rows, list): return
        known = {s.get("fingerprint") for s in flow["suggestions"]}
        dismissed_texts = {}
        for s in flow["suggestions"]:
            if s.get("status") == "dismissed":
                dismissed_texts.setdefault(s.get("type"), []).append(s.get("text", ""))
        for row in rows[:50]:
            if not isinstance(row, dict) or row.get("type") not in SUGGESTION_TYPES: continue
            text = _text(row.get("text") or row.get("title"), 2000)
            fp = self._fingerprint(row["type"], text, row.get("basis") or row.get("reason"))
            if not text or fp in known: continue
            # fingerprintはreasonの言い回しが変わるだけで別物になるため、
            # 却下済みは同種typeの本文類似でも再登録を防ぐ。
            if self._similar(text, dismissed_texts.get(row["type"], [])): continue
            suggestion_id = _text(row.get("id"), 100) or _id("suggestion")
            if suggestion_id in {s["id"] for s in flow["suggestions"]}: continue
            target_id = row.get("targetId") or row.get("agendaId")
            agenda_ids = {a["id"] for a in flow["agendas"]}
            question_ids = {q["id"] for q in flow["questions"]}
            if target_id not in agenda_ids | question_ids: target_id = None
            flow["suggestions"].append({"id": suggestion_id, "type": row["type"], "text": text, "status": "pending",
                                        "agendaId": target_id if target_id in agenda_ids else None, "targetId": target_id,
                                        "reason": _text(row.get("reason") or row.get("basis"), 1000),
                                        "payload": row.get("payload") if isinstance(row.get("payload"), dict) else {},
                                        "evidenceIds": _strings(row.get("evidenceIds"), 50, 100), "fingerprint": fp, "updatedAt": now})
            known.add(fp)

    @staticmethod
    def _fingerprint(kind, text, basis):
        normalized = re.sub(r"[\s\W_]+", "", str(text or "")).lower()
        base = re.sub(r"[\s\W_]+", "", str(basis or "")).lower()
        # normalize_flowの160文字制約内に必ず収める。長い調査結果を
        # 本文のままfingerprint化すると、提案自体が保存されなかった。
        digest = hashlib.sha256((normalized + "\u241f" + base).encode("utf-8")).hexdigest()
        return "%s:%s" % (kind, digest)


__all__ = ["MeetingFlowStore", "FlowError", "ValidationError", "RevisionConflict",
           "empty_flow", "normalize_flow"]
