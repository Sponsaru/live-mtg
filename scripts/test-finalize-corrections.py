#!/usr/bin/env python3
"""Regression coverage for pre-finalization review and confirmed corrections."""

import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


with tempfile.TemporaryDirectory(prefix="live-mtg-finalize-corrections-") as tmp:
    root = Path(tmp)
    os.environ.update({
        "RUN": str(root),
        "MEETINGS_DIR": str(root / "meetings"),
        "DRIVE_SYNC_DIR": str(root / "drive"),
        "PROFILE_MD": str(root / "profile.md"),
        "PLAYBOOK_DIR": str(root / "playbooks"),
    })

    import server

    html = (ROOT / "index.html").read_text(encoding="utf-8")

    def make_session(sid, transcript):
        meeting = Path(server.sdir(sid))
        (meeting / "audio").mkdir(parents=True)
        (meeting / "audio" / "0001.webm").write_bytes(b"recorded-audio")
        (meeting / "meta.json").write_text(
            json.dumps({"id": sid, "title": "Correction regression"}, ensure_ascii=False),
            encoding="utf-8",
        )
        (meeting / "data.json").write_text("{}", encoding="utf-8")
        ok, frozen, signature = server._prepare_final_source(
            sid, {"status": "ready", "transcript": transcript}, force=True
        )
        assert ok, frozen
        return meeting, frozen, signature

    # Long transcripts must be inspected to the very end, while adjacent AI
    # windows retain enough overlap not to lose a term at a split boundary.
    late_term = "LateCompany42"
    long_text = "あ" * 26000 + " " + late_term + " の確認 " + late_term
    windows = server._prep_windows(long_text, size=12000, overlap=500)
    assert len(windows) == 3
    assert all(left[-500:] == right[:500] for left, right in zip(windows, windows[1:])), \
        "adjacent prep windows must preserve their configured overlap"
    assert late_term in windows[-1], "the 20k+ tail of the transcript was not inspected"
    machine_sources = {row["source"] for row in server._machine_prep_term_questions(long_text)}
    assert late_term in machine_sources, "machine term scan missed a repeated term after 20k chars"

    normalized = server._normalize_prep_questions([
        {"q": "Acme?", "kind": "term", "source": "Acme", "guess": "ACME"},
        {"q": "Acme overlap?", "kind": "term", "source": "ACME", "guess": ""},
    ])
    assert len(normalized) == 1, "overlapping windows must not duplicate a term candidate"

    # Long meetings must not turn the machine safety net into 100+ mandatory
    # questions. High-confidence answers are prefilled; only the riskiest
    # machine candidates remain pending and can be reviewed confidence-first.
    review_rows = [
        {"q": "会議の目的は？", "kind": "premise", "guess": "AX導入の方針を決める",
         "confidence": .94},
        {"q": "「創益計算書」の表記は？", "kind": "term", "source": "創益計算書",
         "guess": "損益計算書", "confidence": .96},
    ] + [
        {"q": "「怪しい語%d」の正しい表記を確認してください" % i,
         "kind": "term", "source": "怪しい語%d" % i, "guess": "", "origin": "machine"}
        for i in range(40)
    ]
    review_text = " ".join(row.get("source", "") for row in review_rows)
    review_questions, auto_answers, auto_candidates, review_stats = server._prepare_prep_review(
        review_rows, review_text, {}, {})
    assert auto_answers[next(row["id"] for row in review_questions if row["kind"] == "premise")] == "AX導入の方針を決める"
    corrected = next(row for row in review_questions if row.get("source") == "創益計算書")
    assert auto_candidates[corrected["id"]]["status"] == "replace"
    assert auto_candidates[corrected["id"]]["to"] == "損益計算書"
    cautious_rows, _, cautious_candidates, cautious_stats = server._prepare_prep_review([
        {"q": "「織田社長」の表記は？", "kind": "term", "source": "小田社長",
         "guess": "織田社長", "confidence": .90},
    ], "小田社長", {}, {})
    assert cautious_rows[0]["autoResolved"] is False
    assert cautious_rows[0]["id"] not in cautious_candidates
    assert cautious_stats["pending"] == 1, \
        "a merely plausible name replacement must stay visible for human review"
    obvious_terms = ["スポンサル", "ノウハウ", "バリバリ", "ブッキング", "バーティカル"]
    obvious_rows, _, obvious_candidates, obvious_stats = server._prepare_prep_review([
        {"q": "「%s」の正しい表記を確認してください" % term,
         "kind": "term", "source": term, "guess": "", "origin": "machine", "confidence": .38}
        for term in obvious_terms
    ], " ".join(obvious_terms), {}, {})
    assert all(row["confidence"] == .98 and row["autoResolved"] for row in obvious_rows)
    assert all(obvious_candidates[row["id"]] == {"status": "keep", "to": row["source"], "auto": True}
               for row in obvious_rows), \
        "well-formed known terms must preselect keep even in legacy 38% review rows"
    assert obvious_stats["pending"] == 0
    machine_common = server._machine_prep_term_questions(" ".join(term + " " + term for term in obvious_terms))
    assert not machine_common, "known common terms must not create new spelling-review cards"
    pending_machine = [row for row in review_questions
                       if row["origin"] == "machine" and row["id"] not in auto_candidates]
    assert len(pending_machine) <= server.PREP_MACHINE_REVIEW_LIMIT
    assert review_stats["pending"] <= server.PREP_MACHINE_REVIEW_LIMIT

    # Auto-generated titles already contain the date, so human-facing output
    # names must not repeat it a second time.
    assert server._artifact_prefix("20260723-163000", {
        "created": "2026-07-23T16:30:00", "title": "会議 2026-07-23 16:30",
    }) == "20260723_会議_1630"
    assert server._artifact_prefix("20260723-163000", {
        "created": "2026-07-23T16:30:00", "title": "トライアングル定例",
    }) == "20260723_トライアングル定例"

    # Low-confidence guesses remain suggestions, while high-confidence rows may
    # be server-prefilled and are visibly filterable as automatic answers.
    form_start = html.index("function buildFinalizeForm")
    form_end = html.index("function openSpeakerLog", form_start)
    form = html[form_start:form_end]
    submit_start = html.index("$('fok').onclick")
    submit_end = html.index("$('pdf').onclick", submit_start)
    submit = html[submit_start:submit_end]
    assert "AIの推定と確認結果を仮入力済みです" in form and "fq-suggest" in form
    assert "applyFinalizeReviewFilters" in form and "reviewfilter" in form and "reviewsort" in form
    assert "data-confidence" in form and "data-auto" in form
    assert "status=decision.status||proposedStatus" in form
    assert "hasSavedAnswer?answers[id]:(x.guess||'')" in form, \
        "AI premise/interpretation guesses must start inside the editable textarea"
    assert "proposedStatus=suggested?" in form and "?'keep':'replace'" in form and "data-provisional" in form, \
        "AI term guesses must preselect keep/replace while remaining visibly reviewable"
    assert "const resolved=hasSavedAnswer&&" in form, \
        "a low-confidence prefill must remain visible in the needs-review filter"
    assert "status==='pending'" in submit, "pending term decisions must block finalization"

    # Validation covers every explicit candidate decision, manual corrections,
    # stale audio, conflicts, and cycles.
    source_text = "KEEPOLD REPOLD UNKOLD EXOLD MANOLD A B"
    meeting, frozen, signature = make_session("validation", source_text)
    questions = [
        {"id": "premise", "q": "前提を確認", "kind": "premise"},
        {"id": "keep", "q": "KEEPOLD?", "kind": "term", "source": "KEEPOLD"},
        {"id": "replace", "q": "REPOLD?", "kind": "term", "source": "REPOLD"},
        {"id": "unknown", "q": "UNKOLD?", "kind": "term", "source": "UNKOLD"},
        {"id": "exclude", "q": "EXOLD?", "kind": "term", "source": "EXOLD"},
    ]
    prep = {
        "formVersion": server.PREP_FORM_VER,
        "sourceSignature": signature,
        "questions": questions,
    }
    (meeting / "prep.json").write_text(json.dumps(prep, ensure_ascii=False), encoding="utf-8")
    answers = {"premise": "確認済み"}
    decisions = {
        "keep": {"status": "keep", "to": ""},
        "replace": {"status": "replace", "to": "REPNEW"},
        "unknown": {"status": "unknown", "to": "guessed-value-must-not-apply"},
        "exclude": {"status": "exclude", "to": "ignored-value"},
    }

    pending = dict(decisions)
    pending["replace"] = {"status": "pending", "to": "REPNEW"}
    ok, message, _ = server._validate_prep_submission(
        "validation", answers, pending, [], signature
    )
    assert not ok and "未確認" in message, "pending candidates must be rejected server-side"

    ok, message, corrections = server._validate_prep_submission(
        "validation", answers, decisions,
        [{"from": "MANOLD", "to": "MANNEW"}], signature
    )
    assert ok, message
    mapping = {row["from"]: row["to"] for row in corrections}
    assert "KEEPOLD" not in mapping
    assert mapping["REPOLD"] == "REPNEW"
    assert "UNKOLD" not in mapping, "unknown must remain unresolved, not confirm an AI guess"
    assert mapping["EXOLD"] == ""
    assert mapping["MANOLD"] == "MANNEW"

    ok, message, _ = server._validate_prep_submission(
        "validation", answers, decisions, [{"from": "MANOLD", "to": ""}], signature
    )
    assert not ok and "両方" in message, "half-filled manual corrections must be rejected"

    ok, message, _ = server._validate_prep_submission(
        "validation", answers, decisions,
        [{"from": "A", "to": "B"}, {"from": "B", "to": "A"}], signature
    )
    assert not ok and "循環" in message, "cyclic corrections must be rejected"

    # Chains (A→B plus B→C) make A's final spelling ambiguous under one-pass
    # replacement and previously either aborted the finalize or over-corrected
    # A-derived text to C. They must be rejected with an actionable message.
    ok, message, _ = server._validate_prep_submission(
        "validation", answers, decisions,
        [{"from": "A", "to": "B"}, {"from": "B", "to": "CHAINNEW"}], signature
    )
    assert not ok and "連鎖" in message and "直接指定" in message, \
        "chained corrections must be rejected with a clear instruction"

    (meeting / "audio" / "0002.webm").write_bytes(b"new-audio-after-review")
    ok, message, _ = server._validate_prep_submission(
        "validation", answers, decisions, [], signature
    )
    assert not ok and "録音内容が更新" in message, \
        "answers prepared for an old audio signature must be rejected"

    # Replacement is a single simultaneous pass: longest sources win and a
    # replacement containing another source is not rewritten a second time.
    simultaneous = [
        {"from": "ABC Pro", "to": "Premium"},
        {"from": "ABC", "to": "ABC Japan"},
        {"from": "REMOVE", "to": ""},
    ]
    replaced = server._apply_confirmed_corrections(
        "ABC Pro と ABC と REMOVE", simultaneous
    )
    assert replaced == "Premium と ABC Japan と ", replaced
    bounded = server._apply_confirmed_corrections(
        "ABC / ABCD / XABCY", [{"from": "ABC", "to": "RightCo"}]
    )
    assert bounded == "RightCo / ABCD / XABCY", \
        "an explicit ASCII correction must not corrupt a longer identifier"

    nested = {
        "summary": "WRONGCO",
        "agenda": [{"title": "WRONGCO", "points": ["WRONGCO"]}],
        "todos": [{"who": "WRONGCO", "what": "WRONGCO", "due": "WRONGCO"}],
        "log": [{"who": "WRONGCO", "text": "WRONGCO"}],
        "diagram": "WRONGCO --> WRONGCO",
        "speakers": ["WRONGCO"],
    }
    corrected_nested = server._apply_confirmed_corrections(
        nested, [{"from": "WRONGCO", "to": "RightCo"}]
    )
    assert "WRONGCO" not in json.dumps(corrected_nested, ensure_ascii=False)

    # A prepared source is immutable while its audio signature is unchanged.
    fixed_meeting, fixed_text, fixed_signature = make_session("fixed-source", "FROZEN WRONGCO")
    ok, second_text, second_signature = server._prepare_final_source(
        "fixed-source", {"status": "ready", "transcript": "DIFFERENT"}, force=False
    )
    assert ok and second_text == fixed_text and second_signature == fixed_signature

    ai_calls = []
    server._ai_text = lambda *_args, **_kwargs: ai_calls.append(True) or "{}"
    (fixed_meeting / "audio" / "0002.webm").write_bytes(b"new-audio")
    ok, message = server.finalize_meeting("fixed-source", source_signature=fixed_signature)
    assert not ok and "録音内容が更新" in message and not ai_calls, \
        "stale frozen sources must be refused before calling the AI"

    # The signature is checked again at commit, not just before the slow AI
    # call. Audio accepted while the AI is running invalidates the snapshot and
    # must leave no apparently successful final artifact.
    commit_meeting, _, commit_signature = make_session("commit-stale", "COMMIT SOURCE")
    def stale_during_ai(_prompt, **_kwargs):
        (commit_meeting / "audio" / "0002.webm").write_bytes(b"arrived-during-ai")
        return json.dumps({"summary": "stale output"}, ensure_ascii=False)
    server._ai_text = stale_during_ai
    ok, message = server.finalize_meeting("commit-stale", source_signature=commit_signature)
    assert not ok and "清書中に録音" in message, message
    assert not (commit_meeting / "final.json").exists(), \
        "a stale final snapshot was committed after audio changed"

    # Speaker IDs must be mapped as tokens, never as prefixes of another ID.
    speaker_value = server._apply_speaker_map(
        "SPEAKER_00 / SPEAKER_001 / XSPEAKER_00", {"SPEAKER_00": "Alice"}
    )
    assert speaker_value == "Alice / SPEAKER_001 / XSPEAKER_00", speaker_value

    # The AI may regenerate an old spelling in any output field. The confirmed
    # correction must be applied after JSON parsing as well as before prompting.
    final_meeting, _, final_signature = make_session(
        "all-fields", "SPEAKER_00: WRONGCO の案件を確認"
    )
    ai_output = {
        "summary": "WRONGCO by SPEAKER_00; keep SPEAKER_001",
        "agenda": [{
            "title": "WRONGCO",
            "points": ["WRONGCO"],
            "decisions": ["WRONGCO"],
        }],
        "todos": [{"who": "WRONGCO", "what": "WRONGCO", "due": "WRONGCO"}],
        "open": ["WRONGCO"],
        "diagram": "WRONGCO --> WRONGCO",
        "speakers": ["SPEAKER_00", "SPEAKER_001", "WRONGCO"],
        "log": [{"who": "SPEAKER_00", "text": "WRONGCO"}],
    }
    prompts = []

    def fake_ai(prompt, **_kwargs):
        prompts.append(prompt)
        return json.dumps(ai_output, ensure_ascii=False)

    server._ai_text = fake_ai
    confirmed = [{"from": "WRONGCO", "to": "RightCo", "status": "replace"}]
    ok, message = server.finalize_meeting(
        "all-fields", speaker_map={"SPEAKER_00": "Alice"},
        corrections=confirmed, source_signature=final_signature
    )
    assert ok, message
    assert prompts and "RightCo" in prompts[0] and "WRONGCO" not in prompts[0], \
        "the frozen transcript must be corrected before it is sent to the AI"
    final_json = (final_meeting / "final.json").read_text(encoding="utf-8")
    assert "WRONGCO" not in final_json, \
        "confirmed old spellings must be absent from every final JSON field"
    final_obj = json.loads(final_json)
    assert "Alice" in json.dumps(final_obj, ensure_ascii=False)
    assert "SPEAKER_001" in json.dumps(final_obj, ensure_ascii=False), \
        "mapping SPEAKER_00 must not corrupt SPEAKER_001"
    assert "WRONGCO" not in (final_meeting / "transcript-full.txt").read_text(encoding="utf-8")

    # A finalized snapshot blocks late live patches, including after the
    # in-memory finalizing flag is released. New audio re-enables live updates.
    original_final_data = (final_meeting / "data.json").read_text(encoding="utf-8")
    server.finalizing_sessions.add("all-fields")
    server._merge_patch_to_disk("all-fields", {"summary": "late while finalizing"}, "late")
    server.finalizing_sessions.discard("all-fields")
    assert (final_meeting / "data.json").read_text(encoding="utf-8") == original_final_data
    server._merge_patch_to_disk("all-fields", {"summary": "late after finalize"}, "late")
    assert (final_meeting / "data.json").read_text(encoding="utf-8") == original_final_data, \
        "a queued live patch overwrote the successful final snapshot"
    server._append_context_note("all-fields", "清書後に追加した新しい補足", "live")
    assert not server._final_snapshot_current("all-fields"), \
        "a new persisted live note must release final snapshot protection"
    (final_meeting / "audio" / "0002.webm").write_bytes(b"new-meeting-audio")
    assert not server._final_snapshot_current("all-fields"), \
        "new audio must release final snapshot protection"

    # Preparing a new source after additional audio is not itself a successful
    # finalization. An older final.json must not make prep/cancel/failure freeze
    # all subsequent live patches.
    ok, _, newer_signature = server._prepare_final_source(
        "all-fields", {"status": "ready", "transcript": "NEW SOURCE"}, force=True
    )
    assert ok and newer_signature != final_signature
    assert not server._final_snapshot_current("all-fields"), \
        "opening prep falsely marked the newer audio snapshot as finalized"

    # Legacy finalized meetings have no new marker. Upgrade migration must keep
    # their polished data authoritative until genuinely newer audio/transcript
    # is added.
    legacy = Path(server.sdir("legacy-final"))
    (legacy / "audio").mkdir(parents=True)
    (legacy / "audio" / "inc_1600000000000.webm").write_bytes(b"legacy-audio")
    (legacy / "meta.json").write_text(json.dumps({"id": "legacy-final", "title": "Legacy"}), encoding="utf-8")
    (legacy / "transcript.txt").write_text("legacy transcript", encoding="utf-8")
    legacy_json = json.dumps({"summary": "legacy polished"}, ensure_ascii=False)
    (legacy / "data.json").write_text(legacy_json, encoding="utf-8")
    (legacy / "final.json").write_text(legacy_json, encoding="utf-8")
    old_time = 1_700_000_000
    os.utime(legacy / "audio" / "inc_1600000000000.webm", (old_time, old_time))
    os.utime(legacy / "transcript.txt", (old_time, old_time))
    os.utime(legacy / "final.json", (old_time + 10, old_time + 10))
    assert server._final_snapshot_current("legacy-final"), \
        "upgrade did not protect a legacy polished meeting"
    server._merge_patch_to_disk("legacy-final", {"summary": "backlog overwrite"}, "late")
    assert (legacy / "data.json").read_text(encoding="utf-8") == legacy_json

    # Legacy migration and incoming audio share the audio lock. Pause exactly
    # before migration calculates its snapshot signature, then start a new
    # chunk save. The save must wait; otherwise the old final could be marked
    # as covering the newly arrived audio and freeze live updates indefinitely.
    legacy_race = Path(server.sdir("legacy-final-race"))
    (legacy_race / "audio").mkdir(parents=True)
    (legacy_race / "audio" / "inc_1600000000000.webm").write_bytes(b"legacy-audio")
    (legacy_race / "meta.json").write_text(
        json.dumps({"id": "legacy-final-race", "title": "Legacy race"}), encoding="utf-8"
    )
    (legacy_race / "transcript.txt").write_text("legacy transcript", encoding="utf-8")
    (legacy_race / "data.json").write_text(legacy_json, encoding="utf-8")
    (legacy_race / "final.json").write_text(legacy_json, encoding="utf-8")
    os.utime(legacy_race / "audio" / "inc_1600000000000.webm", (old_time, old_time))
    os.utime(legacy_race / "transcript.txt", (old_time, old_time))
    os.utime(legacy_race / "final.json", (old_time + 10, old_time + 10))
    signature_started = threading.Event()
    release_signature = threading.Event()
    chunk_saved = threading.Event()
    original_source_signature = server._final_source_signature

    def paused_source_signature(sid):
        if sid == "legacy-final-race" and not signature_started.is_set():
            signature_started.set()
            assert release_signature.wait(2), "legacy migration signature was not released"
        return original_source_signature(sid)

    server._final_source_signature = paused_source_signature
    merge_thread = threading.Thread(
        target=server._merge_patch_to_disk,
        args=("legacy-final-race", {"summary": "backlog overwrite"}, "late"),
    )
    save_thread = threading.Thread(
        target=lambda: (server.save_incoming_chunk(
            "legacy-final-race", b"new-audio", chunk_id="1700000000000-1-race"
        ), chunk_saved.set()),
    )
    try:
        merge_thread.start()
        assert signature_started.wait(2), "legacy migration did not reach snapshot calculation"
        save_thread.start()
        assert not chunk_saved.wait(.1), \
            "incoming audio bypassed the legacy migration snapshot lock"
        release_signature.set()
        merge_thread.join(2)
        save_thread.join(2)
    finally:
        release_signature.set()
        server._final_source_signature = original_source_signature
    assert not merge_thread.is_alive() and not save_thread.is_alive(), \
        "legacy migration and incoming audio deadlocked"
    assert chunk_saved.is_set(), "incoming audio was not saved after migration released its lock"
    assert not server._final_snapshot_current("legacy-final-race"), \
        "old final was falsely marked as covering concurrently arriving audio"
    assert (legacy_race / "data.json").read_text(encoding="utf-8") == legacy_json

    # A live note updates transcript.txt without adding meeting audio. If that
    # note is newer than the legacy final, migration must leave live analysis
    # enabled instead of misclassifying the old final as current.
    legacy_note = Path(server.sdir("legacy-note-after-final"))
    (legacy_note / "audio").mkdir(parents=True)
    (legacy_note / "audio" / "inc_1600000000000.webm").write_bytes(b"legacy-audio")
    (legacy_note / "meta.json").write_text(
        json.dumps({"id": "legacy-note-after-final", "title": "Legacy note"}), encoding="utf-8"
    )
    (legacy_note / "transcript.txt").write_text("legacy transcript\nnew live note", encoding="utf-8")
    (legacy_note / "data.json").write_text(legacy_json, encoding="utf-8")
    (legacy_note / "final.json").write_text(legacy_json, encoding="utf-8")
    os.utime(legacy_note / "audio" / "inc_1600000000000.webm", (old_time, old_time))
    os.utime(legacy_note / "final.json", (old_time + 10, old_time + 10))
    os.utime(legacy_note / "transcript.txt", (old_time + 20, old_time + 20))
    assert not server._final_snapshot_current("legacy-note-after-final"), \
        "a live note newer than a legacy final was incorrectly frozen out"
    assert not (legacy_note / "finalized-source.json").exists(), \
        "legacy migration persisted a false finalized marker over a newer live note"

    # The HTTP route fixes its sid before validation. Simulate another tab
    # switching current_id during validation and ensure meeting B's prep ledger
    # is never overwritten with meeting A's answers.
    route_a, _, route_signature = make_session("route-a", "ROUTE A")
    route_b, _, _ = make_session("route-b", "ROUTE B")
    prep_a = {"formVersion": server.PREP_FORM_VER, "sourceSignature": route_signature, "questions": []}
    prep_b = {"formVersion": server.PREP_FORM_VER, "sourceSignature": "b", "questions": [], "answers": {"b": "keep"}}
    (route_a / "prep.json").write_text(json.dumps(prep_a), encoding="utf-8")
    (route_b / "prep.json").write_text(json.dumps(prep_b), encoding="utf-8")
    route_b_before = (route_b / "prep.json").read_bytes()
    finalized_sids = []
    original_validate = server._validate_prep_submission
    original_finalize = server.finalize_meeting
    original_mark = server._mark_finalized_live_progress
    original_sync_drive = server.sync_to_drive
    original_sync_project = server.sync_to_project
    def switch_during_validation(sid, *_args):
        assert sid == "route-a"
        server.current_id = "route-b"
        return True, "", []
    server._validate_prep_submission = switch_during_validation
    server.finalize_meeting = lambda sid, *_args, **_kwargs: finalized_sids.append(sid) or (True, "ok")
    server._mark_finalized_live_progress = lambda *_args: True
    server.sync_to_drive = lambda *_args: None
    server.sync_to_project = lambda *_args: None
    server.current_id = "route-a"
    body = json.dumps({"sid": "route-a", "answers": {}, "candidateAnswers": {},
                       "corrections": [], "speakerMap": {},
                       "sourceSignature": route_signature}).encode("utf-8")
    handler = object.__new__(server.H)
    handler.path = "/api/finalize"
    handler.headers = {"Content-Length": str(len(body)), "Content-Type": "application/json"}
    handler.rfile = io.BytesIO(body)
    response = {}
    handler._send = lambda code, payload, ctype="application/json; charset=utf-8": response.update(code=code, payload=payload)
    try:
        handler.do_POST()
    finally:
        server._validate_prep_submission = original_validate
        server.finalize_meeting = original_finalize
        server._mark_finalized_live_progress = original_mark
        server.sync_to_drive = original_sync_drive
        server.sync_to_project = original_sync_project
    assert finalized_sids == ["route-a"], "finalize switched to another meeting mid-request"
    assert (route_b / "prep.json").read_bytes() == route_b_before, \
        "meeting A confirmation answers corrupted meeting B's prep ledger"

    # A confirmed replacement may legitimately embed another correction's old
    # spelling (one-pass semantics). The leftover check must not abort then.
    embed_meeting, _, embed_signature = make_session("embed-leftover", "OLDNAME と SHORT の確認")
    server._ai_text = lambda prompt, **_kwargs: json.dumps(
        {"summary": "SHORT の件を確認", "log": []}, ensure_ascii=False)
    ok, message = server.finalize_meeting(
        "embed-leftover",
        corrections=[{"from": "OLDNAME", "to": "NEWNAME", "status": "replace"},
                     {"from": "SHORT", "to": "OLDNAME 正式版", "status": "replace"}],
        source_signature=embed_signature)
    assert ok, message
    embed_json = (embed_meeting / "final.json").read_text(encoding="utf-8")
    assert "OLDNAME 正式版" in embed_json, embed_json

    # A live patch racing an in-progress finalize is discarded without
    # advancing analysis coverage, so the same span is recovered afterwards.
    discard = Path(server.sdir("discard-coverage"))
    discard.mkdir(parents=True)
    (discard / "meta.json").write_text(json.dumps({"id": "discard-coverage", "title": "Discard"}), encoding="utf-8")
    (discard / "data.json").write_text("{}", encoding="utf-8")
    (discard / "transcript.txt").write_text("清書中に届いた発話です。" * 5, encoding="utf-8")
    server._ai_text = lambda prompt, **_kwargs: json.dumps({"summary": "discarded"}, ensure_ascii=False)
    server.finalizing_sessions.add("discard-coverage")
    try:
        assert server._claude_update("discard-coverage") is True
        total = len((discard / "transcript.txt").read_text(encoding="utf-8"))
        assert server._analysis_has_unprocessed("discard-coverage", total), \
            "a finalize-time discard must leave the span uncovered for later recovery"
    finally:
        server.finalizing_sessions.discard("discard-coverage")


print("Finalize review and confirmed-correction regressions passed")
