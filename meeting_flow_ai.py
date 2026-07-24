"""Pure helpers for meeting-flow AI prompts and validated diffs.

This module deliberately performs no file, network, clock, or process I/O.  The
existing strategy/live AI calls can embed the schema and prompt fragments below,
then pass their JSON payloads through the normalizers.  Keeping validation here
means a model response is never trusted as a meeting-flow write operation.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence


AGENDA_STATUSES = ("not_started", "discussing", "discussed", "deferred")
RESOLUTION_STATUSES = ("not_applicable", "pending", "agreed", "rejected")
QUESTION_STATUSES = ("next", "queued", "asked", "answered", "deferred", "dismissed")
RESULT_KINDS = ("answers", "decisions", "actions", "unresolved", "summary")
SUGGESTION_TYPES = (
    "agenda_proposal", "question_proposal", "research", "unstuck", "warning"
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
    "answered": {"answered"},
    "deferred": {"deferred"},
    "dismissed": {"dismissed"},
}

_ASR_BOILERPLATE = {
    "字幕を作成しています", "字幕をご覧ください", "日本語字幕をオンにしてご覧ください",
    "話者名やメタデータは創作しない", "ご視聴ありがとうございました",
    "thank you for watching", "subtitles by", "please subscribe",
}
_ACKNOWLEDGEMENTS = {
    "はい", "うん", "ええ", "そう", "そうですね", "なるほど", "了解", "わかりました",
    "ありがとうございます", "ありがとう", "よろしくお願いします", "お疲れさまです",
    "ok", "okay", "yes", "yeah", "right", "i see", "thanks", "thank you",
}
_COMMON_KATAKANA = {
    "アジェンダ", "ミーティング", "プロジェクト", "スケジュール", "システム", "サービス",
    "データ", "ユーザー", "チーム", "メンバー", "クライアント", "タスク", "リスク",
    "コスト", "プラン", "フロー", "ステータス", "サマリー", "フィードバック", "フェーズ",
    "オンライン", "リモート", "アイデア", "コンセプト", "デモ", "テスト", "レビュー",
}
_GREETINGS_RE = re.compile(
    r"^(?:おはようございます?|こんにちは|こんばんは|はじめまして|よろしく(?:お願い)?します|"
    r"hello|hi|good (?:morning|afternoon|evening)|nice to meet you)[。.!！\s]*$",
    re.I,
)
_STRONG_SIGNAL_RE = re.compile(
    r"(?:\?|？|決め|決定|確定|合意|期限|担当|までに|次回|質問|確認|課題|問題|"
    r"どう|なぜ|いつ|誰|いくら|どの|will\b|decid|agree|deadline|owner|who\b|when\b|why\b|how\b)",
    re.I,
)
_EXPLICIT_CUES = {
    "target": re.compile(
        r"(?:着地点|ゴール|目的|狙い|目標|成功条件|今日.{0,12}(?:決め|確認|進め)|"
        r"(?:決め|確認)たい|\b(?:goal|objective)\b|\bwant to (?:decide|confirm)\b)", re.I),
    "agenda": re.compile(
        r"(?:(?:議題|アジェンダ)(?:に)?したい|(?:議題|アジェンダ|論点)(?:に|として)"
        r"(?:入れ|加え|含め|設定|扱)|(?:議題|アジェンダ|論点).{1,30}(?:入れ|加え|含め|設定|扱|したい)|"
        r"(?:議題|アジェンダ|論点)(?:は|:|：)|"
        r"(?:話し|取り上げ|扱い)たい|\b(?:add to (?:the )?agenda|want to discuss|need to cover)\b)", re.I),
    "question": re.compile(
        r"(?:聞きたい|聞いた方が|確認したい|確認すべき|質問したい|尋ねたい|質問すべき|聞くべき|"
        r"\b(?:want to ask|should ask|need to (?:ask|confirm|know))\b)", re.I),
}
_AGREEMENT_RE = re.compile(
    r"(?:(?:決定|確定|合意)(?:です|した(?!い)|しました|します|とします|済み|となりました|"
    r"で(?:よい|いい|お願いします|(?=[。.!！]|$))|[。.!！]|$)|"
    r"それで(?:いき(?:ます|ましょう)|進め(?:ます|ましょう)|決まり(?:です|ました))|"
    r"それに(?:します|しましょう)|これに(?:します|しましょう)|決まり(?:です|ました)|"
    r"(?:agreed|decided|confirmed|let'?s go with)\b)", re.I,
)
_DEFER_RE = re.compile(
    r"(?:持ち越|見送|次回(?:に|へ)|後で(?:決め|確認|話)|保留|defer|postpone|next meeting)", re.I
)
_REJECTION_RE = re.compile(r"(?:見送|やらない|採用しない|却下|中止|断念|reject|declin|not proceed)", re.I)
_NON_AGENDA_RE = re.compile(
    r"(?:(?:会話|会議|文字起こし).{0,12}(?:本文|原本).{0,12}(?:取り込み|提供|共有|不足|なし)|"
    r"(?:本文|原本).{0,12}(?:提供|共有).{0,12}(?:必要|待ち|要)|"
    r"(?:transcript|source).{0,18}(?:required|missing|needed))", re.I,
)


def _default_resolution_status(title: str) -> str:
    return ("pending" if re.search(
        r"(?:決め|確定|合意|選定|可否|方針|条件|進め方|対象|見送り|採否)", str(title or ""))
        else "not_applicable")


def _object(properties: Mapping[str, Any], required: Sequence[str]) -> Dict[str, Any]:
    return {
        "type": "object", "properties": dict(properties),
        "required": list(required), "additionalProperties": False,
    }


def _array(item: Mapping[str, Any]) -> Dict[str, Any]:
    return {"type": "array", "items": dict(item)}


_STR = {"type": "string"}
_BOOL = {"type": "boolean"}
_INT = {"type": "integer", "minimum": 0}


def preparation_schema_extension() -> Dict[str, Any]:
    """JSON-schema value for a strategy response's meeting-flow field.

    The AI's ``explicit`` flag is only a claim.  :func:`prepare_strategy_flow`
    verifies its evidence against the actual user message before accepting it.
    """
    span = _object({"start": _INT, "end": _INT, "text": _STR}, ("start", "end", "text"))
    target = _object(
        {"text": _STR, "successCriteria": _STR, "explicit": _BOOL, "evidence": span},
        ("text", "successCriteria", "explicit", "evidence"),
    )
    agenda = _object(
        {"clientKey": _STR, "title": _STR, "explicit": _BOOL, "evidence": span},
        ("clientKey", "title", "explicit", "evidence"),
    )
    question = _object(
        {
            "clientKey": _STR, "agendaClientKey": _STR, "agendaId": _STR,
            "text": _STR, "reason": _STR, "explicit": _BOOL, "evidence": span,
        },
        ("clientKey", "agendaClientKey", "agendaId", "text", "reason", "explicit", "evidence"),
    )
    return _object(
        {"target": target, "agendas": _array(agenda), "questions": _array(question)},
        ("target", "agendas", "questions"),
    )


def live_schema_extension() -> Dict[str, Any]:
    """JSON-schema value for the existing live response's ``flow`` property."""
    evidence = _object(
        {
            "key": _STR, "deltaStart": _INT, "deltaEnd": _INT, "text": _STR,
            "speaker": _STR, "at": _STR,
        },
        ("key", "deltaStart", "deltaEnd", "text", "speaker", "at"),
    )
    status_update = _object(
        {"agendaId": _STR, "status": {"type": "string", "enum": list(AGENDA_STATUSES)},
         "basis": _STR, "evidenceKeys": _array(_STR)},
        ("agendaId", "status", "basis", "evidenceKeys"),
    )
    resolution_update = _object(
        {"agendaId": _STR, "status": {"type": "string", "enum": list(RESOLUTION_STATUSES)},
         "basis": _STR, "evidenceKeys": _array(_STR)},
        ("agendaId", "status", "basis", "evidenceKeys"),
    )
    question_update = _object(
        {"questionId": _STR, "status": {"type": "string", "enum": list(QUESTION_STATUSES)},
         "answer": _STR, "evidenceKeys": _array(_STR)},
        ("questionId", "status", "answer", "evidenceKeys"),
    )
    result_update = _object(
        {"agendaId": _STR, "kind": {"type": "string", "enum": list(RESULT_KINDS)},
         "text": _STR, "evidenceKeys": _array(_STR)},
        ("agendaId", "kind", "text", "evidenceKeys"),
    )
    agenda_proposal = _object(
        {"clientKey": _STR, "title": _STR, "reason": _STR, "evidenceKeys": _array(_STR)},
        ("clientKey", "title", "reason", "evidenceKeys"),
    )
    question_proposal = _object(
        {"clientKey": _STR, "agendaClientKey": _STR, "agendaId": _STR, "text": _STR, "reason": _STR,
         "evidenceKeys": _array(_STR)},
        ("clientKey", "agendaClientKey", "agendaId", "text", "reason", "evidenceKeys"),
    )
    suggestion_payload = _object({"title": _STR, "text": _STR, "agendaId": _STR},
                                 ("title", "text", "agendaId"))
    suggestion = _object(
        {"type": {"type": "string", "enum": list(SUGGESTION_TYPES)},
         "targetId": _STR, "text": _STR, "reason": _STR, "payload": suggestion_payload,
         "evidenceKeys": _array(_STR)},
        ("type", "targetId", "text", "reason", "payload", "evidenceKeys"),
    )
    return _object(
        {
            "currentAgendaId": _STR,
            "currentAgendaClientKey": _STR,
            "currentAgendaEvidenceKeys": _array(_STR),
            "evidence": _array(evidence),
            "agendaStatusUpdates": _array(status_update),
            "agendaResolutionUpdates": _array(resolution_update),
            "questionUpdates": _array(question_update),
            "resultUpdates": _array(result_update),
            "agendaProposals": _array(agenda_proposal),
            "questionProposals": _array(question_proposal),
            "suggestions": _array(suggestion),
        },
        ("currentAgendaId", "currentAgendaClientKey", "currentAgendaEvidenceKeys", "evidence", "agendaStatusUpdates", "agendaResolutionUpdates", "questionUpdates",
         "resultUpdates", "agendaProposals", "questionProposals", "suggestions"),
    )


