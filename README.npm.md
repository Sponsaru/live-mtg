# LiveMTG

[![npm](https://img.shields.io/npm/v/live-mtg?label=npm&color=0071e3)](https://www.npmjs.com/package/live-mtg)
[![license](https://img.shields.io/badge/license-MIT-1d1d1f)](https://github.com/Sponsaru/live-mtg/blob/main/LICENSE)
[![GitHub Sponsors](https://img.shields.io/badge/sponsor-%E2%9D%A4-db61a2?logo=githubsponsors)](https://github.com/sponsors/Sponsaru)

![LiveMTG — live meeting intelligence](https://raw.githubusercontent.com/Sponsaru/live-mtg/main/docs/hero.jpg)

![Demo: minutes, questions, and maps grow in real time while you record](https://raw.githubusercontent.com/Sponsaru/live-mtg/main/docs/demo.gif)

LiveMTG is a local-first meeting copilot that transcribes conversations in real time and turns them into structured notes, suggested questions, meeting guidance, mind maps, polished minutes, and slides.

Completed slide decks use the vendored neutral **Slide Work** pattern system in hybrid mode: message layouts for conclusions and informative layouts for evidence, comparisons, decisions, and actions.

English and Japanese are supported for the interface, transcription, and AI-generated output.

```bash
npm install -g live-mtg
live-mtg
```

The first `live-mtg` launch automatically starts onboarding. It lets you choose **Claude Code or Codex**, prepares the audio tools, verifies every required component, and opens the dashboard only when it is ready. Your meetings stay on your computer and LiveMTG does not require a shared API key.

In CI, agent shells, and other non-interactive environments, LiveMTG does not silently approve installations. Sign in to your chosen AI CLI first, then explicitly approve the setup steps with:

```bash
live-mtg onboard --yes --provider claude --language en
# Add --no-daemon when auto-start is not wanted.
```

```bash
live-mtg dashboard             # Open the app
live-mtg doctor                # Check dependencies
live-mtg config --language en  # English
live-mtg config --language ja  # Japanese
live-mtg update                # Update to the latest stable release
live-mtg status                # Check service status and recovery guidance
live-mtg logs                  # Show persisted server logs
live-mtg report                # Privacy-safe diagnostic report
```

Background server output is saved to `~/.live-mtg/server.log` and rotated at 5 MB with three backups. On Windows, the supervisor automatically restarts the server after an unexpected exit, using a backoff capped at 30 seconds.

Finished meeting recordings (`m4a`, `mp3`, `wav`, `webm`, and other common audio formats) can be imported from the new-meeting dialog or Review page. LiveMTG creates a new meeting and runs the normal transcription, agenda, decision, next-action, and visualization pipeline automatically.

When capturing Japanese output in Windows PowerShell 5.1, set `[Console]::OutputEncoding` and `$OutputEncoding` to a BOM-less `System.Text.UTF8Encoding` first, and use `Out-File -Encoding utf8` for redirected logs.

Report bugs through [GitHub Issues](https://github.com/Sponsaru/live-mtg/issues). The diagnostic report excludes transcripts, meeting files, and API keys.

## Support the project

LiveMTG is free and open source (MIT). If it saves your meetings, consider [sponsoring on GitHub](https://github.com/sponsors/Sponsaru) — sponsors get priority responses on issues and a say in the roadmap.

## 日本語

LiveMTGは、会話をリアルタイムに文字起こし・整理し、次に聞く質問、会議ガイド、マインドマップ、清書議事録、スライドを表示するローカル実行型ツールです。

完成スライドは **Slide Work** のニュートラル型を正典にし、結論はMESSAGE型、根拠・比較・決定・行動はINFORMATIVE型を使うhybrid構成で生成します。

```bash
npm install -g live-mtg
live-mtg
```

初回の `live-mtg` で初期設定が自動的に始まり、**Claude Code / Codex** と日本語・英語を選べます。必要環境が揃うまで診断し、準備完了後にダッシュボードを開きます。会議データはローカルに保存され、共有APIキーは不要です。

CIやAIエージェントなどの非対話環境では、AI CLIへ事前にログインし、`live-mtg onboard --yes --provider claude --language ja` のように実行してください。`--yes` は必要ツールの導入・モデル取得・常駐化の確認を自動承認します。

バックグラウンドのサーバー出力は `~/.live-mtg/server.log` に保存され、5MBごとに3世代ローテーションします。Windowsではサーバーが異常終了すると監視プロセスが最大30秒間隔で自動復旧します。停止状態の確認は `live-mtg status`、原因確認は `live-mtg logs` を使用してください。

**開発を支援する** — LiveMTGは無料のOSS（MIT）です。役に立ったら [GitHub Sponsors](https://github.com/sponsors/Sponsaru) での支援を検討してください。スポンサーにはIssueの優先対応とロードマップへの発言権があります。
