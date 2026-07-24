#!/usr/bin/env python3
"""Capture the six deterministic UI states used by docs/demo.gif."""

import argparse
import asyncio
import base64
import json
import urllib.request
from pathlib import Path

import websockets


CDP_LIST = "http://127.0.0.1:9333/json"


async def command(ws, seq, method, params=None):
    await ws.send(json.dumps({"id": seq, "method": method, "params": params or {}}))
    while True:
        payload = json.loads(await ws.recv())
        if payload.get("id") == seq:
            if "error" in payload:
                raise RuntimeError(payload["error"])
            return payload.get("result", {})


def meeting_flow(include_global=False):
    suggestions = [
        {
            "id": "agenda-question",
            "type": "question_proposal",
            "text": "拠点ごとに、欠員情報の更新タイミングはどの程度ずれますか？",
            "status": "pending",
            "agendaId": "current",
            "reason": "二重管理の原因が入力項目ではなく更新頻度にある可能性を確認するため",
            "payload": {},
        }
    ]
    if include_global:
        suggestions.extend([
            {
                "id": "global-scope",
                "type": "unstuck",
                "text": "初期リリースは欠員登録と担当者割当だけに絞る",
                "status": "pending",
                "agendaId": None,
                "reason": "全機能を同時に決めようとして議論が広がっているため",
                "payload": {},
            },
            {
                "id": "global-research",
                "type": "research",
                "text": "既存スプレッドシートから移行できる項目を確認する",
                "status": "pending",
                "agendaId": None,
                "reason": "初期入力の負荷が導入可否に直結するため",
                "payload": {},
            },
        ])
    return {
        "revision": 24,
        "target": {"text": "欠員・ヘルプ管理を一つのアプリへ統合し、最初に実装する範囲を決める", "origin": "user", "locked": True},
        "agendas": [
            {
                "id": "current", "title": "東京・埼玉の欠員管理の現状を揃える", "order": 1000,
                "status": "discussing", "resolutionStatus": "pending", "origin": "user",
                "approval": "accepted", "current": True,
                "result": {
                    "summary": {"text": "拠点ごとに更新方法と担当が異なり、夕方の割当判断に時間がかかっている"},
                    "answers": [
                        {"text": "東京はホワイトボード、埼玉はスプレッドシートで管理している"},
                        {"text": "東京では夕方に翌日の割当を調整している"},
                    ],
                    "decisions": [{"text": "欠員情報は一つのアプリへ集約する"}],
                    "actions": [{"text": "田谷さんが既存項目一覧を共有する"}],
                    "unresolved": [{"text": "埼玉側の最終更新責任者は未確認"}],
                },
            },
            {
                "id": "next", "title": "初期リリースへ含める機能を決める", "order": 2000,
                "status": "not_started", "resolutionStatus": "pending", "origin": "ai",
                "approval": "accepted", "current": False,
                "result": {"summary": {"text": ""}, "answers": [], "decisions": [], "actions": [], "unresolved": []},
            },
            {
                "id": "later", "title": "運用開始後のデータ活用を確認する", "order": 3000,
                "status": "not_started", "resolutionStatus": "not_applicable", "origin": "ai",
                "approval": "accepted", "current": False,
                "result": {"summary": {"text": ""}, "answers": [], "decisions": [], "actions": [], "unresolved": []},
            },
        ],
        "questions": [
            {
                "id": "next-question", "agendaId": "current", "order": 1000,
                "text": "現在の割当で最も時間がかかっている作業はどこですか？", "status": "next",
                "origin": "ai", "approval": "draft", "reason": "最初に解消すべき運用負荷を特定するため", "answer": "",
            },
            {
                "id": "answered-question", "agendaId": "current", "order": 2000,
                "text": "東京側の割当判断は現在どのように行っていますか？", "status": "answered",
                "origin": "user", "approval": "accepted", "reason": "現行手順を残すため",
                "answer": "夕方にホワイトボードを囲み、担当者同士で調整している",
            },
        ],
        "suggestions": suggestions,
        "evidence": [], "updatedAt": 1,
    }


