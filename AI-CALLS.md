# LiveMTG AI呼び出しマップ（2026-07-17 現在）

全AI呼び出しは `server.py` の `_ai_text()`（`claude -p` / `codex exec` の使い捨てCLI起動）または
シェルスクリプト経由の同型呼び出し。**常駐プロセス・API直叩きは無い**。
モデルは3段構成：`CLAUDE_MODEL=haiku`（ライブ・速度優先）／`ASSIST_MODEL=sonnet`（対話・調査）／`SLIDE_MODEL=opus`（成果物・品質優先）。

## ライブ中（録音している間、自動で回るもの）

| # | 機能 | プロンプト | モデル | 頻度・タイムアウト | 入力 → 出力 |
|---|------|-----------|--------|------------------|------------|
| 1 | 即時解析（FAST-ANALYSIS） | `LIVE_PATCH_PROMPT` | haiku | チャンク到着毎（約15秒毎）・30s。**連続3失敗で45秒に1回へ自動減速** | 直近500字の差分＋小索引（**arc=会議全体の流れ**含む）＋背景 → summary/**arc**/decision/question/relation/**confirm**/agenda・points・todos・open_add（2026-07-17 リストレーンを統合） |
| 2 | 表示ビュー優先更新（ACTIVE-VIEW） | `ACTIVE_MAP_PROMPT` | haiku | **マップ系ビューのみ**30〜40秒毎・40s（リストは#1に統合済み） | 差分＋マップ索引 → mindmap_add |
| 3 | 自動下調べ（LOOKUP） | `_claude_explore` 内プロンプト | haiku系 | lookups発生時・240s | **cwd=背景フォルダ**（読取専用・フォルダ外出禁止）→ 調査回答（research.json） |

※ 詳細整理（DETAIL-ANALYSIS・`DETAIL_PATCH_PROMPT`・haiku・75s）は**録音中は停止**し、録音停止後にまとめて実行（mindmap_add/diagram/lookups）。

## ユーザー操作で動くもの

| # | 機能 | プロンプト | モデル | タイムアウト | 入力 → 出力 |
|---|------|-----------|--------|------------|------------|
| 4 | 準備チャット（＋事前メモ取り込み） | `STRATEGY_PROMPT` | sonnet | 120s | 発言/取り込み全文＋brief＋履歴＋背景要約 → reply（取り込み時は理解レポート）＋brief＋準備ボードJSON |
| 5 | 疑問のWeb裏取り | `VERIFY_PROMPT` | sonnet＋**WebSearch許可**（bypassPermissions） | 150s | 疑問1件 → 出典付き3〜4文 |
| 6 | 清書前の確認質問生成 | `PREP_PROMPT` | sonnet | 120s | 文字起こし（話者分離済み優先）→ 誤認識しやすい固有名詞の質問リスト |
| 7 | 高精度清書 | `FINAL_PROMPT` | opus | 420s | 全文文字起こし＋確認済みヒント → final.json（議事全体） |
| 8 | 学びと次の一手 | `LEARN_PROMPT` | opus | 180s | 全文＋data.json＋プレイブック → insights（個人レポート）＋playbook（追記案）JSON |
| 9 | 会議スライド | `make-slides.sh` 内プロンプト | opus | 420s | data.json＋全文＋**Slide Workパターン実例**（DOM/classコピー契約）→ スライド断片HTML |
| 10 | 学びスライド | `make-learn-slides.sh` 内プロンプト | opus | 420s | learnings.md＋Slide Workパターン → デッキ断片HTML |
| 11 | AI応答テスト（接続診断） | 固定文「OKとのみ返答」 | 既定 | 35s | 疎通確認のみ |