def preparation_prompt_section() -> str:
    """Instructions to append to the existing strategy prompt."""
    return r"""
【会議進行データ（既存のAI呼出しと同じJSON応答に含める）】
meetingFlowにtarget/agendas/questionsを指定スキーマどおり返す。
- ユーザーが今回の発言で「議題にしたい」「聞きたい」「着地点にしたい」等と明示したものだけexplicit=true。AIの推測や資料由来は必ずfalse。
- evidenceは今回のユーザー発言内の0始まり文字位置。textは userMessage[start:end] と完全一致させる。資料や過去履歴の位置を返さない。
- 議題は会議中に回答・判断・方向付けを進める単位。単純な確認は親議題に紐づく質問にする。
- agendaClientKeyで同じ応答中の議題へ質問を紐づける。既存議題ならagendaIdを使う。不明なら両方を空文字にし、質問を捨てない。
- 人名、会社名、数値、決定、回答を根拠なしに創作しない。ユーザー明示内容をAI提案として重複させない。
""".strip()


def _flow_prompt_index(flow: Mapping[str, Any]) -> Dict[str, Any]:
    agendas = []
    for row in _list(flow.get("agendas")):
        agendas.append({
            "id": _text(row.get("id"), 100), "title": _text(row.get("title"), 160),
            "status": _text(row.get("status"), 30), "statusLocked": bool(row.get("statusLocked")),
            "resolutionStatus": _text(row.get("resolutionStatus"), 30),
            "resolutionLocked": bool(row.get("resolutionLocked")),
            "approval": _text(row.get("approval"), 20),
        })
    questions = []
    for row in _list(flow.get("questions")):
        questions.append({
            "id": _text(row.get("id"), 100), "agendaId": _text(row.get("agendaId"), 100),
            "text": _text(row.get("text"), 220), "status": _text(row.get("status"), 30),
            "approval": _text(row.get("approval"), 20),
        })
    return {"target": flow.get("target") or {}, "agendas": agendas, "questions": questions}