def meeting_data():
    return {
        "summary": "欠員情報を一元化し、まず登録と担当者割当から運用を始める方向で合意した。",
        "conversationMap": {
            "status": "final",
            "types": [
                {"type": "進捗・事実共有", "share": 31, "topics": [{"label": "現在の管理方法", "summary": "東京と埼玉で異なる運用を共有"}], "examples": ["東京はホワイトボード、埼玉はスプレッドシート"]},
                {"type": "課題・懸念の探索", "share": 24, "topics": [{"label": "二重管理", "summary": "更新担当とタイミングのずれを確認"}], "examples": ["夕方の割当判断に時間がかかっている"]},
                {"type": "質問・理解確認", "share": 18, "topics": [{"label": "運用差の確認", "summary": "最も時間がかかる作業を特定"}], "examples": ["現在の割当で最も時間がかかる作業は？"]},
                {"type": "意思決定・合意形成", "share": 15, "topics": [{"label": "初期範囲", "summary": "欠員登録と担当者割当に限定"}], "examples": ["まず小さく始める"]},
                {"type": "実行計画・役割調整", "share": 12, "topics": [{"label": "次回までの準備", "summary": "既存項目と画面案を持ち寄る"}], "examples": ["田谷さんが項目一覧を共有する"]},
            ],
            "insights": ["事実共有と課題探索が会話の半分を占めた", "初期範囲の合意まで到達", "次回までの担当が明確"],
        },
        "diagram": """flowchart LR
  subgraph 現状把握
    A[東京・ホワイトボード] --> C[二重管理]
    B[埼玉・スプレッドシート] --> C
  end
  subgraph 方針決定
    D[二重管理を解消] --> E[一つのアプリへ統合]
    E --> F[欠員登録と割当から開始]
  end
  subgraph 次の行動
    G[初期範囲を合意] --> H[既存項目を共有]
    G --> I[初期画面案を作成]
  end""",
        "mindmap": [], "points": [], "decisions": [], "todos": [], "open": [], "timeline": [],
    }


def js(value):
    return json.dumps(value, ensure_ascii=False)


BASE = r"""
(() => {
  try { if (visualSheetOpen) closeVisualSheet(false); } catch (_) {}
  try { closeDrawer(false); } catch (_) {}
  document.querySelectorAll('.modal-bg,.startup-modal,.recording-setup,.auth-block-bg').forEach(x => x.classList.remove('show'));
  document.querySelectorAll('.modal-bg,.startup-modal,.recording-setup').forEach(x => x.style.display='none');
  startupPending=false; serverDown=false; currentSessionId='public-demo'; meetingTitle='欠員・ヘルプ管理アプリの要件整理';
  hideToast(); window.scrollTo(0,0);
  let cap=document.getElementById('public-demo-caption');
  if(!cap){cap=document.createElement('div');cap.id='public-demo-caption';document.body.appendChild(cap);}
  let demoStyle=document.getElementById('public-demo-style');
  if(!demoStyle){demoStyle=document.createElement('style');demoStyle.id='public-demo-style';demoStyle.textContent='#down{display:none!important}';document.head.appendChild(demoStyle);}
  cap.style.cssText='position:fixed;left:22px;bottom:20px;z-index:99999;padding:10px 15px;border-radius:10px;background:rgba(29,29,31,.92);color:#fff;font:750 16px/1.25 -apple-system,BlinkMacSystemFont,"Helvetica Neue",sans-serif;box-shadow:0 8px 28px rgba(0,0,0,.18);letter-spacing:.01em';
})();
"""


def caption(text):
    return f"document.getElementById('public-demo-caption').textContent={js(text)};"


