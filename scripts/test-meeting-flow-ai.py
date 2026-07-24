#!/usr/bin/env python3
"""Deterministic tests for meeting_flow_ai (no model or network required)."""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import meeting_flow_ai as flow_ai


def span(source, phrase):
    start = source.index(phrase)
    return {"start": start, "end": start + len(phrase), "text": phrase}


def live_evidence(source, key, phrase):
    start = source.index(phrase)
    return {
        "key": key, "deltaStart": start, "deltaEnd": start + len(phrase),
        "text": phrase, "speaker": "SPEAKER_00", "at": "14:22",
    }


# Schemas are strict fragments that can be embedded into the existing calls.
prep_schema = flow_ai.preparation_schema_extension()
live_schema = flow_ai.live_schema_extension()
assert prep_schema["additionalProperties"] is False
assert set(prep_schema["required"]) == {"target", "agendas", "questions"}
assert set(live_schema["required"]) >= {"evidence", "agendaStatusUpdates", "resultUpdates"}
assert "meetingFlow" in flow_ai.preparation_prompt_section()

sample_flow = {
    "target": {"text": "価格を決める", "locked": True},
    "agendas": [{
        "id": "a1", "title": "料金体系を決める", "order": 1000,
        "status": "discussing", "statusLocked": False, "approval": "accepted",
        "result": {"summary": {"text": "比較中", "locked": False}},
    }],
    "questions": [{
        "id": "q1", "agendaId": "a1", "order": 1000,
        "text": "最低契約期間は？", "status": "asked", "approval": "accepted",
    }],
}
prompt = flow_ai.live_prompt_section(sample_flow, 120, "gen-1")
assert "追加文字起こしの絶対開始位置: 120" in prompt
assert '"id":"a1"' in prompt and "根拠から補完・推測しない" in prompt


# Meaningful speech gating: greetings, acknowledgements, boilerplate and exact
# recent duplicates must not consume a flow AI update.
for text in ("はい。", "こんにちは", "字幕をご覧ください。", "ありがとう"):
    assert not flow_ai.is_meaningful_utterance(text), text
assert not flow_ai.is_meaningful_utterance("料金は月額です", ["料金は月額です"])
assert flow_ai.is_meaningful_utterance("料金は月額30万円で確定にしましょう。")
assert flow_ai.classify_utterance("うん")["reason"] == "acknowledgement"
assert flow_ai.is_meaningful_utterance("Hello, we need to decide the launch date.")
assert not flow_ai._AGREEMENT_RE.search("料金を確定したいです")
assert flow_ai._AGREEMENT_RE.search("料金は月額制で確定した。")


# Exact evidence validation includes generation and strips an invented speaker.
source = "SPEAKER_00: 月額制で進めます。"
checked = flow_ai.validate_evidence_span(source, {
    **span(source, "月額制で進めます。"), "transcriptGeneration": "g1",
    "speaker": "田中", "at": "25:90",
}, "g1")
assert checked and "speaker" not in checked and "at" not in checked
assert "speaker" not in flow_ai.validate_evidence_span(source, {
    **span(source, "月額制で進めます。"), "speaker": "SPEAKER_99", "at": "23:59",
})
assert flow_ai.validate_evidence_span(source, {
    **span(source, "月額制で進めます。"), "transcriptGeneration": "old",
}, "g1") is None
assert flow_ai.validate_evidence_span(source, {
    "start": 0, "end": 4, "text": "異なる本文",
}) is None