def live_prompt_section(flow: Mapping[str, Any], transcript_start: int,
                        generation: str) -> str:
    """Instructions/index to append to the existing live prompt.

    ``deltaStart`` and ``deltaEnd`` in the model response are relative to the
    latest transcript delta, avoiding errors caused by prompt decorations.
    """
    index = json.dumps(_flow_prompt_index(flow or {}), ensure_ascii=False, separators=(",", ":"))
    return """
【会議進行flow差分】
同じJSON応答のflowに指定スキーマの差分を返す。全データを書き直さない。
文字起こし世代: {generation}
追加文字起こしの絶対開始位置: {start}
現在flow: {index}
規則:
- evidenceのdeltaStart/deltaEndは【flow対象の最新発話】本文だけを0始まりで数え、textと完全一致させる。
- 短い相槌、挨拶、雑談、重複、字幕定型文だけなら全配列を空にする。
- 議論状態と合意状態を混同しない。statusは not_started / discussing / discussed / deferred。現在話している議題だけdiscussing、話題が移って一区切りした議題はdiscussed。deferredは明示的に次回へ持ち越した場合だけ。
- 合意状態はagendaResolutionUpdatesで別に返す。明示的な合意はagreed、明示的な見送りはrejected、合意が必要だが未決ならpending、情報共有など合意対象外ならnot_applicable。agreed/rejectedには必ず根拠発言を付ける。
- 現在の議題で未回答の採用済み質問から、今聞く価値が高い1〜3件をnext、それ以外をqueuedにする。最新発話が優先度の根拠になる場合だけ更新する。
- 既存議題を話している時はcurrentAgendaId、新しいagendaProposalを話している時はそのclientKeyをcurrentAgendaClientKeyへ入れる。どちらにも根拠をcurrentAgendaEvidenceKeysで必ず付ける。
- 既存IDだけを更新対象にする。ユーザー作成情報の削除・順序変更、locked項目の上書きは禁止。
- 最新発話が既存議題へ無理なく収まらない独立した話題ならagendaProposalへ返す。広すぎる既存議題へ無理に押し込まない。新しく会話から抽出した質問もproposalへ返す。採用済み構造の分割・統合・順序変更はsuggestionsへ返す。
- questionProposalを同じ応答内の新議題に紐づける時はagendaClientKeyを使う。
- agenda_proposal/question_proposalのsuggestionは採用時に作る内容をpayload.title/payload.text/payload.agendaIdに入れる。他の種類はpayloadの各値を空文字で返す。
- 人名・会社名・数値を根拠から補完・推測しない。不明は不明のままにする。
""".strip().format(generation=str(generation), start=max(0, int(transcript_start)), index=index)