def prep_script():
    flow = meeting_flow(False)
    messages = [
        {"role": "user", "text": "東京と埼玉で別々になっている欠員管理を一つにしたい。最初の会議で、どこまで作るか合意したい。"},
        {"role": "assistant", "text": "まず現状の運用差を揃え、初期リリースを欠員登録と担当者割当に絞れるか確認しましょう。決裁に必要な懸念も質問へ入れました。"},
    ]
    board = {
        "outcome": "欠員管理を一元化し、初期リリースの対象機能と担当を決める",
        "counterpart": "東京と埼玉で管理方法・更新担当・更新頻度が異なる",
        "hypotheses": ["二重管理の主因は入力項目より更新タイミングの差", "小さく始める方が運用データを早く蓄積できる"],
        "risks": ["請求やシフトまで含めると初期範囲が広がりすぎる"],
    }
    return BASE + f"""
(async()=>{{
  meetingFlow={js(flow)}; curGoal={js(flow['target']['text'])};curType='商談';curStance='提案する側';curProj='';
  setPhase('prep',false); await new Promise(r=>setTimeout(r,250));
  meetingFlow={js(flow)}; $('ggoal').value=curGoal;$('gtype').value=curType;$('gstance').value=curStance;
  drawStrategy({js(messages)},{js(board)}); {caption('会議前｜AIとの壁打ちから、着地点と議題を準備')}
  window.scrollTo(0,0); return true;
}})()
"""


def live_script(include_global=False):
    flow = meeting_flow(include_global)
    return BASE + f"""
(async()=>{{
  setPhase('live',false);meetingFlow={js(flow)};meetingFlowRebuilding=false;flowExpanded=new Set(['current']);flowLastCurrentId='current';
  renderMeetingFlow(true);recording=true;capturing=true;captureKind='meeting';lastStateSnap={{recording:true,queue:0,analyzing:true,detailing:false,researching:0,exploring:false,aiProvider:'codex'}};
  $('rec').textContent='録音停止';$('rec').classList.add('on');renderPill();
  captureProof={{mic:true,server:true,transcript:true,tab:false,baseline:0}};renderCaptureProof('「更新担当が拠点ごとに違います」 · AI整理済み');
  {caption('会議中｜現在の議題だけを開き、質問と結果をその場で整理')}
  await new Promise(r=>setTimeout(r,220));window.scrollTo(0,Math.max(0,$('flowagendas').offsetTop-235));
  {"localStorage.removeItem(flowGlobalSeenKey());openGlobalFlowInspector($('flowglobal'));document.getElementById('public-demo-caption').textContent='会議中｜新しいAI支援は右ドロワーに届く';await new Promise(r=>setTimeout(r,300));" if include_global else ""}
  return true;
}})()
"""


def map_script(mode):
    label = "放射マップ｜会話タイプの配分と具体的な内容が見える" if mode == "radial" else "会話の関係｜議題・判断・次の行動のつながりを一枚で確認"
    return BASE + f"""
(async()=>{{
  setPhase('live',false);meetingFlow={js(meeting_flow(False))};renderMeetingFlow(true);
  lastData={js(meeting_data())};deckSig='';openVisualSheet('{mode}');$('visualsheet').classList.add('full');$('visualfull').textContent='元の高さ';
  deckZooms['{mode}']=1.22;deckHeight();buildDeck(lastData);applyMindmapMode('{mode}',false);await renderLiveMermaid();
  {caption(label)} await new Promise(r=>setTimeout(r,500));requestMapCenter('{mode}');await new Promise(r=>setTimeout(r,350));return true;
}})()
"""


def review_script():
    return BASE + f"""
(async()=>{{
  recording=false;capturing=false;setPhase('review',false);updateReviewCards({{hasAudio:true,hasFinal:true,hasLearn:true,hasMinutesPdf:true,hasDeck:true,hasDeckPdf:true,jobStatus:{{}}}});
  selectReviewOutputFormat('paper',false);{caption('会議後｜清書・学び・共有PDFまで、同じ画面で完成')}
  await new Promise(r=>setTimeout(r,250));window.scrollTo(0,0);return true;
}})()
"""


