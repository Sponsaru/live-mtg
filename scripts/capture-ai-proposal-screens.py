#!/usr/bin/env python3
"""Capture real LiveMTG UI states through Chromium DevTools without mutating meeting data."""

import asyncio
import base64
import json
import os
import urllib.request

import websockets


CDP_LIST = "http://127.0.0.1:9333/json"
OUT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "artifacts"))


async def command(ws, seq, method, params=None):
    await ws.send(json.dumps({"id": seq, "method": method, "params": params or {}}))
    while True:
        payload = json.loads(await ws.recv())
        if payload.get("id") == seq:
            if "error" in payload:
                raise RuntimeError(payload["error"])
            return payload.get("result", {})


def flow_json(stage):
    base = {
        "revision": 12,
        "target": {
            "text": "東京・埼玉の欠員／ヘルプ管理を一つのアプリへ統合し、最初に実装する範囲を決める",
            "origin": "user",
            "locked": True,
        },
        "agendas": [
            {
                "id": "agenda-current",
                "title": "東京・埼玉の欠員／ヘルプ管理の現状共有",
                "order": 1000,
                "status": "discussing" if stage == 2 else "not_started",
                "origin": "ai",
                "approval": "draft",
                "current": stage == 2,
                "result": {
                    "summary": {"text": "拠点ごとに異なる管理方法と、割当判断に時間がかかる現状を整理する"},
                    "answers": [], "decisions": [], "actions": [], "unresolved": [],
                },
            },
            {
                "id": "agenda-next",
                "title": "アプリ化する最初の機能範囲を決める",
                "order": 2000,
                "status": "not_started",
                "origin": "ai",
                "approval": "draft",
                "current": False,
                "result": {"summary": {"text": ""}, "answers": [], "decisions": [], "actions": [], "unresolved": []},
            },
        ],
        "questions": [
            {
                "id": "question-draft",
                "agendaId": "agenda-current",
                "order": 1000,
                "text": "現在の割当で最も時間がかかっている作業はどこですか？",
                "status": "next" if stage == 2 else "queued",
                "origin": "ai",
                "approval": "draft",
                "reason": "最初に解消すべき運用負荷を特定するため",
                "answer": "",
            },
            *([{
                "id": "question-asked",
                "agendaId": "agenda-current",
                "order": 2000,
                "text": "埼玉側では、欠員情報を最後に更新する担当者は誰ですか？",
                "status": "asked",
                "origin": "user",
                "approval": "accepted",
                "reason": "更新責任の所在を確認するため",
                "answer": "",
            }, {
                "id": "question-answered",
                "agendaId": "agenda-current",
                "order": 3000,
                "text": "東京側の割当判断は、現在どのように行っていますか？",
                "status": "answered",
                "origin": "user",
                "approval": "accepted",
                "reason": "現在の判断手順を残すため",
                "answer": "夕方にホワイトボードを囲み、担当者同士で調整している",
            }] if stage == 2 else [])
        ],
        "suggestions": [],
        "evidence": [],
        "updatedAt": 1,
    }
    if stage == 2:
        base["suggestions"] = [
            {
                "id": "suggestion-old-accepted",
                "type": "research",
                "text": "既存スプレッドシートの項目一覧を確認する",
                "status": "accepted",
                "agendaId": None,
                "reason": "移行対象データを把握するため",
                "payload": {"source": "会議前半の発言"},
            },
            {
                "id": "suggestion-old-dismissed",
                "type": "warning",
                "text": "請求機能まで初回リリースへ含めるか確認する",
                "status": "dismissed",
                "agendaId": None,
                "reason": "初期範囲が広がりすぎる可能性があったため",
                "payload": {},
            },
            {
                "id": "suggestion-question",
                "type": "question_proposal",
                "text": "東京と埼玉で、欠員情報の更新タイミングはどの程度ずれますか？",
                "status": "pending",
                "agendaId": "agenda-current",
                "reason": "二重管理の原因が入力項目ではなく更新頻度にある可能性を確認するため",
                "payload": {"text": "東京と埼玉で、欠員情報の更新タイミングはどの程度ずれますか？", "agendaId": "agenda-current"},
            },
            {
                "id": "suggestion-research",
                "type": "research",
                "text": "既存の休み・欠員連絡データを取り込めるか確認する",
                "status": "pending",
                "agendaId": None,
                "reason": "初期入力の負荷が導入可否に直結するため",
                "payload": {"source": "会議中の発言"},
            },
            {
                "id": "suggestion-unstuck",
                "type": "unstuck",
                "text": "最初は欠員登録と担当者割当だけに絞って運用を始める",
                "status": "pending",
                "agendaId": None,
                "reason": "全機能を同時に決めようとして議論が広がっているため",
                "payload": {},
            },
        ]
    return json.dumps(base, ensure_ascii=False)