def classify_utterance(text: Any, recent_texts: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Classify whether a transcript delta merits a flow AI update."""
    normalized = _norm_space(text)
    compact = _norm_key(normalized)
    if not compact:
        return {"meaningful": False, "reason": "empty", "normalized": normalized}
    boiler = compact.rstrip("。.!！?")
    if boiler in {_norm_key(x) for x in _ASR_BOILERPLATE}:
        return {"meaningful": False, "reason": "asr_boilerplate", "normalized": normalized}
    if recent_texts:
        for previous in recent_texts:
            if compact == _norm_key(previous):
                return {"meaningful": False, "reason": "duplicate", "normalized": normalized}
    plain = compact.rstrip("。.!！?")
    if plain in {_norm_key(x) for x in _ACKNOWLEDGEMENTS}:
        return {"meaningful": False, "reason": "acknowledgement", "normalized": normalized}
    if _GREETINGS_RE.match(normalized):
        return {"meaningful": False, "reason": "greeting", "normalized": normalized}
    visible = re.sub(r"[\s\W_]+", "", normalized, flags=re.UNICODE)
    if len(visible) < 8 and not _STRONG_SIGNAL_RE.search(normalized) and not re.search(r"\d", normalized):
        return {"meaningful": False, "reason": "too_short", "normalized": normalized}
    return {"meaningful": True, "reason": "content", "normalized": normalized}


def is_meaningful_utterance(text: Any, recent_texts: Optional[Iterable[str]] = None) -> bool:
    return bool(classify_utterance(text, recent_texts).get("meaningful"))


def validate_evidence_span(source: str, evidence: Mapping[str, Any],
                           expected_generation: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Validate an absolute evidence span against its exact source text."""
    if not isinstance(evidence, Mapping):
        return None
    if expected_generation is not None:
        supplied = evidence.get("transcriptGeneration")
        if supplied not in (None, "", expected_generation):
            return None
    start = _integer(evidence.get("transcriptStart", evidence.get("start")), -1)
    end = _integer(evidence.get("transcriptEnd", evidence.get("end")), -1)
    if start < 0 or end <= start or end > len(source):
        return None
    exact = source[start:end]
    if str(evidence.get("text", "")) != exact:
        return None
    result = {
        "transcriptStart": start, "transcriptEnd": end, "text": exact,
    }
    if expected_generation is not None:
        result["transcriptGeneration"] = expected_generation
    speaker = _safe_speaker(evidence.get("speaker"), exact)
    at = _safe_time(evidence.get("at"), exact)
    if speaker:
        result["speaker"] = speaker
    if at:
        result["at"] = at
    return result


def is_allowed_agenda_transition(old_status: str, new_status: str,
                                 actor: str = "ai") -> bool:
    if new_status not in AGENDA_STATUSES or old_status not in AGENDA_STATUSES:
        return False
    if actor == "user":
        return True
    return new_status in _AGENDA_AI_TRANSITIONS.get(old_status, set())


def is_allowed_question_transition(old_status: str, new_status: str,
                                   actor: str = "ai") -> bool:
    if new_status not in QUESTION_STATUSES or old_status not in QUESTION_STATUSES:
        return False
    if actor == "user":
        return True
    return new_status in _QUESTION_AI_TRANSITIONS.get(old_status, set())


def prepare_strategy_flow(current_flow: Mapping[str, Any], user_message: str,
                          ai_payload: Any) -> Dict[str, Any]:
    """Normalize a preparation meeting-flow payload into safe create/update ops."""
    payload = _unwrap_payload(ai_payload, ("meetingFlow", "flow"))
    flow = current_flow if isinstance(current_flow, Mapping) else {}
    issues: List[str] = []
    agenda_creates: List[Dict[str, Any]] = []
    agenda_updates: List[Dict[str, Any]] = []
    question_creates: List[Dict[str, Any]] = []
    question_updates: List[Dict[str, Any]] = []
    existing_agendas = _list(flow.get("agendas"))
    existing_questions = _list(flow.get("questions"))
    agenda_by_id = {_text(x.get("id"), 100): x for x in existing_agendas if _text(x.get("id"), 100)}
    client_to_id: Dict[str, str] = {}
    grounding = user_message + "\n" + _flow_grounding_text(flow)

    target_update = None
    raw_target = payload.get("target") if isinstance(payload.get("target"), Mapping) else {}
    target_text = _text(raw_target.get("text"), 300)
    if target_text:
        explicit = _verified_explicit(raw_target, user_message, "target")
        success_criteria = _text(raw_target.get("successCriteria"), 300)
        current_target = flow.get("target") if isinstance(flow.get("target"), Mapping) else {}
        if explicit or not bool(current_target.get("locked")):
            if not _ungrounded_facts(target_text, grounding):
                if _ungrounded_facts(success_criteria, grounding):
                    success_criteria = ""
                    issues.append("target.successCriteria:ungrounded_fact")
                target_update = {
                    "text": target_text,
                    "successCriteria": success_criteria,
                    "origin": "user" if explicit else "ai", "locked": bool(explicit),
                }
            else:
                issues.append("target:ungrounded_fact")

    next_agenda_order = _next_order(existing_agendas)
    for pos, raw in enumerate(_list(payload.get("agendas"))[:8]):
        title = _text(raw.get("title"), 200)
        if not title:
            continue
        explicit = _verified_explicit(raw, user_message, "agenda")
        if not explicit and _NON_AGENDA_RE.search(title):
            issues.append("agenda[%d]:source_request_is_not_agenda" % pos)
            continue
        duplicate = _similar_mapping(title, existing_agendas, "title")
        if duplicate:
            if explicit and _text(duplicate.get("approval"), 20) == "draft":
                agenda_updates.append({
                    "agendaId": _text(duplicate.get("id"), 100),
                    "approval": "accepted", "origin": "user",
                })
            continue
        if _has_similar(title, [x["title"] for x in agenda_creates]):
            continue
        if _ungrounded_facts(title, grounding):
            issues.append("agenda[%d]:ungrounded_fact" % pos)
            continue
        aid = _stable_id("agenda", title)
        row = {
            "id": aid, "title": title, "order": next_agenda_order,
            "status": "not_started", "statusLocked": False,
            "resolutionStatus": _default_resolution_status(title),
            "origin": "user" if explicit else "ai",
            "approval": "accepted" if explicit else "draft",
        }
        agenda_creates.append(row)
        next_agenda_order += 1000
        client_key = _text(raw.get("clientKey"), 100)
        if client_key:
            client_to_id[client_key] = aid

    all_agenda_ids = set(agenda_by_id) | {x["id"] for x in agenda_creates}
    orders: Dict[str, int] = {}
    for raw in _list(payload.get("questions"))[:16]:
        text = _text(raw.get("text"), 300)
        if not text:
            continue
        explicit = _verified_explicit(raw, user_message, "question")
        duplicate = _similar_mapping(text, existing_questions, "text")
        if duplicate:
            if explicit and _text(duplicate.get("approval"), 20) == "draft":
                question_updates.append({
                    "questionId": _text(duplicate.get("id"), 100),
                    "approval": "accepted", "origin": "user",
                })
            continue
        if _has_similar(text, [x["text"] for x in question_creates]):
            continue
        if _ungrounded_facts(text, grounding):
            issues.append("question:ungrounded_fact")
            continue
        agenda_id = _text(raw.get("agendaId"), 100)
        if agenda_id not in all_agenda_ids:
            agenda_id = client_to_id.get(_text(raw.get("agendaClientKey"), 100), "")
        if agenda_id not in all_agenda_ids:
            agenda_id = ""
        if agenda_id not in orders:
            linked = [x for x in existing_questions if _text(x.get("agendaId"), 100) == agenda_id]
            orders[agenda_id] = _next_order(linked)
        row = {
            "id": _stable_id("question", agenda_id + "\n" + text),
            "agendaId": agenda_id, "order": orders[agenda_id], "text": text,
            "status": "queued", "origin": "user" if explicit else "ai",
            "approval": "accepted" if explicit else "draft",
            "reason": _grounded_or_empty(raw.get("reason"), grounding, 300),
            "answer": "", "evidenceIds": [],
        }
        question_creates.append(row)
        orders[agenda_id] += 1000

    return {
        "targetUpdate": target_update,
        "agendaUpdates": agenda_updates,
        "agendaCreates": agenda_creates,
        "questionUpdates": question_updates,
        "questionCreates": question_creates,
        "validationErrors": issues,
    }


def normalize_live_diff(raw_flow: Any, delta: str, start: int, generation: str,
                        flow: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Validate a live model's flow payload and return safe deterministic ops."""
    payload = _unwrap_payload(raw_flow, ("flow",))
    current = flow if isinstance(flow, Mapping) else {}
    start = max(0, int(start))
    generation = str(generation or "")
    result: Dict[str, Any] = {
        "transcriptGeneration": generation,
        "transcriptCursor": start + len(delta),
        "meaningful": is_meaningful_utterance(delta),
        "currentAgendaId": "",
        "currentAgendaClientKey": "",
        "currentAgendaEvidenceIds": [],
        "evidence": [], "agendaStatusUpdates": [], "agendaResolutionUpdates": [], "questionUpdates": [],
        "resultUpdates": [], "agendaCreates": [], "questionCreates": [],
        "suggestions": [], "validationErrors": [],
    }
    if not result["meaningful"]:
        return result

    agendas = _list(current.get("agendas"))
    questions = _list(current.get("questions"))
    agenda_by_id = {_text(x.get("id"), 100): x for x in agendas if _text(x.get("id"), 100)}
    question_by_id = {_text(x.get("id"), 100): x for x in questions if _text(x.get("id"), 100)}
    grounding = delta + "\n" + _flow_grounding_text(current)
    evidence_by_key: Dict[str, Dict[str, Any]] = {}
    for pos, raw in enumerate(_list(payload.get("evidence"))[:24]):
        key = _text(raw.get("key"), 100) or "e%d" % pos
        rel_start = _integer(raw.get("deltaStart"), -1)
        rel_end = _integer(raw.get("deltaEnd"), -1)
        candidate = {
            "start": rel_start, "end": rel_end, "text": str(raw.get("text", "")),
            "speaker": raw.get("speaker", ""), "at": raw.get("at", ""),
        }
        checked = validate_evidence_span(delta, candidate)
        if not checked:
            result["validationErrors"].append("evidence[%s]:invalid_span" % key)
            continue
        absolute = {
            "id": _stable_id("evidence", "%s:%d:%d:%s" % (generation, start + rel_start,
                                                              start + rel_end, checked["text"])),
            "transcriptGeneration": generation,
            "transcriptStart": start + rel_start, "transcriptEnd": start + rel_end,
            "text": checked["text"],
        }
        if checked.get("speaker"):
            absolute["speaker"] = checked["speaker"]
        if checked.get("at"):
            absolute["at"] = checked["at"]
        evidence_by_key[key] = absolute
        result["evidence"].append(absolute)

    current_id = _text(payload.get("currentAgendaId"), 100)
    current_refs = _evidence_refs({"evidenceKeys": payload.get("currentAgendaEvidenceKeys")}, evidence_by_key)
    if current_id in agenda_by_id and current_refs:
        result["currentAgendaId"] = current_id
        result["currentAgendaEvidenceIds"] = current_refs

    for raw in _list(payload.get("agendaStatusUpdates"))[:12]:
        aid = _text(raw.get("agendaId"), 100)
        agenda = agenda_by_id.get(aid)
        if not agenda or bool(agenda.get("statusLocked")):
            continue
        old = _text(agenda.get("status"), 30) or "not_started"
        new = _text(raw.get("status"), 30)
        refs = _evidence_refs(raw, evidence_by_key)
        if not refs or not is_allowed_agenda_transition(old, new):
            continue
        evidence_text = _refs_text(refs, result["evidence"])
        if new == "deferred" and not _DEFER_RE.search(evidence_text):
            continue
        result["agendaStatusUpdates"].append({
            "agendaId": aid, "status": new, "basis": _text(raw.get("basis"), 300),
            "evidenceIds": refs,
        })

    for raw in _list(payload.get("agendaResolutionUpdates"))[:12]:
        aid = _text(raw.get("agendaId"), 100)
        agenda = agenda_by_id.get(aid)
        status = _text(raw.get("status"), 30)
        refs = _evidence_refs(raw, evidence_by_key)
        if not agenda or bool(agenda.get("resolutionLocked")) or status not in RESOLUTION_STATUSES or not refs:
            continue
        evidence_text = _refs_text(refs, result["evidence"])
        if status == "agreed" and not _AGREEMENT_RE.search(evidence_text):
            result["validationErrors"].append("agenda[%s]:agreement_without_evidence" % aid)
            continue
        if status == "rejected" and not _REJECTION_RE.search(evidence_text):
            result["validationErrors"].append("agenda[%s]:rejection_without_evidence" % aid)
            continue
        result["agendaResolutionUpdates"].append({
            "agendaId": aid, "status": status, "basis": _text(raw.get("basis"), 300),
            "evidenceIds": refs,
        })

    for raw in _list(payload.get("questionUpdates"))[:16]:
        qid = _text(raw.get("questionId"), 100)
        question = question_by_id.get(qid)
        if not question:
            continue
        old = _text(question.get("status"), 30) or "queued"
        new = _text(raw.get("status"), 30)
        refs = _evidence_refs(raw, evidence_by_key)
        answer = _text(raw.get("answer"), 500)
        if not refs or not is_allowed_question_transition(old, new):
            continue
        evidence_text = _refs_text(refs, result["evidence"])
        if new == "answered" and (not answer or _ungrounded_facts(answer, evidence_text)):
            result["validationErrors"].append("question[%s]:unsupported_answer" % qid)
            continue
        if new == "deferred" and not _DEFER_RE.search(evidence_text):
            continue
        result["questionUpdates"].append({
            "questionId": qid, "status": new,
            "answer": answer if new == "answered" else "", "evidenceIds": refs,
        })

    for raw in _list(payload.get("resultUpdates"))[:20]:
        aid = _text(raw.get("agendaId"), 100)
        agenda = agenda_by_id.get(aid)
        kind = _text(raw.get("kind"), 30)
        text = _text(raw.get("text"), 700)
        refs = _evidence_refs(raw, evidence_by_key)
        if not agenda or kind not in RESULT_KINDS or not text or not refs:
            continue
        if kind == "summary" and bool(((agenda.get("result") or {}).get("summary") or {}).get("locked")):
            continue
        evidence_text = _refs_text(refs, result["evidence"])
        if _ungrounded_facts(text, evidence_text + "\n" + _flow_grounding_text(current)):
            result["validationErrors"].append("result[%s]:ungrounded_fact" % aid)
            continue
        if kind == "decisions" and not _AGREEMENT_RE.search(evidence_text):
            result["validationErrors"].append("result[%s]:decision_without_agreement" % aid)
            continue
        result["resultUpdates"].append({
            "agendaId": aid, "kind": kind, "text": text, "origin": "ai",
            "locked": False, "evidenceIds": refs,
        })

    agenda_titles = [_text(x.get("title"), 200) for x in agendas]
    next_agenda_order = _next_order(agendas)
    client_to_id: Dict[str, str] = {}
    for raw in _list(payload.get("agendaProposals"))[:5]:
        title = _text(raw.get("title"), 200)
        refs = _evidence_refs(raw, evidence_by_key)
        if not title or not refs or _has_similar(title, agenda_titles):
            continue
        if _ungrounded_facts(title, grounding):
            continue
        aid = _stable_id("agenda", title)
        result["agendaCreates"].append({
            "id": aid, "title": title, "order": next_agenda_order,
            "status": "not_started", "statusLocked": False,
            "resolutionStatus": _default_resolution_status(title),
            "origin": "ai", "approval": "draft", "evidenceIds": refs,
            "reason": _grounded_or_empty(raw.get("reason"), grounding, 300),
        })
        key = _text(raw.get("clientKey"), 100)
        if key:
            client_to_id[key] = aid
        next_agenda_order += 1000
        agenda_titles.append(title)

    current_client_key = _text(payload.get("currentAgendaClientKey"), 100)
    if not result["currentAgendaId"] and current_client_key in client_to_id and current_refs:
        result["currentAgendaId"] = client_to_id[current_client_key]
        result["currentAgendaClientKey"] = current_client_key
        result["currentAgendaEvidenceIds"] = current_refs

    all_agenda_ids = set(agenda_by_id) | {x["id"] for x in result["agendaCreates"]}
    question_texts = [_text(x.get("text"), 300) for x in questions]
    question_orders: Dict[str, int] = {}
    for raw in _list(payload.get("questionProposals"))[:8]:
        text = _text(raw.get("text"), 300)
        refs = _evidence_refs(raw, evidence_by_key)
        if not text or not refs or _has_similar(text, question_texts):
            continue
        if _ungrounded_facts(text, grounding):
            continue
        agenda_id = _text(raw.get("agendaId"), 100)
        if agenda_id not in all_agenda_ids:
            agenda_id = (client_to_id.get(_text(raw.get("agendaClientKey"), 100), "")
                         or client_to_id.get(agenda_id, ""))
        if agenda_id not in all_agenda_ids:
            agenda_id = ""
        if agenda_id not in question_orders:
            linked = [x for x in questions if _text(x.get("agendaId"), 100) == agenda_id]
            question_orders[agenda_id] = _next_order(linked)
        result["questionCreates"].append({
            "id": _stable_id("question", agenda_id + "\n" + text),
            "agendaId": agenda_id, "order": question_orders[agenda_id], "text": text,
            "status": "queued", "origin": "ai", "approval": "draft",
            "reason": _grounded_or_empty(raw.get("reason"), grounding, 300), "answer": "",
            "evidenceIds": refs,
        })
        question_orders[agenda_id] += 1000
        question_texts.append(text)

    for raw in _list(payload.get("suggestions"))[:12]:
        kind = _text(raw.get("type"), 40)
        text = _text(raw.get("text"), 700)
        refs = _evidence_refs(raw, evidence_by_key)
        if kind not in SUGGESTION_TYPES or not text or not refs:
            continue
        if _ungrounded_facts(text, grounding):
            continue
        target_id = _text(raw.get("targetId"), 100)
        if target_id and target_id not in agenda_by_id and target_id not in question_by_id:
            target_id = ""
        raw_action = raw.get("payload") if isinstance(raw.get("payload"), Mapping) else {}
        action_payload: Dict[str, str] = {}
        if kind == "agenda_proposal":
            title = _text(raw_action.get("title"), 200)
            if not title or _ungrounded_facts(title, grounding):
                continue
            action_payload = {"title": title}
        elif kind == "question_proposal":
            question_text = _text(raw_action.get("text"), 300)
            action_agenda = _text(raw_action.get("agendaId"), 100)
            if action_agenda not in agenda_by_id:
                action_agenda = ""
            if not question_text or _ungrounded_facts(question_text, grounding):
                continue
            action_payload = {"text": question_text, "agendaId": action_agenda}
        result["suggestions"].append({
            "id": _stable_id("suggestion", kind + "\n" + target_id + "\n" + text),
            "type": kind, "targetId": target_id, "text": text,
            "reason": _grounded_or_empty(raw.get("reason"), grounding, 400), "status": "pending",
            "origin": "ai", "evidenceIds": refs, "payload": action_payload,
        })
    return result


def call_with_injected_ai(ai_call: Callable[..., Any], prompt: str,
                          schema: Mapping[str, Any], normalizer: Callable[[Any], Dict[str, Any]]) -> Dict[str, Any]:
    """Small deterministic injection boundary useful to server code and tests.

    LiveMTG should normally embed these sections in its existing strategy/live
    calls.  This helper exists for callers that already own an AI invocation; it
    does not select a provider or perform retries.
    """
    payload = ai_call(prompt, schema=schema)
    return normalizer(payload)


def _unwrap_payload(value: Any, keys: Sequence[str]) -> Dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {}
    if not isinstance(value, Mapping):
        return {}
    for key in keys:
        nested = value.get(key)
        if isinstance(nested, Mapping):
            return dict(nested)
    return dict(value)


def _verified_explicit(raw: Mapping[str, Any], source: str, kind: str) -> bool:
    if not bool(raw.get("explicit")):
        return False
    evidence = validate_evidence_span(source, raw.get("evidence") or {})
    value = raw.get("title") if kind == "agenda" else raw.get("text")
    return bool(evidence and _EXPLICIT_CUES[kind].search(evidence["text"])
                and _content_overlap(str(value or ""), evidence["text"]))


def _safe_speaker(value: Any, evidence_text: str) -> str:
    speaker = _text(value, 80)
    if not speaker:
        return ""
    return speaker if speaker in evidence_text else ""


def _safe_time(value: Any, evidence_text: str) -> str:
    at = _text(value, 20)
    return at if (re.fullmatch(r"(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?", at)
                  and at in evidence_text) else ""


def _flow_grounding_text(flow: Mapping[str, Any]) -> str:
    try:
        return json.dumps(_flow_prompt_index(flow), ensure_ascii=False)
    except (TypeError, ValueError):
        return ""


def _ungrounded_facts(text: str, source: str) -> List[str]:
    """Return high-risk factual tokens not present in the grounding source.

    This intentionally focuses on values most harmful to invent: numbers,
    company/person forms, and distinctive Latin product/entity tokens. Ordinary
    paraphrases remain possible.
    """
    source_key = unicodedata.normalize("NFKC", source).casefold()
    value = unicodedata.normalize("NFKC", text)
    candidates = set(re.findall(
        r"(?<![A-Za-z0-9])\d[\d,.]*(?:%|％|円|万円|億円|人|社|件|日|か月|ヶ月|週間|月|年|時|分)?",
        value,
    ))
    candidates.update(re.findall(r"(?:株式会社|合同会社|有限会社)[一-龥ぁ-んァ-ヶA-Za-z0-9ー・]{1,24}", value))
    candidates.update(re.findall(r"[一-龥ぁ-んァ-ヶA-Za-z0-9ー・]{1,24}(?:株式会社|合同会社|有限会社)", value))
    candidates.update(re.findall(r"[ァ-ヶ][ァ-ヶー・]{1,20}社", value))
    candidates.update(re.findall(r"[一-龥]{2,4}(?:さん|氏|様)", value))
    candidates.update(token for token in re.findall(r"[ァ-ヶー]{3,32}", value)
                      if token not in _COMMON_KATAKANA)
    for match in re.finditer(r"([一-龥]{2,4})(?:が|は|の)?(?:担当|社長|部長|責任者|さん|氏|様)", value):
        candidates.add(match.group(1))
    candidates.update(re.findall(r"([一-龥]{2,4})(?:が|は)(?:担当|対応|実施|確認|共有|決定)", value))
    common = {
        "ai", "api", "url", "json", "live", "meeting", "flow", "id", "ui", "ux",
        "the", "and", "or", "with", "from", "for",
    }
    for token in re.findall(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9._-]{1,31}", value):
        if token.casefold() not in common:
            candidates.add(token)
    return sorted(x for x in candidates if unicodedata.normalize("NFKC", x).casefold() not in source_key)


def _grounded_or_empty(value: Any, source: str, limit: int) -> str:
    text = _text(value, limit)
    return "" if _ungrounded_facts(text, source) else text


def _evidence_refs(raw: Mapping[str, Any], evidence: Mapping[str, Mapping[str, Any]]) -> List[str]:
    refs = []
    for key in raw.get("evidenceKeys") or []:
        row = evidence.get(str(key))
        if row and row["id"] not in refs:
            refs.append(row["id"])
    return refs


def _refs_text(refs: Sequence[str], evidence: Sequence[Mapping[str, Any]]) -> str:
    wanted = set(refs)
    return "\n".join(str(x.get("text") or "") for x in evidence if x.get("id") in wanted)


def _stable_id(kind: str, value: str) -> str:
    digest = hashlib.sha256(_norm_key(value).encode("utf-8")).hexdigest()[:20]
    return "%s-%s" % (kind, digest)


def _norm_space(value: Any) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value or ""))).strip()