# Preparation: only an explicit user phrase becomes accepted/user-owned. AI
# inference remains draft, and a same-response question can link to a new agenda.
user = "今日の着地点は料金体系を決めること。料金体系を議題にしたい。最低契約期間も聞きたい。"
payload = {
    "meetingFlow": {
        "target": {
            "text": "料金体系を決める", "successCriteria": "契約条件も確認する",
            "explicit": True, "evidence": span(user, "今日の着地点は料金体系を決めること。"),
        },
        "agendas": [
            {"clientKey": "pricing", "title": "料金体系を決める", "explicit": True,
             "evidence": span(user, "料金体系を議題にしたい。")},
            {"clientKey": "timing", "title": "導入時期を決める", "explicit": False,
             "evidence": span(user, "最低契約期間も聞きたい。")},
        ],
        "questions": [{
            "clientKey": "term", "agendaClientKey": "pricing", "agendaId": "",
            "text": "最低契約期間は？", "reason": "料金条件の確認", "explicit": True,
            "evidence": span(user, "最低契約期間も聞きたい。"),
        }],
    }
}
prepared = flow_ai.prepare_strategy_flow({}, user, payload)
assert prepared["targetUpdate"]["origin"] == "user" and prepared["targetUpdate"]["locked"]
assert len(prepared["agendaCreates"]) == 2
accepted, draft = prepared["agendaCreates"]
assert accepted["approval"] == "accepted" and accepted["origin"] == "user"
assert draft["approval"] == "draft" and draft["origin"] == "ai"
assert prepared["questionCreates"][0]["agendaId"] == accepted["id"]
assert prepared["questionCreates"][0]["approval"] == "accepted"

# Missing-source explanations are system state, never an AI-created agenda.
source_request = flow_ai.prepare_strategy_flow({}, "会議の準備をしたい", {
    "agendas": [{"title": "会話本文の取り込み（要・原本提供）", "explicit": False}],
    "questions": [], "target": {},
})
assert source_request["agendaCreates"] == []
assert "agenda[0]:source_request_is_not_agenda" in source_request["validationErrors"]

# A model cannot turn an inference into accepted merely by setting explicit.
fake_explicit = {
    "target": {"text": "別の目標", "successCriteria": "", "explicit": True,
               "evidence": span(user, "最低契約期間も聞きたい。")},
    "agendas": [], "questions": [],
}
locked = flow_ai.prepare_strategy_flow(sample_flow, user, fake_explicit)
assert locked["targetUpdate"] is None

# If a preparation inference already exists as draft, a later explicit user
# instruction accepts that same entity instead of creating a duplicate.
draft_flow = {
    "agendas": [{"id": "draft-a", "title": "料金体系を決める", "approval": "draft"}],
    "questions": [{"id": "draft-q", "agendaId": "draft-a", "text": "最低契約期間は？",
                   "approval": "draft"}],
}
accepted_later = flow_ai.prepare_strategy_flow(draft_flow, user, payload)
assert accepted_later["agendaUpdates"] == [{
    "agendaId": "draft-a", "approval": "accepted", "origin": "user",
}]
assert accepted_later["questionUpdates"] == [{
    "questionId": "draft-q", "approval": "accepted", "origin": "user",
}]
assert all(x["title"] != "料金体系を決める" for x in accepted_later["agendaCreates"])

# An explicit cue about one subject cannot mark another extracted subject as
# user-owned merely by spanning the same sentence.
wrong_subject = {
    "target": {"text": "", "successCriteria": "", "explicit": False,
               "evidence": span(user, "最低契約期間も聞きたい。")},
    "agendas": [{"clientKey": "wrong", "title": "採用計画を決める", "explicit": True,
                 "evidence": span(user, "料金体系を議題にしたい。")}],
    "questions": [],
}
wrong_subject_diff = flow_ai.prepare_strategy_flow({}, user, wrong_subject)
assert wrong_subject_diff["agendaCreates"][0]["approval"] == "draft"

# Hallucinated numeric/entity facts are rejected; duplicates are not recreated.
hallucinated = {
    "target": {"text": "価格を決める", "successCriteria": "", "explicit": False,
               "evidence": span(user, "最低契約期間も聞きたい。")},
    "agendas": [
        {"clientKey": "old", "title": "料金体系を決める", "explicit": False,
         "evidence": span(user, "最低契約期間も聞きたい。")},
        {"clientKey": "bad", "title": "Acme社の18か月契約を決める", "explicit": False,
         "evidence": span(user, "最低契約期間も聞きたい。")},
    ],
    "questions": [],
}
safe = flow_ai.prepare_strategy_flow(sample_flow, user, hallucinated)
assert not safe["agendaCreates"] and safe["validationErrors"]