def setup_script(stage):
    flow = flow_json(stage)
    next_questions = json.dumps([
        {"q": "東京と埼玉で、いま最も困っている運用差は何ですか？", "intent": "共通化すべき最優先課題を絞る", "kind": "聞く", "at": 9999999999},
        {"q": "最初のリリースを欠員登録と割当に限定すると、誰が困りますか？", "intent": "小さく始める場合の抜け漏れを確認する", "kind": "聞く", "at": 9999999999},
    ], ensure_ascii=False)
    counsel = json.dumps({
        "situation": "必要機能が増え続け、初期範囲を決められていない",
        "options": [
            {"label": "最小構成で開始", "body": "欠員登録と担当者割当だけを先に実装し、運用データをためる。"},
            {"label": "全体設計を先行", "body": "請求・シフト・通知まで含む全体像を固めてから開発する。"},
        ],
        "pick": "最小構成で開始",
        "reason": "現時点では運用データが不足しているため、先に実績をためた方が後続機能の判断精度が上がります。",
        "at": 9999999999,
    }, ensure_ascii=False)
    return f"""
(() => {{
  if(typeof closeDrawer==='function')closeDrawer(false);
  flowGlobalNoticePending=false;
  startupPending=false;
  currentSessionId='screenshot-demo';
  meetingTitle='欠員・ヘルプ管理アプリの要件整理';
  meetingFlow={flow};
  meetingFlowRebuilding=false;
  flowExpanded=new Set(['agenda-current']);
  document.querySelectorAll('.modal-bg,.startup-modal,.recording-setup').forEach(x=>x.style.display='none');
  document.body.classList.remove('phase-prep','phase-review','visual-open','slidemode');
  document.body.classList.add('phase-live');
  setPhase('live',false);
  renderMeetingFlow(true);
  window.scrollTo(0,0);
  {f"renderNextQuestions({next_questions}); renderCounsel({counsel});" if stage == 2 else "renderNextQuestions([]); renderCounsel(null);"}
  return {{width:document.documentElement.scrollWidth,height:document.documentElement.scrollHeight}};
}})()
"""


def usage_flow(stage):
    flow = json.loads(flow_json(2))
    flow["suggestions"] = []
    current, following = flow["agendas"]
    current["approval"] = "accepted"
    following["approval"] = "accepted"
    current["result"] = {
        "summary": {"text": "東京と埼玉で更新方法と担当が異なり、夕方の割当判断に時間がかかっている"},
        "answers": [
            {"id": "answer-1", "text": "東京はホワイトボード、埼玉はスプレッドシートで欠員情報を管理している"},
            {"id": "answer-2", "text": "東京では夕方に担当者同士で翌日の割当を調整している"},
        ],
        "decisions": [], "actions": [],
        "unresolved": [{"id": "open-1", "text": "埼玉側の最終更新責任者はまだ確認できていない"}],
    }
    if stage in ("progress", "transition"):
        current["result"]["summary"]["text"] = "欠員情報を一元化し、まず登録と担当者割当までを初期機能にする方向で整理できた"
        current["result"]["decisions"] = [
            {"id": "decision-1", "text": "東京・埼玉の欠員情報を一つのアプリで一元管理する"},
            {"id": "decision-2", "text": "初期リリースは欠員登録と担当者割当に絞る"},
        ]
        current["result"]["actions"] = [
            {"id": "action-1", "text": "田谷さんが既存スプレッドシートの項目一覧を共有する"},
            {"id": "action-2", "text": "開発側が初期画面案を次回までに用意する"},
        ]
        current["result"]["unresolved"] = [
            {"id": "open-1", "text": "休み・欠員連絡データの取り込み方法は未確定"},
        ]
    if stage == "transition":
        current["status"] = "completed"
        current["current"] = False
        following["status"] = "discussing"
        following["current"] = True
        following["result"] = {
            "summary": {"text": "初期リリースへ含める具体的な画面と入力項目を確認する"},
            "answers": [], "decisions": [], "actions": [], "unresolved": [],
        }
        flow["questions"].append({
            "id": "question-next-agenda", "agendaId": "agenda-next", "order": 1000,
            "text": "欠員登録時に必須にしたい情報は何ですか？", "status": "next",
            "origin": "ai", "approval": "draft", "reason": "初期画面の入力項目を決めるため", "answer": "",
        })
    return json.dumps(flow, ensure_ascii=False)