| 12 | 訂正の遡及適用（RETRO） | `RETRO_PROMPT` | haiku | 補足・訂正/確認カード回答の受信毎・30s | 訂正メモ → 置換ペア抽出（適用は決定論の文字列置換。敬称ペアは敬称なしも自動展開）→ data.json全体を修正 |
| 13 | 確認候補の選別（VET） | `CONFIRM_VET_PROMPT` | haiku | 機械検出の候補発生毎（別スレッド）・45s | 未知カタカナ語・人名の候補＋発話スニペット → 一般語を棄却し固有名詞/聞き間違い疑いだけ確認カード化（2026-07-17 決定1A） |
| 14 | 行き詰まりへの提案（COUNSEL） | `COUNSEL_PROMPT` | **sonnet** | FASTのstuckフラグ＋直近4分決定ゼロの2シグナル一致時・CD5分・1会議3回まで・120s | 背景/brief/research全部入り → situation＋対立2案＋推し＋根拠（2026-07-17 決定3B/4A） |

### 質問候補の鮮度管理（2026-07-17 決定2A）

FASTの `questions[]`（0〜3件・各 `kind:"聞く"|"話す"`）は**全置換**。kindはハイブリッド判定
（【会議の用途】設定を優先、無ければ会話の質から）：引き出す場＝聞く一問／ブレスト・企画＝
「次に話すと良い論点・軽い方向づけ」。UIは主流のkindで見出しを「次に聞く質問」⇄「次に話すこと」に切替（2026-07-18）。（積み上げない＝話題が移れば入れ替わる）。各質問に生成時刻 `at` を持ち、
3分でグレー「少し前の提案」表示・5分で自動消滅。×ボタン却下は `_dismissedQ` に保存され、索引経由でAIに再提案禁止を伝える。

## AIを使わないもの（決定論・誤生成ゼロ）

- マインドマップ成果物HTML（make-mindmap.py）・放射/関係マップPDF（make-map-slide.py）・議事録PDF（make-minutes-deck.py）
- 「解釈の確認」の機械検出（`_mech_confirms`：未知カタカナ語・人名の抽出）
- 話者分離（whispermlx/pyannote）・文字起こし（whisper）

## 各呼び出しのデータフロー（何を読んで、どこへ書くか）

パスの凡例: `M/` = `~/mtg-live/meetings/<会議ID>/`（会議データ）、`R/` = `~/mtg-live/`（共通設定）、`BG/` = 会議に設定した背景フォルダ。

### ライブの基幹ループ（自動）

```
マイク音声チャンク（15秒毎）
  → whisper（M/audio/*.webm → M/transcript.txt へ追記。R/asr-learned.txt を認識ヒントに使用）
  → ① 即時解析 FAST-ANALYSIS（haiku）
       読む: M/transcript.txt の未反映差分500字（オフセットは M/.applied）
             M/data.json（重複防止の小索引） / M/meta.json（題名・目標・立場）
             R/profile.md（依頼主） / M/strategy.json の board.outcome（準備の着地点）
             M/live-notes.json（補足・訂正。最優先扱い）
       書く: M/data.json（summary/decisions/guide.questions/relations/confirm をマージ）
  → ブラウザが M/data.json を2秒毎ポーリングして画面更新
```

- **② 表示ビュー優先更新 ACTIVE-VIEW** — 読む: transcript.txt差分＋data.jsonの該当部（リスト or マップ索引）／書く: data.jsonの該当部のみ。ブラウザの `/api/view-focus` ハートビートで「いま見ているビュー」を判定
- **③ 詳細整理 DETAIL**（録音停止後）— 読む: transcript.txt差分（オフセット M/.detail-applied）＋ `_bg_block`＝profile.md・meta.json・strategy.jsonのbrief・**M/context.json**（背景ダイジェスト）・**M/research.json**（調査結果）・live-notes.json／書く: data.json（mindmap/diagram）＋lookups発行
- **④ 背景3層**（BG/ が設定されている時のみ）:
  - 第1層 探索: 会議開始時に `_claude_explore`（cwd=BG/・読取専用）→ **M/context.json**（ダイジェスト＋ファイルマップ）
  - 第3層 自動下調べ: ①③が出した lookups を直列処理（cwd=BG/）→ **M/research.json** に蓄積 → 次のライブ整理とレール「会議の狙い」に反映

### ユーザー操作系

