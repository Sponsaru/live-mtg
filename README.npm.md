# LiveMTG

LiveMTG is a local-first meeting copilot that transcribes conversations in real time and turns them into structured notes, suggested questions, meeting guidance, mind maps, polished minutes, and slides.

English and Japanese are supported for the interface, transcription, and AI-generated output.

```bash
npm install -g live-mtg@beta
live-mtg onboard
```

Onboarding lets you choose **Claude Code or Codex**, prepares the audio tools, and starts the local service. Your meetings stay on your computer and you sign in with your own Claude Code or Codex account; LiveMTG does not require a shared API key.

```bash
live-mtg dashboard             # Open the app
live-mtg doctor                # Check dependencies
live-mtg config --language en  # English
live-mtg config --language ja  # Japanese
live-mtg update                # Update the beta
live-mtg report                # Privacy-safe diagnostic report
```

Report bugs through [GitHub Issues](https://github.com/Sponsaru/live-mtg/issues). The diagnostic report excludes transcripts, meeting files, and API keys.

## 日本語

LiveMTGは、会話をリアルタイムに文字起こし・整理し、次に聞く質問、会議ガイド、マインドマップ、清書議事録、スライドを表示するローカル実行型ツールです。

```bash
npm install -g live-mtg@beta
live-mtg onboard
```

初期設定で **Claude Code / Codex** と日本語・英語を選べます。会議データはローカルに保存され、共有APIキーは不要です。