# Live normalization: all accepted writes carry exact absolute evidence ranges.
delta = "最低契約期間は12か月です。月額制で確定です。導入時期も確認が必要です。"
e1 = live_evidence(delta, "answer", "最低契約期間は12か月です。")
e2 = live_evidence(delta, "decision", "月額制で確定です。")
e3 = live_evidence(delta, "timing", "導入時期も確認が必要です。")
raw = {
    "currentAgendaId": "a1", "currentAgendaEvidenceKeys": ["timing"], "evidence": [e1, e2, e3],
    "agendaStatusUpdates": [{
        "agendaId": "a1", "status": "discussed", "basis": "話題が一区切り",
        "evidenceKeys": ["decision"],
    }],
    "agendaResolutionUpdates": [{
        "agendaId": "a1", "status": "agreed", "basis": "月額制で合意",
        "evidenceKeys": ["decision"],
    }],
    "questionUpdates": [{
        "questionId": "q1", "status": "answered", "answer": "最低契約期間は12か月",
        "evidenceKeys": ["answer"],
    }],
    "resultUpdates": [
        {"agendaId": "a1", "kind": "answers", "text": "最低契約期間は12か月",
         "evidenceKeys": ["answer"]},
        {"agendaId": "a1", "kind": "decisions", "text": "料金体系は月額制",
         "evidenceKeys": ["decision"]},
    ],
    "agendaProposals": [{
        "clientKey": "timing", "title": "導入時期を決める", "reason": "確認が必要",
        "evidenceKeys": ["timing"],
    }],
    "questionProposals": [{
        "clientKey": "when", "agendaClientKey": "timing", "agendaId": "", "text": "いつ導入するか？",
        "reason": "時期の決定", "evidenceKeys": ["timing"],
    }],
    "suggestions": [{
        "type": "warning", "targetId": "a1", "text": "導入時期が未確認",
        "reason": "会話で確認が必要とされた",
        "payload": {"title": "", "text": "", "agendaId": ""}, "evidenceKeys": ["timing"],
    }],
}
normalized = flow_ai.normalize_live_diff(raw, delta, 500, "gen-live", sample_flow)
assert normalized["meaningful"] and normalized["currentAgendaId"] == "a1"
assert normalized["agendaStatusUpdates"][0]["status"] == "discussed"
assert normalized["agendaResolutionUpdates"][0]["status"] == "agreed"
assert normalized["questionUpdates"][0]["answer"] == "最低契約期間は12か月"
assert len(normalized["resultUpdates"]) == 2
assert normalized["evidence"][0]["transcriptStart"] == 500
assert normalized["evidence"][0]["transcriptGeneration"] == "gen-live"
assert normalized["agendaCreates"][0]["approval"] == "draft"
assert normalized["questionCreates"][0]["agendaId"] == normalized["agendaCreates"][0]["id"]
assert normalized["suggestions"][0]["status"] == "pending"

new_topic = flow_ai.normalize_live_diff({
    "currentAgendaId": "", "currentAgendaClientKey": "staffing",
    "currentAgendaEvidenceKeys": ["topic"],
    "evidence": [live_evidence("採用体制について話します。", "topic", "採用体制について話します。")],
    "agendaStatusUpdates": [], "agendaResolutionUpdates": [], "questionUpdates": [],
    "resultUpdates": [],
    "agendaProposals": [{"clientKey": "staffing", "title": "採用体制を確認する",
                         "reason": "独立した話題", "evidenceKeys": ["topic"]}],
    "questionProposals": [], "suggestions": [],
}, "採用体制について話します。", 0, "g-new", sample_flow)
assert new_topic["agendaCreates"] and new_topic["currentAgendaId"] == new_topic["agendaCreates"][0]["id"]

# Same input yields stable IDs, allowing idempotent retries.
again = flow_ai.normalize_live_diff(json.dumps({"flow": raw}, ensure_ascii=False), delta, 500,
                                    "gen-live", sample_flow)
