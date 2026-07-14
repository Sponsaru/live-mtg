# LiveMTG

会話をリアルタイムに文字起こし・整理し、次に聞く質問、会議ガイド、マインドマップ、清書議事録を表示するローカル実行型ツールです。

```bash
npm install -g live-mtg@beta
live-mtg onboard
```

`onboard`がAI選択、必要なCLIの導入とログイン、音声環境の準備、常駐起動まで案内します。初期設定後は `live-mtg dashboard` で画面を開きます。更新は `live-mtg update`、環境確認は `live-mtg doctor` です。

不具合時は `live-mtg logs` でログ確認、`live-mtg report` で会議本文を含まない診断レポート作成、`live-mtg rollback` で直前の公開版へ戻せます。報告先は [GitHub Issues](https://github.com/Sponsaru/live-mtg/issues) です。

初期設定中に、会議の整理に使うAIを **Claude Code / Codex** から選べます。あとから変更しても会議データはそのままです。

```bash
# Codexを使う場合
npm install -g @openai/codex
codex login
live-mtg config --provider codex

# Claude Codeを使う場合
npm install -g @anthropic-ai/claude-code
claude auth login
live-mtg config --provider claude
```

どちらも各ユーザー自身のアカウントでログインして使うため、LiveMTG用の共有APIキーは不要です。

OpenClawと同じ配布モデルです。CLIがローカルの常駐サービスを管理し、操作画面はブラウザで開きます。macOSではLaunchAgent、Windowsではログオン時のScheduled Taskを使います。

必要な外部ツールはClaude CodeまたはCodexと音声処理環境です。`onboard`が不足を検出し、MacではHomebrew/pipx、Windowsではwingetとwhisper.cpp公式バイナリを使って導入します。Windowsの約1.6GBの日本語文字起こしモデルも確認後に自動取得します。