def cover_script():
    """High-density real product state for the cover device mockup."""
    flow = meeting_flow(False)
    return BASE + f"""
(async()=>{{
  setPhase('live',false);meetingFlow={js(flow)};meetingFlowRebuilding=false;flowExpanded=new Set(['current']);flowLastCurrentId='current';
  renderMeetingFlow(true);recording=true;capturing=true;captureKind='meeting';lastStateSnap={{recording:true,queue:0,analyzing:true,detailing:false,researching:0,exploring:false,aiProvider:'codex'}};
  $('rec').textContent='録音停止';$('rec').classList.add('on');renderPill();
  const style=document.createElement('style');style.id='public-cover-style';style.textContent=`
    .flow-target,.flow-view-label,.flow-view-buttons,.flow-board-head,.flow-question-block,.flow-actions{{display:none!important}}
    body:not(.phase-prep):not(.phase-review) .mainpane{{max-width:1240px!important;padding:20px 18px 24px!important}}
    .agenda-flow-list{{gap:12px!important}}.flow-agenda-head{{padding:13px 16px!important}}
    .flow-title{{font-size:21px!important}}.flow-summary-line{{font-size:15px!important;margin-top:4px!important;-webkit-line-clamp:1!important}}
    .flow-agenda-body{{padding:12px 15px 14px!important}}.flow-cols{{gap:9px!important}}
    .flow-now{{font-size:15px!important;padding:0!important}}.flow-result-grid,.flow-result-column{{gap:9px!important}}
    .flow-result-section{{padding:10px 13px!important}}.flow-result-kind{{font-size:15px!important;margin-bottom:3px!important}}
    .flow-result-text{{font-size:14px!important;line-height:1.45!important;padding:5px 0 5px 15px!important}}
    .flow-result-text::before{{top:12px!important}}.flow-result-text:first-child::before{{top:9px!important}}
    .flow-agenda-suggestions{{gap:5px!important}}.flow-agenda-suggestions>h3{{font-size:16px!important;margin:1px 0!important}}
    .flow-agenda-suggestion{{padding:11px 13px!important;display:grid!important;grid-template-columns:auto minmax(0,1fr) auto!important;align-items:center!important;gap:4px 12px!important}}
    .flow-suggestion-kind{{margin:0!important;font-size:13px!important;grid-row:1/3!important}}
    .flow-suggestion-text{{font-size:16px!important;line-height:1.35!important}}
    .flow-suggestion-reason{{font-size:14px!important;line-height:1.35!important;margin:0!important}}
    .flow-suggestion-actions{{grid-column:3!important;grid-row:1/3!important;margin:0!important;flex-wrap:nowrap!important}}
    .flow-suggestion-actions button{{min-height:36px!important;padding:6px 11px!important;font-size:14px!important}}
  `;document.head.appendChild(style);
  await new Promise(r=>setTimeout(r,350));window.scrollTo(0,0);return true;
}})()
"""


async def capture(ws, seq, path):
    result = await command(ws, seq, "Page.captureScreenshot", {
        "format": "png", "fromSurface": True, "captureBeyondViewport": False,
    })
    path.write_bytes(base64.b64decode(result["data"]))


async def main(output, hide_caption=False, cover=False):
    output.mkdir(parents=True, exist_ok=True)
    pages = json.load(urllib.request.urlopen(CDP_LIST))
    page = next(row for row in pages if row.get("type") == "page")
    scripts = [cover_script()] if cover else [prep_script(), live_script(False), live_script(True), map_script("radial"), map_script("relation"), review_script()]
    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=30_000_000) as ws:
        seq = 1
        await command(ws, seq, "Page.enable"); seq += 1
        await command(ws, seq, "Runtime.enable"); seq += 1
        await command(ws, seq, "Emulation.setDeviceMetricsOverride", {
            "width": 1280, "height": 720, "deviceScaleFactor": 1, "mobile": False,
        }); seq += 1
        await command(ws, seq, "Page.navigate", {"url": "http://127.0.0.1:8877/"}); seq += 1
        await asyncio.sleep(1.3)
        for index, expression in enumerate(scripts, 1):
            await command(ws, seq, "Runtime.evaluate", {"expression": expression, "awaitPromise": True}); seq += 1
            await asyncio.sleep(0.25)
            if hide_caption:
                await command(ws, seq, "Runtime.evaluate", {
                    "expression": "document.getElementById('public-demo-caption').style.display='none'",
                }); seq += 1
            filename = "cover.png" if cover else f"{index:02d}.png"
            await capture(ws, seq, output / filename); seq += 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--hide-caption", action="store_true")
    parser.add_argument("--cover", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.output, args.hide_caption, args.cover))
