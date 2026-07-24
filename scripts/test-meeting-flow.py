#!/usr/bin/env python3
"""Focused tests for the meeting-flow persistence module."""
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from meeting_flow import MeetingFlowStore, RevisionConflict, ValidationError  # noqa: E402


class MeetingFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sid = "20260721-120000"
        self.directory = os.path.join(self.tmp.name, self.sid)
        os.makedirs(self.directory)
        self._write("meta.json", {"id": self.sid, "goal": "価格を決める"})
        self.store = MeetingFlowStore(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name, value):
        with open(os.path.join(self.directory, name), "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False)

    def action(self, flow, action, payload):
        return self.store.apply_action(self.sid, flow["revision"], action, payload)

    def test_empty_and_conservative_migration(self):
        self._write("strategy.json", {"board": {"outcome": "導入可否を決める", "questions": ["予算は？"]}})
        self._write("data.json", {"agenda": ["価格", "時期"], "decisions": ["月額が有力"],
                                  "todos": [{"text": "担当者が確認"}], "open": ["契約期間"]})
        flow = self.store.load(self.sid)
        self.assertEqual(flow["target"]["text"], "導入可否を決める")
        self.assertEqual(flow["questions"][0]["agendaId"], None)
        self.assertEqual([a["approval"] for a in flow["agendas"]], ["draft", "draft"])
        self.assertEqual(flow["unclassifiedResults"]["decisions"][0]["text"], "月額が有力")
        with open(os.path.join(self.directory, "data.json"), encoding="utf-8") as source:
            self.assertEqual(json.load(source)["agenda"], ["価格", "時期"])
        self.assertTrue(os.path.isfile(os.path.join(self.directory, "meeting-flow.json")))

    def test_revision_conflict_contains_current(self):
        flow = self.store.load(self.sid)
        changed = self.action(flow, "target.update", {"text": "新しい着地点"})
        with self.assertRaises(RevisionConflict) as caught:
            self.store.apply_action(self.sid, flow["revision"], "target.update", {"text": "古い更新"})
        self.assertEqual(caught.exception.current["revision"], changed["revision"])
        self.assertEqual(caught.exception.current["target"]["text"], "新しい着地点")

    def test_manual_action_lifecycle(self):
        flow = self.store.load(self.sid)
        flow = self.action(flow, "agenda.create", {"title": "料金体系"})
        flow = self.action(flow, "agenda.create", {"title": "導入時期"})
        first, second = [a["id"] for a in flow["agendas"]]
        flow = self.action(flow, "agenda.reorder", {"agendaIds": [second, first]})
        self.assertEqual(flow["agendas"][0]["id"], second)
        self.assertTrue(flow["agendaOrderLocked"])
        flow = self.action(flow, "agenda.update", {"agendaId": first, "status": "discussing", "current": True})
        self.assertTrue(next(a for a in flow["agendas"] if a["id"] == first)["statusLocked"])
        flow = self.action(flow, "question.create", {"agendaId": first, "text": "最低契約期間は？"})
        qid = flow["questions"][0]["id"]
        flow = self.action(flow, "question.update", {"questionId": qid, "status": "asked"})
        flow = self.action(flow, "question.move", {"questionId": qid, "agendaId": second})
        self.assertEqual(flow["questions"][0]["agendaId"], second)
        flow = self.action(flow, "result.update", {"agendaId": first, "section": "summary", "text": "月額制が有力"})
        agenda = next(a for a in flow["agendas"] if a["id"] == first)
        self.assertTrue(agenda["result"]["summary"]["locked"])
        with self.assertRaises(ValidationError):
            self.action(flow, "agenda.reorder", {"agendaIds": [first]})

    def test_merge_preserves_questions_and_results(self):
        flow = self.store.load(self.sid)
        flow = self.action(flow, "agenda.create", {"title": "価格"}); a1 = flow["agendas"][0]["id"]
        flow = self.action(flow, "agenda.create", {"title": "料金"}); a2 = flow["agendas"][1]["id"]
        flow = self.action(flow, "question.create", {"agendaId": a2, "text": "予算は？"})
        flow = self.action(flow, "result.update", {"agendaId": a2, "section": "actions", "text": "見積もる"})
        flow = self.action(flow, "agenda.merge", {"agendaIds": [a1, a2], "title": "料金体系"})
        self.assertEqual(len(flow["agendas"]), 1)
        self.assertEqual(flow["questions"][0]["agendaId"], a1)
        self.assertEqual(flow["agendas"][0]["result"]["actions"][0]["text"], "見積もる")

    def test_hydrate_final_summary_rebuilds_main_board_and_keeps_manual_locks(self):
        flow = self.store.load(self.sid)
        flow = self.action(flow, "agenda.create", {"title": "料金体系"})
        aid = flow["agendas"][0]["id"]
        flow = self.action(flow, "result.update", {"agendaId": aid, "section": "summary", "text": "手動で確定した要約"})
        final = {"meetingFlow": {"agendas": [
            {"title": "料金体系", "status": "completed", "summary": "AIの最終要約",
             "answers": ["月額制"], "decisions": ["月額10万円"],
             "actions": ["田中：見積書を送る"], "unresolved": []},
            {"title": "導入時期", "status": "deferred", "summary": "来月に再確認",
             "answers": [], "decisions": [], "actions": [], "unresolved": ["開始日"]},
        ]}}
        rebuilt = self.store.hydrate_summary(self.sid, final, finalized=True)
        self.assertEqual(rebuilt["target"]["text"], "価格を決める")
        self.assertEqual([a["title"] for a in rebuilt["agendas"]], ["料金体系", "導入時期"])
        price = rebuilt["agendas"][0]
        self.assertEqual(price["id"], aid)
        self.assertEqual(price["status"], "discussed")
        self.assertEqual(price["resolutionStatus"], "agreed")
        self.assertEqual(price["result"]["summary"]["text"], "手動で確定した要約")
        self.assertEqual(price["result"]["decisions"][0]["text"], "月額10万円")
        revision = rebuilt["revision"]
        with patch("meeting_flow._now", return_value=9999999999):
            self.assertEqual(self.store.hydrate_summary(self.sid, final, finalized=True)["revision"], revision)

    def test_hydrate_legacy_summary_formats_todo_and_populates_results(self):
        summary = {"summary": "価格と導入条件を確認した。", "agenda": ["導入条件"],
                   "points": ["月額制が適切"], "decisions": ["試験導入する"],
                   "todos": [{"who": "佐藤", "what": "申込書を送る", "due": "金曜"}],
                   "open": ["開始日"]}
        rebuilt = self.store.hydrate_summary(self.sid, summary, finalized=True)
        agenda = rebuilt["agendas"][0]
        self.assertEqual(agenda["status"], "discussed")
        self.assertEqual(agenda["resolutionStatus"], "agreed")
        self.assertEqual(agenda["result"]["answers"][0]["text"], "月額制が適切")
        self.assertEqual(agenda["result"]["actions"][0]["text"], "佐藤：申込書を送る（期限：金曜）")

    def test_ai_source_request_is_never_a_meeting_agenda(self):
        placeholder = {
            "meetingFlow": {"agendas": [{
                "title": "会話本文の取り込み（要・原本提供）",
                "status": "not_started",
                "summary": "進行ボードを復元するための文字起こし本文が渡されておらず再構成できません。",
                "unresolved": ["本文がありません"],
            }]}
        }
        rebuilt = self.store.hydrate_summary(self.sid, placeholder, finalized=False)
        self.assertEqual(rebuilt["agendas"], [])

        # Previously persisted AI/migration noise is removed on load, while an
        # identically named agenda explicitly created by the user is preserved.
        raw = rebuilt
        raw["agendas"] = [{
            "id": "bad", "title": "会話本文の取り込み（要・原本提供）",
            "origin": "migrated", "approval": "accepted",
            "summary": "文字起こし本文が不足しており復元できません。",
        }, {
            "id": "manual", "title": "会話本文の取り込み方法を決める",
            "origin": "user", "approval": "accepted",
        }]
        self._write("meeting-flow.json", raw)
        cleaned = self.store.load(self.sid)
        self.assertEqual([a["id"] for a in cleaned["agendas"]], ["manual"])

    def test_discussion_and_agreement_are_independent_and_only_one_is_current(self):
        raw = self.store.load(self.sid)
        raw["agendas"] = [{
            "id": "a1", "title": "料金を決める", "status": "discussing", "current": False,
            "origin": "ai", "approval": "accepted",
            "result": {"decisions": [{"text": "月額制", "origin": "ai"}]},
        }, {
            "id": "a2", "title": "導入時期を決める", "status": "discussing", "current": True,
            "origin": "ai", "approval": "accepted",
            "result": {},
        }]
        self._write("meeting-flow.json", raw)
        migrated = self.store.load(self.sid)
        first, second = migrated["agendas"]
        self.assertEqual((first["status"], first["resolutionStatus"], first["current"]),
                         ("discussed", "agreed", False))
        self.assertEqual((second["status"], second["resolutionStatus"], second["current"]),
                         ("discussing", "pending", True))
        self.assertEqual(sum(a["status"] == "discussing" for a in migrated["agendas"]), 1)

        transcript = "料金の話に戻ります。"
        shifted = self.store.apply_ai_diff(self.sid, {
            "evidence": [{"id": "ev", "transcriptStart": 0, "transcriptEnd": len(transcript),
                          "text": transcript}],
            "currentAgendaId": "a1", "currentAgendaEvidenceIds": ["ev"],
        }, transcript, "g1")
        first, second = shifted["agendas"]
        self.assertTrue(first["current"]); self.assertEqual(first["status"], "discussing")
        self.assertFalse(second["current"]); self.assertEqual(second["status"], "discussed")
        self.assertEqual(first["resolutionStatus"], "agreed")

        new_text = "採用体制の話へ移ります。"
        added = self.store.apply_ai_diff(self.sid, {
            "evidence": [{"id": "ev-new", "transcriptStart": 0, "transcriptEnd": len(new_text),
                          "text": new_text}],
            "agendaCreates": [{"id": "a3", "title": "採用体制を確認する", "origin": "ai",
                               "approval": "draft", "evidenceIds": ["ev-new"]}],
            "currentAgendaId": "a3", "currentAgendaEvidenceIds": ["ev-new"],
        }, new_text, "g2")
        by_id = {a["id"]: a for a in added["agendas"]}
        self.assertEqual(by_id["a1"]["status"], "discussed")
        self.assertTrue(by_id["a3"]["current"]); self.assertEqual(by_id["a3"]["status"], "discussing")
        self.assertEqual(sum(a["status"] == "discussing" for a in added["agendas"]), 1)

        closed = self.store.close_current(self.sid)
        by_id = {a["id"]: a for a in closed["agendas"]}
        self.assertFalse(any(a["current"] for a in closed["agendas"]))
        self.assertFalse(any(a["status"] == "discussing" for a in closed["agendas"]))
        self.assertEqual(by_id["a3"]["status"], "discussed")
        self.assertEqual(by_id["a1"]["resolutionStatus"], "agreed")

    def test_legacy_multi_agenda_does_not_lump_global_results_into_first(self):
        summary = {"summary": "会議全体の要旨", "agenda": ["現状確認", "導入方針"],
                   "points": ["現状の課題"], "decisions": ["導入する"],
                   "todos": [{"who": "佐藤", "what": "見積もる"}], "open": ["開始日"]}
        rebuilt = self.store.hydrate_summary(self.sid, summary, finalized=True)
        self.assertEqual([a["title"] for a in rebuilt["agendas"]], ["現状確認", "導入方針"])
        self.assertEqual(rebuilt["summaryHydration"]["mode"], "legacy")
        self.assertTrue(all(not any(a["result"][key] for key in ("answers", "decisions", "actions", "unresolved"))
                            and not a["result"]["summary"]["text"] for a in rebuilt["agendas"]))

        distributed = {"meetingFlow": {"agendas": [
            {"title": "現状確認", "status": "completed", "answers": ["現状の課題"]},
            {"title": "導入方針", "status": "completed", "decisions": ["導入する"]},
        ]}}
        rebuilt = self.store.hydrate_summary(self.sid, distributed, finalized=True)
        revision = rebuilt["revision"]
        self.assertEqual(rebuilt["summaryHydration"]["mode"], "per_agenda")
        kept = self.store.hydrate_summary(self.sid, summary, finalized=True)
        self.assertEqual(kept["revision"], revision)
        self.assertEqual(kept["agendas"][1]["result"]["decisions"][0]["text"], "導入する")

    def test_ai_respects_locks_requires_evidence_and_deduplicates(self):
        flow = self.store.load(self.sid)
        flow = self.action(flow, "agenda.create", {"title": "料金体系"}); aid = flow["agendas"][0]["id"]
        flow = self.action(flow, "agenda.update", {"agendaId": aid, "status": "deferred"})
        flow = self.action(flow, "result.update", {"agendaId": aid, "section": "summary", "text": "手動の要約"})
        flow = self.action(flow, "question.create", {"agendaId": aid, "text": "初期費用は？"}); qid = flow["questions"][0]["id"]
        transcript = "初期費用は発生しません。月額制で合意しました。"
        start = transcript.index("初期費用")
        evidence_text = "初期費用は発生しません。"
        diff = {"evidence": [{"id": "ev-1", "transcriptStart": start, "transcriptEnd": start + len(evidence_text), "text": evidence_text}],
                "currentAgendaId": aid,
                "currentAgendaEvidenceIds": ["ev-1"],
                "agendaStatusUpdates": [{"agendaId": aid, "status": "discussed"}],
                "questionUpdates": [{"questionId": qid, "status": "answered", "answer": "発生しない", "evidenceIds": ["ev-1"]}],
                "resultUpdates": [{"agendaId": aid, "section": "summary", "text": "AI要約", "evidenceIds": ["ev-1"]},
                                  {"agendaId": aid, "section": "decisions", "text": "根拠なし決定"},
                                  {"agendaId": aid, "section": "answers", "text": "初期費用なし", "evidenceIds": ["ev-1"]}],
                "agendaProposals": [{"title": "導入時期"}, {"title": "導入時期"}],
                "questionProposals": [{"agendaId": aid, "text": "契約期間は？"}, {"agendaId": aid, "text": "契約期間は？"}]}
        updated = self.store.apply_ai_diff(self.sid, diff, transcript, "gen-1")
        agenda = next(a for a in updated["agendas"] if a["id"] == aid)
        self.assertEqual(agenda["status"], "deferred")
        self.assertEqual(agenda["result"]["summary"]["text"], "手動の要約")
        self.assertEqual(agenda["result"]["decisions"], [])
        self.assertEqual(agenda["result"]["answers"][0]["text"], "初期費用なし")
        self.assertEqual(next(q for q in updated["questions"] if q["id"] == qid)["status"], "answered")
        self.assertEqual(len([a for a in updated["agendas"] if a["title"] == "導入時期"]), 1)
        self.assertEqual(len([q for q in updated["questions"] if q["text"] == "契約期間は？"]), 1)
        unchanged = self.store.apply_ai_diff(self.sid, diff, transcript, "gen-1")
        self.assertEqual(unchanged["revision"], updated["revision"])

    def test_invalid_evidence_and_generation_are_not_accepted(self):
        flow = self.store.load(self.sid)
        flow = self.action(flow, "agenda.create", {"title": "料金"}); aid = flow["agendas"][0]["id"]
        flow = self.action(flow, "question.create", {"agendaId": aid, "text": "無料？"}); qid = flow["questions"][0]["id"]
        bad = {"evidence": [{"id": "bad", "transcriptStart": 0, "transcriptEnd": 2, "text": "不一致"}],
               "questionUpdates": [{"questionId": qid, "status": "answered", "evidenceIds": ["bad"]}]}
        updated = self.store.apply_ai_diff(self.sid, bad, "有料です", "new")
        self.assertEqual(next(q for q in updated["questions"] if q["id"] == qid)["status"], "queued")

    def test_ai_normalizer_contract_aliases_and_absolute_cursor(self):
        flow = self.store.load(self.sid)
        diff = {"targetUpdate": {"text": "価格を決める", "successCriteria": "予算内", "origin": "user", "locked": True},
                "agendaCreates": [{"id": "agenda-stable", "title": "価格", "order": 4200,
                                   "origin": "user", "approval": "accepted"}],
                "questionCreates": [{"id": "question-stable", "agendaId": "agenda-stable", "order": 2300,
                                     "text": "予算は？", "origin": "user", "approval": "accepted"}],
                "transcriptCursor": 4}
        updated = self.store.apply_ai_diff(self.sid, diff, "abcdefghij", "gen-a")
        self.assertEqual(updated["target"]["text"], "価格を決める")
        self.assertTrue(updated["target"]["locked"])
        self.assertEqual(updated["agendas"][0]["id"], "agenda-stable")
        self.assertEqual(updated["questions"][0]["id"], "question-stable")
        self.assertEqual(updated["transcriptCursor"], 4)
        # Same generation never regresses an absolute cursor.
        updated = self.store.apply_ai_diff(self.sid, {"transcriptCursor": 2}, "abcdefghij", "gen-a")
        self.assertEqual(updated["transcriptCursor"], 4)
        # A new generation starts its own absolute coordinate space.
        updated = self.store.apply_ai_diff(self.sid, {"transcriptCursor": 1}, "xyz", "gen-b")
        self.assertEqual(updated["transcriptCursor"], 1)
        # An inferred target cannot replace the explicit user target.
        updated2 = self.store.apply_ai_diff(self.sid, {"targetUpdate": {"text": "AIの案", "origin": "ai"},
                                                           "transcriptCursor": 1}, "xyz", "gen-b")
        self.assertEqual(updated2["target"]["text"], "価格を決める")
        self.assertEqual(updated2["revision"], updated["revision"])

    def test_dismissed_ai_agenda_is_not_reproposed(self):
        flow = self.store.load(self.sid)
        flow = self.store.apply_ai_diff(self.sid, {"agendaCreates": [{"title": "価格を決める", "origin": "ai", "approval": "draft"}]})
        aid = flow["agendas"][0]["id"]
        flow = self.action(flow, "agenda.delete", {"agendaId": aid})
        updated = self.store.apply_ai_diff(self.sid, {"agendaCreates": [{"title": "価格を決める", "origin": "ai", "approval": "draft"}]})
        self.assertEqual(updated["agendas"], [])
        self.assertEqual(updated["revision"], flow["revision"])

    def test_dismissed_ai_question_is_not_reproposed(self):
        flow = self.store.load(self.sid)
        flow = self.action(flow, "agenda.create", {"title": "価格"})
        aid = flow["agendas"][0]["id"]
        flow = self.store.apply_ai_diff(self.sid, {"questionCreates": [
            {"text": "予算はいくらですか", "agendaId": aid, "origin": "ai", "approval": "draft"}]})
        qid = flow["questions"][0]["id"]
        flow = self.action(flow, "question.dismiss", {"questionId": qid})
        # 却下済みと同内容は、別議題・未分類として出し直されても復活しない。
        updated = self.store.apply_ai_diff(self.sid, {"questionCreates": [
            {"text": "予算はいくらですか", "origin": "ai", "approval": "draft"}]})
        active = [q for q in updated["questions"] if q["approval"] != "dismissed"]
        self.assertEqual(active, [])
        # ユーザーが明示した同内容の質問は却下履歴に妨げられない。
        restored = self.store.apply_ai_diff(self.sid, {"questionCreates": [
            {"text": "予算はいくらですか", "origin": "user", "approval": "accepted"}]})
        self.assertTrue(any(q["approval"] == "accepted" and q["origin"] == "user"
                            for q in restored["questions"]))

    def test_dismissed_suggestion_with_new_reason_is_not_readded(self):
        flow = self.store.load(self.sid)
        flow = self.store.apply_ai_diff(self.sid, {"suggestions": [
            {"type": "unstuck", "text": "評価軸で2案を比較する", "reason": "議論が平行線"}]})
        suggestion_id = flow["suggestions"][0]["id"]
        flow = self.action(flow, "suggestion.dismiss", {"suggestionId": suggestion_id})
        updated = self.store.apply_ai_diff(self.sid, {"suggestions": [
            {"type": "unstuck", "text": "評価軸で2案を比較する", "reason": "言い回しだけ違う理由"}]})
        pending = [s for s in updated["suggestions"] if s["status"] == "pending"]
        self.assertEqual(pending, [])

    def test_long_research_suggestion_has_bounded_fingerprint(self):
        """長い調査結果もfingerprintの160文字制約で保存失敗しない。"""
        body = "調査結果" * 250
        reason = "会議中の追加調査" * 80
        flow = self.store.load(self.sid)
        updated = self.store.apply_ai_diff(self.sid, {"suggestions": [
            {"type": "research", "text": body, "reason": reason}]})
        self.assertEqual(len(updated["suggestions"]), 1)
        self.assertLessEqual(len(updated["suggestions"][0]["fingerprint"]), 160)
        self.assertTrue(updated["suggestions"][0]["text"].startswith("調査結果"))

    def test_per_session_lock_serializes_writers(self):
        flow = self.store.load(self.sid)
        outcomes = []
        def write(title):
            try: outcomes.append(self.store.apply_action(self.sid, flow["revision"], "agenda.create", {"title": title}))
            except RevisionConflict as e: outcomes.append(e.current)
        threads = [threading.Thread(target=write, args=("議題%d" % i,)) for i in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        final = self.store.load(self.sid)
        self.assertEqual(final["revision"], flow["revision"] + 1)
        self.assertEqual(len(final["agendas"]), 1)
        self.assertEqual(len(outcomes), 2)

    def test_path_traversal_rejected(self):
        with self.assertRaises(ValidationError): self.store.load("../outside")

    def test_invalid_ids_duplicates_and_corrupt_source_are_rejected(self):
        flow = self.store.load(self.sid)
        flow = self.action(flow, "agenda.create", {"title": "価格"})
        flow = self.action(flow, "agenda.create", {"title": "導入時期"})
        with self.assertRaises(ValidationError):
            self.action(flow, "agenda.reorder", {"agendaIds": [{"bad": True}, flow["agendas"][0]["id"]]})
        with self.assertRaises(ValidationError):
            self.action(flow, "agenda.merge", {"agendaIds": [flow["agendas"][0]["id"]] * 2})
        with self.assertRaises(ValidationError):
            self.action(flow, "agenda.update", {"agendaId": flow["agendas"][1]["id"], "title": "価格"})

        path = os.path.join(self.directory, "meeting-flow.json")
        with open(path, "w", encoding="utf-8") as stream:
            stream.write("{broken")
        with self.assertRaises(ValidationError):
            self.store.load(self.sid)
        with open(path, encoding="utf-8") as stream:
            self.assertEqual(stream.read(), "{broken")
        with open(path, "w", encoding="utf-8") as stream:
            json.dump({"version": 999}, stream)
        with self.assertRaises(ValidationError):
            self.store.load(self.sid)


if __name__ == "__main__":
    unittest.main(verbosity=2)