assert again["evidence"] == normalized["evidence"]

# A plausible-looking Japanese company/product name cannot be introduced when
# it is absent from the exact evidence and current confirmed flow.
entity_delta = "導入先について検討します。"
entity_raw = {
    "currentAgendaId": "", "currentAgendaEvidenceKeys": [],
    "evidence": [live_evidence(entity_delta, "entity", entity_delta)],
    "agendaStatusUpdates": [], "questionUpdates": [],
    "resultUpdates": [{"agendaId": "a1", "kind": "summary",
                       "text": "ラクホブへの導入を検討中", "evidenceKeys": ["entity"]}],
    "agendaProposals": [], "questionProposals": [], "suggestions": [],
}
entity_result = flow_ai.normalize_live_diff(entity_raw, entity_delta, 0, "entity-gen", sample_flow)
assert entity_result["resultUpdates"] == []
assert again["agendaCreates"] == normalized["agendaCreates"]


# Wrong spans, hallucinated numbers/entities, non-agreement decisions, forbidden
# regressions and model-requested dismissals are dropped independently.
bad_raw = {
    "currentAgendaId": "missing",
    "evidence": [
        {**e1, "text": "改変された根拠"}, e3,
    ],
    "agendaStatusUpdates": [{
        "agendaId": "a1", "status": "not_started", "basis": "regress",
        "evidenceKeys": ["timing"],
    }],
    "agendaResolutionUpdates": [{
        "agendaId": "a1", "status": "agreed", "basis": "根拠のない合意",
        "evidenceKeys": ["timing"],
    }],
    "questionUpdates": [{
        "questionId": "q1", "status": "dismissed", "answer": "",
        "evidenceKeys": ["timing"],
    }],
    "resultUpdates": [
        {"agendaId": "a1", "kind": "answers", "text": "契約期間は18か月",
         "evidenceKeys": ["timing"]},
        {"agendaId": "a1", "kind": "decisions", "text": "Acmeを採用する",
         "evidenceKeys": ["timing"]},
    ],
    "agendaProposals": [], "questionProposals": [], "suggestions": [],
}
rejected = flow_ai.normalize_live_diff(bad_raw, delta, 0, "g", sample_flow)
assert not rejected["agendaStatusUpdates"]
assert not rejected["agendaResolutionUpdates"]
assert not rejected["questionUpdates"]
assert not rejected["resultUpdates"]
assert rejected["validationErrors"]


# Manually locked discussion states and summaries are never overwritten.
locked_flow = json.loads(json.dumps(sample_flow))
locked_flow["agendas"][0]["status"] = "discussed"
locked_flow["agendas"][0]["statusLocked"] = True
locked_flow["agendas"][0]["result"]["summary"]["locked"] = True
locked_payload = {
    "currentAgendaId": "a1", "currentAgendaEvidenceKeys": ["timing"], "evidence": [e3],
    "agendaStatusUpdates": [{"agendaId": "a1", "status": "discussing", "basis": "",
                             "evidenceKeys": ["timing"]}],
    "questionUpdates": [],
    "resultUpdates": [{"agendaId": "a1", "kind": "summary", "text": "更新",
                       "evidenceKeys": ["timing"]}],
    "agendaProposals": [], "questionProposals": [], "suggestions": [],
}
locked_result = flow_ai.normalize_live_diff(locked_payload, delta, 0, "g", locked_flow)
assert not locked_result["agendaStatusUpdates"] and not locked_result["resultUpdates"]


# The injection boundary passes the schema and remains provider-independent.
called = {}


def fake_ai(prompt, schema):
    called["prompt"] = prompt
    called["schema"] = schema
    return {"value": 7}


out = flow_ai.call_with_injected_ai(fake_ai, "prompt", {"type": "object"},
                                    lambda payload: {"seen": payload["value"]})
assert out == {"seen": 7} and called["schema"]["type"] == "object"

print("meeting flow AI tests passed")
