# LiveMTG Desktop

> 主配布はOpenClawと同じnpm CLI方式へ変更しました。このTauri版は、ターミナルを使いたくない利用者向けの補助配布です。

既存の `server.py` とWeb UIを、Tauriのデスクトップウィンドウで動かす配布用プロジェクトです。

## 配布版の構成

- Tauri: ウィンドウ、アプリ終了時のバックエンド停止、DMG/NSIS生成
- PyInstaller sidecar: Python本体と `server.py`、UI、マインドマップ生成処理を1実行ファイルに同梱
- 初回セットアップ: Claude Code / Codexを選択し、選んだAIのCLI・ログイン状態、ffmpeg、音声認識CLIをアプリ内で確認
- 会議データ: OSのアプリデータ領域へ保存。アプリ更新で消えない

Python、Node.js、Rustは**利用者には不要**です。これらは作者とGitHub Actionsがインストーラーを作る時だけ使います。

## 作者のローカルビルド

必要なもの:

- Node.js 20+
- Rust 1.84+
- Python 3.11〜3.13
- PyInstaller (`python3 -m pip install pyinstaller`)

```bash
cd desktop
npm install
npm run icons
npm run build
```

生成先:

- macOS: `src-tauri/target/release/bundle/dmg/*.dmg`
- Windows: `src-tauri/target/release/bundle/nsis/*-setup.exe`

開発起動は `npm run dev`。既存の常駐版（8777番）と衝突しないよう、デスクトップ版は18777番を使います。

## GitHubで配布する

タグをpushすると `.github/workflows/desktop-release.yml` がMacとWindowsを別々にビルドし、GitHub ReleaseへDMGとWindows setup.exeを添付します。

```bash
git tag v0.1.0
git push origin v0.1.0
```

署名なしでも生成はできますが、一般配布では次が必要です。

- macOS: Apple Developer ID署名とnotarization
- Windows: コード署名証明書
- 自動更新: Tauri updater用の秘密鍵・公開鍵と更新JSON

これらの秘密情報はGitへ置かず、GitHub Actions Secretsへ登録します。

## 現在の境界

- 録音、ライブ解析、議事UI、マインドマップはデスクトップ化の対象です。
- Claude CodeとCodexは各利用者のライセンス・ログインを使うため同梱しません。選択は会議データと別に保存され、あとから切替可能です。
- 音声モデルは数GBあるためインストーラーへ同梱せず、初回取得方式にします。
- 旧「経営者向けデッキ」は `make-slides.sh` がbash依存のため、Windows対応はPython化後に有効化します。