def _norm_key(value: Any) -> str:
    return re.sub(r"[\s\W_]+", "", _norm_space(value).casefold(), flags=re.UNICODE)


def _text(value: Any, limit: int) -> str:
    return _norm_space(value)[:limit]


def _integer(value: Any, default: int) -> int:
    try:
        if isinstance(value, bool):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _list(value: Any) -> List[Mapping[str, Any]]:
    return [x for x in value if isinstance(x, Mapping)] if isinstance(value, list) else []


def _next_order(rows: Sequence[Mapping[str, Any]]) -> int:
    values = [_integer(x.get("order"), 0) for x in rows]
    return (max(values) if values else 0) + 1000


def _has_similar(value: str, others: Iterable[str]) -> bool:
    key = _norm_key(value)
    if not key:
        return True
    for other in others:
        old = _norm_key(other)
        if not old:
            continue
        if key == old or (min(len(key), len(old)) >= 6 and (key in old or old in key)):
            return True
        if SequenceMatcher(None, key, old).ratio() >= 0.86:
            return True
    return False


def _similar_mapping(value: str, rows: Iterable[Mapping[str, Any]], key: str) -> Optional[Mapping[str, Any]]:
    for row in rows:
        if _has_similar(value, [_text(row.get(key), 500)]):
            return row
    return None


def _content_overlap(value: str, evidence: str) -> bool:
    """Conservatively tie an extracted item to the phrase claiming it explicit."""
    left = _norm_key(value)
    right = _norm_key(evidence)
    if not left or not right:
        return False
    if left in right or right in left:
        return True
    left_pairs = {left[i:i + 2] for i in range(max(0, len(left) - 1))}
    right_pairs = {right[i:i + 2] for i in range(max(0, len(right) - 1))}
    if not left_pairs:
        return left in right
    return len(left_pairs & right_pairs) / len(left_pairs) >= 0.35