def usage_setup_script(stage):
    expanded = "agenda-next" if stage == "transition" else "agenda-current"
    return f"""
(() => {{
  if(typeof closeDrawer==='function')closeDrawer(false);
  flowGlobalNoticePending=false;
  startupPending=false;
  currentSessionId='screenshot-usage-{stage}';
  meetingTitle='欠員・ヘルプ管理アプリの要件整理';
  meetingFlow={usage_flow(stage)};
  meetingFlowRebuilding=false;
  flowExpanded=new Set(['{expanded}']);
  document.querySelectorAll('.modal-bg,.startup-modal,.recording-setup').forEach(x=>x.style.display='none');
  document.body.classList.remove('phase-prep','phase-review','visual-open','slidemode');
  document.body.classList.add('phase-live');
  setPhase('live',false);
  renderMeetingFlow(true);
  window.scrollTo(0,0);
  return true;
}})()
"""


async def capture(ws, seq, filename, clip):
    result = await command(ws, seq, "Page.captureScreenshot", {
        "format": "png", "fromSurface": True, "captureBeyondViewport": True,
        "clip": clip,
    })
    with open(os.path.join(OUT_DIR, filename), "wb") as output:
        output.write(base64.b64decode(result["data"]))


async def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    pages = json.load(urllib.request.urlopen(CDP_LIST))
    page = next(row for row in pages if row.get("type") == "page")
    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=20_000_000) as ws:
        seq = 1
        await command(ws, seq, "Page.enable"); seq += 1
        await command(ws, seq, "Runtime.enable"); seq += 1
        await command(ws, seq, "Emulation.setDeviceMetricsOverride", {
            "width": 1440, "height": 1300, "deviceScaleFactor": 1, "mobile": False,
        }); seq += 1
        await command(ws, seq, "Page.navigate", {"url": "http://127.0.0.1:8877/"}); seq += 1
        await asyncio.sleep(1.3)

        # 1. AIが作成した質問は、質問文と意図を議題カード内へ直接表示する。
        await command(ws, seq, "Runtime.evaluate", {"expression": setup_script(1), "awaitPromise": True}); seq += 1
        await asyncio.sleep(0.4)
        await capture(ws, seq, "ai-support-01-question-and-intent.png", {"x": 0, "y": 0, "width": 1440, "height": 1300, "scale": 1}); seq += 1

        # 2. 議題に紐づく提案は、詳細ドロワーではなく議題カード内で操作する。
        await command(ws, seq, "Runtime.evaluate", {"expression": setup_script(2), "awaitPromise": True}); seq += 1
        await asyncio.sleep(0.4)
        await capture(ws, seq, "ai-support-02-agenda-inline-proposal.png", {"x": 0, "y": 0, "width": 1440, "height": 1300, "scale": 1}); seq += 1

        # 3. 議題に紐づかない新着支援は、検知時に右ドロワーを自動表示する。
        await command(ws, seq, "Runtime.evaluate", {
            "expression": "localStorage.removeItem(flowGlobalSeenKey()); closeDrawer(false); adoptFlow(meetingFlow,false); true",
            "awaitPromise": True,
        }); seq += 1
        await asyncio.sleep(0.5)
        await capture(ws, seq, "ai-support-03-global-auto-drawer.png", {"x": 0, "y": 0, "width": 1440, "height": 1300, "scale": 1})

        for stage, filename in (
            ("early", "meeting-usage-01-discussing.png"),
            ("progress", "meeting-usage-02-key-points.png"),
            ("transition", "meeting-usage-03-next-agenda.png"),
        ):
            seq += 1
            await command(ws, seq, "Runtime.evaluate", {
                "expression": usage_setup_script(stage), "awaitPromise": True,
            })
            await asyncio.sleep(0.35)
            seq += 1
            await capture(ws, seq, filename, {"x": 0, "y": 350, "width": 1440, "height": 1500, "scale": 1})


if __name__ == "__main__":
    asyncio.run(main())