| 機能 | 読む | 書く |
|---|---|---|
| 準備チャット / 事前メモ取り込み | M/strategy.json（履歴・board・brief）・meta.json・profile.md・context.json要約・（取り込み時は指定ファイル/フォルダ全文） | M/strategy.json → BG/会議準備/<id>/事前準備.md へ自動書き出し |
| Web裏取り | 疑問文のみ（＋WebSearch） | 画面表示のみ（保存なし） |
| 清書前の確認質問 | M/transcript-full.txt or transcript.txt・M/diarization.json（話者分離済み本文を優先） | M/prep.json（質問・回答・話者対応。次回の初期値） |
| 高精度清書 | M/audio/ 全結合 → whisper再転写 → 全文＋prep.jsonの確定ヒント | M/final.json・M/transcript-full.txt → BG/議事録/<id>/ へ議事録.md等を同期。回答は R/asr-learned.txt へ学習 |
| 学び抽出 | transcript-full/transcript・data.json・R/playbooks/<用途>.md・meta.json | M/learnings.md（自動保存）。承認時のみ R/playbooks/<用途>.md へ追記 |
| 会議スライド | data.json・transcript(-full).txt・slide-work-guide.md・slide-work-pattern-examples.html | M/slides.html（テンプレは slide-work-template.html） |
| 学びスライド | M/learnings.md・Slide Work一式 | M/learn-slides.html |

### フロー定義の所在

「どの順で・何をトリガーに動くか」はすべて server.py のワーカースレッド（chunk_worker → analysis_worker / active_view_worker / detail_worker / research系）とHTTPハンドラに実装されている。**この文書がその写し**であり、プロンプト本文は server.py 内の `*_PROMPT` 定数と make-*.sh 内に定義されている（外部プロンプトファイルは無い）。

## 回帰テスト

`python3 scripts/eval-prompts.py`（ローカル専用・claude CLI必須）が、即時レーンの発火条件
（曖昧人名→confirm・明瞭→非発火・合意→decision・ToDo拾い・目標なし質問・複数質問候補・stuck発火/非発火）、
RETRO/VET/COUNSELの構造、
**関係図のストーリーライン品質**（flowchart・全エッジにラベル・十分な流れの本数）を
ゴールデンケースで検証する。**プロンプトを変更したら必ず実行**（2026-07-17新設・10ケース全PASS確認済み）。

### 会話の関係＝「議論のストーリーライン図」（2026-07-17 依頼者と言語化・確定）

- 話題ごとに独立した流れ（起点→展開→帰結）を2〜4本。ノード＝案・状態・数字・結論
- 全エッジに論旨が再生できるラベル（例: 30万×10社=300万）。実線＝合意・推進、点線＝懸念・未確定
- 人物は流れの主語としてのみ登場（静的な人物一覧は禁止）
- ライブ＝FASTのrelationペア（type18字・tone:懸念→点線）を決定論でMermaid化。
  停止後・清書＝DETAIL/FINALがストーリー図で全面更新。**FASTの機械生成はストーリー図を
  上書きしない**（relations_addが来た時だけ再生成。2026-07-17 の安定性バグ修正）
- 会話の関係・時系列は「積み上げ」が本質なので序盤を捨てない。関係ペアは `LIVE_RELATIONS_MAX`
  （=60）、時系列は `TIMELINE_MAX`（=2000・≒8時間）まで累積。いずれも暴走防止の安全上限で、
  実会議はまず届かない。クライアントも時系列は全件描画（旧: サーバ60件かつ画面直近60件だけ＝
  15分で序盤が消えていた。2026-07-20 依頼者要望で撤廃）

## 共通の設計原則

- プロンプトはすべて **server.py / make-*.sh 内にインライン定義**（このファイルが唯一の一覧）
- ライブ系はJSONのみ返す契約＋`_parse_live_patch` でフェンス除去・スキーマ検証、不一致は1回だけ再要求（L3367）
- 背景フォルダを触る呼び出し（#3, #4）は**読取専用・フォルダ外へ出ない**を厳守（Drive全域検索の再発防止）
- whisperのヒント文混入・話者名の創作を防ぐルールを各プロンプトに明記
