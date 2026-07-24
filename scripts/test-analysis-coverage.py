#!/usr/bin/env python3
"""Live analysis must prefer recent speech without creating permanent gaps."""

import json
import os
from pathlib import Path
import re
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


with tempfile.TemporaryDirectory(prefix="live-mtg-analysis-coverage-") as tmp:
    root = Path(tmp)
    os.environ.update({
        "RUN": str(root),
        "MEETINGS_DIR": str(root / "meetings"),
        "DRIVE_SYNC_DIR": str(root / "drive"),
        "PROFILE_MD": str(root / "profile.md"),
        "PLAYBOOK_DIR": str(root / "playbooks"),
    })

    import server

    server.LANGUAGE = "ja"
    server.request_detail = lambda *_args, **_kwargs: None
    server.request_counsel = lambda *_args, **_kwargs: None
    server._mech_confirms = lambda *_args, **_kwargs: None

    def make_session(title, transcript):
        make_session.counter += 1
        sid = "coverage-test-%02d" % make_session.counter
        meeting = Path(server.sdir(sid))
        meeting.mkdir(parents=True)
        (meeting / "meta.json").write_text(
            json.dumps({"id": sid, "title": title}, ensure_ascii=False), encoding="utf-8")
        (meeting / "data.json").write_text(server.EMPTY_DATA, encoding="utf-8")
        (meeting / "transcript.txt").write_text(transcript, encoding="utf-8")
        return sid
    make_session.counter = 0

    def reset_runtime_state(sid=None):
        # Simulate a process restart without assuming a particular name for the
        # new coverage cache. Durable state on disk must remain authoritative.
        for name in dir(server):
            if not any(word in name.lower() for word in ("applied", "coverage", "processed_range")):
                continue
            value = getattr(server, name)
            if isinstance(value, dict):
                if sid is None:
                    value.clear()
                else:
                    value.pop(sid, None)
        for name, empty in (("fast_fail_streak", 0), ("fast_last_attempt", 0.0)):
            if not hasattr(server, name):
                continue
            value = getattr(server, name)
            if isinstance(value, dict):
                if sid is None:
                    value.clear()
                else:
                    value.pop(sid, None)
            else:
                setattr(server, name, empty)

    def delta_from_prompt(prompt):
        match = re.search(r"【最新発話】(.*)\Z", prompt, re.S)
        if not match:
            match = re.search(r"最新発話=(.*)\Z", prompt, re.S)
        assert match, "live analysis prompt did not contain the transcript delta"
        return match.group(1)

    def detail_delta_from_prompt(prompt):
        match = re.search(r"【追加文字起こし】\n(.*)\Z", prompt, re.S)
        assert match, "detail analysis prompt did not contain the transcript delta"
        return match.group(1)

    def reset_detail_runtime_state(sid):
        """Drop detail cursors/caches while preserving their durable files."""
        for name in dir(server):
            lowered = name.lower()
            if "detail" not in lowered or not any(
                    word in lowered for word in
                    ("applied", "coverage", "cursor", "state", "progress", "generation")):
                continue
            value = getattr(server, name)
            if isinstance(value, dict):
                value.pop(sid, None)

    # More than 4,000 characters, with durable markers throughout each third.
    # Looking only at the tail can never satisfy this check.
    def section(marker, label):
        # Unique numbered utterances avoid Whisper's intentional repetition
        # compressor changing the fixture during transcript-clean migration.
        return "\n".join("%s-%03d|%s%dの発言。" % (marker, i, label, i)
                         for i in range(95)) + "\n"
    def unique_payload(label, length):
        text = "".join("%s%05d。" % (label, i) for i in range(length // 7 + 2))
        return text[:length]
    transcript = (section("HEAD_MARKER", "序盤") +
                  section("MIDDLE_MARKER", "中盤") +
                  section("TAIL_MARKER", "終盤"))
    assert len(transcript) > 4000
    sid = make_session("Coverage", transcript)
    seen_deltas = []

    def successful_ai(prompt, **_kwargs):
        seen_deltas.append(delta_from_prompt(prompt))
        return json.dumps({"summary": "coverage test"}, ensure_ascii=False)

    server._ai_text = successful_ai
    # 500-character work units need fewer than this many successful passes. A
    # few extra calls also prove already-covered text is not processed forever.
    for _ in range((len(transcript) + 349) // 350 + 3):
        assert server._claude_update(sid) is True

    analyzed_text = "\n".join(seen_deltas)
    for marker in ("HEAD_MARKER", "MIDDLE_MARKER", "TAIL_MARKER"):
        assert marker in analyzed_text, "%s was permanently skipped" % marker
    if hasattr(server, "_analysis_gaps"):
        assert server._analysis_gaps(sid, len(transcript)) == [], \
            "successful passes left an uncovered transcript range"
    else:
        assert server.applied.get(sid) == len(transcript), \
            "analysis did not acknowledge complete contiguous coverage"

    # Clearing all in-memory coverage caches emulates a server restart. The
    # durable ranges must prevent old speech from being replayed while allowing
    # newly appended speech through.
    reset_runtime_state(sid)
    appended = "RELOAD_NEW_MARKER|再起動後の新しい発言。" * 20
    Path(server.sdir(sid), "transcript.txt").write_text(transcript + appended, encoding="utf-8")
    after_reload = []

    def reload_ai(prompt, **_kwargs):
        after_reload.append(delta_from_prompt(prompt))
        return json.dumps({"summary": "after reload"}, ensure_ascii=False)

    server._ai_text = reload_ai
    for _ in range(4):
        assert server._claude_update(sid) is True
    replayed = "\n".join(after_reload)
    assert "RELOAD_NEW_MARKER" in replayed, "new speech was not analyzed after coverage reload"
    assert "HEAD_MARKER" not in replayed and "MIDDLE_MARKER" not in replayed, \
        "durable coverage was lost across reload"

    # Coverage belongs to the transcript contents, not merely its character
    # offsets. An external cleanup or full retranscription can replace the file
    # without going through a server helper, including with exactly the same
    # length. Both same-length replacement and truncation must invalidate the
    # old ranges immediately, even while their in-memory cache is warm.
    original_marker = "ORIGINAL_TRANSCRIPT_MARKER|"
    rewrite_original = original_marker + unique_payload("元", 1200 - len(original_marker))
    rewrite_sid = make_session("External transcript rewrite", rewrite_original)
    rewrite_calls = []

    def rewrite_ai(prompt, **_kwargs):
        rewrite_calls.append(delta_from_prompt(prompt))
        return json.dumps({"summary": "rewrite detected"}, ensure_ascii=False)

    server._ai_text = rewrite_ai
    for _ in range(8):
        assert server._claude_update(rewrite_sid) is True
    assert server._analysis_gaps(rewrite_sid, len(rewrite_original)) == []

    same_marker = "SAME_LENGTH_REWRITE_MARKER|"
    same_length = same_marker + unique_payload("同", len(rewrite_original) - len(same_marker))
    assert len(same_length) == len(rewrite_original)
    Path(server.sdir(rewrite_sid), "transcript.txt").write_text(same_length, encoding="utf-8")
    rewrite_calls.clear()
    assert server._claude_update(rewrite_sid) is True
    assert any(same_marker in delta for delta in rewrite_calls), \
        "same-length external transcript replacement reused stale coverage"

    # Finish the replacement generation so the following shorter rewrite starts
    # from a genuinely fully-covered state rather than an existing gap.
    for _ in range(8):
        assert server._claude_update(rewrite_sid) is True
    assert server._analysis_gaps(rewrite_sid, len(same_length)) == []

    short_marker = "SHORTENED_REWRITE_MARKER|"
    shortened = short_marker + ("短" * 375)
    assert len(shortened) < len(same_length)
    Path(server.sdir(rewrite_sid), "transcript.txt").write_text(shortened, encoding="utf-8")
    rewrite_calls.clear()
    assert server._claude_update(rewrite_sid) is True
    assert any(short_marker in delta for delta in rewrite_calls), \
        "shortened external transcript replacement reused stale coverage"

    # Legacy detail cursors are unsafe: the old implementation jumped to the
    # last 3,000 characters and then persisted len(transcript), although the
    # beginning had never been processed. An unversioned cursor must therefore
    # migrate from offset zero. The new versioned state must then survive a
    # process restart and continue with the next range instead of replaying head.
    detail_text = ("DETAIL_HEAD_MARKER|" + unique_payload("甲", 3490) +
                   "DETAIL_AFTER_RESTART_MARKER|" + unique_payload("乙", 3500))
    detail_sid = make_session("Legacy detail cursor migration", detail_text)
    detail_dir = Path(server.sdir(detail_sid))
    (detail_dir / ".detail-applied").write_text(str(len(detail_text)), encoding="utf-8")
    reset_detail_runtime_state(detail_sid)
    detail_calls = []

    def detail_ai(prompt, **_kwargs):
        detail_calls.append(detail_delta_from_prompt(prompt))
        return json.dumps({"mindmap_add": [], "lookups": []}, ensure_ascii=False)

    server._ai_text = detail_ai
    assert server._detail_update(detail_sid) is True
    assert detail_calls and "DETAIL_HEAD_MARKER" in detail_calls[0], \
        "unversioned legacy detail cursor skipped the transcript head"

    reset_detail_runtime_state(detail_sid)  # emulate a new server process
    detail_calls.clear()
    assert server._detail_update(detail_sid) is True
    assert detail_calls and "DETAIL_AFTER_RESTART_MARKER" in detail_calls[0], \
        "versioned detail progress was not restored after restart"
    assert "DETAIL_HEAD_MARKER" not in detail_calls[0], \
        "detail analysis replayed the first range after restart"

    # A failed AI call must not acknowledge its selected range. The exact same
    # speech has to remain eligible for the retry.
    reset_runtime_state()
    failed_sid = make_session("Failure does not advance", "FAIL_RANGE_MARKER|未処理発言。" * 240)
    attempts = []

    def fail_once(prompt, **_kwargs):
        attempts.append(delta_from_prompt(prompt))
        if len(attempts) == 1:
            raise TimeoutError("intentional test timeout")
        return json.dumps({"summary": "retry succeeded"}, ensure_ascii=False)

    server._ai_text = fail_once
    assert server._claude_update(failed_sid) is False
    assert server._claude_update(failed_sid) is True
    assert len(attempts) == 2 and attempts[0] == attempts[1], \
        "a failed range was advanced instead of retried"

    # Failure throttling is session-local. Three failures in meeting A must not
    # suppress the very first call for meeting B.
    reset_runtime_state()
    sid_a = make_session("Failure A", "A_FAILURE_MARKER|発言。" * 80)
    sid_b = make_session("Healthy B", "B_HEALTHY_MARKER|発言。" * 80)
    calls = []

    def fail_a_succeed_b(prompt, **_kwargs):
        delta = delta_from_prompt(prompt)
        calls.append(delta)
        if "A_FAILURE_MARKER" in delta:
            raise TimeoutError("meeting A is intentionally failing")
        return json.dumps({"summary": "meeting B succeeded"}, ensure_ascii=False)

    server._ai_text = fail_a_succeed_b
    assert server._claude_update(sid_a) is False
    assert server._claude_update(sid_a) is False
    assert server._claude_update(sid_a) is True, \
        "three repeated failures must defer the toxic live range instead of blocking the queue"
    deferred = json.loads(Path(server.sdir(sid_a), ".live-analysis-deferred.json").read_text(encoding="utf-8"))
    assert deferred and deferred[-1]["lane"] == "fast"
    assert server._claude_update(sid_b) is True, \
        "meeting A failure streak throttled healthy meeting B"
    assert any("B_HEALTHY_MARKER" in delta for delta in calls), \
        "meeting B never reached the AI after meeting A failures"

    # タイムアウトだけでなく、「入力をください」等のJSONでない
    # 応答も3回で隔離し、その後の新しい発話を通す。
    invalid_sid = make_session("Invalid JSON does not block", "INVALID_JSON_MARKER|発言。" * 80)
    server._ai_text = lambda *_args, **_kwargs: "最新発話の内容を教えてください。"
    assert server._claude_update(invalid_sid) is False
    assert server._claude_update(invalid_sid) is False
    assert server._claude_update(invalid_sid) is True
    old_total = len(Path(server.sdir(invalid_sid), "transcript.txt").read_text(encoding="utf-8"))
    assert server._analysis_gaps(invalid_sid, old_total) != [[0, old_total]], \
        "repeated invalid output must move the blocking head/tail ranges forward"
    with Path(server.sdir(invalid_sid), "transcript.txt").open("a", encoding="utf-8") as stream:
        stream.write("NEW_AFTER_INVALID|新しい発言。")
    recovered_calls = []
    def recovered_ai(prompt, **_kwargs):
        recovered_calls.append(delta_from_prompt(prompt))
        return json.dumps({"summary": "recovered"}, ensure_ascii=False)
    server._ai_text = recovered_ai
    assert server._claude_update(invalid_sid) is True
    assert recovered_calls and "NEW_AFTER_INVALID" in recovered_calls[0]

    # Claudeが正常JSONの後ろへ説明や別JSONを付けても、先頭の完全な
    # オブジェクトをライブ差分として採用する。
    parsed = server._parse_live_patch(
        '{"summary":"先頭を採用"}\n{"summary":"後続は無視"}',
        "FAST-ANALYSIS", sid_b)
    assert parsed == {"summary": "先頭を採用"}, parsed

    # 高速モデルが連続失敗したら、同じClaude Code内の標準モデルへ
    # 退避してライブ解析を止めない。
    reset_runtime_state()
    fallback_sid = make_session("Claude model fallback", "FALLBACK_MARKER|発言。" * 80)
    server.AI_PROVIDER = "claude"
    models = []

    def fail_twice_then_succeed(_prompt, **kwargs):
        models.append(kwargs.get("model"))
        if len(models) <= 2:
            raise TimeoutError("intentional fast-model failure")
        return json.dumps({"summary": "fallback succeeded"}, ensure_ascii=False)

    server._ai_text = fail_twice_then_succeed
    assert server._claude_update(fallback_sid) is False
    assert server._claude_update(fallback_sid) is False
    assert server._claude_update(fallback_sid) is True
    assert models == [server.CLAUDE_MODEL, server.CLAUDE_MODEL, server.ASSIST_MODEL], models
    compact = server._compact_fast_prompt(
        "会議", "最新発話", {"summary": "現在地"})
    assert len(compact) < 1800 and "進行ボードだけ" not in compact
    assert "議題の追加・状態・合意・結果分類は別レーン" in compact
    flow_prompt = server._compact_flow_prompt(
        "会議", "最新発話",
        {"agendas": [{"id": "a1", "title": "要件", "status": "discussing"}],
         "questions": []})
    assert len(flow_prompt) < 2600 and '"id":"a1"' in flow_prompt
    assert "同時にdiscussingは1議題だけ" in flow_prompt and "次に聞く/話す提案" not in flow_prompt

    # 進行ボードは即時解析とは別カバレッジを持ち、失敗時に処理位置を
    # 進めず、再起動後も成功済み範囲だけを復元する。
    flow_sid = make_session("Independent flow coverage", "FLOW_HEAD_MARKER|発言。" * 20)
    flow_attempts = []

    def flow_fail_once(prompt, **_kwargs):
        flow_attempts.append(prompt)
        if len(flow_attempts) == 1:
            raise TimeoutError("intentional flow timeout")
        return json.dumps({"currentAgendaId": "", "evidence": [],
                           "agendaStatusUpdates": [], "agendaResolutionUpdates": [],
                           "questionUpdates": [], "resultUpdates": [],
                           "agendaProposals": [], "questionProposals": []}, ensure_ascii=False)

    server._ai_text = flow_fail_once
    flow_total = len(Path(server.sdir(flow_sid), "transcript.txt").read_text(encoding="utf-8"))
    assert server._flow_update(flow_sid) is False
    assert server._flow_analysis_gaps(flow_sid, flow_total) == [[0, flow_total]], \
        "failed flow call advanced its independent coverage"
    assert server._flow_update(flow_sid) is True
    assert server._flow_analysis_gaps(flow_sid, flow_total) == []
    server.flow_analysis_coverage.pop(flow_sid, None)  # process restart
    appended_flow = "FLOW_AFTER_RESTART|追加発言。"
    with Path(server.sdir(flow_sid), "transcript.txt").open("a", encoding="utf-8") as stream:
        stream.write(appended_flow)
    new_total = flow_total + len(appended_flow)
    flow_attempts.clear()
    def flow_after_restart(prompt, **_kwargs):
        flow_attempts.append(prompt)
        return json.dumps({"currentAgendaId": "", "evidence": [],
                           "agendaStatusUpdates": [], "agendaResolutionUpdates": [],
                           "questionUpdates": [], "resultUpdates": [],
                           "agendaProposals": [], "questionProposals": []}, ensure_ascii=False)
    server._ai_text = flow_after_restart
    assert server._flow_update(flow_sid) is True
    assert appended_flow in flow_attempts[0] and "FLOW_HEAD_MARKER" not in flow_attempts[0]
    assert server._flow_analysis_gaps(flow_sid, new_total) == []


print("Live analysis covers head/middle/tail, persists ranges, and isolates failures by meeting")
