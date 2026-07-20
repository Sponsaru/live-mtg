#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────
# server.py — 議事ライブ整理 コントロールサーバ（クロスOS）
#   録音はブラウザ(マイク＋会議タブ音声)で行い、音声チャンクを /api/chunk で受信。
#   サーバは ffmpeg(decode)→whisper-cli(文字起こし)→claude(整理) を python で直列処理。
#   ヘッダー操作（録音 開始/停止・新規会議・会議切替・スライド化・全文表示）と配信も担当。
#   会議は 1つ=1フォルダ（meetings/<id>/）で独立管理。
# ─────────────────────────────────────────────────────────────
import os, sys, json, subprocess, signal, threading, time, re, html, queue, glob, shutil, difflib, platform, runpy, getpass, shlex
import urllib.request, urllib.parse, tempfile
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

SCRIPT_DIR = os.environ.get(
    "LIVE_MTG_RESOURCE_DIR",
    getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))),
)
try:
    with open(os.path.join(SCRIPT_DIR, "package.json"), encoding="utf-8") as _package_file:
        APP_VERSION = json.load(_package_file).get("version", "development")
except Exception:
    try:
        APP_VERSION = open(os.path.join(SCRIPT_DIR, "VERSION"), encoding="utf-8").read().strip()
    except Exception:
        APP_VERSION = os.environ.get("LIVE_MTG_VERSION", "development")
DESKTOP    = os.environ.get("LIVE_MTG_DESKTOP", "") == "1"
RUN        = os.environ.get("RUN", os.path.expanduser("~/mtg-live"))            # ローカル: state.json / 一時wav
# 会議データの保存先＝ローカル（2026-07-10変更）。録音中の高頻度I/OをGoogle Driveに当てると
# FileProviderが詰まって全機能ハングするため、ライブはローカルで動かし、完成品だけDriveへ自動同期する。
SESS       = os.environ.get("MEETINGS_DIR", os.path.join(RUN, "meetings"))
# チーム共有用の同期先（旧保存場所＝共有ドライブ内）。SESSと同一パスなら同期は自動でスキップ
DRIVE_DIR  = os.environ.get("DRIVE_SYNC_DIR", SESS if DESKTOP else os.path.join(SCRIPT_DIR, "meetings"))
WAVROOT    = os.path.join(RUN, "wav")                                           # 一時wavはローカル（ドライブ同期を汚さない）
PORT       = int(os.environ.get("PORT", "8777"))
MIC        = os.environ.get("MIC", "1")
CHUNK      = os.environ.get("CHUNK", "30")   # 録音チャンク秒。長めにすると文脈が効いて精度↑・更新は遅くなる（差分更新なので要約コストは一定）
# 文字起こしバックエンド： mlx=mlx_whisper(Apple Silicon・高精度large-v3・既定) / cpp=whisper-cli(クロスOS・Windows配布用フォールバック)
ASR_BACKEND  = os.environ.get("ASR_BACKEND", "mlx")
# mlx用モデル（HF repo）。large-v3(非turbo)＝turboより誤変換が少なく、読めない音は無理に埋めず素直に崩れる（＝嘘を作りにくい）
MLX_MODEL    = os.environ.get("MLX_MODEL", "mlx-community/whisper-large-v3-mlx")
# cpp(whisper-cli)用モデル。Windows等でmlxが使えない環境向け
MODEL        = os.environ.get("MODEL", os.path.expanduser("~/.cache/whisper-cpp/ggml-large-v3-turbo.bin"))
# 固有名詞の誤変換を減らす辞書ヒント（whisperのinitial prompt）。会議で頻出する社名・人名・専門語を並べる
ASR_HINT     = os.environ.get("ASR_HINT", "")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "haiku")    # ライブ更新は速度最優先。清書・スライドは下記の品質優先モデルを使う
SLIDE_MODEL  = os.environ.get("SLIDE_MODEL", "opus")      # スライド生成モデル（品質優先）
ASSIST_MODEL = os.environ.get("ASSIST_MODEL", "sonnet")  # AIサポートのWeb検索裏取り用（オンデマンド。速さ優先でsonnet）
ASSIST_TOOLS = os.environ.get("ASSIST_TOOLS", "WebSearch,WebFetch")  # 非対話のclaude -pにWeb検索を許可（これが無いとツール許可待ちでハングする）
AI_PROVIDER  = os.environ.get("AI_PROVIDER", "claude").strip().lower()
SETTINGS_FILE = os.path.join(RUN, "config.json")
try:
    _SETTINGS = json.load(open(SETTINGS_FILE, encoding="utf-8")) if os.path.isfile(SETTINGS_FILE) else {}
except Exception:
    _SETTINGS = {}
if "AI_PROVIDER" not in os.environ:
    AI_PROVIDER = str(_SETTINGS.get("aiProvider", AI_PROVIDER)).lower()
if AI_PROVIDER not in ("claude", "codex"):
    AI_PROVIDER = "claude"
# 文字起こしモデルのUI切替（接続診断から変更可）。環境変数 MLX_MODEL 指定が最優先
ASR_MODELS = {"accurate": "mlx-community/whisper-large-v3-mlx",
              "fast": "mlx-community/whisper-large-v3-turbo"}
ASR_CHOICE = str(_SETTINGS.get("asrModel", "accurate")).strip().lower()
if ASR_CHOICE not in ASR_MODELS:
    ASR_CHOICE = "accurate"
LANGUAGE = str(os.environ.get("LIVE_MTG_LANGUAGE", _SETTINGS.get("language", "ja"))).strip().lower()
if LANGUAGE not in ("ja", "en"):
    LANGUAGE = "ja"
HF_CREDENTIAL_SERVICE = "live-mtg.huggingface"
HF_TOKEN_OVERRIDE = str(os.environ.get("HF_TOKEN", "")).strip()
LEGACY_HF_TOKEN = str(_SETTINGS.get("hfToken", "")).strip()
CODEX_MODEL   = os.environ.get("CODEX_MODEL", "").strip()  # 空ならCodex CLI側の推奨既定モデル
SILENCE_DB   = float(os.environ.get("SILENCE_DB", "-45")) # mean_volumeがこれ未満(dB)なら無音とみなす
# 用途別プレイブック（商談.md / 採用面接.md 等）＝「やり方のノウハウ」の蓄積場所。
# フォルダ（案件の事実）とは別軸で、どの案件で使っても同じ用途なら同じプレイブックが効く。
# 共有ドライブ内に置く＝チームで読める・手でも編集できる（ナレッジは.mdで管理の方針）
PLAYBOOK_DIR = os.environ.get("PLAYBOOK_DIR", os.path.join(RUN, "playbooks") if DESKTOP else os.path.join(SCRIPT_DIR, "playbooks"))
# 依頼主プロフィール（録音している本人＝私は誰か）。画面メニュー「プロフィール」で設定し、
# ライブ整理・ガイド・清書・自動下調べの全AIに注入する（話者ラベル・助言の立場・話者推定の精度が上がる）
PROFILE_MD   = os.environ.get("PROFILE_MD", os.path.join(RUN, "profile.md") if DESKTOP else os.path.join(SCRIPT_DIR, "profile.md"))

def _t(ja, en):
    return en if LANGUAGE == "en" else ja

def _localized_prompt(prompt):
    if LANGUAGE == "en":
        return str(prompt) + "\n\nIMPORTANT LANGUAGE RULE: Write every user-facing value in English. Keep JSON keys and Mermaid syntax unchanged."
    return str(prompt) + "\n\n重要な言語ルール：ユーザー向けの値はすべて日本語で書く。JSONキーとMermaid記法は変更しない。"

def _save_setting(key, value):
    config = {}
    try:
        if os.path.isfile(SETTINGS_FILE):
            config = json.load(open(SETTINGS_FILE, encoding="utf-8"))
    except Exception:
        config = {}
    config[key] = value
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    try: os.chmod(SETTINGS_FILE, 0o600)
    except Exception: pass

def _delete_setting(key):
    try:
        config = json.load(open(SETTINGS_FILE, encoding="utf-8")) if os.path.isfile(SETTINGS_FILE) else {}
    except Exception:
        config = {}
    if key not in config:
        return
    config.pop(key, None)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    try: os.chmod(SETTINGS_FILE, 0o600)
    except Exception: pass

def _credential_get_hf_token():
    """OS資格情報ストアから取得。値はHTTP・ログ・設定JSONへ返さない。"""
    if HF_TOKEN_OVERRIDE:
        return HF_TOKEN_OVERRIDE
    try:
        if sys.platform == "darwin":
            r = subprocess.run(["/usr/bin/security", "find-generic-password", "-a", getpass.getuser(),
                                "-s", HF_CREDENTIAL_SERVICE, "-w"], capture_output=True, text=True, timeout=10)
            return r.stdout.strip() if r.returncode == 0 else ""
        if os.name == "nt":
            path = os.path.join(RUN, "hf-token.dpapi")
            if not os.path.isfile(path): return ""
            script = ('$b=[IO.File]::ReadAllBytes($args[0]);'
                      '$p=[Security.Cryptography.ProtectedData]::Unprotect($b,$null,[Security.Cryptography.DataProtectionScope]::CurrentUser);'
                      '[Console]::Out.Write([Text.Encoding]::UTF8.GetString($p))')
            r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script, path],
                               capture_output=True, text=True, timeout=10)
            return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        pass
    return ""

def _credential_set_hf_token(token):
    token = str(token or "").strip()
    if not token.startswith("hf_") or len(token) < 10:
        return False
    try:
        if sys.platform == "darwin":
            # `-w`を最後に置くとsecurityがstdinから安全に入力を読む。argvへ秘密を載せない。
            r = subprocess.run(["/usr/bin/security", "add-generic-password", "-U", "-a", getpass.getuser(),
                                "-s", HF_CREDENTIAL_SERVICE, "-l", "LiveMTG Hugging Face", "-w"],
                               input=token + "\n" + token + "\n", capture_output=True, text=True, timeout=30)
            return r.returncode == 0
        if os.name == "nt":
            os.makedirs(RUN, exist_ok=True)
            path = os.path.join(RUN, "hf-token.dpapi")
            script = ('$t=[Console]::In.ReadToEnd();$b=[Text.Encoding]::UTF8.GetBytes($t.Trim());'
                      '$p=[Security.Cryptography.ProtectedData]::Protect($b,$null,[Security.Cryptography.DataProtectionScope]::CurrentUser);'
                      '[IO.File]::WriteAllBytes($args[0],$p)')
            r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script, path],
                               input=token, capture_output=True, text=True, timeout=30)
            return r.returncode == 0
    except Exception:
        pass
    return False

def _hf_token_configured():
    return bool(_credential_get_hf_token())

def _init_runtime():
    """GUI起動でもCLIを発見できるPATHと、書き込み可能な初期データ領域を用意する。"""
    extras = []
    if sys.platform == "darwin":
        extras = [os.path.expanduser("~/.local/bin"), "/opt/homebrew/bin", "/usr/local/bin"]
    elif os.name == "nt":
        extras = [os.path.expandvars(r"%LOCALAPPDATA%\Programs\Claude")]
    current = os.environ.get("PATH", "")
    os.environ["PATH"] = os.pathsep.join([p for p in extras if p] + ([current] if current else []))
    os.makedirs(RUN, exist_ok=True)
    if not DESKTOP:
        return
    os.makedirs(PLAYBOOK_DIR, exist_ok=True)
    source_playbooks = os.path.join(SCRIPT_DIR, "playbooks")
    if not os.path.isdir(source_playbooks):
        source_playbooks = os.path.join(SCRIPT_DIR, "defaults", "playbooks")
    if os.path.isdir(source_playbooks):
        for src in glob.glob(os.path.join(source_playbooks, "*.md")):
            dst = os.path.join(PLAYBOOK_DIR, os.path.basename(src))
            if not os.path.exists(dst):
                shutil.copy2(src, dst)
    for suffix in (".md", ".json"):
        src = os.path.join(SCRIPT_DIR, "profile" + suffix)
        dst = os.path.splitext(PROFILE_MD)[0] + suffix
        if os.path.isfile(src) and not os.path.exists(dst):
            shutil.copy2(src, dst)

_init_runtime()

# beta.36以前の平文configを一度だけOS資格情報ストアへ移し、成功後に削除する。
if LEGACY_HF_TOKEN:
    if _hf_token_configured() or _credential_set_hf_token(LEGACY_HF_TOKEN):
        _delete_setting("hfToken")

def desktop_health():
    """初回セットアップ画面用。サーバ起動と外部CLIの準備状況を分けて返す。"""
    has_mlx, has_cpp = bool(shutil.which("mlx_whisper")), bool(shutil.which("whisper-cli"))
    has_diarization = bool(shutil.which("whispermlx"))
    asr_ok = has_mlx or has_cpp
    asr_name = "mlx_whisper" if has_mlx else ("whisper-cli" if has_cpp else "mlx_whisper / whisper-cli")
    ai_cmd, ai_label = ("codex", "Codex") if AI_PROVIDER == "codex" else ("claude", "Claude Code")
    ai_installed = bool(shutil.which(ai_cmd))
    ai_login_cmd = [_cli("codex"), "login", "status"] if AI_PROVIDER == "codex" else [_cli("claude"), "auth", "status"]
    ai_login_help = ("Run codex login" if LANGUAGE == "en" else "codex login を実行してください") if AI_PROVIDER == "codex" else ("Run claude auth login" if LANGUAGE == "en" else "claude auth login を実行してください")
    ai_logged_in = False
    if ai_installed:
        try:
            ai_logged_in = subprocess.run(ai_login_cmd, capture_output=True, timeout=8,
                                           env=_ai_env()).returncode == 0
        except Exception:
            pass
    checks = [
        {"id": "ai", "label": "%s CLI" % ai_label, "ok": ai_installed,
         "required": True,
         "help": ("npm install -g @openai/codex" if AI_PROVIDER == "codex"
                  else "npm install -g @anthropic-ai/claude-code")},
        {"id": "ai-login", "label": (("%s sign-in" if LANGUAGE == "en" else "%sへのログイン") % ai_label), "ok": ai_logged_in,
         "required": True, "help": ai_login_help},
        {"id": "ffmpeg", "label": _t("音声変換（ffmpeg）", "Audio conversion (ffmpeg)"), "ok": bool(shutil.which("ffmpeg")),
         "required": True, "help": _t("Macは brew install ffmpeg、Windowsは winget install Gyan.FFmpeg", "Mac: brew install ffmpeg; Windows: winget install Gyan.FFmpeg")},
        {"id": "asr", "label": (_t("文字起こし（%s）", "Transcription (%s)") % asr_name), "ok": asr_ok,
         "required": True,
         "help": _t("Macは pipx install mlx-whisper、Windowsはwhisper.cppのwhisper-cliとモデルを設定してください", "Mac: pipx install mlx-whisper; Windows: configure whisper-cli and its model")},
        {"id": "diarization", "label": _t("話者分離（whispermlx）", "Speaker diarization (whispermlx)"),
         "ok": has_diarization and _hf_token_configured(), "required": False,
         "help": _t("live-mtg onboardでwhispermlxを導入し、画面の『AI・音声の接続診断』でHFトークンを設定", "Install whispermlx with live-mtg onboard, then set an HF token in AI & audio diagnostics")},
    ]
    if ASR_BACKEND == "cpp" or (not has_mlx and has_cpp):
        checks.append({"id": "model", "label": _t("文字起こしモデル", "Transcription model"), "ok": os.path.isfile(MODEL),
                       "required": True, "help": _t("ggml-large-v3-turbo.binを取得し、MODELに保存先を設定してください", "Download ggml-large-v3-turbo.bin and set MODEL to its path")})
    ai_ok = all(x["ok"] for x in checks if x["id"] in ("ai", "ai-login"))
    audio_ok = all(x["ok"] for x in checks if x["id"] in ("ffmpeg", "asr", "model"))
    return {"ok": ai_ok and audio_ok, "aiOk": ai_ok, "audioOk": audio_ok, "checks": checks,
            "platform": platform.system(), "dataDir": RUN, "version": APP_VERSION,
            "aiProvider": AI_PROVIDER, "language": LANGUAGE, "asrModel": ASR_CHOICE,
            "speakerDiarization": {"installed": has_diarization, "tokenConfigured": _hf_token_configured()}}

def service_health():
    """CLIと録音UI用の軽量生存確認。外部CLIは呼ばず即応する。"""
    return {"ok": True, "version": APP_VERSION, "service": "live-mtg"}

def set_ai_provider(provider):
    global AI_PROVIDER
    provider = str(provider or "").strip().lower()
    if provider not in ("claude", "codex"):
        return False
    AI_PROVIDER = provider
    _save_setting("aiProvider", provider)
    return True

def set_language(language):
    global LANGUAGE
    language = str(language or "").strip().lower()
    if language not in ("ja", "en"):
        return False
    LANGUAGE = language
    _save_setting("language", language)
    return True

asr_warmup = {"status": "idle", "model": ""}   # モデル準備の進行状況（UI表示用）
def _warmup_asr_model():
    """切替直後にモデルのダウンロード/ロードを済ませる（会議中の初回チャンクで数分待たされないように。
    2026-07-17 依頼者指示）。無音0.4秒を1回文字起こしするだけ＝HFからのDLとメモリロードが走る。"""
    def job():
        work = tempfile.mkdtemp(prefix="livemtg-asrwarm-")
        asr_warmup.update(status="preparing", model=ASR_CHOICE)
        try:
            wav = os.path.join(work, "warm.wav")
            _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                  "-i", "anullsrc=r=16000:cl=mono", "-t", "0.4", wav], timeout=30)
            sys.stderr.write("[ASR-WARMUP] %s 準備開始（初回はダウンロードあり）\n" % _asr_model()); sys.stderr.flush()
            _whisper_mlx_once(wav, None)
            asr_warmup.update(status="ready")
            sys.stderr.write("[ASR-WARMUP] %s 準備完了\n" % _asr_model()); sys.stderr.flush()
        except Exception as e:
            asr_warmup.update(status="error")
            sys.stderr.write("[ASR-WARMUP] 失敗 %r\n" % e); sys.stderr.flush()
        finally:
            shutil.rmtree(work, ignore_errors=True)
    threading.Thread(target=job, daemon=True).start()

def set_asr_model(choice):
    global ASR_CHOICE
    choice = str(choice or "").strip().lower()
    if choice not in ASR_MODELS:
        return False
    ASR_CHOICE = choice
    _save_setting("asrModel", choice)
    if shutil.which("mlx_whisper"):
        _warmup_asr_model()
    return True

def _asr_model():
    if os.environ.get("MLX_MODEL"):
        return MLX_MODEL
    return ASR_MODELS[ASR_CHOICE]

def set_hf_token(token):
    """話者分離用HFトークンをOS資格情報ストアへ保存する。"""
    ok = _credential_set_hf_token(token)
    if ok:
        _delete_setting("hfToken")
    return ok

def _cli(name):
    """CLIコマンド名をフルパスへ解決する。Windowsでは実体が claude.cmd / codex.cmd
    （npmのバッチシム）で、素の名前ではCreateProcessが見つけられず起動に失敗する
    （2026-07-18 PC109実機レポート：ログイン判定の誤判定）。whichはPATHEXTを考慮する。"""
    return shutil.which(name) or name

def _ai_env():
    """claude をローカル自動実行する時の共通環境（morning-routine.sh の plist 準拠）。
    ★ DISABLE_AUTOUPDATER=1 が無いと、auto-update が走った直後に claude -p が固まる（実測）。
    PATHは .local/bin のヘルスチェック済み claude を優先。全ての claude 呼び出しでこれを使う。"""
    env = dict(os.environ)
    env["DISABLE_AUTOUPDATER"] = "1"
    if os.name != "nt":
        preferred = [os.path.expanduser("~/.local/bin"), "/opt/homebrew/bin", "/usr/local/bin",
                     "/usr/bin", "/bin"]
        env["PATH"] = os.pathsep.join(preferred + [env.get("PATH", "")])
    # Windowsはnpm/winget/LiveMTGが追加したPATHをそのまま使う。Unix向けPATHで上書きすると
    # claude/codex、ffmpeg、whisper-cliをすべて見失う。
    return env

_claude_env = _ai_env  # 既存の補助スクリプト互換

def _kill_process_tree(p):
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, timeout=10)
        else:
            os.killpg(p.pid, signal.SIGKILL)
    except Exception:
        try: p.kill()
        except Exception: pass

def _ai_text(prompt, timeout=120, cwd=None, model=None, web=False, schema=None, background=False):
    """選択中のClaude Code/Codexを非対話実行し、最終回答テキストを返す。"""
    prompt = _localized_prompt(prompt)
    cwd = cwd if cwd and os.path.isdir(cwd) else tempfile.gettempdir()
    if AI_PROVIDER == "claude":
        cmd = [_cli("claude"), "-p", "--model", model or ASSIST_MODEL]
        if web:
            cmd += ["--permission-mode", "bypassPermissions", "--allowedTools", ASSIST_TOOLS]
        if background:
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, cwd=cwd, env=_ai_env(), start_new_session=(os.name != "nt"))
            _register_background_process(p)
            try:
                try: stdout, stderr = p.communicate(input=prompt, timeout=timeout)
                except subprocess.TimeoutExpired:
                    _kill_process_tree(p); raise
                r = subprocess.CompletedProcess(cmd, p.returncode, stdout, stderr)
            finally:
                _unregister_background_process(p)
        else:
            r = _run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout,
                     env=_ai_env(), cwd=cwd)
        if r.returncode != 0 and not (r.stdout or "").strip():
            raise RuntimeError((r.stderr or "Claude Codeの実行に失敗しました")[:500])
        return (r.stdout or "").strip()

    output = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, dir=tempfile.gettempdir())
    output.close()
    schema_path = None
    try:
        cmd = [_cli("codex")]
        if web:
            cmd.append("--search")
        cmd += ["exec", "--ephemeral", "--sandbox", "read-only", "--skip-git-repo-check",
                "--color", "never", "-C", cwd, "-o", output.name]
        if CODEX_MODEL:
            cmd += ["--model", CODEX_MODEL]
        if schema:
            sf = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                             dir=tempfile.gettempdir(), encoding="utf-8")
            json.dump(schema, sf, ensure_ascii=False); sf.close(); schema_path = sf.name
            cmd += ["--output-schema", schema_path]
        cmd.append(prompt)
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.PIPE, text=True, cwd=cwd, env=_ai_env(),
                             start_new_session=True)
        if background: _register_background_process(p)
        _register_long_process(p)
        try:
            _, err = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_process_tree(p)
            raise TimeoutError("Codexの応答が時間内に完了しませんでした")
        _check_long_cancelled()
        out = _read_text(output.name).strip()
        if p.returncode != 0 and not out:
            raise RuntimeError((err or "Codexの実行に失敗しました")[-800:])
        return out
    finally:
        if 'p' in locals() and background: _unregister_background_process(p)
        for path in (output.name, schema_path):
            if path:
                try: os.remove(path)
                except FileNotFoundError: pass

# 録音中はMac自体をスリープさせない（画面が暗くなってもシステムが起きていれば録音は続く）
_caff = [None]
def _caffeinate(on):
    if on and _caff[0] is None:
        try: _caff[0] = subprocess.Popen(["caffeinate", "-di"])   # -d=画面 -i=アイドルスリープ禁止
        except Exception: _caff[0] = None
    elif not on and _caff[0] is not None:
        try: _caff[0].terminate()
        except Exception: pass
        _caff[0] = None
import atexit
atexit.register(lambda: _caffeinate(False))   # サーバ終了時に必ず解除（スリープ禁止を残さない）

def _sync_files(src, dst):
    """ディレクトリ/ファイルを上書き同期。Windowsは標準ライブラリだけで動かす。"""
    if os.name == "nt" or not shutil.which("rsync"):
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
        return True
    source = src + "/" if os.path.isdir(src) else src
    target = dst + "/" if os.path.isdir(src) else dst
    return subprocess.run(["rsync", "-a", "--timeout=30", source, target],
                          capture_output=True, text=True, timeout=180).returncode == 0

def sync_to_drive(sid):
    """会議フォルダをローカル→共有ドライブへ非同期コピー（チーム共有用）。
    録音停止・清書・スライド生成の完了時に呼ぶ。Driveが不調でも本体を巻き込まない（別スレッド＋timeout）。"""
    if sid in deleted_sessions:
        return
    src = sdir(sid)
    dst = os.path.join(DRIVE_DIR, sid)
    if not os.path.isdir(src) or os.path.realpath(src) == os.path.realpath(dst):
        return
    def _run_sync():
        try:
            if sid in deleted_sessions:
                return
            os.makedirs(dst, exist_ok=True)
            ok = _sync_files(src, dst)
            sys.stderr.write("[SYNC] %s → Drive %s\n" % (sid, "OK" if ok else "失敗"))
        except Exception as e:
            sys.stderr.write("[SYNC] %s 失敗 %r\n" % (sid, e))
        sys.stderr.flush()
    threading.Thread(target=_run_sync, daemon=True).start()

# ---------- 清書一式を背景フォルダ（案件フォルダ）へ届ける ----------
def _safe_name(s):
    """フォルダ名に使えない文字を除去（会議タイトル用）"""
    s = re.sub(r'[/\\:*?"<>|\r\n]', "", (s or "").strip())
    return s[:60] or "会議"

def _render_minutes_md(meta, obj):
    """final.json（清書版議事）から、人がそのまま読める議事録Markdownを組み立てる。"""
    L = ["# " + meta.get("title", "会議"), ""]
    L.append("- 日時：%s" % meta.get("created", ""))
    sp = [s for s in (obj.get("speakers") or []) if s]
    if sp: L.append("- 参加者：" + "、".join(sp))
    goal = (meta.get("goal") or "").strip()
    if goal: L.append("- 目標：" + goal)
    L.append("")
    if obj.get("summary"):
        L += ["## 要旨", str(obj["summary"]), ""]
    def sec(title, items, fmt=None):
        items = items or []
        if not items: return
        L.append("## " + title)
        for it in items:
            try: L.append(fmt(it) if fmt else "- %s" % it)
            except Exception: L.append("- %s" % it)
        L.append("")
    def _todo(t):
        if not isinstance(t, dict): return "- [ ] %s" % t
        due = ("（期限：%s）" % t["due"]) if t.get("due") else ""
        return "- [ ] %s：%s%s" % (t.get("who") or "未定", t.get("what", ""), due)
    def _say(x):
        if not isinstance(x, dict): return "- %s" % x
        return "- **%s**：%s" % (x.get("who") or "不明", x.get("text", ""))
    sec("議題", obj.get("agenda"))
    sec("論点・意見", obj.get("points"))
    sec("決定事項", obj.get("decisions"))
    sec("TODO", obj.get("todos"), _todo)
    sec("未解決・要確認", obj.get("open"))
    if (obj.get("diagram") or "").strip():
        L += ["## 図解", "```mermaid", obj["diagram"].strip(), "```", ""]
    sec("主要発言", obj.get("log"), _say)
    L += ["---", "＊live-mtg の清書（finalize）から自動生成。全文は同フォルダの「全文文字起こし.txt」。"]
    return "\n".join(L) + "\n"

def sync_to_project(sid):
    """清書一式を会議の背景フォルダ（案件フォルダ）へ非同期コピー。
    <背景フォルダ>/議事録/<会議ID> <題名>/ に 議事録.md・全文文字起こし.txt・final.json・マインドマップ.html を置く。
    清書前（final.json 無し）は何もしない。Driveが不調でも本体を巻き込まない（別スレッド＋rsync timeout）。"""
    if sid in deleted_sessions:
        return
    m = read_meta(sid)
    pdir = (m.get("project_dir") or "").strip()
    d = sdir(sid)
    if not pdir or not os.path.isfile(os.path.join(d, "final.json")):
        return
    def _run_copy():
        try:
            if sid in deleted_sessions:
                return
            if not os.path.isdir(pdir):
                sys.stderr.write("[SYNC] %s → 背景フォルダが見つからない: %s\n" % (sid, pdir)); sys.stderr.flush(); return
            # ローカルで一式を組み立ててから rsync（Driveの遅延・ハングを組み立て中に浴びない）
            stage = tempfile.mkdtemp(prefix="mtg-proj-")
            try:
                with open(os.path.join(d, "final.json"), encoding="utf-8") as f:
                    obj = json.load(f)
            except Exception:
                obj = {}
            with open(os.path.join(stage, "議事録.md"), "w", encoding="utf-8") as f:
                f.write(_render_minutes_md(m, obj))
            for src_name, dst_name in (("transcript-full.txt", "全文文字起こし.txt"), ("final.json", "final.json")):
                p = os.path.join(d, src_name)
                if os.path.isfile(p): shutil.copy2(p, os.path.join(stage, dst_name))
            sl = os.path.join(d, "mindmap.html")
            has_slides = os.path.isfile(sl)
            if has_slides:
                txt = (neutral_generated_html(sl, persist=True) or "").replace(
                    'src="../../mermaid.min.js"', 'src="../mermaid.min.js"')
                with open(os.path.join(stage, "マインドマップ.html"), "w", encoding="utf-8") as f:
                    f.write(txt)
            dk = os.path.join(d, "slides.html")
            if os.path.isfile(dk):
                txt = (neutral_generated_html(dk, persist=True) or "").replace(
                    'src="../../mermaid.min.js"', 'src="../mermaid.min.js"').replace(
                    'url("/brand-logo.png")', 'url("../brand-logo.png")').replace(
                    'url("/slide-bg.jpg")', 'url("../slide-bg.jpg")')
                with open(os.path.join(stage, "スライド.html"), "w", encoding="utf-8") as f:
                    f.write(txt)
            # 追加成果物（2026-07-16）：マップPDF・学びレポート・学びスライドも案件フォルダへ届ける
            for src_name, dst_name in (("map-radial.pdf", "放射マップ.pdf"),
                                       ("map-relation.pdf", "会話の関係.pdf"),
                                       ("map-topics.pdf", "論点ツリー.pdf"),
                                       ("map-timeline.pdf", "時系列.pdf"),
                                       ("minutes.pdf", "議事録.pdf"),
                                       ("learn-slides.pdf", "学びと次の一手.pdf"),
                                       ("learnings.md", "学びと次の一手.md")):
                p_src = os.path.join(d, src_name)
                if os.path.isfile(p_src): shutil.copy2(p_src, os.path.join(stage, dst_name))
            ls_html = os.path.join(d, "learn-slides.html")
            if os.path.isfile(ls_html):
                txt = (neutral_generated_html(ls_html, persist=True) or "").replace(
                    'src="../../mermaid.min.js"', 'src="../mermaid.min.js"').replace(
                    'url("/brand-logo.png")', 'url("../brand-logo.png")').replace(
                    'url("/slide-bg.jpg")', 'url("../slide-bg.jpg")')
                with open(os.path.join(stage, "学びスライド.html"), "w", encoding="utf-8") as f:
                    f.write(txt)
            base = os.path.join(pdir, "議事録")
            dst = os.path.join(base, "%s %s" % (sid, _safe_name(m.get("title", ""))))
            os.makedirs(dst, exist_ok=True)
            ok = _sync_files(stage, dst)
            # スライドが参照する mermaid.min.js は 議事録/ 直下に1部だけ置く（会議ごとに複製しない）
            mm_src, mm_dst = os.path.join(SCRIPT_DIR, "mermaid.min.js"), os.path.join(base, "mermaid.min.js")
            if has_slides and os.path.isfile(mm_src) and not os.path.isfile(mm_dst):
                _sync_files(mm_src, mm_dst)
            lg_src, lg_dst = os.path.join(SCRIPT_DIR, "brand-logo.png"), os.path.join(base, "brand-logo.png")
            if os.path.isfile(lg_src) and not os.path.isfile(lg_dst):
                _sync_files(lg_src, lg_dst)
            bg_src, bg_dst = os.path.join(SCRIPT_DIR, "slide-bg.jpg"), os.path.join(base, "slide-bg.jpg")
            if os.path.isfile(bg_src) and not os.path.isfile(bg_dst):
                _sync_files(bg_src, bg_dst)

            shutil.rmtree(stage, ignore_errors=True)
            sys.stderr.write("[SYNC] %s → 背景フォルダ %s %s\n" % (sid, dst, "OK" if ok else "失敗"))
        except Exception as e:
            sys.stderr.write("[SYNC] %s → 背景フォルダ 失敗 %r\n" % (sid, e))
        sys.stderr.flush()
    threading.Thread(target=_run_copy, daemon=True).start()

def _strategy_export_dir(sid, meta=None):
    meta = meta or read_meta(sid)
    pdir = (meta.get("project_dir") or "").strip()
    if not pdir:
        return ""
    return os.path.join(pdir, "会議準備", "%s %s" % (sid, _safe_name(meta.get("title", ""))))

def _render_strategy_md(meta, st):
    """作戦チャットの最新状態を、選択フォルダで読みやすい会議準備Markdownにする。"""
    b = st.get("board") if isinstance(st.get("board"), dict) else {}
    L = ["# %s｜会議事前準備" % meta.get("title", "会議"), "",
         "- 会議ID：%s" % meta.get("id", ""), "- 作成・更新：%s" % st.get("updated", "")]
    if meta.get("goal"): L.append("- 設定目標：" + meta["goal"])
    if meta.get("stance"): L.append("- 自分の立場：" + meta["stance"])
    L += ["", "## 準備ボード", ""]
    if b.get("outcome"): L += ["### 今回の着地点", str(b["outcome"]), ""]
    if b.get("counterpart"): L += ["### 相手の状況", str(b["counterpart"]), ""]
    for key, title in (("hypotheses", "仮説"), ("questions", "会議で聞くこと"),
                       ("risks", "懸念・見落とし"), ("avoid", "避けること")):
        vals = b.get(key) if isinstance(b.get(key), list) else []
        if vals: L += ["### " + title] + ["- " + str(x) for x in vals if str(x).strip()] + [""]
    sources = b.get("sources") if isinstance(b.get("sources"), list) else []
    if sources:
        L += ["### 参照した資料"]
        for x in sources:
            if isinstance(x, dict) and x.get("path"):
                L.append("- `%s`：%s" % (x.get("path"), x.get("use", "")))
        L.append("")
    if st.get("brief"): L += ["## ライブ参謀へ渡す作戦ブリーフ", str(st["brief"]), ""]
    msgs = st.get("messages") if isinstance(st.get("messages"), list) else []
    if msgs:
        L += ["## 壁打ちログ", ""]
        for x in msgs:
            if not isinstance(x, dict): continue
            L += ["### " + ("自分" if x.get("role") == "user" else "AI参謀"), str(x.get("text", "")), ""]
    L += ["---", "＊live-mtg の事前準備室から自動更新。"]
    return "\n".join(L) + "\n"

def sync_strategy_to_project(sid, st=None, stale_dir=""):
    """各チャット後に事前準備.mdを選択中の背景フォルダへ非同期で更新。"""
    if sid in deleted_sessions: return
    meta = read_meta(sid); dst = _strategy_export_dir(sid, meta)
    if not dst or not os.path.isdir((meta.get("project_dir") or "").strip()): return
    st = st or _load_strategy(sid)
    if not st: return
    content = _render_strategy_md(meta, st)
    def _run():
        stage = tempfile.mkdtemp(prefix="mtg-prep-")
        try:
            if sid in deleted_sessions: return
            with open(os.path.join(stage, "事前準備.md"), "w", encoding="utf-8") as f: f.write(content)
            os.makedirs(dst, exist_ok=True)
            ok = _sync_files(stage, dst)
            # 明示された相手名で会議名が変わった場合、同期成功後に旧題名の準備フォルダだけを除去。
            # 同じ会議IDで始まるものに限定し、別会議・別案件は触らない。
            if ok and stale_dir and os.path.realpath(stale_dir) != os.path.realpath(dst):
                old_name = os.path.basename(stale_dir)
                if (old_name == sid or old_name.startswith(sid + " ")) and os.path.isdir(stale_dir):
                    shutil.rmtree(stale_dir, ignore_errors=True)
            sys.stderr.write("[PREP-SYNC] %s → %s %s\n" % (sid, dst, "OK" if ok else "失敗"))
        except Exception as e:
            sys.stderr.write("[PREP-SYNC] %s 失敗 %r\n" % (sid, e))
        finally:
            shutil.rmtree(stage, ignore_errors=True); sys.stderr.flush()
    threading.Thread(target=_run, daemon=True).start()

_drive_woke = [0.0]
def _read_text(path, timeout=20):
    """ファイルを timeout 付きで読む（Google Drive の FileProvider がオフライン時、
    通常の open().read() は無限ハングするため cat を timeout 付き subprocess で叩く）。
    読めなければ '' を返し、Google Drive を起こす（次回に備える）。"""
    if not os.path.isfile(path):
        return ""
    if os.name == "nt":
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception:
            return ""
    try:
        r = subprocess.run(["cat", path], capture_output=True, text=True, timeout=timeout)
        if r.returncode == 0:
            return r.stdout
    except subprocess.TimeoutExpired:
        pass
    except Exception:
        return ""
    # ここに来た＝読めなかった（Driveオフラインの疑い）。Driveを起こす（頻繁に呼ばない）
    if sys.platform == "darwin":
        try:
            subprocess.run(["open", "-ga", "Google Drive"], timeout=5)
        except Exception:
            pass
    return ""

# 録音はブラウザ(マイク＋会議タブ音声)で行い、音声チャンクを /api/chunk で受け取り
# サーバ側で ffmpeg(decode)→whisper(文字起こし)→claude(整理) する。
# whisperが無音・雑音時に吐きやすい定型ハルシネーション句（単独行なら捨てる）
# whisperはYouTube字幕で学習しているため、無音・雑音区間で字幕定番句を発話として吐く。
# 行全体がその定型に一致する時だけ捨てる（「音楽が好き」「チャンネル登録数を分析」等の実発話は残す）。
_HALLU_CTA = r'(?:を?お願いします?|して(?:ね|ください)?|よろしく(?:お願いします?)?|お願いいたします)'
HALLU = re.compile(r'^[\s]*('
                   r'おやすみなさい|ご視聴ありがとうございました|ご清聴ありがとうございました|'
                   r'最後までご覧いただきありがとうございました|'
                   r'(?:高評価と)?チャンネル登録(?:と高評価)?' + _HALLU_CTA + r'?|高評価' + _HALLU_CTA + r'|バイバイ|'
                   r'(?:私は)?この動画を見てみましょう|次(?:回|の動画|回の動画)でお会いしましょう|'
                   r'ありがとうございました|thanks for watching|thank you for watching|subscribe to the channel'
                   r')[\s、。.!！]*$', re.I)
# [音楽]（拍手）♪ 等のマーカー行。行全体がマーカーの時だけ捨てる（実発話「音楽が好き」は残る）
MARKER = re.compile(r'^[\s]*[\[\(（【]?\s*(音楽|拍手|笑|BGM|効果音|チャイム|ベル|ざわざわ|沈黙|無音)\s*[\]\)）】]?[\s、。.!！♪〜～ー]*$')
MUSIC = re.compile(r'^[\s♪♬〜～\-—ー・。、]*[♪♬][\s♪♬〜～\-—ー・。、]*$')  # ♪記号を含む記号だけの行
# 聞き取り不能時にwhisperが吐く無意味な擬音・短断片（単独行なら捨てる。例:「ブーブー」「ブーバイブー」）
NOISE = re.compile(r'^[\s、。.!！]*((ブ[ーぶ]*)+|(ブー*バ?イ?)+|んー*|あー*|えー*|うー*|[ぁ-んゝ]{1,2})[\s、。.!！]*$')
# ライブ関係図が保持する関係ペア数。会話の関係は「積み上げ」なので序盤を捨てない。
# 実会議はまず届かない大きさにし、暴走防止の安全上限としてのみ機能させる（2026-07-20 依頼者要望）。
LIVE_RELATIONS_MAX = 60
# 時系列に保持する発話数。旧60（≒15秒チャンクで15分）だと長い会議の序盤が消えていた。
# 時系列も積み上げが本質なので実質無制限にし、安全上限だけ残す（2026-07-20 依頼者要望）。
TIMELINE_MAX = 2000
# 時系列の過去エントリ掃除（幻聴・ヒント漏れ）を実行済みかを示す浄化ルールの版。
# 掃除は「版が変わった時に一度だけ」：毎チャンク全件_cleanするとエントリ毎のメタ読取＋
# LCSで長い会議ほど重くなる（2026-07-20 レビューで発覚）。ルールを変えたら+1する。
TL_CLEAN_VER = 2

EMPTY_DATA = json.dumps({
    "updated": _t("待機中", "Waiting"),
    "summary": _t("ヘッダーの「録音開始」を押すと整理が始まります。", "Press Start recording in the header to begin."),
    "agenda": [], "points": [], "decisions": [], "todos": [], "open": []
}, ensure_ascii=False)

os.makedirs(SESS, exist_ok=True)

lock       = threading.Lock()
current_id = None          # 現在表示中の会議ID
recording  = False         # ブラウザが録音送信中か（表示用フラグ）
capture_heartbeat = 0.0    # ブラウザのMediaRecorderが実際に生きている最終時刻
deleted_sessions = set()   # 削除中/削除済みの会議を非同期処理が復活させない
chunk_q    = queue.Queue() # (session_id, webm_path) を順に処理するキュー
applied    = {}            # session_id -> これまでにclaude整理へ反映済みのtranscript文字数（差分更新用）
analysis_q = queue.Queue() # 音声キューとは独立して議事JSONを更新（長時間発話でも解析を止めない）
analysis_pending = set()   # 同じ会議の解析要求は1件に集約
analysis_lock = threading.Lock()
analysis_failures = {}     # session_id -> 連続失敗回数（無限再試行を防ぐ）
view_q = queue.Queue()      # 表示中のリスト/マップだけを背景更新
view_pending = set()
view_applied = {}           # (session_id, canonical_view) -> transcript文字数
view_last_run = {}
view_lock = threading.Lock()
view_clients = {}           # browser client_id -> {sid, view, visible, updated}
view_clients_lock = threading.Lock()
detail_q = queue.Queue()   # マインドマップ・関係整理・調査判断は即時解析と別レーン
detail_pending = set()
detail_applied = {}        # session_id -> 詳細解析で確認済みのtranscript文字数
detail_lock = threading.Lock()
detail_deferred = set()    # 録音中は即時解析を優先し、詳細整理は停止後に再開
live_notes_lock = threading.Lock()
detail_failures = {}
data_write_lock = threading.Lock()       # AIは並列、data.jsonの統合だけ直列
background_ai_lock = threading.Lock()    # 即時AI＋背景AIの最大2呼び出しに制限
background_process_lock = threading.Lock()
background_processes = set()
live_diarization_q = queue.Queue()
live_diarization_pending = set()
live_diarization_dirty = set()
live_diarization_lock = threading.Lock()
live_diarization_last = {}
audio_duration_cache = {}
live_diarizer_process = None
live_diarizer_io_lock = threading.Lock()
long_job_lock = threading.Lock()
long_jobs = {}                           # (sid, kind) -> {process, cancelled}
long_job_local = threading.local()

class JobCancelled(Exception): pass
class JobBusy(Exception): pass

def _register_background_process(process):
    with background_process_lock: background_processes.add(process)

def _unregister_background_process(process):
    with background_process_lock: background_processes.discard(process)

def _cancel_background_ai():
    """録音開始時、背景探索・詳細整理を止めてライブ解析へ資源を譲る。"""
    with background_process_lock: processes = list(background_processes)
    for process in processes:
        if process and process.poll() is None: _kill_process_tree(process)
    return len(processes)

class long_job_scope:
    def __init__(self, sid, kind): self.key = (sid, kind)
    def __enter__(self):
        with long_job_lock:
            if self.key in long_jobs: raise JobBusy(self.key[1])
            long_jobs[self.key] = {"process": None, "cancelled": False}
        long_job_local.key = self.key
        return self
    def __exit__(self, *_):
        with long_job_lock: long_jobs.pop(self.key, None)
        if getattr(long_job_local, "key", None) == self.key: long_job_local.key = None

def _register_long_process(p):
    key = getattr(long_job_local, "key", None)
    if not key: return
    with long_job_lock:
        job = long_jobs.get(key)
        if not job or job.get("cancelled"):
            _kill_process_tree(p); raise JobCancelled(key[1])
        job["process"] = p

def _check_long_cancelled():
    key = getattr(long_job_local, "key", None)
    if not key: return
    with long_job_lock:
        if long_jobs.get(key, {}).get("cancelled"): raise JobCancelled(key[1])

def cancel_long_job(sid, kind):
    key = (sid, str(kind or ""))
    with long_job_lock:
        job = long_jobs.get(key)
        if not job: return False
        job["cancelled"] = True
        p = job.get("process")
    if p: _kill_process_tree(p)
    return True

# ---------- セッション管理 ----------
def sdir(sid):            return os.path.join(SESS, sid)
def is_session(sid):      return bool(sid) and os.path.isfile(os.path.join(sdir(sid), "meta.json"))

def read_meta(sid):
    try:
        with open(os.path.join(sdir(sid), "meta.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"id": sid, "title": sid, "created": "", "updated": ""}

def write_meta(sid, meta):
    with open(os.path.join(sdir(sid), "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

def list_sessions():
    out = []
    for name in os.listdir(SESS):
        if is_session(name):
            m = read_meta(name)
            out.append({
                "id": name,
                "title": m.get("title", name),
                "created": m.get("created", ""),
                "hasSlides": os.path.isfile(os.path.join(sdir(name), "mindmap.html")),
            })
    # created（=id先頭のタイムスタンプ）降順。新しい会議が上。
    out.sort(key=lambda x: x["id"], reverse=True)
    return out

def new_session(title, project_dir="", goal="", mtype="", stance="", language=None):
    sid = time.strftime("%Y%m%d-%H%M%S")
    d = sdir(sid)
    deleted_sessions.discard(sid)
    os.makedirs(d, exist_ok=True)   # 一時wavはドライブに置かない（WAVROOT側で管理）
    now = time.strftime("%Y-%m-%d %H:%M")
    language = str(language or LANGUAGE).lower()
    if language not in ("ja", "en"): language = LANGUAGE
    title = (title or "").strip() or ((_t("会議 ", "Meeting ")) + now)
    project_dir = (project_dir or "").strip()
    if project_dir and not os.path.isdir(project_dir):
        project_dir = ""
    write_meta(sid, {"id": sid, "title": title, "created": now, "updated": now,
                     "project_dir": project_dir, "goal": (goal or "").strip(),
                     "mtype": (mtype or "").strip(), "stance": (stance or "").strip(),
                     "language": language})
    with open(os.path.join(d, "transcript.txt"), "w", encoding="utf-8") as f:
        f.write("")
    with open(os.path.join(d, "data.json"), "w", encoding="utf-8") as f:
        f.write(EMPTY_DATA)
    if project_dir:
        _remember_project(project_dir)
        explore_project(sid)   # 背景フォルダの探索を非同期で開始
    return sid

def delete_session(sid):
    """会議本体・Drive同期コピー・案件フォルダの清書コピーを、会議ID限定で削除する。"""
    if not is_session(sid):
        return False, "会議が見つかりません"
    meta = read_meta(sid)
    deleted_sessions.add(sid)
    removed = []

    def remove_tree(path, root):
        try:
            rp, rr = os.path.realpath(path), os.path.realpath(root)
            if os.path.commonpath([rp, rr]) != rr or rp == rr:
                return
            if os.path.isdir(path):
                shutil.rmtree(path)
                removed.append(path)
        except Exception as e:
            sys.stderr.write("[DELETE] %s 削除失敗 %s: %r\n" % (sid, path, e))

    # 案件フォルダの書き出しは「<sid> <会議名>」。改名履歴も考慮しID前方一致を全て消す。
    pdir = (meta.get("project_dir") or "").strip()
    if pdir:
        for dirname in ("議事録", "会議準備"):
            base = os.path.join(pdir, dirname)
            for path in glob.glob(os.path.join(base, sid + "*")):
                if os.path.basename(path) == sid or os.path.basename(path).startswith(sid + " "):
                    remove_tree(path, base)
    remove_tree(os.path.join(DRIVE_DIR, sid), DRIVE_DIR)
    remove_tree(os.path.join(WAVROOT, sid), WAVROOT)
    remove_tree(sdir(sid), SESS)  # 最後にローカル本体
    for cache in (applied, detail_applied, exploring, researching):
        cache.pop(sid, None)
    with detail_lock:
        detail_pending.discard(sid)
    if hasattr(queue_lookups, "_seen"):
        queue_lookups._seen.pop(sid, None)
    sys.stderr.write("[DELETE] %s 会議データ削除 %d箇所\n" % (sid, len(removed))); sys.stderr.flush()
    return True, removed

# ---------- 用途別プレイブック（ノウハウの蓄積・参照）----------
def _playbook_path(mtype):
    mt = re.sub(r'[/\\:*?"<>|]', "", (mtype or "").strip())
    return os.path.join(PLAYBOOK_DIR, mt + ".md") if mt else ""

_pb_cache = {}
def _playbook_text(mtype):
    """用途のプレイブックを読む（60秒キャッシュ・Driveハング保護付き・長すぎたら先頭＋末尾を採用）"""
    p = _playbook_path(mtype)
    if not p or not os.path.isfile(p):
        return ""
    now = time.time()
    hit = _pb_cache.get(p)
    if hit and now - hit[0] < 60:
        return hit[1]
    txt = _read_text(p)
    if len(txt) > 3500:
        txt = txt[:1200] + "\n…（中略）…\n" + txt[-2300:]   # 冒頭の方針＋末尾の新しい学びを残す
    _pb_cache[p] = (now, txt)
    return txt

_prof_cache = [0.0, ""]
def _profile_text():
    """依頼主プロフィール（profile.md）を読む（60秒キャッシュ・Driveハング保護。保存時にキャッシュ破棄）"""
    now = time.time()
    if now - _prof_cache[0] < 60:
        return _prof_cache[1]
    txt = (_read_text(PROFILE_MD) or "").strip() if os.path.isfile(PROFILE_MD) else ""
    _prof_cache[0], _prof_cache[1] = now, txt[:1200]
    return _prof_cache[1]

def append_playbook(mtype, title, text):
    """学びをプレイブックに追記（依頼者が承認した内容のみ呼ばれる）"""
    p = _playbook_path(mtype)
    if not p or not (text or "").strip():
        return False
    try:
        os.makedirs(PLAYBOOK_DIR, exist_ok=True)
        new = not os.path.isfile(p)
        with open(p, "a", encoding="utf-8") as f:
            if new:
                f.write("# %s プレイブック\n\n会議のたびにAIが抽出した学びを承認制で蓄積。手での編集・追記も歓迎。\n" % mtype)
            f.write("\n## %s 「%s」からの学び\n%s\n" % (time.strftime("%Y-%m-%d"), title, text.strip()))
        _pb_cache.pop(p, None)
        return True
    except Exception as e:
        sys.stderr.write("[PLAYBOOK] 追記失敗 %r\n" % e)
        return False

# 最近使ったプロジェクトフォルダ（新規会議モーダルの候補に出す）
PROJ_FILE = os.path.join(RUN, "projects.txt")
def _recent_projects():
    try:
        with open(PROJ_FILE, encoding="utf-8") as f:
            return [l.strip() for l in f if l.strip()]
    except Exception:
        return []

def _remember_project(p):
    ps = [x for x in _recent_projects() if x != p]
    ps.insert(0, p)
    try:
        os.makedirs(RUN, exist_ok=True)
        with open(PROJ_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(ps[:12]) + "\n")
    except Exception:
        pass

def load_state():
    try:
        with open(os.path.join(RUN, "state.json"), encoding="utf-8") as f:
            return json.load(f).get("current")
    except Exception:
        return None

def save_state():
    try:
        with open(os.path.join(RUN, "state.json"), "w", encoding="utf-8") as f:
            json.dump({"current": current_id}, f)
    except Exception:
        pass

# ---------- 背景フォルダの探索（Claude Code式：実際にファイルを読んで把握する）----------
# 第1層＝会議開始時にフォルダを探索してダイジェスト＋ファイルマップを作る（context.json）
# 第3層＝会議中、ライブAIが「資料が要る」と判断した項目を自動で調べる（research.json）
# どちらも headless claude（pty＋bypassPermissions＋ファイル出力＝実証済みの叩き方）で、ライブループは止めない。
exploring  = {}                 # sid -> True（探索中の表示用）
explore_deferred = set()
lookup_q   = queue.Queue()      # (sid, need, why, immediate) 深掘りジョブ
deferred_lookups = {}           # 録音終了後に再開する自動調査
deferred_lookup_lock = threading.Lock()
researching = {}                # sid -> 実行中ジョブ数

def _first_json(text):
    """前後にCLI表示が混ざっても、最初の完全なJSON値だけを取り出す。"""
    dec = json.JSONDecoder()
    # Claudeのtrust警告には projects["/path"] が含まれる。左から [ を拾うと
    # そのパスだけをJSON配列と誤認するため、API応答であるオブジェクトを先に探す。
    for opener in ("{", "["):
        for i, ch in enumerate(text or ""):
            if ch != opener: continue
            try:
                return dec.raw_decode(text[i:])[0]
            except json.JSONDecodeError:
                continue
    raise json.JSONDecodeError("JSON value not found", text or "", 0)

def _strategy_object(value):
    """Claude CLIのラッパーや配列が重なっても、作戦会議の本体を再帰的に見つける。"""
    if isinstance(value, dict):
        if all(k in value for k in ("reply", "brief", "board")):
            return value
        # CLIのresultがJSON文字列で返る場合もある。
        for key in ("structured_output", "result", "content", "data"):
            child = value.get(key)
            if isinstance(child, str):
                try: child = _first_json(child)
                except json.JSONDecodeError: continue
            found = _strategy_object(child)
            if found: return found
        for child in value.values():
            found = _strategy_object(child)
            if found: return found
    elif isinstance(value, list):
        for child in value:
            found = _strategy_object(child)
            if found: return found
    return None

def _claude_explore(project_dir, prompt, timeout=240, json_schema=None,
                    tools="Read,Glob,Grep", max_turns=None, model=None):
    """プロジェクトフォルダをcwdにし、選択中AIを読取専用でheadless実行。"""
    if AI_PROVIDER == "codex":
        try:
            return _ai_text(prompt, timeout=timeout, cwd=project_dir,
                            web=("WebSearch" in (tools or "")), schema=json_schema)
        except Exception as e:
            sys.stderr.write("[CODEX] 探索失敗 %r\n" % e); sys.stderr.flush()
            return ""
    prompt = _localized_prompt(prompt)
    cmd = ([_cli("claude"), "-p", prompt] if os.name == "nt"
           else ["script", "-q", "/dev/null", "claude", "-p", prompt])
    cmd += ["--model", model or ASSIST_MODEL, "--tools", tools,
           "--permission-mode", "bypassPermissions",
           "--output-format", "json" if json_schema else "text"]
    if json_schema:
        cmd += ["--json-schema", json.dumps(json_schema, ensure_ascii=False)]
    if max_turns:
        cmd += ["--max-turns", str(max_turns)]
    tf = tempfile.NamedTemporaryFile("w", suffix=".out", delete=False, dir=tempfile.gettempdir())
    tmp = tf.name; tf.close()
    try:
        with open(tmp, "w") as fout:
            # start_new_session=True でプロセスグループを分離し、タイムアウト時は killpg で
            # script→claude→検索コマンドの孫まで確実に止める。subprocess.run(timeout=) は
            # 直接の子(script)しか殺さず、孤児化した grep がドライブ全域を2時間暴走した実障害
            # （2026-07-13）の再発防止。
            p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=fout, stderr=subprocess.STDOUT,
                                 cwd=project_dir, env=_claude_env(), start_new_session=True)
            _register_background_process(p)
            try:
                p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                _kill_process_tree(p)
                p.wait(timeout=10)
            finally:
                _unregister_background_process(p)
        with open(tmp, encoding="utf-8", errors="ignore") as fin:
            raw = fin.read()
    except Exception as e:
        return ""
    finally:
        try: os.remove(tmp)
        except Exception: pass
    raw = re.sub(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)', '', raw)
    raw = re.sub(r'\x1b[\[\(][0-9;?<>=]*[a-zA-Z]', '', raw)
    raw = re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', raw)
    raw = re.sub(r'^\s*\^D', '', raw)
    raw = raw.strip()
    if json_schema and raw:
        try:
            wrapper = _first_json(raw)
            if isinstance(wrapper, dict):
                structured = wrapper.get("structured_output")
                if isinstance(structured, (dict, list)):
                    return json.dumps(structured, ensure_ascii=False)
                if isinstance(wrapper.get("result"), str):
                    return wrapper["result"].strip()
        except Exception:
            pass
    return raw

EXPLORE_PROMPT = """あなたは会議アシスタントの下調べ係です。このフォルダはこれから行う会議「{title}」の背景資料です。
【会議の目標・対象】{goal}
フォルダを探索して（一覧を見て、重要そうな .md/.txt 等を実際に読んで）、次の**有効なJSONのみ**を出力してください。
前置き・説明・コードフェンス禁止。JSONだけ。
{{
  "project": "このプロジェクト/案件が何かの一言",
  "digest": "会議の参謀が知っておくべき背景の要点（800字以内。事実のみ・出典ファイル名を括弧で添える）",
  "filemap": [{{"path": "相対パス", "what": "何が書いてあるか一言"}}]
}}
ルール: filemapは重要な順に最大15件。読んでいないファイルの中身を推測で書かない。数字・固有名詞は正確に。目標に書かれた会社名・人物名は正とし、名前の似た別会社に置き換えない。該当資料が無い場合は「見当たらない」とする。
【厳守】探索はこのフォルダ（カレントディレクトリ）の中だけ。親フォルダ・共有ドライブ全体・他プロジェクトへの cd や検索（grep -r / find 等）は絶対にしない（Google Driveの全域検索は全ファイルのダウンロードを誘発しPCを止める）。"""

def explore_project(sid):
    """背景フォルダを非同期で探索して context.json を作る（第1層）。"""
    m = read_meta(sid)
    pd = m.get("project_dir", "")
    if not pd or not os.path.isdir(pd):
        return
    def _job():
        exploring[sid] = True
        out = ""
        try:
            out = _claude_explore(pd, EXPLORE_PROMPT.format(title=m.get("title", "会議"),
                                                            goal=m.get("goal", "") or "（未設定）"))
            mm = re.search(r"\{.*\}", out, re.S)
            if mm:
                try:
                    obj = json.loads(mm.group(0))
                    with open(os.path.join(sdir(sid), "context.json"), "w", encoding="utf-8") as f:
                        json.dump(obj, f, ensure_ascii=False, indent=2)
                    sys.stderr.write("[EXPLORE] %s 完了 digest=%d字 filemap=%d件\n"
                                     % (sid, len(obj.get("digest", "")), len(obj.get("filemap", []))))
                except Exception as e:
                    sys.stderr.write("[EXPLORE] %s JSON失敗 %r\n" % (sid, e))
            else:
                sys.stderr.write("[EXPLORE] %s 出力なし\n" % sid)
        finally:
            if recording and sid == current_id and not out:
                explore_deferred.add(sid)
            exploring.pop(sid, None)
            sys.stderr.flush()
    threading.Thread(target=_job, daemon=True).start()

LOOKUP_PROMPT = """あなたは会議アシスタントの下調べ係です。いま進行中の会議「{title}」で、次の情報が必要になりました。
【依頼主（録音している話し手本人）】
{profile}
【調べたいこと】{need}
【なぜ必要か】{why}
【フォルダの地図（参考）】
{filemap}
このフォルダ内の関連ファイルを実際に読み、会議中に3秒で読める形式で答えてください。
形式は「結論：40文字以内」＋「要点：最大2件（各50文字以内）」＋「出典：ファイル名のみ」。背景説明・前置き・同じ内容の言い換えは禁止。全体180文字以内。
見つからなければ「資料内に見当たらない」と正直に書く。推測で埋めない。回答本文のみ出力。
【厳守】調査はこのフォルダ（カレントディレクトリ）の中だけ。見つからなくても親フォルダ・共有ドライブ全体へ検索を広げない（cd・grep -r・find での外出は絶対禁止。Driveの全域検索はPCを止める）。"""

def _needs_web_fallback(answer):
    """資料調査で回答が得られなかったかを判定。フォルダ内で見つかった場合はWebへ出ない。"""
    a = (answer or "").strip()
    if not a or a == "（調査失敗）":
        return True
    markers = ("資料内に見当たらない", "資料内には見当たらない",
               "フォルダ内に見当たらない", "特定できない", "確認できない")
    return any(x in a for x in markers)

IMPORT_NOTE_EXT = (".md", ".markdown", ".txt", ".text", ".json", ".yaml", ".yml", ".csv")

def _read_import_notes(path, cap=12000):
    """事前メモの取り込み：ファイルならそのまま、フォルダなら直下のテキストを新しい順に最大5件読む。
    ユーザーがダイアログで明示指定したパスのみ読む（背景フォルダ封鎖の正規の搬入口）。サブフォルダへは降りない。"""
    real = os.path.realpath(os.path.expanduser(path or ""))
    texts, used = [], []
    def _read_one(fp):
        try:
            if os.path.getsize(fp) > 2_000_000:
                return ""
            with open(fp, encoding="utf-8", errors="ignore") as f:
                return f.read(cap).strip()
        except Exception:
            return ""
    if os.path.isfile(real):
        t = _read_one(real)
        if t:
            texts.append(t); used.append(os.path.basename(real))
    elif os.path.isdir(real):
        try:
            files = [os.path.join(real, n) for n in os.listdir(real)
                     if not n.startswith(".") and os.path.splitext(n)[1].lower() in IMPORT_NOTE_EXT
                     and os.path.isfile(os.path.join(real, n))]
        except Exception:
            files = []
        files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
        for fp in files[:5]:
            t = _read_one(fp)
            if t:
                texts.append("### %s\n%s" % (os.path.basename(fp), t))
                used.append(os.path.basename(fp))
            if sum(len(x) for x in texts) >= cap:
                break
    return "\n\n".join(texts)[:cap], "、".join(used)

def _research_path(sid):
    return os.path.join(sdir(sid), "research.json")

def _strategy_path(sid):
    return os.path.join(sdir(sid), "strategy.json")

def _live_notes_path(sid):
    return os.path.join(sdir(sid), "live-notes.json")

def _load_live_notes(sid):
    try:
        with open(_live_notes_path(sid), encoding="utf-8") as f:
            value = json.load(f)
            return value if isinstance(value, list) else []
    except Exception:
        return []

def _append_context_note(sid, text, kind="live"):
    """明示的な背景情報を追加。事前打ち合わせは本会議の発言と混ぜない。"""
    with live_notes_lock:
        notes = _load_live_notes(sid)
        note = {"text": text[:2000], "ts": time.strftime("%H:%M"), "kind": kind}
        notes.append(note)
        notes = notes[-30:]
        with open(_live_notes_path(sid), "w", encoding="utf-8") as f:
            json.dump(notes, f, ensure_ascii=False, indent=2)
    return notes

def _explicit_participants(text):
    """「参加者はAとBの2名のみ」の明示訂正だけを安全に抽出する。"""
    sentence = next((x.strip() for x in re.split(r"[。\n]", text) if "参加者は" in x), "")
    if not sentence or not re.search(r"(?:のみ|[0-9０-９一二三四五六七八九十]+名)", sentence):
        return []
    body = sentence.split("参加者は", 1)[1]
    body = re.sub(r"(?:の)?[0-9０-９一二三四五六七八九十]+名(?:のみ)?(?:です)?$", "", body)
    body = re.sub(r"(?:のみ)?です$|のみ$", "", body)
    names = [x.strip(" ・、,") for x in re.split(r"\s*(?:と|、|,)\s*", body) if x.strip(" ・、,")]
    return names if 1 <= len(names) <= 12 and all(len(x) <= 40 for x in names) else []

def _explicit_rejected_speakers(text):
    """「A・Bは文字起こし由来の誤認名」のA/Bだけを抽出する。"""
    sentence = next((x.strip() for x in re.split(r"[。\n]", text) if "誤認名" in x), "")
    if not sentence or "は" not in sentence:
        return []
    left = sentence.split("は", 1)[0]
    names = [x.strip(" ・、,") for x in re.split(r"\s*(?:・|と|、|,)\s*", left) if x.strip(" ・、,")]
    return names if 1 <= len(names) <= 12 and all(len(x) <= 40 for x in names) else []

RETRO_PROMPT = """依頼主の訂正メモから、既存の議事テキストへ機械的に適用できる「置換ペア」を抽出してください。
JSONのみを返す：{{"replacements":[{{"from":"誤った表記","to":"正しい表記"}}]}}
- 確実な表記の訂正（人名・社名・製品名・用語・数値）だけを対象にする。
- 文意の変更・追加情報・曖昧な指示は含めない（その場合は空配列）。
- fromは議事に実際に現れる最小の固有表現。一般語（会議、担当、次回等）をfromにしない。
訂正メモ：{note}"""

def _retro_replace(value, pairs):
    if isinstance(value, str):
        for p_ in pairs:
            value = value.replace(p_["from"], p_["to"])
        return value
    if isinstance(value, list):
        return [_retro_replace(x, pairs) for x in value]
    if isinstance(value, dict):
        return {k: (v if k.startswith("_") else _retro_replace(v, pairs)) for k, v in value.items()}
    return value

def _retro_apply(sid, note_text):
    """訂正を過去の議事にも遡って効かせる（2026-07-17 改修）。
    「次の解析から反映」だけだと、序盤の誤記が清書まで画面に残り続ける。
    AIには置換ペアの抽出だけをさせ、適用は決定論の文字列置換（文意を壊さない）。"""
    try:
        out = _ai_text(RETRO_PROMPT.format(note=note_text[:500]), timeout=30, model=CLAUDE_MODEL, background=True)
        m = re.search(r"\{.*\}", out or "", re.S)
        pairs = json.loads(m.group(0)).get("replacements") if m else []
        pairs = [p_ for p_ in (pairs or [])
                 if isinstance(p_, dict) and len(str(p_.get("from") or "")) >= 2
                 and str(p_.get("from") or "").strip() and str(p_.get("to") or "").strip()
                 and p_["from"] != p_["to"]]
        if not pairs:
            return
        # 「田中さん→中田さん」なら「田中→中田」も適用（敬称なしの出現を取りこぼさない）
        extra = []
        for p_ in pairs:
            for hon in ("さん", "様", "氏"):
                f, t = str(p_["from"]), str(p_["to"])
                if f.endswith(hon) and t.endswith(hon) and len(f) > len(hon) and len(t) > len(hon):
                    bare = {"from": f[:-len(hon)], "to": t[:-len(hon)]}
                    if len(bare["from"]) >= 2 and bare not in pairs and bare not in extra:
                        extra.append(bare)
        pairs = pairs + extra
        with data_write_lock:
            obj = _read_live_data(sid)
            fixed = _retro_replace(obj, [{"from": str(p_["from"]), "to": str(p_["to"])} for p_ in pairs])
            tmp = os.path.join(sdir(sid), "data.json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(fixed, f, ensure_ascii=False, indent=2)
            os.replace(tmp, os.path.join(sdir(sid), "data.json"))
        sys.stderr.write("[RETRO] %s 遡及置換 %s\n" % (sid, ", ".join("%s→%s" % (p_["from"], p_["to"]) for p_ in pairs)))
        sys.stderr.flush()
    except Exception as e:
        sys.stderr.write("[RETRO] %s 失敗 %r\n" % (sid, e)); sys.stderr.flush()

def add_live_note(sid, text):
    """会議中の補足・訂正を最優先の明示情報として保存し、次の解析へ即時投入。"""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return False, _t("内容が空です", "The note is empty")
    notes = _append_context_note(sid, text, "live")
    threading.Thread(target=_retro_apply, args=(sid, text), daemon=True).start()
    confirmed = _explicit_participants(text)
    if confirmed:
        # AIの解析完了を待たず、その場で正しい参加者と関連表示を確定する。
        with data_write_lock:
            obj = _read_live_data(sid)
            obj["_confirmedSpeakers"] = confirmed
            rejected = sorted({name for note in notes for name in _explicit_rejected_speakers(str(note.get("text") or ""))})
            if rejected:
                obj["_rejectedSpeakers"] = rejected
            obj = _enforce_confirmed_speakers(obj, confirmed, rejected)
            tmp = os.path.join(sdir(sid), "data.json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=2)
            os.replace(tmp, os.path.join(sdir(sid), "data.json"))
    # 発話と混同しないラベルを付ける。差分解析はこれを新情報として読み、既存の誤認を訂正する。
    with open(os.path.join(sdir(sid), "transcript.txt"), "a", encoding="utf-8") as f:
        f.write("【依頼者のライブ補足・訂正（文字起こしより優先）】" + text[:2000] + "\n")
    if re.search(r"https?://", text):
        queue_lookups(sid, [{"need": text[:500], "why": "依頼者が会議中に追加したURL・背景情報の確認"}], immediate=True)
    request_analysis(sid)
    request_detail(sid)
    return True, notes

def _load_strategy(sid):
    try:
        with open(_strategy_path(sid), encoding="utf-8") as f:
            obj = json.load(f)
            return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}

def _save_strategy(sid, obj):
    with open(_strategy_path(sid), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def apply_strategy_to_data(sid, st=None):
    """事前準備を保存した時点で、ライブ画面の初期データと質問バブルへ反映。"""
    st = st or _load_strategy(sid)
    brief = str(st.get("brief", "")).strip()
    board = st.get("board") if isinstance(st.get("board"), dict) else {}
    if not brief and not board:
        return
    p = os.path.join(sdir(sid), "data.json")
    try:
        with open(p, encoding="utf-8") as f: data = json.load(f)
    except Exception:
        data = json.loads(EMPTY_DATA)
    prep = {"brief": brief, "outcome": str(board.get("outcome", "")).strip(),
            "counterpart": str(board.get("counterpart", "")).strip(),
            "hypotheses": board.get("hypotheses") or [], "questions": board.get("questions") or [],
            "risks": board.get("risks") or [], "avoid": board.get("avoid") or [],
            "updated": st.get("updated") or time.strftime("%H:%M:%S")}
    data["preparation"] = prep
    qs = [str(x).strip() for x in prep["questions"] if str(x).strip()]
    if qs:
        guide = data.get("guide") if isinstance(data.get("guide"), dict) else {}
        existing = guide.get("questions") if isinstance(guide.get("questions"), list) else []
        seen = {str(x.get("q", "")).strip() for x in existing if isinstance(x, dict)}
        guide["questions"] = ([{"q": q, "intent": "事前準備で整理した確認事項"} for q in qs if q not in seen] + existing)[:6]
        guide.setdefault("progress", "事前準備を反映済み。会議の発言に合わせて更新します")
        guide.setdefault("insights", [])
        data["guide"] = guide
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    if os.path.isfile(os.path.join(sdir(sid), "mindmap.html")):
        refresh_mindmap(sid)

def _load_research(sid):
    try:
        with open(_research_path(sid), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def lookup_worker():
    """深掘りジョブを直列処理（ライブループとは独立のスレッド）。結果は research.json に蓄積し、次のライブ整理から文脈に入る。"""
    while True:
        sid, need, why, immediate = lookup_q.get()
        try:
            if recording and sid == current_id and not immediate:
                with deferred_lookup_lock:
                    deferred_lookups.setdefault(sid, []).append((sid, need, why, False))
                continue
            m = read_meta(sid)
            pd = m.get("project_dir", "")
            researching[sid] = researching.get(sid, 0) + 1
            ctx = {}
            try:
                with open(os.path.join(sdir(sid), "context.json"), encoding="utf-8") as f:
                    ctx = json.load(f)
            except Exception:
                pass
            fmap = "\n".join("- %s: %s" % (x.get("path", ""), x.get("what", "")) for x in ctx.get("filemap", [])[:15])
            prof = _profile_text() or "（未設定）"
            if (m.get("stance") or "").strip():
                prof += "\nこの会議での立場：" + m["stance"].strip()
            # 依頼者がURLを直接貼った場合は、フォルダ探索を挟まずそのURLをWeb調査へ渡す。
            explicit_web = bool(re.search(r"https?://", need or ""))
            has_project = bool(pd and os.path.isdir(pd) and not explicit_web)
            # 調査は即時議事AIと並列に動かすが、詳細整理とは1スロットを共有する。
            with background_ai_lock:
                ans = (_claude_explore(pd, LOOKUP_PROMPT.format(title=m.get("title", "会議"), need=need, why=why,
                                                                profile=prof, filemap=fmap or "（未探索）"), timeout=180)
                       if has_project else "")
                source = "materials" if has_project else "web"
                if not has_project or _needs_web_fallback(ans):
                    ok, web_ans = assist_verify(need)
                    if ok:
                        ans = ((ans.strip() + "\n\n") if ans.strip() else "") + "【Web調査】\n" + web_ans
                        source = "materials+web" if has_project else "web"
            res = _load_research(sid)
            res.append({"need": need, "answer": ans or "（調査失敗）", "source": source,
                        "ts": time.strftime("%H:%M")})
            with open(_research_path(sid), "w", encoding="utf-8") as f:
                json.dump(res[-20:], f, ensure_ascii=False, indent=2)
            sys.stderr.write("[LOOKUP] %s 「%s」→ %d字\n" % (sid, need[:30], len(ans)))
        except Exception as e:
            sys.stderr.write("[LOOKUP] 例外 %r\n" % e)
        finally:
            researching[sid] = max(0, researching.get(sid, 1) - 1)
            lookup_q.task_done()
            sys.stderr.flush()

def queue_lookups(sid, lookups, immediate=False):
    """ライブAIが要求した調査項目を、重複を除いてジョブ投入。"""
    if not lookups:
        return
    done = {r.get("need", "") for r in _load_research(sid)}
    if not hasattr(queue_lookups, "_seen"):
        queue_lookups._seen = {}
    queued = queue_lookups._seen.setdefault(sid, set())
    for lk in lookups[:3]:
        need = str(lk.get("need", "")).strip()
        if need and need not in done and need not in queued:
            queued.add(need)
            lookup_q.put((sid, need, str(lk.get("why", "")), bool(immediate)))

RESEARCH_COMMAND = re.compile(r"(調べて|調べてくれ|検索して|調査して|裏取りして|(?:Web|web|ウェブ)で確認して)")
def queue_spoken_lookup(sid, text):
    """明示的な「調べて」を通常の議事整理より先にキューへ入れる。"""
    if not RESEARCH_COMMAND.search(text or ""):
        return
    try:
        with open(os.path.join(sdir(sid), "transcript.txt"), encoding="utf-8") as f:
            context_lines = [re.sub(r"\s+", " ", x).strip() for x in f.readlines() if x.strip()]
    except Exception:
        context_lines = []
    lines = [re.sub(r"\s+", " ", x).strip() for x in (text or "").splitlines() if x.strip()]
    current = (lines[-1] if lines else text)[-220:]
    previous = context_lines[-2] if len(context_lines) >= 2 else ""
    need = ((previous + " / ") if previous and len(current) < 45 else "") + current
    need = RESEARCH_COMMAND.sub("", need).strip("、。 ")
    need = re.sub(r"(?:を|について)\s*$", "", need).strip("、。 ")
    if need:
        queue_lookups(sid, [{"need": need, "why": "会議中の明示的な音声リクエスト"}], immediate=True)

# ---------- 音声チャンク処理（decode→whisper→claude。すべてpython/クロスOS）----------
# 日本語Windows（cp932）対策：罫線等の表示で起動即死しない・子プロセスのUTF-8出力を読み違えない
# （2026-07-17 Windows実機レポートの壁①④。PYTHONUTF8未設定の環境でも自衛する）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

def _run(cmd, **kw):
    key = getattr(long_job_local, "key", None)
    # 呼び出し側が capture_output/text を明示する箇所が多い。既定値を直接
    # subprocess.run へ重ねると TypeError になり、文字起こしだけ進んでAI解析が
    # 全停止するため、必ず先に取り出して1回だけ渡す。
    capture = kw.pop("capture_output", True)
    text_mode = kw.pop("text", True)
    if text_mode:
        # 子プロセス（whisper-cli等）の出力はUTF-8。Windowsのロケール既定（cp932）で
        # 読むとUnicodeDecodeErrorでワーカーが死ぬ（2026-07-17 実機レポートの壁④）
        kw.setdefault("encoding", "utf-8")
        kw.setdefault("errors", "replace")
    if not key:
        return subprocess.run(cmd, capture_output=capture, text=text_mode, **kw)
    timeout = kw.pop("timeout", None)
    input_value = kw.pop("input", None)
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE if input_value is not None else subprocess.DEVNULL,
                         stdout=subprocess.PIPE if capture else None,
                         stderr=subprocess.PIPE if capture else None,
                         text=text_mode, start_new_session=(os.name != "nt"), **kw)
    _register_long_process(p)
    try:
        stdout, stderr = p.communicate(input=input_value, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_tree(p)
        raise
    _check_long_cancelled()
    return subprocess.CompletedProcess(cmd, p.returncode, stdout, stderr)

def _mean_db(wav):
    """ffmpeg volumedetect で平均音量(dB)を返す。取れなければ -99。"""
    try:
        r = _run(["ffmpeg", "-hide_banner", "-i", wav, "-af", "volumedetect", "-f", "null", "-"])
        m = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", r.stderr or "")
        return float(m.group(1)) if m else -99.0
    except Exception:
        return -99.0

# 清書Q&Aで確定した固有名詞の学習辞書（使うほどwhisperの聞き取りが賢くなる）
LEARN_FILE = os.path.join(RUN, "asr-learned.txt")
def _learned_terms():
    try:
        with open(LEARN_FILE, encoding="utf-8") as f:
            return [l.strip() for l in f if l.strip()]
    except Exception:
        return []

# よくある一般カタカナ語（確認候補にしないストップリスト）。網羅ではなく体感ノイズの削減が目的
_KATA_COMMON = set("""ミーティング スケジュール プロジェクト システム サービス ユーザー データ メール オンライン リモート
パソコン アプリ ツール コード テスト レビュー タスク チーム メンバー クライアント コンサル リスト マップ スライド
ファイル フォルダ ドライブ ブラウザ タイミング イメージ ポイント ケース ベース レベル ペース パターン バージョン
エラー サーバー サーバ カレンダー グーグル ズーム スピーカー マイク カメラ アジェンダ フィードバック スタート
ゴール コスト バランス シンプル トータル メリット デメリット サポート セキュリティ アカウント パスワード ログイン
ダウンロード アップロード インストール アップデート スクリーン キャンセル ステータス スケール ビジネス マーケティング
セミナー イベント オフィス メンテナンス トラブル マニュアル プレゼン デザイン レイアウト コミュニケーション
インターネット ネットワーク ソフト ハード デバイス ロジック プロセス フロー モデル プロンプト エンジニア
プログラム ダッシュボード ホワイトボード スプレッドシート フィックス ヒアリング スケジュール ケースバイケース""".split())
_NAME_STOP = {"お客", "客", "皆", "お疲れ", "お母", "お父", "お兄", "お姉", "奥", "嫁", "婿", "患者", "店員"}

CONFIRM_VET_PROMPT = """会議の文字起こしから機械的に拾った語の選別です。依頼主に「聞き間違いでないか」を確認する価値がある語だけを残してください。
JSONのみを返す：{{"keep":[{{"term":"元の語","point":"確認カードの一文（断定形・40字以内）"}}]}}
- 残す：人名・社名・製品/サービス名などの固有名詞、または聞き間違いの疑いが強い不自然な語
- 捨てる：一般的なビジネス・技術カタカナ語（エージェント、プロダクト、ドライバー等）、呼称・普通名詞（お客さん等）
- 迷ったら捨てる。keepは最大2件
候補：
{terms}"""

def _vet_confirm_candidates(sid, cands):
    """機械検出したカタカナ語・人名候補を、確認カード化する前に小型AIで選別する
    （2026-07-17 依頼者決定1A。ストップリスト方式は一般語の誤爆が原理的に止まらないため）。"""
    try:
        terms = "\n".join("- 「%s」（発話: %s）" % (t, b) for t, _p, b in cands)
        out = _ai_text(CONFIRM_VET_PROMPT.format(terms=terms), timeout=45, model=CLAUDE_MODEL, background=True)
        m = re.search(r"\{.*\}", out or "", re.S)
        keep = (json.loads(m.group(0)).get("keep") or []) if m else []
        add = []
        by_term = {t: (pt, b) for t, pt, b in cands}
        for k in keep[:2]:
            term = str(k.get("term") or "").strip("「」 ")
            if term not in by_term:
                continue
            point = str(k.get("point") or "").strip() or by_term[term][0]
            add.append({"point": point[:60], "basis": ("発話：" + by_term[term][1])[:40]})
        if add:
            _merge_patch_to_disk(sid, {"confirm_add": add}, time.strftime("%H:%M:%S"))
            sys.stderr.write("[MECH-CONFIRM] %s 選別通過 %d/%d件\n" % (sid, len(add), len(cands)))
        else:
            sys.stderr.write("[MECH-CONFIRM] %s 全候補を一般語として棄却（%d件）\n" % (sid, len(cands)))
        sys.stderr.flush()
    except Exception as e:
        sys.stderr.write("[MECH-CONFIRM] %s 選別失敗 %r\n" % (sid, e)); sys.stderr.flush()

def _mech_confirm_path(sid):
    return os.path.join(sdir(sid), "confirm-raised.json")

def _mech_confirms(sid, delta, transcript):
    """AIの自己申告に頼らず、珍しいカタカナ語・人名を機械抽出して「解釈の確認」へ挙げる。
    whisperの誤変換はカタカナ語・人名に集中するため、既知情報（学習用語・プロフィール・
    参加者・ライブ補足）に無い語はとりあえず依頼主に確認してもらう（2026-07-16 依頼者方針）。"""
    try:
        raised = json.load(open(_mech_confirm_path(sid), encoding="utf-8"))
        if not isinstance(raised, list):
            raised = []
    except Exception:
        raised = []
    known = "\n".join([
        "\n".join(_learned_terms()), _profile_text() or "",
        "\n".join(str(n.get("text") or "") for n in _load_live_notes(sid)),
        json.dumps(_read_live_data(sid).get("speakers") or [], ensure_ascii=False),
        (read_meta(sid).get("title") or ""), "\n".join(raised)])
    def snippet(m):
        a, b = max(0, m.start() - 14), min(len(delta), m.end() + 14)
        return re.sub(r"\s+", " ", delta[a:b]).strip()
    cands = []
    for m in re.finditer(r"([一-龠ァ-ヶ]{1,5})(?:さん|様|氏)", delta):
        nm = m.group(1)
        if nm in _NAME_STOP or nm.endswith("屋") or nm in known:
            continue
        cands.append((nm, "参加者・関係者に「%sさん」がいて表記も正しい" % nm, snippet(m)))
    for m in re.finditer(r"[ァ-ヴー]{4,12}", delta):
        w = m.group(0)
        if w in _KATA_COMMON or w in known or transcript.count(w) < 2:
            continue
        cands.append((w, "「%s」は聞き間違いではなく正しい語" % w, snippet(m)))
    picked, seen = [], set()
    for key, point, basis in cands:
        if key in seen:
            continue
        seen.add(key); raised.append(key)
        picked.append((key, point, basis))
        if len(picked) >= 4:
            break
    if not picked:
        return
    try:
        with open(_mech_confirm_path(sid), "w", encoding="utf-8") as f:
            json.dump(raised[-200:], f, ensure_ascii=False)
    except Exception:
        pass
    # カード化の前にAI選別を挟む（別スレッド＝即時レーンを遅らせない）
    threading.Thread(target=_vet_confirm_candidates, args=(sid, picked), daemon=True).start()

def _learn_terms(answers):
    """清書前Q&Aで依頼者が確定した表記を辞書に追加（次回以降の文字起こしヒントに効く）。
    文章っぽい回答（前提説明など）は除外し、固有名詞・短い用語だけ学習する。"""
    if not isinstance(answers, dict):
        return
    terms = _learned_terms()
    for v in answers.values():
        v = str(v).strip()
        # 回答の言い回しを剥がして固有名詞だけにする（「東京海上であってる」→「東京海上」）
        v = re.sub(r"(で|に)?(あってる|あってます|合ってる|合ってます|です|でお願いします|だと思います|かな)$", "", v).strip()
        if not v or len(v) > 20 or v in terms or re.search(r"[。、\n]", v):
            continue
        if v.count("の") >= 2:
            continue   # 「私の社内の事例」のような説明文は固有名詞ではない（ヒント汚染防止）
        terms.append(v)
    terms = terms[-60:]   # 直近60語まで（ヒントが長すぎると逆効果）
    try:
        os.makedirs(RUN, exist_ok=True)
        with open(LEARN_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(terms) + "\n")
    except Exception:
        pass

def _asr_hint(sid=None):
    """音声に含まれない人物名・会議名は渡さない。

    Whisperは無音・不明瞭区間でinitial_promptを発話として吐くことがあるため、
    プロフィールや題名をここへ入れると偽の「話者名は〜」が本文へ混入する。
    """
    lang = _asr_language(sid)
    return ASR_HINT or ("Transcribe the spoken audio faithfully. Do not invent speaker names or metadata."
                        if lang == "en" else "実際に聞こえる日本語の発話だけを忠実に文字起こしする。話者名やメタデータを創作しない。")

def _asr_language(sid=None):
    if sid and is_session(sid):
        return "en" if str(read_meta(sid).get("language") or LANGUAGE).lower() == "en" else "ja"
    return "en" if LANGUAGE == "en" else "ja"

def _whisper_mlx_once(wav, sid=None):
    """mlx_whisperを1回呼ぶ（分割なし）。txtを読んで返す。"""
    base = os.path.splitext(wav)[0]
    name = os.path.basename(base)
    _run(["mlx_whisper", "--model", _asr_model(), "--language", _asr_language(sid),
          "--initial-prompt", _asr_hint(sid), "--condition-on-previous-text", "False",
          "-f", "txt", "--output-name", name, "-o", os.path.dirname(base) or ".", wav])
    txt = ""
    try:
        with open(base + ".txt", encoding="utf-8") as f:
            txt = f.read()
    except Exception:
        pass
    try: os.remove(base + ".txt")
    except Exception: pass
    return txt

def _whisper_mlx(wav, sid=None):
    """mlx_whisper(Apple Silicon)で文字起こし。large-v3(非turbo)＋辞書ヒント。
    長尺(15分超)は5分刻みに分割して独立デコードする：長い1本を一発で流すと途中で
    デコードが脱線し以降が反復文字列で全滅する（2026-07-14 実障害：48分音声が5:50以降崩壊。
    condition-on-previous-text=False でも防げず、分割の新規デコードでは全区間正常だった）。
    ライブの短チャンクは従来どおり1回で処理（dur<=900で素通り）。"""
    try:
        r = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                  "-of", "default=noprint_wrappers=1:nokey=1", wav], timeout=30)
        dur = float((r.stdout or "0").strip())
    except Exception:
        dur = 0.0
    if dur <= 900:
        return _whisper_mlx_once(wav, sid)
    parts, step = [], 300
    for st in range(0, int(dur), step):
        piece = "%s_p%05d.wav" % (os.path.splitext(wav)[0], st)
        _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(st), "-t", str(step),
              "-i", wav, "-ac", "1", "-ar", "16000", piece])
        if not os.path.isfile(piece):
            continue
        parts.append(_whisper_mlx_once(piece, sid))
        try: os.remove(piece)
        except Exception: pass
    return "\n".join(p for p in parts if p.strip())

def _whisper_cpp(wav, sid=None):
    """whisper-cli(whisper.cpp)で文字起こし。mlxが使えない環境(Windows等)向けフォールバック。"""
    base = os.path.splitext(wav)[0]
    _run(["whisper-cli", "-m", MODEL, "-f", wav, "-l", _asr_language(sid), "-otxt", "-of", base,
          "--no-timestamps", "-np", "--prompt", _asr_hint(sid)])
    txt = ""
    try:
        with open(base + ".txt", encoding="utf-8") as f:
            txt = f.read()
    except Exception:
        pass
    try: os.remove(base + ".txt")
    except Exception: pass
    return txt

def _whisper(wav, sid=None):
    """バックエンドを ASR_BACKEND で切替。mlxが失敗したらcppにフォールバック。"""
    if ASR_BACKEND == "mlx":
        try:
            txt = _whisper_mlx(wav, sid)
            if txt.strip():
                return txt
        except Exception:
            pass
        return _whisper_cpp(wav, sid)   # mlx不可の環境や失敗時は自動でcppへ
    return _whisper_cpp(wav, sid)

def _norm_leak(s):
    """ヒント漏れ判定用の正規化：語の文字（かな・カナ・漢字・英数）だけ残す。
    句読点・記号・空白は全て落とすので「創作しない！」等の記号付き漏れも同一視できる。"""
    return "".join(re.findall(r"[0-9A-Za-z぀-ヿ一-鿿]", s or ""))

def _lcs_len(a, b):
    """最長共通部分文字列（連続一致）の長さ。ヒントの崩れ吐き（1〜2文字違い）を捕まえる。
    a,bとも短い（数十字）ので素朴なDPで十分。"""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    best = 0
    for ca in a:
        cur = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if ca == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best

def _hint_leak_parts(sid=None):
    """ヒント漏れ判定の材料：ヒントを文単位の正規化断片＋全文正規化に分解する。
    断片単位で見ると「同じ文の2連結」や「文字起こしする→文字起こしない」の崩れ吐きも捕まる。"""
    raw = _asr_hint(sid)
    hn = _norm_leak(raw)
    frags = [f for f in (_norm_leak(s) for s in re.split(r"[。.!?！？\n]", raw)) if len(f) >= 6]
    frags.append(hn)
    return frags, hn

def _is_hint_echo(n, frags, hintn):
    """正規化済みの行nがヒントの漏れ吐きかを判定。
    ・断片が行に含まれる/行が断片に含まれる（2連結・記号付きもここで捕まる）
    ・連続一致が短い方の7割以上（1〜2字の崩れ吐き）
    ・8字以下で全文字がヒント由来かつ5字連続一致（「文字起こしない」等の短い崩れ）"""
    if len(n) < 6:
        return False
    for f in frags:
        if f and (n in f or f in n or _lcs_len(n, f) >= max(7, int(min(len(n), len(f)) * 0.7))):
            return True
    if len(n) <= 8 and set(n) <= set(hintn) and _lcs_len(n, hintn) >= 5:
        return True
    return False

def _clean(txt, sid=None):
    hint_frags, hintn = _hint_leak_parts(sid)   # whisperは聞き取れない区間でinitial_promptを吐く（既知の癖）
    lines, prev = [], None
    for ln in (txt or "").replace("\r", "").split("\n"):
        if not ln.strip():           continue
        # initial-promptの書式そのものが無音区間で発話として漏れるケースを除外。
        # 会議名・目的はmetaに既にあるため、実発話を多少落とすより議事への偽情報混入を防ぐ。
        if re.match(r"^\s*(?:(?:頻出する)?固有名詞|会議名(?:前)?|目的|録音者)\s*[：:、]", ln):
            continue
        if "固有名詞：" in ln or "固有名詞:" in ln:
            continue
        if re.match(r"^\s*(?:話者名|話題)\s*(?:は|[:：]).{1,60}[。.]?\s*$", ln):
            continue   # initial prompt由来の偽メタデータ（実音声の話者分離には使わない）
        if re.match(r"^\s*(?:the\s+)?speaker(?:'s)?\s+name\s+is\b.{1,60}[.]?\s*$", ln, re.I):
            continue
        if HALLU.match(ln):           continue   # YouTube字幕由来の定型ハルシネーション行を捨てる
        if MARKER.match(ln):          continue   # [音楽]（拍手）等のマーカー行を捨てる
        if MUSIC.match(ln):           continue   # ♪など記号だけの行を捨てる
        if NOISE.match(ln):           continue   # 聞き取り不能の擬音・短断片を捨てる（ブーブー等）
        ln = re.sub(r"(.{4,}?)\1{2,}", r"\1", ln)  # 同一フレーズの連続反復を圧縮
        ln = re.sub(r"(\S{2,12})(?:[ 　、]+\1){2,}", r"\1", ln)  # 「誠一 誠一 誠一」型の語反復を圧縮
        n = _norm_leak(ln)
        if _is_hint_echo(n, hint_frags, hintn):
            continue   # ヒントの漏れ出し行。2連結・記号違い・1〜2字の崩れ吐きも捨てる
        if prev is not None and n == prev:
            continue   # 直前と同一の行（whisperの反復癖）を捨てる
        prev = n
        lines.append(ln)
    return "\n".join(lines).strip()

# 差分更新プロンプト：毎回「現在の議事(JSON)＋新しく増えた文字起こしだけ」を渡す。
# これで1回の整理コストが会議の長さに依存せず、ほぼ一定になる（全文再読み込みをやめる）。
# ※未使用（2026-07-16判明）：並列レーン分割（LIVE_PATCH/DETAIL_PATCH/ACTIVE_*）への移行後、
#   このプロンプトはどこからも呼ばれていない。confirm（解釈の確認）はLIVE_PATCH_PROMPTへ移植済み。
#   スキーマ定義の参照資料として残す。新機能はレーン側のプロンプトに追加すること。
INCR_PROMPT = """あなたは会議「{title}」のリアルタイム書記兼参謀です。
【現在の議事(JSON)】と【新しく追加された文字起こし】を渡します。
現在の議事に新しい内容を反映し、**更新後のJSONのみ**を出力してください。
前置き・説明・コードフェンス(```)は一切禁止。JSONオブジェクトだけを返す。

スキーマ:
{{
  "updated": "{now}",
  "summary": "今まさに何を議論しているかを1〜2文の自然文で（最新状況に更新）",
  "agenda": ["扱っている/扱った議題を体言止めで"],
  "points": ["出た論点・意見・主張を短く"],
  "decisions": ["合意・決定したこと"],
  "todos": [{{"who":"担当者名（不明なら未定）","what":"やること"}}],
  "open": ["未解決・保留・要確認の事項"],
  "preparation": {{"brief":"事前準備室で共有した構想・狙い","outcome":"着地点","counterpart":"相手情報","hypotheses":[],"questions":[],"risks":[],"avoid":[],"updated":"保存時刻"}},
  "mindmap": [{{"topic":"大分類（15文字以内）","groups":[{{"label":"類似論点のまとまり（20文字以内）","items":[{{"label":"マップに表示する短い見出し（24文字以内）","detail":"発言・数字・背景・含意を省略しない詳細","status":"決定|仮説|未解決|行動|事実","source":"根拠となる発言の要旨。不明なら空文字"}}]}}]}}],
  "diagram": "いま議論の中心にある流れ・相関・体制・時系列を表すMermaid記法。無ければ空文字",
  "speakers": ["判明した参加者名（呼びかけ・自己言及から推定）。不明なら空配列"],
  "log": [{{"who":"発言者名（推定。判別不能は不明）","text":"その発言"}}],
  "assist": [{{"q":"会話から拾った疑問・調べたい点・意見が欲しそうな論点","a":"簡潔な補足（2〜3文）","check":"要確認・裏取りが必要な点。無ければ空文字"}}],
  "confirm": [{{"point":"AIの現在の解釈・前提のうち自信が持てないもの（40字以内・断定形の一文）","basis":"その解釈の根拠にした発言・資料（30字以内）"}}],
  "guide": {{"progress":"目標に対する現在地を1行で","questions":[{{"q":"次にすべき最適な質問","intent":"その質問の意図・狙い"}}],"answered":[{{"question":"AIが提案し、実際に聞いた質問","answer":"相手の回答要旨","analysis":"回答が示す本音・懸念・意思決定条件","next":"次に深掘りすべきこと"}}],"insights":[{{"said":"相手の注目発言","reading":"その解析（本音・懸念・シグナル）"}}]}},
  "lookups": [{{"need":"背景フォルダで調べてほしいこと（具体的に）","why":"なぜ今それが必要か"}}]
}}

ルール:
- 既存の項目は保持しつつ、新情報を追記・更新・統合する（重複は避ける）。撤回・変更があれば直す。
- preparationは事前準備室で依頼主が確定した前提。削除・改変せず必ずそのまま保持し、guide・mindmap・会議の解析の出発点に使う。
- 各配列は重要な順に最大8件。log は直近最大15件（古いものは落として良い）。
- whisperの誤変換は文脈から補正（固有名詞・数字に注意）。憶測で埋めない。日本語で。
- **文字起こしが崩れて意味が取れない箇所は、無理に議題・論点・決定に起こさない**（"それっぽい嘘"を作らない）。要点が拾えない区間は open に「(一部聞き取り不能)」と1件だけ残すか、何も足さない。確信のあることだけ書く。
- diagram: プロセス/相関/体制/時系列が出ていれば**有効なMermaid**（例: "flowchart LR\\n  A[紙台帳] --> B[スプレッド] --> C[自動通知]"）。ノード6個程度、日本語ラベルは[]で囲む。無ければ""。
- mindmap: 内容の意味の近さで統合し、必ず「会議→topic→類似論点group→個別item」の4層以上に整理する。topicは最大8件、groupsは各最大4件、itemsは各最大5件。近い発言を重複させず上位概念にまとめる。item.labelは一目で区別できる短い見出しとし、detailには数字・主語・条件・留保を落とさず記録する。「…」や「...」で省略しない。sourceは実際の発言か背景資料に根拠がある場合のみ書く。
- speakers/log: リアルタイム中の実名は候補にすぎない。本人の明確な自己紹介・第三者からの明確な呼びかけなど、実際の発話に根拠がある場合だけ推定する。判別不能は"不明"。
- 音声分離結果の匿名ID（SPEAKER_00等）があれば保持し、実名を勝手に当てない。文字起こし行頭の「人名＋空白/：」、「○○さんの話」、「話者名は○○」だけを根拠に参加者を新規作成しない。プロフィール名は依頼主候補であり、全発話者の確定情報ではない。
- 「会議名：」「目的：」「録音者：」「固有名詞：」のようなメタ情報形式の行はwhisper初期ヒントの自己漏洩であり、実際の発言として議題・質問・固有名詞へ採用しない。
- confirm（解釈の確認）: 固有名詞の同定・人物の関係・数値・前提の理解など、**議事の解釈に自信が持てない点**を最大3件、依頼主が「合ってる／違う」で即答できる断定形の一文にする（例：「ラグアップ＝ラクハブ社の旧称、という理解」）。【依頼者のライブ補足・訂正】で既に確認・訂正済みの点は二度と出さない。解釈に迷いが無ければ []。
- assist（AIサポート）: 会話中に**①一般的な知識を知りたそうな問い ②事実を確認したいこと ③意見・示唆が欲しそうな論点**が出たときだけ、簡潔な補足を最大3件。**【背景】【調査結果】に該当情報があればそれを優先して使い、出典（ファイル名）を添える**。無い情報は断定せず「一般には〜と言われる」の留保＋checkに「要確認」（会議に誤情報を流さないことを最優先）。問いが無ければ []。
- guide（参謀）: **【会議の目標】が設定されている場合のみ**出力（無ければ null）。目標達成のために、いまの会話の流れを踏まえた**次の一手の質問を1〜3件**（intentに狙いを明記。背景・調査結果の事実を活かす）。insightsは相手の発言から読み取れる本音・懸念・前向きシグナルを最大3件。決めつけず「〜の可能性」と表現。
- guide.answered: 現在のquestionsにある質問を依頼主が実際に聞き、相手が答えたと判断できるときだけ、回答を即時解析して追加。重複させず直近最大3件。
- 新しい発話に「調べて」「検索して」「裏取りして」「確認して」が含まれる場合、その直前の話題をlookupsの最優先にする。
- lookups（自動下調べ）: **背景フォルダがある場合のみ**。assistやguideの回答に**資料の具体的な情報（数字・過去の経緯・顧客情報等）が必要なのに手元に無い**とき、調べたいことを最大2件書く（ファイルマップを参考に具体的に）。既に【調査結果】にあるものは書かない。不要なら []。

{bg}
【現在の議事(JSON)】
{prev}

【新しく追加された文字起こし】
{delta}"""

LIVE_PATCH_PROMPT = """会議「{title}」の最新発話を即時整理してください。JSONだけを返し、推測や前置きは禁止です。
重い資料調査・議事一覧・マインドマップ作成は別処理なので行いません。今回の発話だけを短く判断してください。
{{
 "summary":"今の議論を50文字以内",
 "decision":"明確な合意だけ35文字以内。無ければ空文字",
 "questions":[{{"q":"提案を45文字以内","intent":"なぜ今それかを30文字以内","kind":"聞く|話す"}}],
 "stuck":"議論が明確に行き詰まっている時だけ、何に詰まっているかを20字以内。兆候（同じ話の反復・決め手が無い・迷いの発話）が無ければ省略",
 "relation":{{"from":"短い要素（案・状態・数字・結論）","to":"短い要素","type":"間の論理を18文字以内（数字歓迎。例:30万×10社=300万）","tone":"推進|懸念"}},
 "confirm":{{"point":"自信のない解釈を断定形で40文字以内","basis":"根拠にした発言の要旨を30文字以内"}}
}}
合意していないことをdecisionにしない。資料にない人名・会社名・数字を作らない。
relationは明確な因果・展開・帰結がある時だけ（話の流れの1ステップとして読めるもの）。typeは読むだけで論旨が再生できる言葉。懸念・未確定の関係はtone:"懸念"。無ければ空オブジェクト。
questionsは【今の話題】に対する提案0〜3件。会議のモードでkindを選ぶ：
- 相手から引き出す場（商談・面談・ヒアリング）→ kind="聞く"＝相手にそのまま言える一問
- 一緒に作る場（ブレスト・企画・作戦の壁打ち）→ kind="話す"＝次に話すと良い論点・方向（例:「単価×社数の掛け算で考えると広がる」「そろそろ収束に切替」「制約条件を先に固める」）
【会議の用途】があればそれを優先し、無ければ会話の質から判断する。混在も可。
話題が変わったら前の提案を引き継がず、今に合うものだけを出す（純粋な雑談なら省略）。索引のdismissedと同趣旨は再提案しない。序盤の積み残し（open）の回収を優先する。
【最重要・速度】値が無いキーは丸ごと省略する（空文字・空オブジェクトを書かない）。考えすぎず即答する。
confirmは「間違えると議事が狂う解釈」に不安がある時だけ出す。対象は4種のみ：
①聞き取りが曖昧な固有名詞の同定（人名・社名・製品名） ②誰が何を担当するかの対応 ③金額・数量・期日 ④初出の略語・専門語の意味。
依頼主が「合ってる/違う」で即答できる断定形の一文にする（例「先方の担当は中田さん（田中ではなく）という理解」）。
発話自体が曖昧・多義な場合だけ。話者が言わなかった詳細（具体日付・正式名称など）を確認事項にしない。
文脈で普通に確定する事柄も出さない。迷ったら出さない。
確信がある時・【依頼者のライブ補足・訂正】に答えが既にある時は空オブジェクト。
{bg}
【既存の短い索引】{index}
【最新発話】{delta}"""

DETAIL_PATCH_PROMPT = """あなたは会議「{title}」の背景整理担当です。
即時書記とは別レーンで、追加文字起こしをマインドマップの4層構造へ整理し、必要な調査だけを判定します。
前置き・コードフェンスなしで次のJSONだけを返してください。
{{
 "mindmap_add":[{{"topic":"大分類","groups":[{{"label":"類似論点","items":[{{"label":"24文字以内の見出し","detail":"数字・主語・条件を省略しない詳細","status":"決定|仮説|未解決|行動|事実","source":"実際の根拠。無ければ空文字"}}]}}]}}],
 "diagram":"会話の関係図の全面更新Mermaid。不要なら空文字",
 "lookups":[{{"need":"調べること","why":"必要な理由"}}]
}}
mindmap_addは新情報のみ。類似性でまとめ、詳細を「…」で省略しない。lookupsは手元の調査結果に無い項目だけ最大2件。
diagramは「議論のストーリーライン図」にする（2026-07-17 依頼者と言語化した仕様）：
- 会議で交わされた主要な話の流れを、話題ごとに独立した1本の流れ（起点→展開→帰結）として描く。流れは2〜4本
- ノード＝案・状態・数字・結論（短い名詞句）。人物一覧のような静的な列挙はしない
- 全エッジにその間の論理をラベルで書く（-->|30万×10社=300万| のように、読むだけで論旨が再生できる言葉）
- 実線-->＝合意・推進の流れ、点線-.->＝懸念・未確定の分岐
- 合計10〜16ノード。flowchart LR。3ノード以下の骨組みは返さない（既存を維持＝空文字）
例: flowchart LR
  A[個社コンサル] -->|30万×10社=300万| B[各社を実績化] -->|月単価100万へ| C[合計1000万規模]
  D[新サービス] -->|システム＋コンサル| E[1対1と1対Nの中間] -.->|低労力・高単価| F[アプリ型・1対N]
「【事前打ち合わせの背景情報】」は本会議の発言や決定ではない。仮説・事前準備としてのみ整理する。
{bg}
【既存マインドマップ索引】
{index}
【追加文字起こし】
{delta}"""

ACTIVE_LIST_PROMPT = """会議「{title}」の表示中リストに、最新発話から新たに確定できる内容だけを追加します。
前置きなしでJSONだけを返してください。各配列は最大2件、無ければ空配列です。
{{"agenda_add":[],"points_add":[],"decisions_add":[],"todos_add":[],"open_add":[],"arc":"会議全体の流れを2文以内（変化がなければ空文字）"}}
todos_addに追加する場合だけ{{"who":"担当者（不明なら未定）","what":"やること"}}の形にする。
合意のない事項を決定にしない。人名・会社名・数値を推測で作らない。
【既存リスト】{index}
【最新発話】{delta}"""

ACTIVE_REL_PROMPT = """会議「{title}」の「会話の関係」図を、最新の会話まで反映した形へ全面更新します。
前置きなしでJSONだけを返してください：{{"diagram":"Mermaid全文。更新の必要がなければ空文字"}}
図は「議論のストーリーライン図」：話題ごとに独立した流れ（起点→展開→帰結）を2〜4本。subgraphで流れごとに束ねる。
ノード＝案・状態・数字・結論。全エッジに論旨が再生できるラベル。実線-->＝合意・推進、点線-.->＝懸念・未確定。
合計10〜16ノード。flowchart LR。人物の静的な列挙はしない。既存の図の良い構造は保ちつつ新しい展開を統合する。
【現在の図】
{index}
【最新の会話】
{delta}"""

ACTIVE_MAP_PROMPT = """会議「{title}」の表示中マインドマップに、最新発話の新情報だけを追加します。
前置きなしでJSONだけを返してください。
{{"mindmap_add":[{{"topic":"大分類","groups":[{{"label":"類似論点","items":[{{"label":"24文字以内","detail":"主語・数字・条件を落とさない詳細","status":"決定|仮説|未解決|行動|事実","source":"根拠発話"}}]}}]}}]}}
新情報が無ければ{{"mindmap_add":[]}}。詳細を「…」で省略しない。推測しない。
【既存マップ】{index}
【最新発話】{delta}"""

def _live_index(obj):
    """差分AIに必要な重複防止用の小さな索引だけを渡す。"""
    return json.dumps({"summary": obj.get("summary", ""),
                       "dismissed": [str(x)[:45] for x in (obj.get("_dismissedQ") or [])[-5:]],
                       "arc": str(obj.get("arc") or "")[:400],
                       "agenda": (obj.get("agenda") or [])[-4:],
                       "open": (obj.get("open") or [])[-3:],
                       "decisions": (obj.get("decisions") or [])[-3:],
                       "guide_questions": ((obj.get("guide") or {}).get("questions") or [])[:2],
                       "relations": (obj.get("relations") or [])[-4:]},
                      ensure_ascii=False)

def _relations_to_mermaid(relations):
    """小さな関係差分を安全なMermaidへ決定的に変換する。"""
    nodes, lines = {}, ["flowchart LR"]
    def node(label):
        label = re.sub(r"[\[\]{}()\"'`;]", "", str(label or "")).strip()[:24]
        if not label: return ""
        if label not in nodes: nodes[label] = "R%d" % len(nodes)
        return nodes[label]
    clean = []
    for rel in (relations or [])[-LIVE_RELATIONS_MAX:]:
        if not isinstance(rel, dict): continue
        a, b = node(rel.get("from")), node(rel.get("to"))
        if not a or not b: continue
        kind = re.sub(r"[^0-9A-Za-zぁ-んァ-ヶ一-龠ー×=→ ]", "", str(rel.get("type") or "関連"))[:18] or "関連"
        row = {"from": next(k for k, v in nodes.items() if v == a),
               "to": next(k for k, v in nodes.items() if v == b), "type": kind,
               "tone": ("懸念" if str(rel.get("tone") or "") == "懸念" else "推進")}
        if row not in clean: clean.append(row)
    for label, ident in nodes.items(): lines.append('  %s["%s"]' % (ident, label))
    for rel in clean:
        arrow = "-.->" if rel.get("tone") == "懸念" else "-->"
        lines.append("  %s %s|%s| %s" % (nodes[rel["from"]], arrow, rel["type"], nodes[rel["to"]]))
    return "\n".join(lines) if clean else ""

def _detail_index(obj):
    mm = []
    for t in (obj.get("mindmap") or []):
        mm.append({"topic": t.get("topic", ""), "groups": [
            {"label": g.get("label", ""), "items": [i.get("label", "") for i in (g.get("items") or [])]}
            for g in (t.get("groups") or [])]})
    return json.dumps(mm, ensure_ascii=False)

def _append_unique(old, new, limit, key=None):
    out, seen = list(old or []), set()
    def sig(x):
        if key:
            return key(x)
        return json.dumps(x, ensure_ascii=False, sort_keys=True) if isinstance(x, (dict, list)) else str(x).strip()
    for x in out:
        seen.add(sig(x))
    for x in (new or []):
        s = sig(x)
        if s and s not in seen:
            out.append(x); seen.add(s)
    return out[-limit:]

def _live_list_text(value):
    """AIが文字列欄に返した {label, detail} 等を情報を落とさず1行化。"""
    if value is None:
        return ""
    if not isinstance(value, (dict, list)):
        text = str(value).strip()
        return "" if re.fullmatch(r"\[object Object\]", text, re.I) else text
    if isinstance(value, list):
        return " ・ ".join(filter(None, (_live_list_text(x) for x in value)))
    first = next((_live_list_text(value.get(k)) for k in
                  ("label", "issue", "title", "question", "q", "what", "text", "name")
                  if _live_list_text(value.get(k))), "")
    detail = next((_live_list_text(value.get(k)) for k in
                   ("detail", "description", "answer", "analysis", "reading", "status")
                   if _live_list_text(value.get(k)) and _live_list_text(value.get(k)) != first), "")
    if first and detail:
        return first + " — " + detail
    if first:
        return first
    return " — ".join(filter(None, (_live_list_text(x) for x in value.values())))

def _enforce_confirmed_speakers(obj, corrected, rejected=None):
    """明示確定された参加者を、AIの各差分より後に必ず適用する。"""
    corrected = _append_unique([], list(filter(None, (_live_list_text(x) for x in (corrected or [])))), 12)
    if not corrected:
        return obj
    old_speakers = list(filter(None, (_live_list_text(x) for x in (obj.get("speakers") or []))))
    removed = (set(old_speakers) - set(corrected)) | set(filter(None, rejected or obj.get("_rejectedSpeakers") or []))
    obj["_confirmedSpeakers"] = corrected
    if removed:
        obj["_rejectedSpeakers"] = sorted(removed)
    obj["speakers"] = corrected
    if any(name in str(obj.get("summary") or "") for name in removed):
        obj["summary"] = ""
    obj["open"] = [item for item in (obj.get("open") or [])
                   if not any(name in _live_list_text(item) for name in removed)
                   and not re.search(r"(?:話者|参加者|スピーカー).*(?:矛盾|同一|確認|不明|表記)", _live_list_text(item))]
    for entry in (obj.get("log") or []):
        if isinstance(entry, dict) and _live_list_text(entry.get("who")) in removed:
            entry["who"] = "不明"
    return obj

def _merge_live_patch(old, patch, now):
    """AIの小さな差分を既存議事へ決定的に統合する。"""
    obj = dict(old or {})
    obj["updated"] = now
    if (patch.get("summary") or "").strip():
        obj["summary"] = patch["summary"].strip()
    if (patch.get("arc") or "").strip():
        obj["arc"] = str(patch["arc"]).strip()[:600]
    for dst, src in (("agenda", "agenda_add"), ("points", "points_add"),
                     ("decisions", "decisions_add"), ("open", "open_add")):
        old_items = list(filter(None, (_live_list_text(x) for x in (obj.get(dst) or []))))
        new_items = list(filter(None, (_live_list_text(x) for x in (patch.get(src) or []))))
        obj[dst] = _append_unique(old_items, new_items, 8)
    if patch.get("decisions_add"):
        obj["_lastDecisionAt"] = int(time.time())
    obj["todos"] = _append_unique(obj.get("todos"), patch.get("todos_add"), 8,
                                   lambda x: (str(x.get("who", "")) + "|" + str(x.get("what", ""))).strip() if isinstance(x, dict) else str(x))
    old_speakers = list(filter(None, (_live_list_text(x) for x in (obj.get("speakers") or []))))
    new_speakers = list(filter(None, (_live_list_text(x) for x in (patch.get("speakers_add") or []))))
    speakers_set = patch.get("speakers_set")
    if isinstance(speakers_set, list):
        # 明示訂正だけはappend-onlyにしない。誤認名が永遠に残るのを防ぐ。
        corrected = _append_unique([], list(filter(None, (_live_list_text(x) for x in speakers_set))), 12)
        obj["_confirmedSpeakers"] = corrected
    else:
        obj["speakers"] = _append_unique(old_speakers, new_speakers, 12)
    confirm_add = [x for x in (patch.get("confirm_add") or [])
                   if isinstance(x, dict) and str(x.get("point") or "").strip()]
    if confirm_add:
        obj["confirm"] = _append_unique(obj.get("confirm"), confirm_add, 4,
                                        lambda x: str(x.get("point", "")).strip() if isinstance(x, dict) else str(x))
    log_add = []
    for entry in (patch.get("log_add") or []):
        if isinstance(entry, dict):
            entry = dict(entry); entry.setdefault("at", now[:5])
        log_add.append(entry)
    obj["log"] = _append_unique(obj.get("log"), log_add, 30)
    topics = list(obj.get("mindmap") or [])
    for nt in (patch.get("mindmap_add") or []):
        topic = str(nt.get("topic", "")).strip()
        if not topic: continue
        target = next((t for t in topics if str(t.get("topic", "")).strip() == topic), None)
        if target is None:
            target = {"topic": topic, "groups": []}; topics.append(target)
        groups = target.setdefault("groups", [])
        for ng in (nt.get("groups") or []):
            label = str(ng.get("label", "")).strip()
            if not label: continue
            group = next((g for g in groups if str(g.get("label", "")).strip() == label), None)
            if group is None:
                group = {"label": label, "items": []}; groups.append(group)
            group["items"] = _append_unique(group.get("items"), ng.get("items"), 5,
                                             lambda x: str(x.get("label", "")).strip() if isinstance(x, dict) else str(x))
        target["groups"] = groups[-4:]
    obj["mindmap"] = topics[-8:]
    relations = _append_unique(obj.get("relations"), patch.get("relations_add"), LIVE_RELATIONS_MAX,
                               lambda x: "%s|%s|%s" % (x.get("from", ""), x.get("to", ""), x.get("type", "")) if isinstance(x, dict) else str(x))
    obj["relations"] = relations
    # 機械生成の関係図は「新しいペアが来た時」だけ再生成する。無条件に作り直すと、
    # DETAIL/清書が書いた良いストーリーライン図を毎チャンク上書きして消してしまう
    # （2026-07-17 実障害：良い図が録音中に消えて安定しなかった構造原因）
    story_at = float(obj.get("_diagramStoryAt") or 0)
    if patch.get("relations_add") and time.time() - story_at > 300:
        # 機械生成は「直近5分にストーリー図の更新が無い」時だけ（ACTIVE関係レーンとの競合防止）
        relation_diagram = _relations_to_mermaid(relations)
        if relation_diagram: obj["diagram"] = relation_diagram
    if (patch.get("diagram") or "").strip():
        obj["diagram"] = patch["diagram"].strip()
        obj["_diagramStoryAt"] = int(time.time())
    if patch.get("assist"): obj["assist"] = (patch.get("assist") or [])[:3]
    pg, og = patch.get("guide") or {}, obj.get("guide") or {}
    dismissed = set(str(x) for x in (obj.get("_dismissedQ") or []))
    if pg:
        if pg.get("progress"): og["progress"] = pg["progress"]
        if pg.get("questions"):
            # 新提案が来たら全置換（積み上げない＝話題が移れば入れ替わる。2026-07-17 依頼者決定2A）
            og["questions"] = [q for q in pg["questions"][:3]
                               if str(q.get("q") or "") not in dismissed]
    # 鮮度切れ（5分）の質問は自動で消す
    now_epoch = time.time()
    if og.get("questions"):
        og["questions"] = [q for q in og["questions"]
                           if now_epoch - float(q.get("at") or now_epoch) < 300
                           and str(q.get("q") or "") not in dismissed]
        if pg.get("insights"): og["insights"] = pg["insights"][:3]
        og["answered"] = _append_unique(og.get("answered"), pg.get("answered_add"), 3,
                                         lambda x: str(x.get("question", "")) if isinstance(x, dict) else str(x))
        obj["guide"] = og
    obj["lookups"] = (patch.get("lookups") or [])[:2]
    return _enforce_confirmed_speakers(obj, obj.get("_confirmedSpeakers"), obj.get("_rejectedSpeakers"))

def _read_live_data(sid):
    try:
        with open(os.path.join(sdir(sid), "data.json"), encoding="utf-8") as f:
            obj = json.load(f)
            return obj if isinstance(obj, dict) else json.loads(EMPTY_DATA)
    except Exception:
        return json.loads(EMPTY_DATA)

def _parse_live_patch(out, lane, sid):
    out = re.sub(r"^```json\s*|^```\s*|```\s*$", "", out or "", flags=re.M).strip()
    m = re.search(r"\{.*\}", out, re.S)
    if m:
        out = m.group(0)
    try:
        patch = json.loads(out)
        if not isinstance(patch, dict):
            raise ValueError("JSON object required")
        return patch
    except Exception as e:
        sys.stderr.write("[%s] %s JSON失敗 %r out=%r\n" % (lane, sid, e, out[:300])); sys.stderr.flush()
        return None

def _normalize_fast_patch(patch):
    """低遅延の単数レスポンスを既存の差分統合形式へ変換する。"""
    patch = dict(patch or {})
    decision = str(patch.pop("decision", "") or "").strip()
    questions = patch.pop("questions", None)
    if questions is None:
        q1 = patch.pop("question", {})
        questions = [q1] if isinstance(q1, dict) else []
    relation = patch.pop("relation", {})
    if decision: patch["decisions_add"] = [decision]
    qs = [{"q": str(x.get("q") or "")[:45], "intent": str(x.get("intent") or "")[:30],
           "kind": ("話す" if str(x.get("kind") or "") == "話す" else "聞く"), "at": int(time.time())}
          for x in (questions or []) if isinstance(x, dict) and str(x.get("q") or "").strip()][:3]
    if qs:
        patch["guide"] = {"progress": str(patch.get("summary") or "")[:30], "questions": qs}
    stuck = str(patch.pop("stuck", "") or "").strip()
    if stuck:
        patch["_stuck"] = stuck[:40]
    if isinstance(relation, dict) and relation.get("from") and relation.get("to"):
        patch["relations_add"] = [relation]
    confirm = patch.pop("confirm", {})
    if isinstance(confirm, dict) and str(confirm.get("point") or "").strip():
        patch["confirm_add"] = [{"point": str(confirm.get("point") or "").strip()[:60],
                                 "basis": str(confirm.get("basis") or "").strip()[:40]}]
    return patch

def _merge_patch_to_disk(sid, patch, now, view_key=None):
    """AIレーンは並列実行し、最新dataの再読込みと統合だけを直列化。"""
    d = sdir(sid)
    with data_write_lock:
        obj = _merge_live_patch(_read_live_data(sid), patch, now)
        obj["_analysisUpdatedAt"] = int(time.time())
        if view_key:
            vu = obj.get("_viewUpdatedAt") if isinstance(obj.get("_viewUpdatedAt"), dict) else {}
            vu[view_key] = int(time.time()); obj["_viewUpdatedAt"] = vu
        tmp = os.path.join(d, "data.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, os.path.join(d, "data.json"))
    return obj

def _speaker_display(speaker):
    m = re.fullmatch(r"SPEAKER_(\d+)", str(speaker or ""))
    return _t("話者%s" % chr(65 + int(m.group(1))), "Speaker %s" % chr(65 + int(m.group(1)))) if m else _t("不明", "Unknown")

def _write_live_receipt(sid, text, transcript_end, audio_name=""):
    """文字起こし完了をAIより先に画面へ反映する。議事本文には混ぜない。"""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text or not is_session(sid):
        return
    d = sdir(sid); path = os.path.join(d, "data.json")
    with data_write_lock:
        obj = _read_live_data(sid)
        if not obj.get("_analysisUpdatedAt"):
            try: obj["_analysisUpdatedAt"] = int(os.path.getmtime(path))
            except Exception: obj["_analysisUpdatedAt"] = 0
        live_diarization = _load_live_diarization(sid)
        audio_speakers = live_diarization.get("audioSpeakers") if isinstance(live_diarization.get("audioSpeakers"), dict) else {}
        speaker = str(audio_speakers.get(audio_name, ""))
        who = _speaker_display(speaker) if speaker else _t("話者確認中", "Identifying speaker")
        obj["liveReceipt"] = {"text": text[-240:], "at": int(time.time()),
                              "transcriptEnd": int(transcript_end), "analyzed": False,
                              "audio": audio_name, "speaker": speaker, "who": who}
        # 時系列はAIを待たず、文字起こしが届いた時点で更新する。
        timeline = obj.get("timeline") if isinstance(obj.get("timeline"), list) else []
        # 旧版が書いた幻聴・ヒント漏れ行の掃除は、浄化ルールの版が変わった時に一度だけ。
        # 新規エントリは追記前に_clean済みなので、毎チャンクの全件走査は不要（性能対策）
        if obj.get("_tlCleanVer") != TL_CLEAN_VER:
            timeline = [e for e in timeline
                        if isinstance(e, dict) and _clean(str(e.get("text") or ""), sid)]
            obj["_tlCleanVer"] = TL_CLEAN_VER
        timeline.append({"at": time.strftime("%H:%M"), "who": who, "speaker": speaker,
                         "audio": audio_name, "text": text})
        obj["timeline"] = timeline[-TIMELINE_MAX:]
        vu = obj.get("_viewUpdatedAt") if isinstance(obj.get("_viewUpdatedAt"), dict) else {}
        vu["timeline"] = int(time.time()); obj["_viewUpdatedAt"] = vu
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

def _mark_live_receipt_analyzed(sid, transcript_end):
    path = os.path.join(sdir(sid), "data.json")
    with data_write_lock:
        obj = _read_live_data(sid)
        receipt = obj.get("liveReceipt") if isinstance(obj.get("liveReceipt"), dict) else {}
        if not receipt or int(receipt.get("transcriptEnd") or 0) > int(transcript_end):
            return
        receipt["analyzed"] = True; obj["liveReceipt"] = receipt
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

def _fast_bg_block(sid, meta):
    """即時レーンは本人・目標・会議前の着地点だけ。ファイルマップと調査全文は背景レーンに任せる。"""
    parts = []
    prof = (_profile_text() or "").strip()
    if prof:
        parts.append("【依頼主】\n" + prof[:500])
    mtype = (meta.get("mtype") or "").strip()
    if mtype:
        parts.append("【会議の用途】" + mtype[:40])
    goal = (meta.get("goal") or "").strip()
    if goal:
        parts.append("【会議の目標】\n" + goal[:300])
    strategy = _load_strategy(sid)
    board = strategy.get("board") if isinstance(strategy.get("board"), dict) else {}
    outcome = str(board.get("outcome", "")).strip()
    if outcome:
        parts.append("【事前準備の着地点】\n" + outcome[:400])
    notes = _load_live_notes(sid)
    if notes:
        parts.append("【依頼者のライブ補足・訂正（文字起こし・資料より優先）】\n" +
                     "\n".join("- %s" % x.get("text", "") for x in notes[-6:])[:1800])
    return "\n\n".join(parts)

fast_fail_streak = 0
fast_last_attempt = 0.0
COUNSEL_PROMPT = """あなたは会議の参謀です。議論が行き詰まっています：{stuck}
背景・準備・調査結果を根拠に、依頼主への「あなた自身の意見」を返してください。JSONのみ：
{{"situation":"何に詰まっているかの言語化（30字以内）",
 "options":[{{"label":"案1の名前（15字以内）","body":"要点を2文"}},{{"label":"案2の名前","body":"要点を2文"}}],
 "pick":"推す案のlabel","reason":"推す理由を2〜3文。背景・数字・過去の経緯を具体的な根拠として引く"}}
- 一般論で埋めない。この会議・この案件の固有の事実に立脚する。
- 案は本当に対立する2つに絞る（水増しの3案目を作らない）。
{bg}
【会議全体の流れ】
{arc}
【直近の会話】
{delta}"""

def request_counsel(sid, stuck, delta):
    """行き詰まり検知時の意見生成（2026-07-17 依頼者決定3B/4A：2案＋推し・CD5分・1会議3回まで）。
    発火条件は「AIのstuckフラグ」＋「直近4分決定ゼロ」の2シグナル一致。生成はsonnetの別レーン。"""
    obj = _read_live_data(sid)
    now = time.time()
    if now - float(obj.get("_counselAt") or 0) < 300:
        return
    if int(obj.get("_counselCount") or 0) >= 3:
        return
    if now - float(obj.get("_lastDecisionAt") or 0) < 240 and float(obj.get("_lastDecisionAt") or 0) > 0:
        return   # 直近に決定が出ている＝停滞ではない
    def job():
        try:
            meta = read_meta(sid)
            prompt = COUNSEL_PROMPT.format(stuck=stuck, bg=_bg_block(sid, meta)[:2600],
                                           arc=str(obj.get("arc") or obj.get("summary") or "")[:400],
                                           delta=delta[-1200:])
            out = _ai_text(prompt, timeout=120, cwd=tempfile.gettempdir(), model=ASSIST_MODEL, background=True)
            m = re.search(r"\{.*\}", out or "", re.S)
            if not m:
                return
            c = json.loads(m.group(0))
            if not str(c.get("situation") or "").strip() or not (c.get("options") or []):
                return
            counsel = {"situation": str(c.get("situation"))[:40],
                       "options": [{"label": str(o.get("label") or "")[:20], "body": str(o.get("body") or "")[:200]}
                                   for o in (c.get("options") or [])[:2]],
                       "pick": str(c.get("pick") or "")[:20],
                       "reason": str(c.get("reason") or "")[:400], "at": int(time.time())}
            with data_write_lock:
                cur = _read_live_data(sid)
                cur["counsel"] = counsel
                cur["_counselAt"] = int(time.time())
                cur["_counselCount"] = int(cur.get("_counselCount") or 0) + 1
                tmp = os.path.join(sdir(sid), "data.json.tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(cur, f, ensure_ascii=False, indent=2)
                os.replace(tmp, os.path.join(sdir(sid), "data.json"))
            sys.stderr.write("[COUNSEL] %s 提案を生成（%s）\n" % (sid, counsel["situation"])); sys.stderr.flush()
        except Exception as e:
            sys.stderr.write("[COUNSEL] %s 失敗 %r\n" % (sid, e)); sys.stderr.flush()
    threading.Thread(target=job, daemon=True).start()

def _claude_update(sid):
    global fast_fail_streak, fast_last_attempt
    # 連続失敗中（whisper高負荷等でCLIがタイムアウトし続ける時）は45秒に1回だけ試す。
    # 15秒毎の全滅ループはCPUを焼くだけで1件も成果を出さない（2026-07-16 実障害：559回失敗）
    if fast_fail_streak >= 3 and time.time() - fast_last_attempt < 45:
        return False
    d = sdir(sid)
    try:
        with open(os.path.join(d, "transcript.txt"), encoding="utf-8") as f:
            transcript = f.read()
    except Exception:
        transcript = ""
    # 前回反映済みの位置から先（＝新しく増えた分）だけを渡す＝会議が長くても一定コスト
    if sid not in applied:
        try:
            with open(os.path.join(d, ".applied"), encoding="utf-8") as f:
                applied[sid] = int(f.read().strip())
        except Exception:
            # 旧版からの移行時は、既存data.jsonに既に入っている前半を重ねすぎない。
            # 直近8000文字を少分けで再反映すれば、停止期間を拾いつつ重複はAI側で統合できる。
            applied[sid] = max(0, len(transcript) - 8000) if os.path.isfile(os.path.join(d, "data.json")) else 0
    off = applied.get(sid, 0)
    if off > len(transcript):     # transcriptが作り直された等 → 全体を対象に
        off = 0
    # 即時レーンは応答時間を優先。大量の古い未処理は生音声・全文に保持し、
    # 詳細レーンへ任せて直近900文字へジャンプする。
    if len(transcript) - off > 2700:
        off = max(off, len(transcript) - 900)
    end = min(len(transcript), off + 500)
    delta = transcript[off:end].strip()
    if not delta:
        applied[sid] = end
        return True
    old_obj = _read_live_data(sid)
    meta = read_meta(sid)
    title = meta.get("title", "会議")
    now = time.strftime("%H:%M:%S")
    try:
        _mech_confirms(sid, delta, transcript)   # AIが落ちても機械検出の確認候補は届ける
    except Exception as e:
        sys.stderr.write("[MECH-CONFIRM] %s 失敗 %r\n" % (sid, e)); sys.stderr.flush()
    prompt = LIVE_PATCH_PROMPT.format(title=title, delta=delta, bg=_fast_bg_block(sid, meta), index=_live_index(old_obj))
    fast_last_attempt = time.time()
    try:
        # haikuの構造化生成は25〜75秒で大きく揺れる（2026-07-17実測。API側の変動）。
        # 短いタイムアウトは全滅ループを生むだけなので、90秒で「遅くても必ず進む」を最優先。
        # 遅延分の未処理はoffのジャンプ（直近900字へ）とdetailレーンが吸収する
        out = _ai_text(prompt, timeout=90, model=CLAUDE_MODEL)
    except Exception as e:
        fast_fail_streak += 1
        sys.stderr.write("[FAST-ANALYSIS] %s 実行失敗(連続%d) %r\n" % (sid, fast_fail_streak, e)); sys.stderr.flush()
        return False
    patch = _parse_live_patch(out, "FAST-ANALYSIS", sid)
    if patch is None:
        fast_fail_streak += 1
        return False
    fast_fail_streak = 0
    patch = _normalize_fast_patch(patch)
    stuck = patch.pop("_stuck", "")
    _merge_patch_to_disk(sid, patch, now)
    if stuck:
        request_counsel(sid, stuck, delta)
    _mark_live_receipt_analyzed(sid, end)
    applied[sid] = end   # 今回の小分け差分まで反映済み
    try:
        with open(os.path.join(d, ".applied"), "w", encoding="utf-8") as f:
            f.write(str(end))
    except Exception:
        pass
    request_detail(sid)
    return True

def _bg_block(sid, meta):
    """背景ダイジェスト・ファイルマップ・調査結果・目標をプロンプト用に組み立てる（無いものは省略）。"""
    parts = []
    prof = _profile_text()
    stance = (meta.get("stance") or "").strip()
    if prof or stance:
        block = "【依頼主＝この会議の主（録音している本人）】\n" + (prof or "（プロフィール未設定）")
        if stance:
            block += "\nこの会議での立場：" + stance
        block += ("\n※speakers/logではこの人物を上記の名前で表記する（「私」「不明」にしない）。"
                  "guide・assistはこの人の立場に立って助言する。")
        parts.append(block)
    goal = (meta.get("goal") or "").strip()
    if goal:
        parts.append("【会議の目標】\n" + goal)
    strategy = _load_strategy(sid)
    brief = (strategy.get("brief") or "").strip()
    if brief:
        parts.append("【会議前の作戦会議ブリーフ（依頼主の構想・狙い・仮説）】\n" + brief[:3000])
    notes = _load_live_notes(sid)
    if notes:
        parts.append("【依頼者のライブ補足・訂正（最優先）】\n" +
                     "\n".join("- %s" % x.get("text", "") for x in notes[-10:])[:3000])
    mtype = (meta.get("mtype") or "").strip()
    if mtype:
        pb = _playbook_text(mtype)
        if pb:
            parts.append("【%sのプレイブック（過去の会議から蓄積したノウハウ。guideの質問・読みに積極的に活かす）】\n%s" % (mtype, pb))
    try:
        with open(os.path.join(sdir(sid), "context.json"), encoding="utf-8") as f:
            ctx = json.load(f)
        dg = (ctx.get("digest") or "").strip()
        if dg:
            parts.append("【背景（プロジェクト資料のダイジェスト）】\n" + dg[:2200])
        fm = ctx.get("filemap") or []
        if fm:
            parts.append("【背景フォルダのファイルマップ】\n" +
                         "\n".join("- %s: %s" % (x.get("path", ""), x.get("what", "")) for x in fm[:15]))
    except Exception:
        pass
    res = _load_research(sid)
    if res:
        parts.append("【調査結果（背景フォルダから取得済み）】\n" +
                     "\n".join("- %s\n%s" % (r.get("need", ""), (r.get("answer", "") or "")[:600]) for r in res[-5:]))
    return ("\n\n".join(parts) + "\n") if parts else ""

def request_detail(sid):
    """マインドマップ・関係整理を最新の1ジョブに集約する。"""
    if not is_session(sid):
        return
    if recording and sid == current_id:
        detail_deferred.add(sid)
        return
    with detail_lock:
        if sid in detail_pending:
            return
        detail_pending.add(sid)
        detail_q.put(sid)

def _detail_update(sid):
    if recording and sid == current_id:
        detail_deferred.add(sid)
        return True
    d = sdir(sid)
    try:
        with open(os.path.join(d, "transcript.txt"), encoding="utf-8") as f:
            transcript = f.read()
    except Exception:
        return False
    if sid not in detail_applied:
        try:
            with open(os.path.join(d, ".detail-applied"), encoding="utf-8") as f:
                detail_applied[sid] = int(f.read().strip())
        except Exception:
            # 新規会議は最初から、旧版の長尺会議は直近3,000文字だけ再整理。
            detail_applied[sid] = max(0, len(transcript) - 3000)
    off = min(detail_applied.get(sid, 0), len(transcript))
    end = len(transcript)
    start = max(off, end - 3000)
    delta = transcript[start:end].strip()
    if not delta:
        detail_applied[sid] = end
        return True
    meta = read_meta(sid)
    current = _read_live_data(sid)
    prompt = DETAIL_PATCH_PROMPT.format(title=meta.get("title", "会議"), delta=delta,
                                        bg=_bg_block(sid, meta)[:2600], index=_detail_index(current))
    try:
        with background_ai_lock:
            out = _ai_text(prompt, timeout=75, model=CLAUDE_MODEL, background=True)
    except Exception as e:
        sys.stderr.write("[DETAIL-ANALYSIS] %s 実行失敗 %r\n" % (sid, e)); sys.stderr.flush()
        return False
    patch = _parse_live_patch(out, "DETAIL-ANALYSIS", sid)
    if patch is None:
        return False
    obj = _merge_patch_to_disk(sid, patch, time.strftime("%H:%M:%S"))
    detail_applied[sid] = end
    try:
        with open(os.path.join(d, ".detail-applied"), "w", encoding="utf-8") as f:
            f.write(str(end))
    except Exception:
        pass
    if os.path.isfile(os.path.join(d, "mindmap.html")):
        refresh_mindmap(sid)
    try:
        queue_lookups(sid, obj.get("lookups") or [])
    except Exception:
        pass
    return True

def detail_worker():
    """即時レーンを止めず、最新の詳細構造を背景で更新。"""
    while True:
        sid = detail_q.get()
        ok = False
        try:
            if is_session(sid):
                ok = _detail_update(sid)
        except Exception as e:
            sys.stderr.write("詳細解析エラー: %r\n" % e)
        finally:
            with detail_lock:
                detail_pending.discard(sid)
            detail_q.task_done()
        if ok:
            detail_failures.pop(sid, None)
        else:
            failures = detail_failures.get(sid, 0) + 1
            detail_failures[sid] = failures
            if failures < 3 and is_session(sid):
                threading.Timer(20, request_detail, args=(sid,)).start()
        try:
            with open(os.path.join(sdir(sid), "transcript.txt"), encoding="utf-8") as f:
                total = len(f.read())
            if ok and total > detail_applied.get(sid, 0):
                request_detail(sid)
        except Exception:
            pass

def _overlap_wav(sid, wav, kind="meeting"):
    """前チャンク末尾2秒を現チャンクの先頭に付加。会議再開等で45秒以上空いた尾部は使わない。"""
    tdir = os.path.join(WAVROOT, sid); os.makedirs(tdir, exist_ok=True)
    tail = os.path.join(tdir, "asr-tail-prep.wav" if kind == "prep" else "asr-tail.wav")
    merged = os.path.splitext(wav)[0] + "-overlap.wav"
    use_tail = os.path.isfile(tail) and time.time() - os.path.getmtime(tail) <= 45
    if use_tail:
        r = _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", tail, "-i", wav,
                  "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1[out]", "-map", "[out]",
                  "-ar", "16000", "-ac", "1", merged])
        if r.returncode != 0 or not os.path.isfile(merged):
            merged = wav
    else:
        merged = wav
    # 次回用尾部は「現チャンク原音」から作る（重複の累積を防ぐ）。
    tail_tmp = tail + ".tmp.wav"
    r = _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-sseof", "-2", "-i", wav,
              "-ar", "16000", "-ac", "1", tail_tmp])
    if r.returncode == 0 and os.path.isfile(tail_tmp):
        os.replace(tail_tmp, tail)
    else:
        try: os.remove(tail_tmp)
        except FileNotFoundError: pass
    return merged

def _dedup_asr_boundary(previous, current):
    """2秒オーバーラップで二重文字起こしされた境界文を除去。"""
    if not previous or not current:
        return current
    skip = set(" \t\r\n　、。,.!！?？・：:;；「」『』（）()")
    def normalized_with_pos(s):
        chars, pos = [], []
        for i, c in enumerate(s):
            if c not in skip:
                chars.append(c); pos.append(i)
        return "".join(chars), pos
    pn, _ = normalized_with_pos(previous[-240:])
    cn, cpos = normalized_with_pos(current[:240])
    limit = min(len(pn), len(cn), 100)
    remove_n = 0
    # まず完全一致の最長境界を探す。次文の先頭を曖昧一致で飲み込まない。
    for n in range(limit, 7, -1):
        a, b = pn[-n:], cn[:n]
        if a == b:
            remove_n = n; break
    if not remove_n:
        candidates = []
        for n in range(12, limit + 1):
            ratio = difflib.SequenceMatcher(None, pn[-n:], cn[:n]).ratio()
            if ratio >= .92:
                candidates.append((ratio, n))
        if candidates:
            remove_n = max(candidates)[1]
    if remove_n and len(cpos) >= remove_n:
        return current[cpos[remove_n - 1] + 1:].lstrip(" \t\r\n　、。,.!！?？")
    return current

def process_chunk(sid, webm):
    """webmチャンク → wav化 → 無音判定 → whisper → 整形 → transcript追記。
    議事JSONの更新は独立ワーカーへ依頼し、音声キューを塞がない。"""
    wav = os.path.splitext(webm)[0] + ".wav"
    try:
        _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
              "-i", webm, "-ar", "16000", "-ac", "1", wav])
        if not os.path.isfile(wav) or _mean_db(wav) < SILENCE_DB:
            return  # 無音・デコード失敗はスキップ
        is_prep = os.path.basename(webm).startswith("prep_")
        # 事前打ち合わせは本会議の原本音声と分離し、最終議事録の決定事項へ混入させない。
        try:
            adir = os.path.join(sdir(sid), "prep-audio" if is_prep else "audio"); os.makedirs(adir, exist_ok=True)
            shutil.copy2(webm, os.path.join(adir, os.path.basename(webm)))
        except Exception:
            pass
        asr_wav = _overlap_wav(sid, wav, "prep" if is_prep else "meeting")
        txt = _clean(_whisper(asr_wav, sid), sid)
        if txt:
            try:
                transcript_name = "prep-transcript.txt" if is_prep else "transcript.txt"
                with open(os.path.join(sdir(sid), transcript_name), encoding="utf-8") as f:
                    txt = _dedup_asr_boundary(f.read(), txt)
            except Exception:
                pass
        if txt:
            transcript_name = "prep-transcript.txt" if is_prep else "transcript.txt"
            with open(os.path.join(sdir(sid), transcript_name), "a", encoding="utf-8") as f:
                f.write(txt + "\n")
            if not is_prep:
                try:
                    with open(os.path.join(sdir(sid), transcript_name), encoding="utf-8") as f:
                        transcript_end = len(f.read())
                    _write_live_receipt(sid, txt, transcript_end, os.path.basename(webm))
                    request_live_diarization(sid)
                except Exception:
                    pass
            queue_spoken_lookup(sid, txt)
            if is_prep:
                _append_context_note(sid, "事前打ち合わせ音声：" + txt, "prep-audio")
                with open(os.path.join(sdir(sid), "transcript.txt"), "a", encoding="utf-8") as f:
                    f.write("【事前打ち合わせの背景情報（本会議の発言・決定ではない）】" + txt + "\n")
            request_analysis(sid)
            request_active_view_update(sid)
            request_detail(sid)
    finally:
        for p in (webm, wav, os.path.splitext(wav)[0] + "-overlap.wav"):
            try: os.remove(p)
            except Exception: pass

def clear_queue():
    """キューに残った未処理チャンクを破棄（一時ファイルも削除）。停止/新規/切替時に呼ぶ。"""
    dropped = 0
    while True:
        try:
            _sid, webm = chunk_q.get_nowait()
        except queue.Empty:
            break
        try: os.remove(webm)
        except Exception: pass
        chunk_q.task_done(); dropped += 1
    return dropped

def chunk_worker():
    """音声キューを1件ずつ処理。AI解析は別ワーカーなので、話し続けても文字起こしを優先できる。"""
    while True:
        sid, webm = chunk_q.get()
        try:
            if is_session(sid):
                process_chunk(sid, webm)
        except Exception as e:
            sys.stderr.write("chunk処理エラー: %r\n" % e)
        finally:
            chunk_q.task_done()

def request_analysis(sid):
    """同一会議の未処理解析を最大1件にまとめて投入する。"""
    if not is_session(sid):
        return
    with analysis_lock:
        if sid in analysis_pending:
            return
        analysis_pending.add(sid)
        analysis_q.put(sid)

def _canonical_view(view):
    view = str(view or "list")
    return "map" if view in ("tree", "radial", "cards") else view if view in ("list", "relation", "timeline") else "list"

def active_view(sid=None):
    """表示中かつ最近heartbeatが来たタブのビュー。複数タブは最新操作を優先する。"""
    now = time.time()
    with view_clients_lock:
        stale = [k for k, v in view_clients.items() if now - float(v.get("updated", 0)) > 18]
        for k in stale: view_clients.pop(k, None)
        rows = [v for v in view_clients.values() if v.get("visible") and (not sid or v.get("sid") == sid)]
    return max(rows, key=lambda x: x.get("updated", 0)).get("view", "list") if rows else "list"

def request_active_view_update(sid, force=False):
    """非表示のビューは解析しない。関係図は即時AI、時系列は文字起こしから更新済み。"""
    if not is_session(sid): return
    view = _canonical_view(active_view(sid))
    # timelineは文字起こしから直接更新。list/map/relationは表示中のビューを低頻度で更新
    # （2026-07-17: FASTへの統合はプロンプト肥大でhaikuが長考し全滅したため分離へ回帰）
    if view in ("timeline",): return
    key = (sid, view); interval = 30 if view == "list" else 45 if view == "relation" else 40
    with view_lock:
        if key in view_pending: return
        delay = 0 if force else max(0, interval - (time.time() - view_last_run.get(key, 0)))
        view_pending.add(key)
    def enqueue(): view_q.put(key)
    if delay: threading.Timer(delay, enqueue).start()
    else: enqueue()

def active_view_worker():
    """質問支援の即時レーンと分離し、見ているビューだけを小さく更新する。"""
    while True:
        sid, view = view_q.get(); key = (sid, view); ok = False
        try:
            # 即時質問の解析中はそちらを先に終わらせる（再投入はfinallyで一元的に行う）。
            if sid in analysis_pending:
                continue
            with open(os.path.join(sdir(sid), "transcript.txt"), encoding="utf-8") as f: transcript = f.read()
            off = min(view_applied.get(key, max(0, len(transcript) - 1400)), len(transcript))
            end = len(transcript); delta = transcript[off:end].strip()
            if not delta: ok = True; continue
            obj = _read_live_data(sid); title = read_meta(sid).get("title", "会議")
            if view == "list":
                index = json.dumps({k: obj.get(k, []) for k in ("agenda", "points", "decisions", "todos", "open")}, ensure_ascii=False)
                prompt = ACTIVE_LIST_PROMPT.format(title=title, index=index[-1800:], delta=delta[-1400:])
            elif view == "relation":
                # 「会話の関係」を開いている間はストーリー図を45秒毎に全面更新する
                # （従来は即時レーンの散発的な関係ペア頼みで、開いていても実質更新されなかった。2026-07-17 依頼者指摘）
                rel_index = json.dumps({"diagram": str(obj.get("diagram") or "")[:1600],
                                        "arc": str(obj.get("arc") or "")[:300]}, ensure_ascii=False)
                prompt = ACTIVE_REL_PROMPT.format(title=title, index=rel_index, delta=delta[-1400:])
            else:
                prompt = ACTIVE_MAP_PROMPT.format(title=title, index=_detail_index(obj)[-1800:], delta=delta[-1400:])
            with background_ai_lock:
                out = _ai_text(prompt, timeout=75, model=CLAUDE_MODEL, background=True)
            patch = _parse_live_patch(out, "ACTIVE-VIEW", sid)
            if patch is None: continue
            _merge_patch_to_disk(sid, patch, time.strftime("%H:%M:%S"), view)
            view_applied[key] = end; view_last_run[key] = time.time(); ok = True
        except Exception as e:
            sys.stderr.write("[ACTIVE-VIEW] %s/%s 失敗 %r\n" % (sid, view, e)); sys.stderr.flush()
        finally:
            if not ok and sid not in analysis_pending:
                view_last_run[key] = time.time()  # AI不調時も発話ごとの連打を防ぐ
            if ok or sid not in analysis_pending:
                with view_lock: view_pending.discard(key)
            else:
                # 解析中に持ち越した・失敗したキーは必ず再投入する。ここで再投入しないと
                # view_pending に残留し、以後このビューの更新依頼が全て弾かれて
                # 「見ているマップだけ永久に更新されない」（2026-07-16 実障害：
                # map処理がAIタイムアウトと即時解析の同時発生で停止した）
                threading.Timer(3, lambda k=key: view_q.put(k)).start()
            view_q.task_done()

def analysis_worker():
    """文字起こしと並行して最新差分を解析。実行中に増えた発話は完了直後の次回へまとめる。"""
    while True:
        sid = analysis_q.get()
        before_len = 0
        ok = False
        try:
            if is_session(sid):
                try:
                    with open(os.path.join(sdir(sid), "transcript.txt"), encoding="utf-8") as f:
                        before_len = len(f.read())
                except Exception:
                    pass
                ok = _claude_update(sid)
        except Exception as e:
            sys.stderr.write("議事解析エラー: %r\n" % e)
        finally:
            with analysis_lock:
                analysis_pending.discard(sid)
            analysis_q.task_done()
        if ok:
            analysis_failures.pop(sid, None)
        else:
            failures = analysis_failures.get(sid, 0) + 1
            analysis_failures[sid] = failures
            if failures < 3 and is_session(sid):
                threading.Timer(10, request_analysis, args=(sid,)).start()
        try:
            tp = os.path.join(sdir(sid), "transcript.txt")
            with open(tp, encoding="utf-8") as f:
                after_len = len(f.read())
            # 成功時は残りの小分け差分へ進む。失敗時は新発話があった場合だけ再試行する。
            if is_session(sid) and ((ok and after_len > applied.get(sid, 0)) or (not ok and after_len > before_len)):
                request_analysis(sid)
        except Exception:
            pass

def analysis_watchdog():
    """未反映文字があるのに解析が止まっていれば自動再開する。"""
    while True:
        time.sleep(30)
        sid = current_id
        if not is_session(sid):
            continue
        try:
            tp, dp, ap = (os.path.join(sdir(sid), n) for n in ("transcript.txt", "data.json", ".applied"))
            with open(tp, encoding="utf-8") as f:
                total = len(f.read())
            if sid not in applied:
                try:
                    with open(ap, encoding="utf-8") as f:
                        applied[sid] = int(f.read().strip())
                except Exception:
                    # 更新済みdataの方が新しければ既存全文は反映済みとみなす。
                    applied[sid] = total if os.path.isfile(dp) and os.path.getmtime(dp) >= os.path.getmtime(tp) else max(0, total - 8000)
            if total > applied.get(sid, 0) and sid not in analysis_pending:
                analysis_failures.pop(sid, None)
                request_analysis(sid)
        except Exception:
            pass

def recover_pending_chunks():
    """サービス再起動でキューから外れた一時webmを拾い直し、会議途中の音声を欠落させない。"""
    recovered = current_recovered = 0
    pending = glob.glob(os.path.join(WAVROOT, "*", "inc_*.webm")) + glob.glob(os.path.join(WAVROOT, "*", "prep_*.webm"))
    for path in sorted(pending):
        sid = os.path.basename(os.path.dirname(path))
        if is_session(sid):
            chunk_q.put((sid, path)); recovered += 1
            if sid == current_id:
                current_recovered += 1
    if recovered:
        sys.stderr.write("[RECOVER] 未処理音声 %d件をキューへ復元\n" % recovered)
        sys.stderr.flush()
    return current_recovered

# ---------- 1画面マインドマップ生成 ----------
def make_slides(theme="neutral", sid=None):
    sid = sid or current_id
    m = read_meta(sid)
    env = dict(_claude_env(), SDIR=sdir(sid), TITLE=m.get("title", _t("会議", "Meeting")),
               SLIDE_MODEL=SLIDE_MODEL, THEME=theme, LIVE_MTG_LANGUAGE=LANGUAGE)   # 画面で選択中のデザインをデッキにも反映
    cmd = ([sys.executable, "--live-mtg-helper", "make-mindmap.py"] if getattr(sys, "frozen", False)
           else [sys.executable, os.path.join(SCRIPT_DIR, "make-mindmap.py")])
    r = _run(cmd, env=env, capture_output=True, text=True, timeout=300)
    ok = r.returncode == 0 and os.path.isfile(os.path.join(sdir(sid), "mindmap.html"))
    if ok:
        sync_to_drive(sid)     # スライドを共有ドライブへ非同期コピー
        sync_to_project(sid)   # 清書済みなら背景フォルダの一式もスライド込みに更新（清書前は内部でスキップ）
    return ok, (r.stderr or r.stdout or "").strip()

def make_deck(theme="neutral", sid=None):
    """Slide Work正典のhybrid型で完成スライドデッキを生成する。"""
    sid = sid or current_id
    m = read_meta(sid)
    env = dict(_claude_env(), SDIR=sdir(sid), TITLE=m.get("title", _t("会議", "Meeting")),
               SLIDE_MODEL=SLIDE_MODEL, THEME=theme, AI_PROVIDER=AI_PROVIDER,
               CODEX_MODEL=CODEX_MODEL, LIVE_MTG_LANGUAGE=LANGUAGE)
    r = _run(["bash", os.path.join(SCRIPT_DIR, "make-slides.sh")],
             env=env, capture_output=True, text=True, timeout=420)
    ok = r.returncode == 0 and os.path.isfile(os.path.join(sdir(sid), "slides.html"))
    if ok:
        sync_to_drive(sid)     # デッキを共有ドライブへ非同期コピー
        sync_to_project(sid)   # 清書済みなら背景フォルダの一式もデッキ込みに更新
    return ok, (r.stderr or r.stdout or "").strip()

def _chrome_bin():
    mac_chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if os.path.isfile(mac_chrome):
        return mac_chrome
    return shutil.which("chromium") or shutil.which("google-chrome") or shutil.which("chrome")

MAP_PDF_VIEWS = ("radial", "relation", "topics", "timeline")
def _map_pdf_path(sid, view):
    return os.path.join(sdir(sid), "map-%s.pdf" % view)

def export_map_pdf(sid, view):
    """生成済み mindmap.html の指定ビューを、ヘッドレスChromeでPDFファイル化して保存する。
    ブラウザの印刷ダイアログを経由せず、成果物としてフォルダに残す（2026-07-16 依頼者指示）。"""
    if view not in MAP_PDF_VIEWS:
        view = "radial"
    chrome = _chrome_bin()
    if not chrome:
        return False, _t("PDF変換に使うChrome/Chromiumが見つかりません", "Chrome/Chromium not found for PDF export")
    if view in ("radial", "relation"):
        # スライド1枚様式（左下ロゴ＋うっすら背景＝デッキと同じデザイン言語。2026-07-16 依頼者指示）。
        # data.jsonから直接組むのでマップ成果物（mindmap.html）が無くても書き出せる
        m = read_meta(sid)
        env = dict(os.environ, SDIR=sdir(sid), TITLE=m.get("title", "会議"),
                   VIEW=view, LIVE_MTG_LANGUAGE=LANGUAGE)
        cmd = ([sys.executable, "--live-mtg-helper", "make-map-slide.py"] if getattr(sys, "frozen", False)
               else [sys.executable, os.path.join(SCRIPT_DIR, "make-map-slide.py")])
        r = _run(cmd, env=env, capture_output=True, text=True, timeout=60)
        if r.returncode != 0 or not os.path.isfile(os.path.join(sdir(sid), "map-slide-%s.html" % view)):
            return False, (r.stderr or r.stdout or "マップスライドの生成に失敗").strip()[:300]
        ok = _html_to_pdf("/map-slide.html?view=%s" % view, _map_pdf_path(sid, view))
        if ok:
            sync_to_drive(sid)
            sync_to_project(sid)
        return ok, ("" if ok else _t("PDF変換に失敗しました", "PDF export failed"))
    # topics/timeline はマップ成果物の該当ビューを印刷（旧テンプレートならURLビュー対応版へ再生成）
    map_html = os.path.join(sdir(sid), "mindmap.html")
    if not os.path.isfile(map_html):
        return False, _t("先にマップ（成果物）を作成してください", "Create the map artifact first")
    if "urlView" not in _read_text(map_html):
        m = read_meta(sid)
        env = dict(_claude_env(), SDIR=sdir(sid), TITLE=m.get("title", "会議"), THEME="neutral")
        cmd = ([sys.executable, "--live-mtg-helper", "make-mindmap.py"] if getattr(sys, "frozen", False)
               else [sys.executable, os.path.join(SCRIPT_DIR, "make-mindmap.py")])
        _run(cmd, env=env, capture_output=True, text=True, timeout=60)
        if "urlView" not in _read_text(map_html):
            return False, _t("マップ成果物の更新に失敗しました（作り直すを試してください）",
                             "Could not refresh the map artifact — try regenerating it")
    ok = _html_to_pdf("/slides.html?view=%s" % view, _map_pdf_path(sid, view))
    if ok:
        sync_to_drive(sid)
        sync_to_project(sid)
    return ok, ("" if ok else _t("PDF変換に失敗しました", "PDF export failed"))

def _html_to_pdf(path_with_query, out):
    """サーバ配信中のHTMLをヘッドレスChromeでPDFファイル化する。
    生成ページは自動更新ポーリングで「終わらない」ことがあるため、Chromeの自然終了を待たない：
    --timeout=15秒で強制印刷させ、PDFファイルのサイズが安定した時点でこちらから終了させる
    （--virtual-time-budget はポーリングを延々と早送りしてハング、自然終了待ちは90秒超のハング。2026-07-16 実測）"""
    chrome = _chrome_bin()
    if not chrome:
        return False
    tmp_out = out + ".tmp.pdf"
    try: os.remove(tmp_out)
    except FileNotFoundError: pass
    sep = "&" if "?" in path_with_query else "?"
    url = "http://127.0.0.1:%d%s%sts=%d" % (PORT, path_with_query, sep, int(time.time()))
    profile = tempfile.mkdtemp(prefix="livemtg-pdf-")   # 起動中のChromeとプロファイルを共有しない
    proc = subprocess.Popen([chrome, "--headless=new", "--disable-gpu", "--user-data-dir=%s" % profile,
                             "--window-size=1440,810", "--timeout=15000", "--no-pdf-header-footer",
                             "--print-to-pdf=%s" % tmp_out, url],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=(os.name != "nt"))
    ok = False
    try:
        deadline = time.time() + 60
        last_size = -1
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            size = os.path.getsize(tmp_out) if os.path.isfile(tmp_out) else -1
            if size > 5000 and size == last_size:
                break   # 書き出し完了（サイズ安定）。Chromeの終了は待たない
            last_size = size
            time.sleep(1.0)
        ok = os.path.isfile(tmp_out) and os.path.getsize(tmp_out) > 5000
    finally:
        _kill_process_tree(proc)
        shutil.rmtree(profile, ignore_errors=True)
    if ok:
        os.replace(tmp_out, out)
    else:
        try: os.remove(tmp_out)
        except FileNotFoundError: pass
    return ok

def export_minutes_pdf(sid):
    """議事録をSlide Workデザインのデッキに組んでPDFファイル化する。
    デッキ組みはAI不要の決定論変換（make-minutes-deck.py）＝全文保証・即時・ハルシネーション無し。"""
    m = read_meta(sid)
    env = dict(os.environ, SDIR=sdir(sid), TITLE=m.get("title", "会議"), LIVE_MTG_LANGUAGE=LANGUAGE)
    cmd = ([sys.executable, "--live-mtg-helper", "make-minutes-deck.py"] if getattr(sys, "frozen", False)
           else [sys.executable, os.path.join(SCRIPT_DIR, "make-minutes-deck.py")])
    r = _run(cmd, env=env, capture_output=True, text=True, timeout=60)
    if r.returncode != 0 or not os.path.isfile(os.path.join(sdir(sid), "minutes-deck.html")):
        return False, (r.stderr or r.stdout or "議事録デッキの生成に失敗").strip()[:300]
    ok = _html_to_pdf("/minutes-deck.html", os.path.join(sdir(sid), "minutes.pdf"))
    if ok:
        sync_to_drive(sid)
        sync_to_project(sid)
    return ok, ("" if ok else _t("PDF変換に失敗しました", "PDF export failed"))

def make_learn_deck(sid=None):
    """保存済み学びレポート（learnings.md）をSlide Work正典デッキへ変換する。"""
    sid = sid or current_id
    m = read_meta(sid)
    env = dict(_claude_env(), SDIR=sdir(sid), TITLE=m.get("title", _t("会議", "Meeting")),
               SLIDE_MODEL=SLIDE_MODEL, AI_PROVIDER=AI_PROVIDER, CODEX_MODEL=CODEX_MODEL,
               LIVE_MTG_LANGUAGE=LANGUAGE, GOAL=(m.get("goal") or ""), STANCE=(m.get("stance") or ""))
    r = _run(["bash", os.path.join(SCRIPT_DIR, "make-learn-slides.sh")],
             env=env, capture_output=True, text=True, timeout=420)
    ok = r.returncode == 0 and os.path.isfile(os.path.join(sdir(sid), "learn-slides.html"))
    if ok:
        # 議事録と同じく「そのまま保存できる形式」で残す（2026-07-20 依頼者要望）：
        # HTMLに加えPDFも書き出し、会議フォルダ保存＋案件フォルダ同期（学びと次の一手.pdf）
        try:
            _html_to_pdf("/learn-slides.html", os.path.join(sdir(sid), "learn-slides.pdf"))
        except Exception as e:
            sys.stderr.write("[LEARN-SLIDES] %s PDF変換失敗 %r\n" % (sid, e)); sys.stderr.flush()
        sync_to_drive(sid)
        sync_to_project(sid)
    return ok, (r.stderr or r.stdout or "").strip()

mindmap_refreshing = set()
def refresh_mindmap(sid):
    """既にマップを開いている会議のdata.jsonが更新されたら、非同期でHTMLも更新。"""
    if sid in mindmap_refreshing or not is_session(sid):
        return
    mindmap_refreshing.add(sid)
    def job():
        try:
            m = read_meta(sid)
            env = dict(_claude_env(), SDIR=sdir(sid), TITLE=m.get("title", "会議"),
                       THEME="neutral")
            cmd = ([sys.executable, "--live-mtg-helper", "make-mindmap.py"] if getattr(sys, "frozen", False)
                   else [sys.executable, os.path.join(SCRIPT_DIR, "make-mindmap.py")])
            subprocess.run(cmd,
                           env=env, capture_output=True, text=True, timeout=45)
        except Exception as e:
            sys.stderr.write("[MINDMAP] ライブ更新失敗 %r\n" % e)
        finally:
            mindmap_refreshing.discard(sid)
    threading.Thread(target=job, daemon=True).start()

# ---------- 会議後の一括清書（finalize）----------
# ライブは「発話の切れ目で区切った可変長チャンクを逐次」処理するため、境界で文脈が切れる。
# finalizeは保存した原本音声を全て結合し、分断なしの1本として whisper→claude で整理し直す＝最高精度の清書版。
def _audio_signature(sid):
    files = sorted(glob.glob(os.path.join(sdir(sid), "audio", "*.webm")))
    return [{"name": os.path.basename(p), "size": os.path.getsize(p), "mtime": int(os.path.getmtime(p))}
            for p in files if os.path.isfile(p)]

def _concat_meeting_audio(sid, stem="_full"):
    """保存済みチャンクを時系列で1本の16kHz mono WAVへ連結する。"""
    d = sdir(sid); webms = sorted(glob.glob(os.path.join(d, "audio", "*.webm")))
    if not webms:
        return "", "", "保存された音声がありません"
    listf, wav = os.path.join(d, stem + ".txt"), os.path.join(d, stem + ".wav")
    with open(listf, "w", encoding="utf-8") as f:
        for path in webms:
            f.write("file '%s'\n" % path.replace("'", "'\\''"))
    try:
        _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
              "-i", listf, "-ar", "16000", "-ac", "1", wav])
    except Exception:
        try: os.remove(listf)
        except Exception: pass
        raise
    if not os.path.isfile(wav):
        return "", listf, "音声の結合に失敗しました"
    return wav, listf, ""

def _diarization_path(sid):
    return os.path.join(sdir(sid), "diarization.json")

def _load_diarization(sid):
    try:
        with open(_diarization_path(sid), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def _live_diarization_path(sid):
    return os.path.join(sdir(sid), "live-diarization.json")

def _load_live_diarization(sid, compact=False):
    try:
        with open(_live_diarization_path(sid), encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict): return {}
        if compact:
            return {k: data.get(k) for k in ("status", "speakers", "updated", "message", "latencySeconds",
                                               "windowSeconds", "audioThrough") if k in data}
        return data
    except Exception:
        return {}

def _diarizer_python():
    exe = shutil.which("whispermlx")
    if not exe:
        return ""
    try:
        first = open(exe, encoding="utf-8").readline().strip()
        if first.startswith("#!"):
            command = shlex.split(first[2:])
            if command and os.path.isfile(command[0]): return command[0]
    except Exception:
        pass
    return ""

def _diarizer_worker_script():
    return os.path.join(SCRIPT_DIR, "scripts", "live-diarize-worker.py")

def _call_diarizer(wav, max_speakers=8):
    """常駐ワーカーへ音声パスだけ渡す。トークンはワーカーがOS資格情報から読む。"""
    global live_diarizer_process
    request = {"id": "%d" % time.time_ns(), "command": "diarize", "wav": wav,
               "maxSpeakers": int(max_speakers)}
    with live_diarizer_io_lock:
        # ロック内で起動すると再入するため、ここでは直接起動する。
        if not live_diarizer_process or live_diarizer_process.poll() is not None:
            python = _diarizer_python(); script = _diarizer_worker_script()
            if not python or not os.path.isfile(script): raise RuntimeError("話者分離ワーカーが見つかりません")
            env = dict(os.environ); env["LIVE_MTG_HOME"] = RUN
            live_diarizer_process = subprocess.Popen(
                [python, script], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sys.stderr,
                text=True, bufsize=1, env=env,
            )
        process = live_diarizer_process
        try:
            process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n"); process.stdin.flush()
        except Exception:
            _kill_process_tree(process); live_diarizer_process = None
            raise RuntimeError("話者分離ワーカーへ接続できません")
        # モデル取得や長時間音声が固まっても専用レーンを永久に占有しない。
        response_q = queue.Queue(maxsize=1)
        reader = threading.Thread(target=lambda: response_q.put(process.stdout.readline()), daemon=True)
        reader.start()
        try:
            line = response_q.get(timeout=600)
        except queue.Empty:
            _kill_process_tree(process); live_diarizer_process = None
            raise RuntimeError("話者分離が10分以内に完了しませんでした")
        if not line:
            live_diarizer_process = None
            raise RuntimeError("話者分離ワーカーが終了しました")
    response = json.loads(line)
    if not response.get("ok"):
        raise RuntimeError(response.get("error") or "話者分離に失敗しました")
    return response.get("turns") or []

def _audio_duration(path):
    """ffprobe結果をmtime/size単位でキャッシュし、ローリング窓の再計算を軽くする。"""
    try:
        signature = (os.path.getsize(path), os.path.getmtime(path))
        cached = audio_duration_cache.get(path)
        if cached and cached[0] == signature: return cached[1]
        r = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                  "-of", "default=noprint_wrappers=1:nokey=1", path], timeout=20)
        duration = max(0.0, float((r.stdout or "0").strip()))
        audio_duration_cache[path] = (signature, duration)
        return duration
    except Exception:
        return 0.0

def _concat_audio_files(files, sid, stem):
    listf, wav = os.path.join(sdir(sid), stem + ".txt"), os.path.join(sdir(sid), stem + ".wav")
    with open(listf, "w", encoding="utf-8") as f:
        for path in files: f.write("file '%s'\n" % path.replace("'", "'\\''"))
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
          "-i", listf, "-ar", "16000", "-ac", "1", wav])
    return wav, listf

def _rolling_diarization_audio(sid, window_seconds=90):
    """会議全体ではなく直近90秒だけを結合。累積時間を保ち、前回窓と重ねられるようにする。"""
    files = sorted(glob.glob(os.path.join(sdir(sid), "audio", "*.webm")))
    rows, cursor = [], 0.0
    for path in files:
        duration = _audio_duration(path)
        if duration <= 0: continue
        rows.append({"path": path, "name": os.path.basename(path), "start": cursor, "end": cursor + duration})
        cursor += duration
    if not rows: return "", "", 0.0, [], cursor
    cutoff = max(0.0, cursor - float(window_seconds)); start_index = 0
    for i, row in enumerate(rows):
        if row["end"] > cutoff:
            start_index = max(0, i - 1)  # 境界の話者を繋ぐため1チャンク重ねる
            break
    selected = rows[start_index:]
    wav, listf = _concat_audio_files([x["path"] for x in selected], sid, "_live-diarize")
    return wav, listf, selected[0]["start"], selected, cursor

def _dominant_speaker(span, turns, threshold=.62):
    """1チャンクに複数人いれば、無理に1人の発話としない。"""
    scores = {}
    for turn in turns:
        overlap = _overlap_seconds(span, turn)
        if overlap > 0: scores[turn["speaker"]] = scores.get(turn["speaker"], 0) + overlap
    total = sum(scores.values())
    if not scores or total <= 0: return "", 0.0
    speaker = max(scores, key=scores.get); confidence = scores[speaker] / total
    return (speaker if confidence >= threshold else ""), round(confidence, 3)

def _overlap_seconds(a, b):
    return max(0.0, min(float(a.get("end", 0)), float(b.get("end", 0))) -
               max(float(a.get("start", 0)), float(b.get("start", 0))))

def _stable_live_speakers(sid, raw_turns):
    """全音声の再解析結果を前回時間軸へ重ね、Speaker A/Bの入れ替わりを防ぐ。"""
    previous = _load_live_diarization(sid).get("turns") or []
    raw_ids = sorted({str(x.get("speaker")) for x in raw_turns},
                     key=lambda speaker: min((float(x.get("start", 0)) for x in raw_turns if str(x.get("speaker")) == speaker), default=0))
    old_ids = sorted({str(x.get("speaker")) for x in previous})
    candidates = []
    for raw in raw_ids:
        for old in old_ids:
            score = sum(_overlap_seconds(n, p) for n in raw_turns for p in previous
                        if str(n.get("speaker")) == raw and str(p.get("speaker")) == old)
            if score > 0: candidates.append((score, raw, old))
    mapping, used = {}, set()
    for _, raw, old in sorted(candidates, reverse=True):
        if raw not in mapping and old not in used:
            mapping[raw] = old; used.add(old)
    # 今回窓に現れなかった既存IDを、新規話者へ再利用しない。
    # 長い無発話後は同一人を新IDにする方が、別人を誤って同一人にするより安全。
    reserved = set(old_ids)
    next_index = 0
    for raw in raw_ids:
        if raw in mapping: continue
        while "SPEAKER_%02d" % next_index in used | reserved: next_index += 1
        stable = "SPEAKER_%02d" % next_index
        mapping[raw] = stable; used.add(stable); next_index += 1
    turns = [{"speaker": mapping.get(str(x.get("speaker")), str(x.get("speaker"))),
              "start": float(x.get("start", 0)), "end": float(x.get("end", 0))} for x in raw_turns]
    info = {}
    for turn in turns:
        row = info.setdefault(turn["speaker"], {"id": turn["speaker"], "seconds": 0})
        row["seconds"] += max(0, turn["end"] - turn["start"])
    speakers = sorted(info.values(), key=lambda x: x["id"])
    for row in speakers: row["seconds"] = round(row["seconds"], 1)
    return speakers, turns

def request_live_diarization(sid):
    if not is_session(sid) or not shutil.which("whispermlx") or not _hf_token_configured():
        return
    with live_diarization_lock:
        if sid in live_diarization_pending:
            live_diarization_dirty.add(sid)
            return
        live_diarization_pending.add(sid)
        elapsed = time.time() - live_diarization_last.get(sid, 0)
    # 処理量はローリング窓で一定。発話チャンクと同じ15秒ペースを上限にする。
    delay = max(0, 15 - elapsed)
    def enqueue(): live_diarization_q.put(sid)
    if delay:
        timer = threading.Timer(delay, enqueue); timer.daemon = True; timer.start()
    else: enqueue()

def live_diarization_worker():
    while True:
        sid = live_diarization_q.get(); wav = listf = ""; started = time.time()
        try:
            if not is_session(sid): continue
            current = _load_live_diarization(sid)
            current.update({"status": "processing", "updated": int(time.time())})
            with open(_live_diarization_path(sid), "w", encoding="utf-8") as f:
                json.dump(current, f, ensure_ascii=False, indent=2)
            wav, listf, window_start, spans, audio_through = _rolling_diarization_audio(sid, 90)
            if not wav: raise RuntimeError("保存された音声がありません")
            raw_turns = _call_diarizer(wav)
            # ワーカーは窓内0秒起点なので、会議全体の累積時間へ戻す。
            absolute_turns = [dict(x, start=float(x.get("start", 0)) + window_start,
                                   end=float(x.get("end", 0)) + window_start) for x in raw_turns]
            _current_speakers, stable_window = _stable_live_speakers(sid, absolute_turns)
            previous = _load_live_diarization(sid).get("turns") or []
            history = [x for x in previous if float(x.get("end", 0)) <= window_start + .25]
            turns = history + stable_window
            # 無制限に肥大化させず、最近30分の話者履歴を保持。
            turns = [x for x in turns if float(x.get("end", 0)) >= max(0, audio_through - 1800)]
            info = {}
            for turn in turns:
                row = info.setdefault(turn["speaker"], {"id": turn["speaker"], "seconds": 0})
                row["seconds"] += max(0, float(turn["end"]) - float(turn["start"]))
            speakers = sorted(info.values(), key=lambda x: x["id"])
            for row in speakers: row["seconds"] = round(row["seconds"], 1)
            audio_speakers, audio_confidence = {}, {}
            for span in spans:
                speaker, confidence = _dominant_speaker(span, stable_window)
                audio_confidence[span["name"]] = confidence
                if speaker: audio_speakers[span["name"]] = speaker
            # 同じ音声名を持つ文字起こしへ、暂定話者を後から差し戻す。
            with data_write_lock:
                obj = _read_live_data(sid)
                for entry in (obj.get("timeline") or []):
                    speaker = audio_speakers.get(str(entry.get("audio", "")))
                    if speaker: entry["speaker"] = speaker; entry["who"] = _speaker_display(speaker)
                receipt = obj.get("liveReceipt") if isinstance(obj.get("liveReceipt"), dict) else {}
                speaker = audio_speakers.get(str(receipt.get("audio", "")))
                if speaker: receipt["speaker"] = speaker; receipt["who"] = _speaker_display(speaker); obj["liveReceipt"] = receipt
                vu = obj.get("_viewUpdatedAt") if isinstance(obj.get("_viewUpdatedAt"), dict) else {}
                vu["timeline"] = int(time.time()); obj["_viewUpdatedAt"] = vu
                path = os.path.join(sdir(sid), "data.json"); tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f: json.dump(obj, f, ensure_ascii=False, indent=2)
                os.replace(tmp, path)
            data = {"status": "ready", "speakers": speakers, "turns": turns,
                    "audioSpeakers": audio_speakers, "audioSpeakerConfidence": audio_confidence,
                    "windowSeconds": 90, "audioThrough": round(audio_through, 1),
                    "signature": _audio_signature(sid), "updated": int(time.time()),
                    "latencySeconds": round(time.time() - started, 1)}
            with open(_live_diarization_path(sid), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as error:
            sys.stderr.write("[LIVE-DIARIZE] sid=%s error=%r\n" % (sid, error)); sys.stderr.flush()
            with open(_live_diarization_path(sid), "w", encoding="utf-8") as f:
                json.dump({"status": "error", "message": str(error)[:300], "updated": int(time.time())},
                          f, ensure_ascii=False, indent=2)
        finally:
            for path in (wav, listf):
                try:
                    if path: os.remove(path)
                except Exception: pass
            live_diarization_last[sid] = time.time()
            rerun = False
            with live_diarization_lock:
                live_diarization_pending.discard(sid)
                if sid in live_diarization_dirty:
                    live_diarization_dirty.discard(sid); rerun = True
            if rerun: request_live_diarization(sid)
            live_diarization_q.task_done()

def _speaker_payload(result, sid=None):
    """WhisperX互換JSONを、清書確認UI用の安定した匿名話者と発言例へ変換する。"""
    turns, current = [], None
    for seg in (result.get("segments") or []):
        if not isinstance(seg, dict):
            continue
        speaker = str(seg.get("speaker") or "").strip()
        if not speaker:
            words = [w for w in (seg.get("words") or []) if isinstance(w, dict) and w.get("speaker")]
            speaker = str(words[0].get("speaker")) if words else "SPEAKER_UNKNOWN"
        text = re.sub(r"\s+", " ", str(seg.get("text") or "")).strip()
        # ライブ本線と同じ基準で、initial_promptの漏れ出し・定型ハルシネーションを除外する
        # （whisperは無音区間でヒント文を発話として吐く。発言例と清書用transcriptの両方を守る）
        text = _clean(text, sid)
        if not text:
            continue
        start, end = float(seg.get("start") or 0), float(seg.get("end") or seg.get("start") or 0)
        if current and current["speaker"] == speaker and start - current["end"] <= 2.0:
            current["text"] = (current["text"] + " " + text).strip(); current["end"] = end
        else:
            current = {"speaker": speaker, "start": start, "end": end, "text": text}; turns.append(current)
    speakers = _speakers_from_turns(turns)
    transcript = "\n".join("[%s] %s" % (t["speaker"], t["text"]) for t in turns)
    return speakers, turns, transcript

def _speakers_from_turns(turns):
    info = {}
    for turn in turns:
        speaker = turn["speaker"]
        row = info.setdefault(speaker, {"id": speaker, "seconds": 0, "examples": []})
        row["seconds"] += max(0, turn["end"] - turn["start"])
        if len(row["examples"]) < 2 and len(turn["text"]) >= 8:
            # 発言例は「誰の声か思い出す」ためのもの。読む負担を最小にするため2件×40字まで
            snippet = turn["text"][:40] + ("…" if len(turn["text"]) > 40 else "")
            if snippet not in row["examples"]:
                row["examples"].append(snippet)
    return sorted(info.values(), key=lambda x: (-x["seconds"], x["id"]))

def _sanitize_diarization(sid, data):
    """旧バージョンが保存した話者分離キャッシュへ、現行の浄化ルール（ヒント漏れ除去・
    発言例80字・重複なし）を適用し直す。whisperの再実行なしで直せる部分だけ直し、
    変化があればキャッシュも上書きする。"""
    turns_in = [t for t in (data.get("turns") or []) if isinstance(t, dict) and t.get("speaker")]
    if not turns_in:
        return data
    kept, changed = [], False
    for t in turns_in:
        text = _clean(re.sub(r"\s+", " ", str(t.get("text") or "")).strip(), sid)
        if not text:
            changed = True
            continue
        if text != t.get("text"):
            changed = True
        kept.append({**t, "text": text})
    speakers = _speakers_from_turns(kept)
    if speakers != data.get("speakers"):
        changed = True
    if not changed:
        return data
    data = {**data, "speakers": speakers, "turns": kept,
            "transcript": "\n".join("[%s] %s" % (t["speaker"], t["text"]) for t in kept)}
    try:
        with open(_diarization_path(sid), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return data

def prepare_diarization(sid, regen=False):
    """清書前に全音声を話者分離する。利用不可でも清書本体は止めない。"""
    signature = _audio_signature(sid)
    cached = _load_diarization(sid)
    if not regen and cached.get("signature") == signature and cached.get("speakers"):
        return _sanitize_diarization(sid, cached)   # 旧版キャッシュにも現行の浄化ルールを適用
    if not shutil.which("whispermlx"):
        return {"status": "setup", "installed": False, "tokenConfigured": _hf_token_configured(), "speakers": []}
    if not _hf_token_configured():
        return {"status": "setup", "installed": True, "tokenConfigured": False, "speakers": []}
    wav = listf = ""; work = tempfile.mkdtemp(prefix="livemtg-diarize-")
    try:
        wav, listf, err = _concat_meeting_audio(sid, "_diarize")
        if err:
            return {"status": "error", "message": err, "speakers": []}
        diarized_turns = _call_diarizer(wav)
        # ASRと話者分離を分けることでHFトークンをCLI引数へ載せない。
        cmd = ["whispermlx", wav, "--model", _asr_model(), "--language", _asr_language(sid),
               "--output_format", "json", "--output_dir", work,
               "--no_align", "--verbose", "False", "--print_progress", "False",
               "--initial_prompt", _asr_hint(sid)]
        _run(cmd, timeout=900)
        outputs = sorted(glob.glob(os.path.join(work, "*.json")))
        if not outputs:
            raise RuntimeError("whispermlxのJSON出力が見つかりません")
        with open(outputs[0], encoding="utf-8") as f:
            raw = json.load(f)
        # ASRセグメントへ、時間の重なりが最大の匿名話者を付与する。
        raw_ids = sorted({str(x.get("speaker")) for x in diarized_turns},
                         key=lambda speaker: min((float(x.get("start", 0)) for x in diarized_turns
                                                  if str(x.get("speaker")) == speaker), default=0))
        normalized = {speaker: "SPEAKER_%02d" % i for i, speaker in enumerate(raw_ids)}
        for seg in (raw.get("segments") or []):
            if not isinstance(seg, dict): continue
            probe = {"start": float(seg.get("start") or 0), "end": float(seg.get("end") or seg.get("start") or 0)}
            best = max(diarized_turns, key=lambda turn: _overlap_seconds(probe, turn), default=None)
            if best and _overlap_seconds(probe, best) > 0:
                seg["speaker"] = normalized.get(str(best.get("speaker")), "SPEAKER_UNKNOWN")
        speakers, turns, transcript = _speaker_payload(raw, sid)
        data = {"status": "ready", "signature": signature, "speakers": speakers,
                "turns": turns, "transcript": transcript, "generated": time.strftime("%Y-%m-%d %H:%M")}
        with open(_diarization_path(sid), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return data
    except JobCancelled:
        raise
    except Exception as e:
        sys.stderr.write("[DIARIZE] sid=%s error=%r\n" % (sid, e)); sys.stderr.flush()
        return {"status": "error", "message": str(e)[:300], "speakers": []}
    finally:
        for p in (wav, listf):
            try:
                if p: os.remove(p)
            except Exception: pass
        shutil.rmtree(work, ignore_errors=True)

FINAL_PROMPT = """あなたは会議「{title}」の議事録を清書する編集者です。
以下は会議の**全文文字起こし**（時系列・whisperの自動認識のため誤変換あり）です。
全体を通して読み、会議後の視点で**整合の取れた最終議事録**を、**有効なJSONのみ**で出力してください。
前置き・説明・コードフェンス(```)は禁止。JSONオブジェクトだけを返す。

スキーマ:
{{
  "updated": "清書",
  "summary": "会議全体の要旨を2〜3文で",
  "agenda": ["扱った議題を体言止めで（時系列）"],
  "points": ["重要な論点・意見・主張"],
  "decisions": ["合意・決定したこと"],
  "todos": [{{"who":"担当者名（不明なら未定）","what":"やること","due":"期限があれば"}}],
  "open": ["未解決・保留・要確認"],
  "diagram": "会議全体の関係図Mermaid（flowchart LR）。人物・組織・サービス・主要論点・決定を10〜16ノード、subgraphでグループ化し全エッジに関係ラベルを付ける。無ければ空文字",
  "speakers": ["参加者名（呼びかけ・文脈から推定）"],
  "log": [{{"who":"発言者名（推定）","text":"要点となる発言"}}]
}}

清書のルール（ライブ版との違い）:
- **全文を俯瞰**し、前半の不明点が後半で判明していれば反映する。会議中の**撤回・変更・結論**を正しく最終状態に統合する。
- whisperの誤変換は文脈で補正（固有名詞・数字に注意）。ただし**意味の取れない箇所は憶測で埋めず**、必要なら open に「(一部聞き取り不能)」と残す（"それっぽい嘘"を作らない）。
- 各配列は重要な順。log は会議の骨子がわかる主要発言を最大20件。
- diagram は清書の総仕上げの「議論のストーリーライン図」：主要な話の流れを話題ごとに独立した流れ（起点→展開→帰結）として2〜4本描く。ノード＝案・状態・数字・結論、全エッジに論理のラベル（読むだけで論旨が再生できる言葉）、実線＝合意・推進／点線＝懸念・未確定、合計10〜16ノード、flowchart LR。人物の静的な列挙はしない。有効なMermaid（日本語ラベルは[]で囲む）。無ければ""。日本語で。
- **【正しい固有名詞・事実】は絶対の正とする**：会議の目的・参加者・人物名・会社名・用語・事象はここに書かれた内容を最優先で採用し、文字起こしの類似音・誤変換・読み違えはすべてこれに合わせて補正する。**文字起こしがこれと矛盾して見える場合は、文字起こし側の誤認識とみなす**。
- **わからない所をストーリーで繋がない**：確認事実からも文字起こしからも判断できない箇所は、無理に文脈を創作せず open に「(不明瞭)」と残す。
- **話者名を創作しない**：SPEAKER_00等は音声から分離した匿名IDである。【正しい固有名詞・事実】に対応関係が明記されたIDだけ実名へ置き換え、それ以外は匿名IDのまま残す。「話者名は○○」「話題は○○」という単独行や、文脈だけの推測から実名を確定しない。

【依頼者が確認した正しい固有名詞・事実（最優先で使う）】
{hints_block}

【全文文字起こし】
{transcript}"""

def _apply_speaker_map(value, speaker_map):
    """確定した匿名話者→実名対応を清書JSON全体へ決定的に反映する。"""
    mapping = {str(k): str(v).strip() for k, v in (speaker_map or {}).items() if str(v).strip()}
    if isinstance(value, dict):
        return {k: _apply_speaker_map(v, mapping) for k, v in value.items()}
    if isinstance(value, list):
        return [_apply_speaker_map(v, mapping) for v in value]
    if isinstance(value, str):
        for old, new in mapping.items():
            value = value.replace(old, new)
    return value

def finalize_meeting(sid, hints="", speaker_map=None):
    """会議の全原本音声を結合→whisper一括→claudeで全体整理した"清書版"を作る。戻り値 (ok, msg)。"""
    d = sdir(sid)
    full_wav = listf = ""
    try:
        full_wav, listf, concat_error = _concat_meeting_audio(sid, "_full")
    except JobCancelled:
        for p in (listf, full_wav):
            try: os.remove(p)
            except Exception: pass
        raise
    except Exception as e:
        return False, "音声の結合に失敗しました：%s" % str(e)[:200]
    if concat_error:
        try: os.remove(listf)
        except Exception: pass
        return False, concat_error
    try:
        # 清書前に話者分離済みなら匿名ラベル付き全文を使う。無ければ従来ASRへ安全に戻る。
        diarization = _load_diarization(sid)
        txt = str(diarization.get("transcript") or "").strip()
        if not txt:
            txt = _clean(_whisper(full_wav, sid), sid)
        with open(os.path.join(d, "transcript-full.txt"), "w", encoding="utf-8") as f:
            f.write(txt)
        if not txt.strip():
            return False, "文字起こしが空でした（音声が無音か認識できませんでした）"
        # 一括claude整理（品質優先モデル）
        fmeta = read_meta(sid)
        title = fmeta.get("title", "会議")
        hints_block = hints.strip() if (hints or "").strip() else "（特に指定なし。文字起こしから慎重に判断し、不確かな固有名詞は断定しない）"
        live_notes = _load_live_notes(sid)
        corrections = "\n".join("- " + (n.get("text") or "").strip()
                                for n in live_notes if (n.get("text") or "").strip())
        if corrections:
            hints_block = ("【会議中に依頼者が追加した補足・訂正（最優先）】\n"
                           + corrections + "\n\n" + hints_block)
        prof = _profile_text()
        stance = (fmeta.get("stance") or "").strip()
        if prof or stance:
            hints_block = ("（依頼主＝録音している本人のプロフィール。話者推定・敬称・立場の判断に使う）\n"
                           + (prof or "（プロフィール未設定）")
                           + (("\nこの会議での立場：" + stance) if stance else "")
                           + "\n\n" + hints_block)
        if speaker_map:
            mappings = "\n".join("- %s = %s" % (k, v) for k, v in speaker_map.items() if str(v).strip())
            if mappings:
                hints_block = ("【依頼者が確定した話者対応（絶対にこの表記へ置換）】\n"
                               + mappings + "\n\n" + hints_block)
        prompt = FINAL_PROMPT.format(title=title, hints_block=hints_block, transcript=txt)
        out = _ai_text(prompt, timeout=420, model=SLIDE_MODEL)
        out = re.sub(r"^```json\s*|^```\s*|```\s*$", "", out, flags=re.M).strip()
        m = re.search(r"\{.*\}", out, re.S)
        if m: out = m.group(0)
        obj = _apply_speaker_map(json.loads(out), speaker_map)  # 確定対応はAI任せにせずサーバーでも置換
        obj["updated"] = "清書"        # updatedは短く固定（要旨はsummaryに入る。ヘッダーに長文が出るのを防ぐ）
        out = json.dumps(obj, ensure_ascii=False, indent=2)
    except JobCancelled:
        return False, "__cancelled__"
    except json.JSONDecodeError:
        return False, "整理結果がJSONになりませんでした（もう一度お試しください）"
    except Exception as e:
        return False, "清書処理に失敗：%r" % e
    finally:
        for p in (listf, full_wav):
            try: os.remove(p)
            except Exception: pass
    # ライブ版を退避し、清書版を final.json とし、画面表示(data.json)も清書版に差し替え
    try:
        live = os.path.join(d, "data.json")
        if os.path.isfile(live):
            shutil.copy2(live, os.path.join(d, "data-live.json"))
    except Exception:
        pass
    with open(os.path.join(d, "final.json"), "w", encoding="utf-8") as f:
        f.write(out)
    with open(os.path.join(d, "data.json"), "w", encoding="utf-8") as f:
        f.write(out)
    return True, "ok"

# ---------- AIサポート：Web検索で裏取り（オンデマンド。バブルのボタンから呼ばれる）----------
VERIFY_PROMPT = """次の問いについて**Web検索で事実確認**し、会議の参考になるよう簡潔に(3〜4文)日本語で答えてください。
- 確かな情報源に基づき、断定できない点や情報が見つからない点は「要確認」と明記する。憶測で埋めない。
- 参照したURLを2件程度、末尾に「参照:」として併記する。
問い: {q}"""

def assist_verify(q):
    """疑問をWeb検索付きでclaudeに投げ、出典入りの回答テキストを返す。戻り値 (ok, answer)。
    ★ morning-routine.sh と同じ叩き方に準拠:
      - script(1) で pty をかませる（no-tty環境の起動hung対策。これが無いとsubprocessで固まる）
      - --permission-mode bypassPermissions（WebSearch等ツールの許可）
      - プロンプトは -p 引数で渡し、stdin は /dev/null（scriptはstdinがsocketだと失敗するため）"""
    prompt = VERIFY_PROMPT.format(q=q)
    if AI_PROVIDER == "codex":
        try:
            ans = _ai_text(prompt, timeout=150, cwd=tempfile.gettempdir(), web=True)
            return (True, ans) if ans else (False, "検索結果が空でした")
        except TimeoutError:
            return False, "検索がタイムアウトしました（もう一度お試しください）"
        except Exception as e:
            return False, "検索に失敗：%r" % e
    if os.name == "nt":
        try:
            ans = _ai_text(prompt, timeout=150, cwd=tempfile.gettempdir(), web=True)
            return (True, ans) if ans else (False, "検索結果が空でした")
        except TimeoutError:
            return False, "検索がタイムアウトしました（もう一度お試しください）"
        except Exception as e:
            return False, "検索に失敗：%r" % e
    cmd = ["script", "-q", "/dev/null", "claude", "-p", prompt,
           "--model", ASSIST_MODEL, "--permission-mode", "bypassPermissions", "--output-format", "text"]
    env = _claude_env()   # 自動更新無効化＋.local/binのclaude（全claude呼び出し共通）
    # ★ 出力はファイルに逃がす。scriptのpty出力をパイプ(capture_output)で受けると
    #    WebSearchの多い出力でバッファが詰まりデッドロック→ハングするため（実測で確定）。
    tf = tempfile.NamedTemporaryFile("w", suffix=".out", delete=False, dir=tempfile.gettempdir())
    tmp = tf.name; tf.close()
    try:
        with open(tmp, "w") as fout:
            # cwdはローカル(/tmp)。Google Drive配下だとCLAUDE.md読みでTCC未認可ハングするため
            subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=fout, stderr=subprocess.STDOUT,
                           timeout=150, cwd=tempfile.gettempdir(), env=env)
        with open(tmp, encoding="utf-8", errors="ignore") as fin:
            raw = fin.read()
    except subprocess.TimeoutExpired:
        return False, "検索がタイムアウトしました（もう一度お試しください）"
    except Exception as e:
        return False, "検索に失敗：%r" % e
    finally:
        try: os.remove(tmp)
        except Exception: pass
    # scriptのpty経由出力から制御シーケンス(OSC/CSI/制御文字)を除去して回答本文だけ取り出す
    ans = raw or ''
    ans = re.sub(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)', '', ans)   # OSC (\x1b]...BEL/ST)
    ans = re.sub(r'\x1b[\[\(][0-9;?<>=]*[a-zA-Z]', '', ans)       # CSI等
    ans = re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', ans)           # 制御文字(改行\x0aは残す)
    ans = re.sub(r'^\s*\^D', '', ans)                            # ptyが先頭に吐く「^D」表示を除去
    ans = ans.strip()
    if not ans:
        return False, "回答が得られませんでした（もう一度お試しください）"
    return True, ans

# ---------- 会議前の作戦会議（任意チャット） ----------
STRATEGY_PROMPT = """あなたは会議前の準備参謀です。依頼主の構想を受け止め、整理・言い換え・仮説追加・反論・質問候補へ展開する「壁打ち相手」です。穴埋めヒアリングにしないでください。
次の有効なJSONだけを返してください。
{{"reply":"依頼主への返答。まず理解を短く言い換え、価値のある仮説・別視点・会議での使い方を提案。資料を使った事実には（相対パス）を付ける。質問は本当に必要な場合のみ1つ","brief":"ここまでの会議準備ブリーフ。ライブ参謀がこれ単体で使える完結した文章","board":{{"outcome":"会議の成功条件/着地点。不明なら空文字","counterpart":"相手の状況・関係性・関心。不明なら空文字","hypotheses":["検証すべき仮説"],"questions":["会議で聞く候補"],"risks":["懸念・制約・見落とし"],"avoid":["避けるべき進め方"],"sources":[{{"path":"実際に読んだファイルの相対パス","use":"何の根拠に使ったか"}}]}}}}
ルール:
- 依頼主の言葉を勝手に事実へ拡張しない。不明点は質問する。
- 【最重要・現在性】今回の対象を決める根拠の優先順位は「依頼主の新しい発言 ＞ 会議設定 ＞ 現在のbrief ＞ フォルダ資料」。フォルダ資料にある人物・会社も参考情報として使ってよいが、今回の対象だと断定しない。
- 新しい発言にない固有名詞を資料から使う場合は「過去資料では」「以前の仮説では」など出所と時点を明示する。一般的な役割（クラブ、お店、相手、社長）を、説明なく特定の会社・人物へ置き換えない。
- フォルダには過去案・終了した仮説・別の取引先・古い会議メモが共存し得る。日付や「latest」というファイル名だけで現在案と断定せず、新しい発言との直接の一致を確認する。
- 資料だけに登場する人物・会社は、過去事例・比較材料としてなら提示してよい。現在の対象・意思決定者であるかは、依頼主の発言に根拠がなければ未確認とする。
- 毎回質問で終わらない。情報が少なくても、その時点の仮説や選択肢を2〜4個返し、実用的な価値を先に出す。
- すでに答えた内容を再度聞かない。「なぜ？」だけの抽象的な質問を連発しない。
- boardは毎回全体を更新。各配列は重複を避け、重要な順に最大6件。不明な項目は作り話で埋めない。
- briefは毎回、それ単体でライブ参謀に渡せる完結した文章に更新する。
- 背景欄には、選択フォルダから今回の発言との関連性を事前判定した最大3件の資料本文だけが入る。記載がある事実だけを使い、関係が薄い資料を無理に結び付けない。
- 読んだファイルはboard.sourcesに相対パスと用途を残す。読んでいない資料は根拠にしない。該当資料がなければ「フォルダ内には見当たらない」と明記する。
- 【厳守】読取専用。ファイルの作成・編集・削除はしない。親フォルダや他案件へ出ない。現在会議の自動生成済み「会議準備/.../事前準備.md」は、履歴とbriefに既に含まれるため資料根拠として再読込しない。

【会議設定】
{meta}
【依頼主】
{profile}
【背景資料の要約】
{context}
【現在の作戦ブリーフ】
{brief}
【これまでのチャット】
{history}
【依頼主の新しい発言】
{message}"""

STRATEGY_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "reply": {"type": "string"}, "brief": {"type": "string"},
        "board": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "outcome": {"type": "string"}, "counterpart": {"type": "string"},
                "hypotheses": {"type": "array", "items": {"type": "string"}},
                "questions": {"type": "array", "items": {"type": "string"}},
                "risks": {"type": "array", "items": {"type": "string"}},
                "avoid": {"type": "array", "items": {"type": "string"}},
                "sources": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {"path": {"type": "string"}, "use": {"type": "string"}},
                    "required": ["path", "use"]}}
            },
            "required": ["outcome", "counterpart", "hypotheses", "questions", "risks", "avoid", "sources"]
        }
    },
    "required": ["reply", "brief", "board"]
}

def _strategy_files(project_dir, fallback_ctx=None):
    """Google Drive全内容をAI検索させず、最新の候補ファイル名だけを短時間で列挙する。"""
    try:
        r = subprocess.run(["rg", "--files", "-g", "*.md", "-g", "*.txt", "-g", "*.json",
                            "-g", "*.html", "-g", "*.pdf", "-g", "!会議準備/**"],
                           cwd=project_dir, capture_output=True, text=True, timeout=8)
        paths = [x.strip() for x in r.stdout.splitlines() if x.strip()]
        if paths:
            return paths[:250]
    except Exception as e:
        sys.stderr.write("[STRATEGY] file list失敗 %r\n" % e); sys.stderr.flush()
    if os.name == "nt":
        found = []
        allowed = {".md", ".txt", ".json", ".html", ".pdf"}
        try:
            for base, dirs, files in os.walk(project_dir):
                dirs[:] = [d for d in dirs if d != "会議準備" and not d.startswith(".")]
                for name in files:
                    if os.path.splitext(name)[1].lower() in allowed:
                        found.append(os.path.relpath(os.path.join(base, name), project_dir))
                        if len(found) >= 250:
                            return found
        except Exception:
            pass
        if found:
            return found
    fmap = (fallback_ctx or {}).get("filemap", [])
    return [str(x.get("path")) for x in fmap[:30]
            if isinstance(x, dict) and x.get("path")]

SOURCE_SELECT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"paths": {"type": "array", "maxItems": 3, "items": {"type": "string"}}},
    "required": ["paths"]
}

def _claude_structured(prompt, schema, timeout=90, model=None):
    """フォルダ外の/tmpで、ツールなしClaudeに構造化JSONだけを返させる。"""
    # このMacのclaude -pはPTYなしだと稀に無応答になるため、実績のあるscript経由に統一。
    # cwd=/tmpかつtools=""なので、選択フォルダのtrust警告や追加探索は発生しない。
    out = _claude_explore(tempfile.gettempdir(), prompt, timeout=timeout, json_schema=schema,
                          tools="", max_turns=3, model=model)
    try:
        return _first_json(out)
    except json.JSONDecodeError:
        # PTY＋json-schema経路が空またはCLIラッパーだけを返す環境がある。
        # ツールなしの通常 -p は別経路で安定しているため、同じ内容をJSON限定で再実行する。
        retry = _ai_text(prompt + "\n\n返答は指定スキーマに合うJSON値だけ。説明やコードフェンスは禁止。",
                         timeout=timeout, cwd=tempfile.gettempdir(), model=model or ASSIST_MODEL)
        return _first_json(retry)

EXPLICIT_MEETING_RE = re.compile(
    r"^(?P<counterpart>.{1,40}?)(?:との|と)(?:ミーティング|会議|打ち合わせ)(?:なんだ|なの|だよ|です|だ)?[。！!]*$")

def _explicit_meeting_counterpart(message):
    """「田部井社長とのミーティングだよ」のような明示訂正をAIなしで確定する。"""
    text = re.sub(r"^(?:これは|今回は|この会議は)\s*", "", str(message or "").strip())
    m = EXPLICIT_MEETING_RE.match(text)
    if not m:
        return ""
    counterpart = m.group("counterpart").strip(" 、。『』「」")
    if not counterpart or counterpart in ("相手", "この人", "あの人"):
        return ""
    return counterpart

def _apply_explicit_meeting_identity(sid, message):
    """依頼主の明示した相手を会議設定へ即時反映し、旧同期先を返す。"""
    counterpart = _explicit_meeting_counterpart(message)
    if not counterpart:
        return "", ""
    meta = read_meta(sid)
    old_export = _strategy_export_dir(sid, meta)
    old_title = str(meta.get("title", "")).strip()
    old_counterpart = old_title.split("との", 1)[0].strip() if "との" in old_title else ""
    suffix = old_title.split("との", 1)[1].strip() if "との" in old_title else "ミーティング"
    meta["title"] = counterpart + "との" + (suffix or "ミーティング")
    meta["counterpart"] = counterpart
    goal = str(meta.get("goal", ""))
    if old_counterpart and old_counterpart in goal:
        meta["goal"] = goal.replace(old_counterpart, counterpart)
    meta["updated"] = time.strftime("%Y-%m-%d %H:%M")
    write_meta(sid, meta)
    return counterpart, old_export

def _strategy_source_context(project_dir, meta_text, message, paths):
    """候補名から最大3件を選ばせ、サーバー側で範囲・容量を検証して読み込む。"""
    if not paths: return "（関連資料なし）"
    select_prompt = """会議準備で読む資料を選んでください。ファイル名一覧から、今回の発言と直接関係するものだけ最大3件選び、指定JSONで返してください。
過去案件・別会社・別人物の資料を、役割名が似ているだけで選ばないでください。ただし今回の論点に有用な過去事例・仮説なら選んで構いません。その場合は最終回答で過去資料だと区別します。関係が薄ければ0件で構いません。
【会議設定】
%s
【今回の発言】
%s
【ファイル一覧】
%s""" % (meta_text, message, "\n".join("- " + x for x in paths))
    try:
        choice = _claude_structured(select_prompt, SOURCE_SELECT_SCHEMA, timeout=45, model="haiku")
        selected = choice.get("paths", []) if isinstance(choice, dict) else []
    except Exception as e:
        sys.stderr.write("[STRATEGY] source select失敗 %r\n" % e); sys.stderr.flush()
        selected = []
    allowed, root = set(paths), os.path.realpath(project_dir)
    chunks, total = [], 0
    for rel in selected[:3]:
        if rel not in allowed: continue
        full = os.path.realpath(os.path.join(project_dir, rel))
        try:
            if os.path.commonpath([root, full]) != root or not os.path.isfile(full): continue
            with open(full, encoding="utf-8", errors="ignore") as f: txt = f.read(14000)
            if rel.lower().endswith(".html"):
                txt = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", txt, flags=re.I)
                txt = re.sub(r"<[^>]+>", " ", txt)
            txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
            room = 26000 - total
            if room <= 0: break
            txt = txt[:room]; total += len(txt)
            chunks.append("【資料: %s】\n%s" % (rel, txt))
        except Exception as e:
            sys.stderr.write("[STRATEGY] source read失敗 %s %r\n" % (rel, e)); sys.stderr.flush()
    return "\n\n".join(chunks) or "（今回の発言に直接関係する資料は選ばれなかった）"

def strategy_chat(sid, message):
    """選択フォルダをClaude Codeがその場で読みながら壁打ちし、ライブAI用briefを保存する。"""
    explicit_counterpart, old_export = _apply_explicit_meeting_identity(sid, message)
    m = read_meta(sid)
    st = _load_strategy(sid)
    msgs = st.get("messages") if isinstance(st.get("messages"), list) else []
    if explicit_counterpart:
        board = dict(st.get("board") or {}) if isinstance(st.get("board"), dict) else {}
        board["counterpart"] = explicit_counterpart + "とのミーティング"
        for key in ("hypotheses", "questions", "risks", "avoid", "sources"):
            if not isinstance(board.get(key), list): board[key] = []
        note = "今回の相手は%s。" % explicit_counterpart
        old_brief = str(st.get("brief", "")).strip()
        brief = old_brief if note in old_brief else ((old_brief + "\n\n") if old_brief else "") + note
        reply = "「%sとのミーティング」として、会議設定・準備ボード・ライブ解析へ反映しました。" % explicit_counterpart
        # 同じ訂正を直前の失敗後に再送した場合は、失敗メッセージを成功確認へ置換して
        # チャット履歴を重複させない。
        if (len(msgs) >= 2 and isinstance(msgs[-2], dict) and isinstance(msgs[-1], dict) and
                msgs[-2].get("role") == "user" and
                str(msgs[-2].get("text", "")).strip() == message and msgs[-1].get("role") == "assistant"):
            msgs[-1] = {"role": "assistant", "text": reply}
        else:
            msgs.extend([{"role": "user", "text": message}, {"role": "assistant", "text": reply}])
        saved = {"messages": msgs[-40:], "brief": brief, "board": board,
                 "folderMode": bool((m.get("project_dir") or "").strip()),
                 "updated": time.strftime("%Y-%m-%d %H:%M")}
        _save_strategy(sid, saved)
        apply_strategy_to_data(sid, saved)
        sync_strategy_to_project(sid, saved, stale_dir=old_export)
        return True, {"reply": reply, "brief": brief, "board": board, "messages": msgs[-40:],
                      "exportPath": _strategy_export_dir(sid), "folderMode": saved["folderMode"]}
    history = "\n".join(("依頼主: " if x.get("role") == "user" else "参謀: ") + str(x.get("text", ""))
                        for x in msgs[-16:] if isinstance(x, dict))
    ctx = {}
    try:
        with open(os.path.join(sdir(sid), "context.json"), encoding="utf-8") as f: ctx = json.load(f)
    except Exception: pass
    project_dir = (m.get("project_dir") or "").strip()
    folder_mode = bool(project_dir and os.path.isdir(project_dir))
    # フォルダ全体の探索ダイジェストは古い案件名まで広く含み、現在の相手と誤認させやすい。
    # folder_modeでは必要資料を毎ターン直接読むため、広域ダイジェストは注入しない。
    meta_text = "\n".join("%s: %s" % (k, m.get(k, "")) for k in ("title", "goal", "mtype", "stance"))
    context_text = (_strategy_source_context(project_dir, meta_text, message,
                                              _strategy_files(project_dir, ctx)) if folder_mode else
                    (ctx.get("digest") or "（未読込/資料なし）")[:2200])
    prompt = STRATEGY_PROMPT.format(meta=meta_text, profile=_profile_text() or "（未設定）",
                                    context=context_text,
                                    brief=st.get("brief", "") or "（まだなし）",
                                    history=history or "（まだなし）", message=message)
    try:
        if folder_mode:
            # 候補選定・読込は直前に範囲限定して完了済み。最終生成はツールなしで安定実行。
            obj = _claude_structured(prompt, STRATEGY_SCHEMA, timeout=90)
            out = json.dumps(obj, ensure_ascii=False)
        else:
            out = _ai_text(prompt, timeout=120, cwd=tempfile.gettempdir(), model=ASSIST_MODEL)
        out = re.sub(r"^```json\s*|^```\s*|```\s*$", "", out.strip(), flags=re.M).strip()
        try:
            parsed = _first_json(out)
            obj = _strategy_object(parsed)
            if not obj:
                raise json.JSONDecodeError("strategy object not found", out, 0)
        except json.JSONDecodeError:
            sys.stderr.write("[STRATEGY] JSON不正 sid=%s folder=%s output=%r\n" %
                             (sid, folder_mode, out[:1200]))
            sys.stderr.flush()
            if folder_mode:
                retry_prompt = prompt + "\n\n直前の応答形式が不正でした。内容を維持し、指定JSONスキーマに適合する値だけを返してください。"
                obj = _claude_structured(retry_prompt, STRATEGY_SCHEMA, timeout=90)
                out = json.dumps(obj, ensure_ascii=False)
                obj = _strategy_object(obj)
                if not obj:
                    raise json.JSONDecodeError("strategy object not found after retry", out, 0)
            else:
                raise
        reply, brief = str(obj.get("reply", "")).strip(), str(obj.get("brief", "")).strip()
        board = obj.get("board") if isinstance(obj.get("board"), dict) else {}
        if not reply: raise ValueError("empty strategy reply")
    except json.JSONDecodeError:
        sys.stderr.write("[STRATEGY] 形式失敗をメモ保存へfallback sid=%s\n" % sid); sys.stderr.flush()
        reply = "内容を準備記録とライブ解析へ反映しました。準備ボードの自動整理は次の入力時に再試行します。"
        old_brief = str(st.get("brief", "")).strip()
        brief = (old_brief + "\n\n" if old_brief else "") + "【依頼主の追加メモ】\n" + message
        board = st.get("board") if isinstance(st.get("board"), dict) else {}
    except Exception as e:
        sys.stderr.write("[STRATEGY] 失敗 sid=%s error=%r\n" % (sid, e)); sys.stderr.flush()
        reply = "内容を準備記録とライブ解析へ反映しました。準備ボードの自動整理は次の入力時に再試行します。"
        old_brief = str(st.get("brief", "")).strip()
        brief = (old_brief + "\n\n" if old_brief else "") + "【依頼主の追加メモ】\n" + message
        board = st.get("board") if isinstance(st.get("board"), dict) else {}
    msgs.extend([{"role": "user", "text": message}, {"role": "assistant", "text": reply}])
    saved = {"messages": msgs[-40:], "brief": brief, "board": board, "folderMode": folder_mode,
             "updated": time.strftime("%Y-%m-%d %H:%M")}
    _save_strategy(sid, saved)
    apply_strategy_to_data(sid, saved)
    sync_strategy_to_project(sid, saved)
    return True, {"reply": reply, "brief": brief, "board": board, "messages": msgs[-40:],
                  "exportPath": _strategy_export_dir(sid), "folderMode": folder_mode}

# ---------- 清書前の確認Q&A生成 ----------
# フルの文字起こしを読み、誤認識しやすい固有名詞を洗い出して「答えやすい確認質問」を作る。
# WebSearchは使わない純テキスト生成なので、finalize同様 capture_output で安定して動く。
PREP_PROMPT = """あなたは会議「{title}」の書記アシスタントです。
以下は会議の文字起こし（whisperの自動認識で、人物名・会社名・専門用語に誤変換が多く、**会議の前提すら読み違えている可能性がある**）です。
清書の前に依頼者に確認すべきことを質問リストにしてください。**わかった気にならないこと**が最重要です。
**有効なJSONのみ**出力（前置き・説明・コードフェンス禁止）:
{{
  "questions": [
    {{"q": "確認したいこと（依頼者が短く答えられる具体的な質問文）",
      "guess": "あなたの現時点の理解・推定（自信が無ければ空文字にして率直に聞く）",
      "heard": "根拠となる文字起こしの該当箇所（前提質問なら省略可）"}}
  ]
}}
質問は次の3層を**必ずこの順で**含める:
【A. そもそもの前提（最重要・2〜4件・リストの先頭に）】
- この会議は**何についての会議**か（あなたの理解をguessに書いて確認）
- **参加者は誰と誰**か（名前と役割。呼びかけから推定できた分をguessに）
- **主に何を決めよう/進めようとしていた**か
- 文字起こしが崩れて前提が読み取れないなら、guessを空にして「教えてください」と率直に聞く
【B. 人物・会社・用語の表記（3〜8件）】
- 誤変換されていそうな固有名詞。「guessで合っていれば確認、違えば訂正」で済む形に
【C. 解釈の確認（1〜4件）】
- 重要そうだが意味が取りづらい発言・数字・決定事項について「〜と解釈しましたが合っていますか？」
ルール:
- **推測で埋めない。わからないことは、わからないと認めて聞く**（guessを空にする勇気を持つ）
- SPEAKER_00等は音声から分離した匿名ID。発言例を根拠に実名を断定せず、名前の対応は画面上の話者確認欄に任せる。
- 明らかに正しい一般語は入れない。確認価値のあるものだけ。合計6〜14件。日本語で。

文字起こし:
{transcript}"""

def _load_prep(sid):
    p = os.path.join(sdir(sid), "prep.json")
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_prep(sid, data):
    try:
        with open(os.path.join(sdir(sid), "prep.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def save_prep_answers(sid, answers, speaker_map=None):
    """清書時の確定回答を prep.json に保存（次回の初期値に使う）＋固有名詞を学習辞書へ。"""
    if not isinstance(answers, dict):
        return
    data = _load_prep(sid)
    data["answers"] = answers
    if isinstance(speaker_map, dict):
        data["speakerMap"] = {str(k): str(v).strip() for k, v in speaker_map.items() if str(v).strip()}
    _save_prep(sid, data)
    _learn_terms(answers)   # 確定した表記を次回以降のwhisperヒントに自動反映

def finalize_prep(sid, regen=False):
    """清書前の確認質問を生成。既に prep.json があれば再生成せず返す（2回目以降の効率化）。
    戻り値 (ok, {questions, answers, cached} or msg)。"""
    sys.stderr.write("[PREP] start sid=%s regen=%s\n" % (sid, regen)); sys.stderr.flush()
    saved = _load_prep(sid)
    diarization = prepare_diarization(sid, regen=regen)
    if not regen and saved.get("questions"):
        return True, {"questions": saved["questions"], "answers": saved.get("answers", {}),
                      "speakerMap": saved.get("speakerMap", {}), "diarization": diarization, "cached": True}
    d = sdir(sid)
    txt = ""
    for fn in ("transcript-full.txt", "transcript.txt"):   # 清書済みなら全文、無ければライブ版
        txt = _read_text(os.path.join(d, fn))
        if txt.strip():
            break
    # 話者分離に成功した場合は、安全なASRヒントで再転写した匿名話者付き本文を優先する。
    # 旧ライブ文字起こしに混入した偽の人名を確認質問へ持ち込まない。
    if diarization.get("status") == "ready" and str(diarization.get("transcript") or "").strip():
        txt = str(diarization["transcript"])
    sys.stderr.write("[PREP] transcript読了 len=%d\n" % len(txt)); sys.stderr.flush()
    if not txt.strip():
        return False, "文字起こしを読めませんでした（会議フォルダに transcript が無いか読み取り不能）"
    title = read_meta(sid).get("title", "会議")
    prompt = PREP_PROMPT.format(title=title, transcript=txt[:14000])
    try:
        sys.stderr.write("[PREP] %s起動 promptlen=%d\n" % (AI_PROVIDER, len(prompt))); sys.stderr.flush()
        t0 = time.time()
        out = _ai_text(prompt, timeout=120, cwd=tempfile.gettempdir(), model=ASSIST_MODEL)
        sys.stderr.write("[PREP] %s終了 %.1f秒 outlen=%d\n"
                         % (AI_PROVIDER, time.time()-t0, len(out))); sys.stderr.flush()
    except JobCancelled:
        raise
    except Exception as e:
        sys.stderr.write("[PREP] claude例外 %r\n" % e); sys.stderr.flush()
        return False, "確認質問の生成に失敗：%r" % e
    out = re.sub(r"^```json\s*|^```\s*|```\s*$", "", out, flags=re.M).strip()
    m = re.search(r"\{.*\}", out, re.S)
    if m:
        out = m.group(0)
    try:
        obj = json.loads(out)
        questions = obj.get("questions", [])
    except Exception:
        return False, "確認質問の生成結果が不正でした（もう一度お試しください）"
    answers = saved.get("answers", {})   # 作り直しても前回の回答は保持
    speaker_map = saved.get("speakerMap", {})
    _save_prep(sid, {"questions": questions, "answers": answers, "speakerMap": speaker_map,
                     "generated": time.strftime("%Y-%m-%d %H:%M")})
    return True, {"questions": questions, "answers": answers, "speakerMap": speaker_map,
                  "diarization": diarization, "cached": False}

# ---------- 会議後の学び抽出（用途別プレイブックへの蓄積・承認制）----------
LEARN_PROMPT = """あなたは会議直後の振り返りを手伝う参謀です。依頼主（録音している話し手本人）のために、この会議の学びを2種類に分けて整理します。
前置き・説明・コードフェンス禁止。次のJSONオブジェクトだけを返してください。
{{
 "insights": "1本のMarkdown文字列（配列にしない）。依頼主個人への振り返りレポート。行頭-の箇条書き3〜7個。依頼主の立場と目標に照らして：①この会議で得られた気づき ②見落としていた・聞き漏らした視点 ③次に活きる教訓・次の一手。案件固有の名前・数字をそのまま使い、具体的に。良かった点だけでなく聞き漏らしや甘かった詰めも率直に書く",
 "playbook": "1本のMarkdown文字列（配列にしない）。次回以降の「{mtype}」でそのまま使える一般化された学び。行頭-の箇条書き0〜5個。案件固有の事実（顧客名・金額・個人名）は書かず一般化する。既存プレイブックとの重複・一般論の水増しは禁止。特筆すべきものが無ければ空文字"
}}
ルール:
- 会議の記録から**実際に確認できた**ことだけ。憶測で埋めない。
- insightsは依頼主がこの後3分で読める分量にする。

【依頼主の立場】{stance}
【会議の目標】{goal}
【既存プレイブック】
{playbook}

【整理済み議事】
{data}

【文字起こし】
{transcript}"""

def extract_learnings(sid):
    """会議の記録から学びを抽出して返す（保存はしない＝依頼者の承認を待つ）。
    戻り値 (ok, dict)。dict = {"insights": 個人向け気づき, "playbook": プレイブック追記案}。"""
    m = read_meta(sid)
    mtype = (m.get("mtype") or "").strip()
    d = sdir(sid)
    txt = ""
    for fn in ("transcript-full.txt", "transcript.txt"):
        txt = _read_text(os.path.join(d, fn))
        if txt.strip():
            break
    if not txt.strip():
        return False, "文字起こしがまだありません"
    data = _read_text(os.path.join(d, "data.json"))
    prompt = LEARN_PROMPT.format(mtype=mtype or "同種の会議",
                                 playbook=(_playbook_text(mtype) or "（まだ空）") if mtype else "（用途未設定のため無し）",
                                 goal=m.get("goal", "") or "（未設定）",
                                 stance=m.get("stance", "") or "（未設定）",
                                 data=data[:4000], transcript=txt[:10000])
    try:
        out = _ai_text(prompt, timeout=180, cwd=tempfile.gettempdir(), model=SLIDE_MODEL)
        mm = re.search(r"\{.*\}", out, re.S)
        if mm:
            try:
                obj = json.loads(mm.group(0))
                # AIが箇条書きを配列で返すことがある（2026-07-17 実障害：str(list)のPython表記が
                # そのまま画面とlearnings.mdに出た）。型に依存せずMarkdownへ正規化する
                def _learn_text(v):
                    if isinstance(v, list):
                        rows = []
                        for x in v:
                            t = _learn_text(x)
                            if t:
                                rows.append(t if t.lstrip().startswith("-") else "- " + t)
                        return "\n".join(rows)
                    if isinstance(v, dict):
                        return "\n".join(filter(None, (_learn_text(x) for x in v.values())))
                    return str(v or "").strip()
                ins = _learn_text(obj.get("insights"))
                pb = _learn_text(obj.get("playbook"))
                if ins or pb:
                    return True, {"insights": ins, "playbook": pb}
            except Exception:
                pass
        # JSONで返らなかった場合は全文を個人向けレポートとして扱う（水増しよりまし）
        out = re.sub(r"^```(md|markdown|json)?\s*|```\s*$", "", out, flags=re.M).strip()
        if not out:
            return False, "抽出結果が空でした"
        return True, {"insights": out, "playbook": ""}
    except Exception as e:
        return False, "抽出に失敗：%r" % e

# ---------- HTTP ----------
def neutral_generated_html(path, persist=False):
    """Render old generated files with the current product identity.

    Older decks embedded a customer logo as base64 and carried company-specific
    palettes. Keep their meeting content, but remove that branding at serve time
    so users do not have to regenerate every historical deck.
    """
    if not os.path.isfile(path):
        return None
    text = _read_text(path)
    if not text:
        return text
    original = text
    text = re.sub(
        r'background:\s*url\(["\']data:image/[^"\']+["\']\)\s*no-repeat left center;?',
        'background: none;', text, flags=re.I)
    # Legacy variants used background-image or different spacing. Remove every
    # embedded bitmap URL from generated chrome before applying the wordmark.
    text = re.sub(r'url\(\s*(["\']?)data:image/.*?\1\s*\)', 'none', text, flags=re.I | re.S)
    # Remove the former company-theme wordmark block itself, not just its image.
    text = re.sub(
        r'/\*\s*sponsaru テーマ.*?body\[data-theme="sponsaru"\]\s*\.slide::after\s*\{.*?\}\s*',
        '', text, flags=re.I | re.S)
    text = text.replace('data-theme="mainichi"', 'data-theme="neutral"')
    text = text.replace('data-theme="sponsaru"', 'data-theme="neutral"')
    text = re.sub(
        r'(<div class="cover-meta">[^<]*?｜)[^<]*?議事サマリ(</div>)',
        r'\1 LiveMTG\2', text)
    # Legacy Mermaid scripts contain fixed corporate colors; normalize both branches.
    color_map = {
        "#00a0e9": "#0071e3", "#0079b3": "#86868b", "#007cb8": "#0066cc",
        "#11233a": "#1d1d1f", "#12232e": "#1d1d1f", "#33485f": "#424245",
        "#0a3a5c": "#1d1d1f", "#e0f4fd": "#f5f5f7", "#eef7fd": "#ffffff",
        "#f3f8fc": "#f5f5f7", "#dde8f1": "#d2d2d7", "#15233f": "#1d1d1f",
        "#f15a24": "#0071e3", "#0e1a30": "#1d1d1f", "#fdeee5": "#f5f5f7",
        "#f5f7fa": "#ffffff", "#e3e8ef": "#d2d2d7",
    }
    for old, new in color_map.items():
        text = re.sub(re.escape(old), new, text, flags=re.I)
    text = text.replace("\U0001f5a8 PDF保存", "PDF保存")
    # 新テンプレートは実ロゴ（--logo: brand-logo.png）を左下に持つため、文字ワードマークを
    # 重ねない（ロゴと"LiveMTG"テキストが二重に写る。2026-07-16 実測）。旧生成物にだけ注入する
    wordmark = ('' if "brand-logo.png" in text else
                '.slide::after{content:"LiveMTG"!important;background:none!important;width:auto!important;height:auto!important;color:#6e6e73!important;font-size:17px!important;font-weight:650!important;letter-spacing:-.02em!important}\n')
    overrides = """<style id="livemtg-neutral-identity">
:root{--ink:#1d1d1f;--ink2:#424245;--gray:#86868b;--panel:#f5f5f7;--line:#d2d2d7;--blue:#0071e3;--blue-ink:#0066cc;--blue-deep:#1d1d1f;--blue-soft:#eef5fc;--blue-soft2:#f5f5f7;--mark:#d9e8fb}
body{font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","Helvetica Neue","Hiragino Sans","Yu Gothic UI",sans-serif!important}
""" + wordmark + """
.tree-lines path{stroke:#8e8e93!important}.tree-node{border:1px solid #d2d2d7!important;color:#1d1d1f!important;box-shadow:none!important}.tree-node.root{background:#1d1d1f!important;color:#fff!important}.tree-node.branch{background:#e8e8ed!important}.tree-node.group{background:#f2f2f4!important}.tree-node.item,.tree-node.leaf,.tree-node.item.tone-1,.tree-node.item.tone-2,.tree-node.item.tone-3,.tree-node.item.tone-4,.tree-node.leaf.tone-1,.tree-node.leaf.tone-2,.tree-node.leaf.tone-3,.tree-node.leaf.tone-4{background:#fff!important}.tree-detail{background:#f5f5f7!important;border-color:#d2d2d7!important}
</style>"""
    if 'id="livemtg-neutral-identity"' not in text:
        text = text.replace("</head>", overrides + "\n</head>", 1)
    back_style = """<style id="livemtg-back-style">
#livemtg-back{position:fixed;left:22px;top:22px;z-index:999;color:#1d1d1f;background:rgba(255,255,255,.94);border:1px solid #d2d2d7;border-radius:999px;padding:10px 16px;text-decoration:none;font:700 14px -apple-system,BlinkMacSystemFont,"Helvetica Neue",sans-serif;box-shadow:0 4px 18px rgba(0,0,0,.09)}
@media print{#livemtg-back{display:none!important}}
</style>"""
    if 'id="livemtg-back-style"' not in text:
        text = text.replace("</head>", back_style + "\n</head>", 1)
    if 'id="livemtg-back"' not in text:
        text = re.sub(r'(<body\b[^>]*>)', r'\1\n<a id="livemtg-back" href="/">← %s</a>' % _t("ダッシュボード", "Dashboard"), text, count=1)
    if persist and text != original:
        tmp = path + ".neutral.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    return text

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 静かに
        pass

    def _origin_allowed(self):
        origin = str(self.headers.get("Origin") or "").strip()
        if not origin: return True   # curl/CLI/同一オリジンの一部ブラウザ要求
        try:
            parsed = urllib.parse.urlparse(origin)
            return (parsed.scheme in ("http", "https") and
                    parsed.hostname in ("127.0.0.1", "localhost", "::1") and
                    (parsed.port or (443 if parsed.scheme == "https" else 80)) == PORT)
        except Exception:
            return False

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        origin = str(self.headers.get("Origin") or "").strip()
        if origin and self._origin_allowed():
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        try: self.wfile.write(body)
        except Exception: pass

    def _file(self, path, ctype):
        if not os.path.isfile(path):
            return self._send(404, "not found", "text/plain; charset=utf-8")
        with open(path, "rb") as f:
            self._send(200, f.read(), ctype)

    def _body_json(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    def do_OPTIONS(self):
        if not self._origin_allowed():
            return self._send(403, json.dumps({"ok": False, "error": "origin not allowed"}))
        return self._send(204, b"", "text/plain")

    def _view_jobs(self):
        """タブ別の更新状況（赤ポチ・次回更新までの秒数）。2026-07-17 依頼者要望。"""
        jobs = {}
        if current_id:
            jobs["list"] = {"running": (current_id in analysis_pending) or chunk_q.qsize() > 0}
            key = (current_id, "map")
            running = key in view_pending
            nxt = None
            if recording and not running:
                last = view_last_run.get(key, 0)
                nxt = max(0, int(40 - (time.time() - last))) if last else 0
            jobs["map"] = {"running": running, "nextInSec": nxt}
        return jobs

    def _state(self):
        global recording, capture_heartbeat
        # タブ終了・リロード・権限取消後に「録音停止」と表示し続けない。
        # ブラウザは3秒ごとにheartbeat、遅くとも15秒ごとに音声を送る。
        # 非アクティブタブのタイマー間引きや送信の揺らぎで停止扱いにしない。
        if recording and (not capture_heartbeat or time.time() - capture_heartbeat > 45):
            recording = False
            capture_heartbeat = 0.0
            _caffeinate(False)
        cur = read_meta(current_id) if current_id else {}
        strategy = _load_strategy(current_id) if current_id else {}
        data_obj = _read_live_data(current_id) if current_id else {}
        data_path = os.path.join(sdir(current_id), "data.json") if current_id else ""
        analysis_updated = int(data_obj.get("_analysisUpdatedAt") or 0)
        if not analysis_updated and data_path and os.path.isfile(data_path):
            analysis_updated = int(os.path.getmtime(data_path))
        with long_job_lock:
            busy = sorted({k[1] for k in long_jobs})   # 実行中の長時間ジョブ種別（清書/学び/スライド等）
        return {
            "ver": "v66-runtime-truth",   # デバッグ用：稼働中コードの版を確認するマーカー
            "recording": recording,
            "busy": busy,
            "asrWarmup": dict(asr_warmup),
            "viewJobs": self._view_jobs(),
            "captureHeartbeatAt": int(capture_heartbeat),
            "queue": chunk_q.qsize(),
            "analyzing": bool(current_id) and current_id in analysis_pending,
            "detailing": bool(current_id) and current_id in detail_pending,
            "activeView": active_view(current_id) if current_id else "list",
            "viewUpdating": bool(current_id) and any(k[0] == current_id for k in view_pending),
            "liveDiarization": _load_live_diarization(current_id, compact=True) if current_id else {},
            "dataUpdatedAt": analysis_updated,
            "transcriptUpdatedAt": int(os.path.getmtime(os.path.join(sdir(current_id), "transcript.txt"))) if current_id and os.path.isfile(os.path.join(sdir(current_id), "transcript.txt")) else 0,
            "current": {"id": current_id, "title": cur.get("title", ""),
                        "created": cur.get("created", "")},
            "sessions": list_sessions(),
            "hasSlides": bool(current_id) and os.path.isfile(os.path.join(sdir(current_id), "mindmap.html")),
            "hasDeck": bool(current_id) and os.path.isfile(os.path.join(sdir(current_id), "slides.html")),
            "hasAudio": bool(current_id) and os.path.isdir(os.path.join(sdir(current_id), "audio"))
                        and bool(glob.glob(os.path.join(sdir(current_id), "audio", "*.webm"))),
            "hasFinal": bool(current_id) and os.path.isfile(os.path.join(sdir(current_id), "final.json")),
            "hasLearn": bool(current_id) and os.path.isfile(os.path.join(sdir(current_id), "learnings.md")),
            "hasLearnSlides": bool(current_id) and os.path.isfile(os.path.join(sdir(current_id), "learn-slides.html")),
            "hasLearnPdf": bool(current_id) and os.path.isfile(os.path.join(sdir(current_id), "learn-slides.pdf")),
            "hasMinutesPdf": bool(current_id) and os.path.isfile(os.path.join(sdir(current_id), "minutes.pdf")),
            "hasRadialPdf": bool(current_id) and os.path.isfile(_map_pdf_path(current_id, "radial")),
            "hasRelationPdf": bool(current_id) and os.path.isfile(_map_pdf_path(current_id, "relation")),
            "projectDir": cur.get("project_dir", ""),
            "goal": cur.get("goal", ""),
            "mtype": cur.get("mtype", ""),
            "stance": cur.get("stance", ""),
            "hasStrategy": bool(current_id) and bool((strategy.get("brief") or "").strip()),
            "hasContext": bool(current_id) and os.path.isfile(os.path.join(sdir(current_id), "context.json")),
            "exploring": bool(current_id) and exploring.get(current_id, False),
            "researching": researching.get(current_id, 0) if current_id else 0,
            "recentProjects": _recent_projects(),
            "slideModel": SLIDE_MODEL,
            "aiProvider": AI_PROVIDER,
            "language": LANGUAGE,
            "chunk": int(CHUNK),
        }

    def do_GET(self):
        p = self.path.split("?", 1)[0]
        if p == "/api/health":
            return self._send(200, json.dumps(service_health(), ensure_ascii=False))
        if p == "/api/desktop-health":
            return self._send(200, json.dumps(desktop_health(), ensure_ascii=False))
        if p == "/api/settings":
            return self._send(200, json.dumps({"ok": True, "aiProvider": AI_PROVIDER,
                                                "language": LANGUAGE,
                                                "speakerDiarization": {"installed": bool(shutil.which("whispermlx")),
                                                                        "tokenConfigured": _hf_token_configured(),
                                                                        "credentialStore": ("keychain" if sys.platform == "darwin" else "dpapi" if os.name == "nt" else "unavailable")}}, ensure_ascii=False))
        if p in ("/", "/index.html"):
            return self._file(os.path.join(SCRIPT_DIR, "index.html"), "text/html; charset=utf-8")
        if p == "/brand-logo.png":
            return self._file(os.path.join(SCRIPT_DIR, "brand-logo.png"), "image/png")
        if p == "/slide-bg.jpg":
            return self._file(os.path.join(SCRIPT_DIR, "slide-bg.jpg"), "image/jpeg")
        if p in ("/app-icon.png", "/favicon.png"):
            return self._file(os.path.join(SCRIPT_DIR, "app-icon.png"), "image/png")
        if p == "/mermaid.min.js":
            return self._file(os.path.join(SCRIPT_DIR, "mermaid.min.js"),
                              "application/javascript; charset=utf-8")
        if p == "/data.json":
            if current_id:
                return self._file(os.path.join(sdir(current_id), "data.json"),
                                  "application/json; charset=utf-8")
            return self._send(200, EMPTY_DATA)
        if p == "/slides.html":
            if current_id:
                html = neutral_generated_html(os.path.join(sdir(current_id), "mindmap.html"), persist=True)
                sync_to_project(current_id)
                return self._send(200, html, "text/html; charset=utf-8") if html is not None else self._send(404, "not found", "text/plain; charset=utf-8")
            return self._send(404, "no slides", "text/plain; charset=utf-8")
        if p == "/map.pdf":
            if not current_id:
                return self._send(404, "no meeting", "text/plain; charset=utf-8")
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            view = (q.get("view") or ["radial"])[0]
            path = _map_pdf_path(current_id, view if view in MAP_PDF_VIEWS else "radial")
            if not os.path.isfile(path):
                return self._send(404, "not found", "text/plain; charset=utf-8")
            return self._file(path, "application/pdf")
        if p == "/map-slide.html":
            if current_id:
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                view = (q.get("view") or ["radial"])[0]
                view = view if view in ("radial", "relation") else "radial"
                html = neutral_generated_html(os.path.join(sdir(current_id), "map-slide-%s.html" % view), persist=True)
                return self._send(200, html, "text/html; charset=utf-8") if html is not None else self._send(404, "not found", "text/plain; charset=utf-8")
            return self._send(404, "no meeting", "text/plain; charset=utf-8")
        if p == "/minutes.pdf":
            if not current_id:
                return self._send(404, "no meeting", "text/plain; charset=utf-8")
            path = os.path.join(sdir(current_id), "minutes.pdf")
            if not os.path.isfile(path):
                return self._send(404, "not found", "text/plain; charset=utf-8")
            return self._file(path, "application/pdf")
        if p == "/minutes-deck.html":
            if current_id:
                html = neutral_generated_html(os.path.join(sdir(current_id), "minutes-deck.html"), persist=True)
                return self._send(200, html, "text/html; charset=utf-8") if html is not None else self._send(404, "not found", "text/plain; charset=utf-8")
            return self._send(404, "no minutes", "text/plain; charset=utf-8")
        if p == "/learn-slides.pdf":
            if not current_id:
                return self._send(404, "no meeting", "text/plain; charset=utf-8")
            path = os.path.join(sdir(current_id), "learn-slides.pdf")
            if not os.path.isfile(path):
                return self._send(404, "not found", "text/plain; charset=utf-8")
            return self._file(path, "application/pdf")
        if p == "/learn-slides.html":
            if current_id:
                html = neutral_generated_html(os.path.join(sdir(current_id), "learn-slides.html"), persist=True)
                return self._send(200, html, "text/html; charset=utf-8") if html is not None else self._send(404, "not found", "text/plain; charset=utf-8")
            return self._send(404, "no slides", "text/plain; charset=utf-8")
        if p == "/api/learnings":
            if not current_id:
                return self._send(400, json.dumps({"ok": False, "msg": _t("会議がありません", "No meeting")}, ensure_ascii=False))
            txt = _read_text(os.path.join(sdir(current_id), "learnings.md"))
            return self._send(200, json.dumps({"ok": bool(txt.strip()), "insights": txt}, ensure_ascii=False))
        if p == "/deck.html":
            # 従来の経営者向けスライドデッキ（マインドマップとは別成果物）
            if current_id:
                html = neutral_generated_html(os.path.join(sdir(current_id), "slides.html"), persist=True)
                sync_to_project(current_id)
                return self._send(200, html, "text/html; charset=utf-8") if html is not None else self._send(404, "not found", "text/plain; charset=utf-8")
            return self._send(404, "no deck", "text/plain; charset=utf-8")
        if p == "/api/state":
            with lock:
                return self._send(200, json.dumps(self._state(), ensure_ascii=False))
        if p == "/research.json":
            if current_id and os.path.isfile(_research_path(current_id)):
                return self._file(_research_path(current_id), "application/json; charset=utf-8")
            return self._send(200, "[]")
        if p == "/api/transcript":
            if current_id:
                return self._file(os.path.join(sdir(current_id), "transcript.txt"),
                                  "text/plain; charset=utf-8")
            return self._send(200, "", "text/plain; charset=utf-8")
        if p == "/api/profile":
            txt = (_read_text(PROFILE_MD) or "") if os.path.isfile(PROFILE_MD) else ""
            fields = {}
            fj = os.path.splitext(PROFILE_MD)[0] + ".json"
            if os.path.isfile(fj):
                try: fields = json.loads(_read_text(fj) or "{}")
                except Exception: fields = {}
            return self._send(200, json.dumps({"ok": True, "text": txt, "fields": fields}, ensure_ascii=False))
        if p == "/api/strategy":
            if not current_id:
                return self._send(400, json.dumps({"ok": False, "msg": "会議がありません"}, ensure_ascii=False))
            st = _load_strategy(current_id)
            meta = read_meta(current_id); pd = (meta.get("project_dir") or "").strip()
            return self._send(200, json.dumps({"ok": True, "messages": st.get("messages", []),
                                                "brief": st.get("brief", ""), "board": st.get("board", {}),
                                                "exportPath": _strategy_export_dir(current_id),
                                                "folderMode": bool(pd and os.path.isdir(pd))}, ensure_ascii=False))
        if p == "/api/dismiss-question":
            # 質問候補の個別却下（×ボタン）。以後同じ質問は再提案しない
            if not current_id:
                return self._send(400, json.dumps({"ok": False}, ensure_ascii=False))
            q = str((b or {}).get("q") or "").strip()
            if q:
                with data_write_lock:
                    obj = _read_live_data(current_id)
                    dq = [x for x in (obj.get("_dismissedQ") or []) if x != q] + [q]
                    obj["_dismissedQ"] = dq[-30:]
                    g = obj.get("guide") or {}
                    if g.get("questions"):
                        g["questions"] = [x for x in g["questions"] if str(x.get("q") or "") != q]
                    tmp = os.path.join(sdir(current_id), "data.json.tmp")
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(obj, f, ensure_ascii=False, indent=2)
                    os.replace(tmp, os.path.join(sdir(current_id), "data.json"))
            return self._send(200, json.dumps({"ok": True}, ensure_ascii=False))

        if p == "/api/live-notes":
            return self._send(200, json.dumps({"ok": True, "notes": _load_live_notes(current_id) if current_id else []}, ensure_ascii=False))
        return self._send(404, "not found", "text/plain; charset=utf-8")

    def do_POST(self):
        global current_id, recording, capture_heartbeat
        if not self._origin_allowed():
            return self._send(403, json.dumps({"ok": False, "error": "origin not allowed"}))
        p = self.path.split("?", 1)[0]
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

        if p == "/api/settings":
            body = self._body_json()
            results = []
            if "aiProvider" in body: results.append(set_ai_provider(body.get("aiProvider")))
            if "hfToken" in body: results.append(set_hf_token(body.get("hfToken")))
            if "asrModel" in body: results.append(set_asr_model(body.get("asrModel")))
            if "language" in body:
                language_ok = set_language(body.get("language"))
                results.append(language_ok)
                if language_ok and current_id and is_session(current_id):
                    meta = read_meta(current_id); meta["language"] = LANGUAGE; write_meta(current_id, meta)
            ok = bool(results) and all(results)
            return self._send(200 if ok else 400,
                              json.dumps({"ok": ok, "aiProvider": AI_PROVIDER,
                                          "language": LANGUAGE,
                                          "speakerDiarization": {"installed": bool(shutil.which("whispermlx")),
                                                                  "tokenConfigured": _hf_token_configured(),
                                                                  "credentialStore": ("keychain" if sys.platform == "darwin" else "dpapi" if os.name == "nt" else "unavailable")}}, ensure_ascii=False))

        if p == "/api/ai-check":
            started = time.time()
            try:
                answer = _ai_text("接続確認です。OKとのみ返答してください。", timeout=35,
                                  cwd=tempfile.gettempdir())
                if not (answer or "").strip():
                    raise RuntimeError("AIから空の応答が返りました")
                return self._send(200, json.dumps({"ok": True, "aiProvider": AI_PROVIDER,
                                                    "elapsed": round(time.time() - started, 1)}, ensure_ascii=False))
            except Exception as e:
                return self._send(200, json.dumps({"ok": False, "aiProvider": AI_PROVIDER,
                                                    "error": str(e)[:300]}, ensure_ascii=False))

        if p == "/api/recording-heartbeat":
            if current_id:
                recording = True
                capture_heartbeat = time.time()
                _caffeinate(True)
            return self._send(200, json.dumps(self._state(), ensure_ascii=False))

        if p == "/api/view-focus":
            body = self._body_json()
            client_id = re.sub(r"[^A-Za-z0-9_-]", "", str(body.get("clientId", "")))[:64]
            view = str(body.get("view", "list"))
            if view not in ("list", "tree", "radial", "relation", "timeline"): view = "list"
            if client_id and current_id:
                with view_clients_lock:
                    view_clients[client_id] = {"sid": current_id, "view": view,
                                               "visible": bool(body.get("visible", True)), "updated": time.time()}
                request_active_view_update(current_id, force=bool(body.get("changed")))
            return self._send(200, json.dumps({"ok": True, "activeView": active_view(current_id)}, ensure_ascii=False))

        # 音声チャンク（バイナリ）: ブラウザのMediaRecorderから届く webm を受けてキューへ
        if p == "/api/chunk":
            try:
                n = int(self.headers.get("Content-Length", 0))
                data = self.rfile.read(n) if n else b""
            except Exception:
                data = b""
            sid = current_id
            if not sid or not data:
                return self._send(200, json.dumps({"ok": False}))
            # サービス再起動後もブラウザが送信を続けていれば、録音状態を自動復帰する。
            if not recording:
                recording = True
                _caffeinate(True)
            capture_heartbeat = time.time()
            d = os.path.join(WAVROOT, sid)
            os.makedirs(d, exist_ok=True)
            prefix = "prep" if (query.get("kind", [""])[0] == "prep") else "inc"
            path = os.path.join(d, "%s_%d.webm" % (prefix, int(time.time() * 1000)))
            with open(path, "wb") as f:
                f.write(data)
            chunk_q.put((sid, path))
            return self._send(200, json.dumps({"ok": True, "queue": chunk_q.qsize()}))

        b = self._body_json()

        if p == "/api/cancel":
            kind = str((b or {}).get("kind", "")).strip()
            ok = bool(current_id and cancel_long_job(current_id, kind))
            return self._send(200, json.dumps({"ok": ok, "cancelled": ok}, ensure_ascii=False))

        if p == "/api/start":
            with lock:
                _cancel_background_ai()
                recording = True
                capture_heartbeat = time.time()
                _caffeinate(True)    # 録音中はMacをスリープさせない
                return self._send(200, json.dumps(self._state(), ensure_ascii=False))

        if p == "/api/stop":
            with lock:
                recording = False
                capture_heartbeat = 0.0
                _caffeinate(False)
                clear_queue()   # 未処理チャンクを破棄 → 停止後に更新が続かない
                if current_id:
                    sync_to_drive(current_id)   # 会議データを共有ドライブへ非同期コピー
                    if current_id in detail_deferred:
                        detail_deferred.discard(current_id)
                        threading.Timer(.5, request_detail, args=(current_id,)).start()
                    if current_id in explore_deferred:
                        explore_deferred.discard(current_id)
                        threading.Timer(.8, explore_project, args=(current_id,)).start()
                    with deferred_lookup_lock:
                        waiting_lookups = deferred_lookups.pop(current_id, [])
                    for job in waiting_lookups: lookup_q.put(job)
                return self._send(200, json.dumps(self._state(), ensure_ascii=False))

        if p == "/api/new":
            with lock:
                recording = False
                capture_heartbeat = 0.0
                _caffeinate(False)
                clear_queue()
                current_id = new_session(b.get("title", ""), b.get("project_dir", ""),
                                         b.get("goal", ""), b.get("mtype", ""), b.get("stance", ""),
                                         b.get("language", LANGUAGE))
                save_state()
                return self._send(200, json.dumps(self._state(), ensure_ascii=False))

        if p == "/api/switch":
            sid = b.get("id", "")
            with lock:
                if is_session(sid):
                    recording = False
                    capture_heartbeat = 0.0
                    _caffeinate(False)
                    clear_queue()
                    current_id = sid
                    save_state()
                return self._send(200, json.dumps(self._state(), ensure_ascii=False))

        if p == "/api/delete":
            sid = str((b or {}).get("id", "")).strip()
            with lock:
                if recording:
                    return self._send(200, json.dumps({"ok": False, "msg": "録音中は削除できません。先に録音を停止してください"}, ensure_ascii=False))
                if not sid or sid != current_id:
                    return self._send(200, json.dumps({"ok": False, "msg": "表示中の会議のみ削除できます"}, ensure_ascii=False))
                clear_queue()
                ok, result = delete_session(sid)
                if not ok:
                    return self._send(200, json.dumps({"ok": False, "msg": result}, ensure_ascii=False))
                ss = list_sessions()
                current_id = ss[0]["id"] if ss else new_session("")
                save_state()
                return self._send(200, json.dumps({"ok": True, "deleted": sid,
                                                    "removed": len(result), "state": self._state()}, ensure_ascii=False))

        if p == "/api/rename":
            with lock:
                if current_id:
                    m = read_meta(current_id)
                    m["title"] = (b.get("title", "") or "").strip() or m.get("title", "")
                    write_meta(current_id, m)
                return self._send(200, json.dumps(self._state(), ensure_ascii=False))

        if p == "/api/slides":
            # 生成は時間がかかる（opusで数十秒）。ロックは取らず、現IDを固定して実行。
            if not current_id:
                return self._send(400, json.dumps({"ok": False, "err": "会議がありません"}))
            theme = "neutral"
            sid = current_id
            try:
                with long_job_scope(sid, "slides"): ok, msg = make_slides(theme, sid)
            except JobCancelled: ok, msg = False, "__cancelled__"
            except JobBusy: return self._send(200, json.dumps({"ok": False, "busy": True, "msg": "既に生成中です"}, ensure_ascii=False))
            return self._send(200, json.dumps(
                {"ok": ok, "cancelled": msg == "__cancelled__", "url": "/slides.html?ts=%d" % int(time.time()), "msg": msg},
                ensure_ascii=False))

        if p == "/api/deck":
            # 従来の経営者向けスライドデッキ生成（マインドマップ=/api/slidesとは別）
            if not current_id:
                return self._send(400, json.dumps({"ok": False, "err": "会議がありません"}))
            theme = "neutral"
            sid = current_id
            try:
                with long_job_scope(sid, "deck"): ok, msg = make_deck(theme, sid)
            except JobCancelled: ok, msg = False, "__cancelled__"
            except JobBusy: return self._send(200, json.dumps({"ok": False, "busy": True, "msg": "既に生成中です"}, ensure_ascii=False))
            return self._send(200, json.dumps(
                {"ok": ok, "cancelled": msg == "__cancelled__", "url": "/deck.html?ts=%d" % int(time.time()), "msg": msg},
                ensure_ascii=False))

        if p == "/api/map_pdf":
            if not current_id:
                return self._send(400, json.dumps({"ok": False, "err": "会議がありません"}))
            view = str((b or {}).get("view") or "radial")
            sid = current_id
            try:
                with long_job_scope(sid, "mappdf"): ok, msg = export_map_pdf(sid, view)
            except JobCancelled: ok, msg = False, "__cancelled__"
            except JobBusy: return self._send(200, json.dumps({"ok": False, "busy": True, "msg": "既に生成中です"}, ensure_ascii=False))
            return self._send(200, json.dumps(
                {"ok": ok, "cancelled": msg == "__cancelled__", "url": "/map.pdf?view=%s&ts=%d" % (view, int(time.time())), "msg": msg},
                ensure_ascii=False))

        if p == "/api/minutes_pdf":
            if not current_id:
                return self._send(400, json.dumps({"ok": False, "err": "会議がありません"}))
            sid = current_id
            try:
                with long_job_scope(sid, "minutespdf"): ok, msg = export_minutes_pdf(sid)
            except JobCancelled: ok, msg = False, "__cancelled__"
            except JobBusy: return self._send(200, json.dumps({"ok": False, "busy": True, "msg": "既に生成中です"}, ensure_ascii=False))
            return self._send(200, json.dumps(
                {"ok": ok, "cancelled": msg == "__cancelled__", "url": "/minutes.pdf?ts=%d" % int(time.time()), "msg": msg},
                ensure_ascii=False))

        if p == "/api/learn_slides":
            # 学びレポートのスライド化（依頼者のボタン押下時のみ。自動生成しない＝opusの無駄打ち防止）
            if not current_id:
                return self._send(400, json.dumps({"ok": False, "err": "会議がありません"}))
            sid = current_id
            try:
                with long_job_scope(sid, "learnslides"): ok, msg = make_learn_deck(sid)
            except JobCancelled: ok, msg = False, "__cancelled__"
            except JobBusy: return self._send(200, json.dumps({"ok": False, "busy": True, "msg": "既に生成中です"}, ensure_ascii=False))
            return self._send(200, json.dumps(
                {"ok": ok, "cancelled": msg == "__cancelled__", "url": "/learn-slides.html?ts=%d" % int(time.time()), "msg": msg},
                ensure_ascii=False))

        if p == "/api/finalize":
            # 会議後の一括清書。保存済み原本音声を結合→whisper一括→claude整理（時間がかかる）
            if not current_id:
                return self._send(400, json.dumps({"ok": False, "msg": "会議がありません"}, ensure_ascii=False))
            body = b or {}   # ボディは do_POST 冒頭で読み済み（再読みすると永遠にブロックする）
            hints = body.get("hints", "")
            speaker_map = body.get("speakerMap") if isinstance(body.get("speakerMap"), dict) else {}
            if body.get("answers") is not None:
                save_prep_answers(current_id, body.get("answers"), speaker_map)   # 次回の確認初期値に
            sid = current_id
            try:
                with long_job_scope(sid, "finalize"): ok, msg = finalize_meeting(sid, hints, speaker_map)
            except JobCancelled: ok, msg = False, "__cancelled__"
            except JobBusy: return self._send(200, json.dumps({"ok": False, "busy": True, "msg": "既に清書中です"}, ensure_ascii=False))
            if ok:
                sync_to_drive(sid)     # 清書版を共有ドライブへ非同期コピー
                sync_to_project(sid)   # 清書一式を背景フォルダ（案件フォルダ）へも届ける
            return self._send(200, json.dumps({"ok": ok, "cancelled": msg == "__cancelled__", "msg": msg}, ensure_ascii=False))

        if p == "/api/finalize_prep":
            # 清書前：文字起こしから確認したい固有名詞のQ&Aを生成して返す（prep.jsonにキャッシュ）
            if not current_id:
                return self._send(400, json.dumps({"ok": False, "msg": "会議がありません"}, ensure_ascii=False))
            regen = bool((b or {}).get("regen"))   # ボディは読み済み。再読み禁止
            sid = current_id
            try:
                with long_job_scope(sid, "finalize_prep"): ok, res = finalize_prep(sid, regen)
            except JobCancelled: ok, res = False, "__cancelled__"
            except JobBusy: return self._send(200, json.dumps({"ok": False, "busy": True, "msg": "既に準備中です"}, ensure_ascii=False))
            if ok:
                return self._send(200, json.dumps({"ok": True, **res}, ensure_ascii=False))
            return self._send(200, json.dumps({"ok": False, "cancelled": res == "__cancelled__", "msg": res}, ensure_ascii=False))

        if p == "/api/profile":
            # 依頼主プロフィール（私は誰か）を保存。全会議共通・以後の整理/ガイド/清書/下調べに即反映。
            # 一問一答の回答(fields)は profile.json に保存し（フォーム再表示用）、
            # AIに注入する整形テキストを profile.md に書く。旧形式 {"text": ...} も受ける。
            body = b or {}
            fields = body.get("fields")
            try:
                if isinstance(fields, dict):
                    # 「会議での立場」はプロフィールではなく会議ごとの設定（meta.stance）に持つ
                    fields = {k: str(fields.get(k, "")).strip()
                              for k in ("name", "org", "notes")}
                    lines = []
                    if fields["name"]:  lines.append(_t("名前：", "Name: ") + fields["name"])
                    if fields["org"]:   lines.append(_t("会社・役職：", "Company and role: ") + fields["org"])
                    if fields["notes"]: lines.append(_t("補足：", "Notes: ") + fields["notes"])
                    txt = "\n".join(lines)
                    with open(os.path.splitext(PROFILE_MD)[0] + ".json", "w", encoding="utf-8") as f:
                        json.dump(fields, f, ensure_ascii=False, indent=2)
                else:
                    txt = str(body.get("text", "")).strip()
                with open(PROFILE_MD, "w", encoding="utf-8") as f:
                    f.write(txt + ("\n" if txt else ""))
                _prof_cache[0] = 0.0   # キャッシュ破棄＝次のサイクルから反映
                return self._send(200, json.dumps({"ok": True}))
            except Exception as e:
                return self._send(200, json.dumps({"ok": False, "msg": repr(e)}, ensure_ascii=False))

        if p == "/api/dismiss-question":
            # 質問候補の個別却下（×ボタン）。以後同じ質問は再提案しない
            if not current_id:
                return self._send(400, json.dumps({"ok": False}, ensure_ascii=False))
            q = str((b or {}).get("q") or "").strip()
            if q:
                with data_write_lock:
                    obj = _read_live_data(current_id)
                    dq = [x for x in (obj.get("_dismissedQ") or []) if x != q] + [q]
                    obj["_dismissedQ"] = dq[-30:]
                    g = obj.get("guide") or {}
                    if g.get("questions"):
                        g["questions"] = [x for x in g["questions"] if str(x.get("q") or "") != q]
                    tmp = os.path.join(sdir(current_id), "data.json.tmp")
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(obj, f, ensure_ascii=False, indent=2)
                    os.replace(tmp, os.path.join(sdir(current_id), "data.json"))
            return self._send(200, json.dumps({"ok": True}, ensure_ascii=False))

        if p == "/api/live-notes":
            if not current_id:
                return self._send(400, json.dumps({"ok": False, "msg": _t("会議がありません", "No meeting")}, ensure_ascii=False))
            ok, result = add_live_note(current_id, (b or {}).get("text", ""))
            return self._send(200, json.dumps({"ok": ok, **({"notes": result} if ok else {"msg": result})}, ensure_ascii=False))

        if p == "/api/strategy":
            if not current_id:
                return self._send(400, json.dumps({"ok": False, "msg": "会議がありません"}, ensure_ascii=False))
            body = b or {}
            if body.get("reset"):
                try: os.remove(_strategy_path(current_id))
                except FileNotFoundError: pass
                dst = _strategy_export_dir(current_id)
                if dst:
                    try: shutil.rmtree(dst)
                    except FileNotFoundError: pass
                return self._send(200, json.dumps({"ok": True, "messages": [], "brief": "", "board": {}}, ensure_ascii=False))
            message = str(body.get("message", "")).strip()
            if not message:
                return self._send(400, json.dumps({"ok": False, "msg": "メッセージが空です"}, ensure_ascii=False))
            ok, result = strategy_chat(current_id, message)
            return self._send(200, json.dumps({"ok": ok, **(result if ok else {"msg": result})}, ensure_ascii=False))

        if p == "/api/strategy_open":
            if not current_id:
                return self._send(400, json.dumps({"ok": False, "msg": "会議がありません"}, ensure_ascii=False))
            meta = read_meta(current_id); dst = _strategy_export_dir(current_id, meta)
            pdir = (meta.get("project_dir") or "").strip()
            if not dst or not pdir or not os.path.isdir(pdir):
                return self._send(200, json.dumps({"ok": False, "msg": "背景フォルダが未設定です"}, ensure_ascii=False))
            try:
                if os.path.commonpath([os.path.realpath(dst), os.path.realpath(pdir)]) != os.path.realpath(pdir):
                    raise ValueError("保存先が背景フォルダ外です")
                os.makedirs(dst, exist_ok=True)
                subprocess.Popen(["open", dst], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return self._send(200, json.dumps({"ok": True, "path": dst}, ensure_ascii=False))
            except Exception as e:
                return self._send(200, json.dumps({"ok": False, "msg": repr(e)}, ensure_ascii=False))

        if p == "/api/goal":
            # 現在の会議に目標・背景フォルダを設定（商談ガイドON）。フォルダが変わったら再探索
            if not current_id:
                return self._send(400, json.dumps({"ok": False, "msg": "会議がありません"}, ensure_ascii=False))
            m = read_meta(current_id)
            old_project_dir = (m.get("project_dir") or "").strip()
            goal = str((b or {}).get("goal", "")).strip()
            pd = str((b or {}).get("project_dir", "")).strip()
            if pd and not os.path.isdir(pd):
                return self._send(200, json.dumps({"ok": False, "msg": "フォルダが見つかりません: " + pd}, ensure_ascii=False))
            changed = ((pd and pd != m.get("project_dir", "")) or goal != (m.get("goal", "") or ""))
            m["goal"] = goal
            if (b or {}).get("mtype") is not None:
                m["mtype"] = str(b.get("mtype", "")).strip()
            if (b or {}).get("stance") is not None:
                m["stance"] = str(b.get("stance", "")).strip()
            if pd:
                if old_project_dir and pd != old_project_dir:
                    old_base = os.path.join(old_project_dir, "会議準備")
                    for old in glob.glob(os.path.join(old_base, current_id + "*")):
                        if os.path.basename(old) == current_id or os.path.basename(old).startswith(current_id + " "):
                            try: shutil.rmtree(old)
                            except FileNotFoundError: pass
                m["project_dir"] = pd
                _remember_project(pd)
            write_meta(current_id, m)
            if changed:
                # 案件/目標が変わったら、前の会社の探索・調査結果を混ぜない。
                for fn in ("context.json", "research.json"):
                    try: os.remove(os.path.join(sdir(current_id), fn))
                    except FileNotFoundError: pass
                if hasattr(queue_lookups, "_seen"):
                    queue_lookups._seen.pop(current_id, None)
            if changed or (pd and not os.path.isfile(os.path.join(sdir(current_id), "context.json"))):
                explore_project(current_id)
            if _load_strategy(current_id):
                sync_strategy_to_project(current_id)
            return self._send(200, json.dumps({"ok": True, "exploring": exploring.get(current_id, False)}, ensure_ascii=False))

        if p == "/api/learn":
            # 会議の学びを抽出して返す（承認制。保存は /api/learn_save）
            if not current_id:
                return self._send(400, json.dumps({"ok": False, "msg": "会議がありません"}, ensure_ascii=False))
            ok, res = extract_learnings(current_id)
            if ok:
                m = read_meta(current_id)
                ins = res.get("insights", "")
                if ins.strip():
                    # レポートは作った時点で成果物として自動保存する（2026-07-16 依頼者指示）。
                    # 承認制なのはプレイブックへの一般化ノウハウ追記のみ
                    try:
                        with open(os.path.join(sdir(current_id), "learnings.md"), "w", encoding="utf-8") as f:
                            f.write("# 学びと次の一手 — %s（%s）\n\n%s\n" % (m.get("title", "会議"),
                                    time.strftime("%Y-%m-%d %H:%M"), ins))
                        # 古い学びスライドは旧レポートの内容なので無効化する（2026-07-17 依頼者指摘：
                        # 作り直したのに「スライドを見る」が旧内容を開くのは矛盾）。カードは「スライドを作る」に戻る
                        try:
                            os.remove(os.path.join(sdir(current_id), "learn-slides.html"))
                            sys.stderr.write("[LEARN-SAVE] %s 旧学びスライドを無効化\n" % current_id)
                        except FileNotFoundError:
                            pass
                        try:
                            os.remove(os.path.join(sdir(current_id), "learn-slides.pdf"))
                        except FileNotFoundError:
                            pass
                        sync_to_drive(current_id)
                    except Exception as e:
                        sys.stderr.write("[LEARN-SAVE] %s 失敗 %r\n" % (current_id, e)); sys.stderr.flush()
                return self._send(200, json.dumps({"ok": True, "insights": ins,
                                                    "playbook": res.get("playbook", ""),
                                                    "mtype": (m.get("mtype") or "").strip()}, ensure_ascii=False))
            return self._send(200, json.dumps({"ok": False, "msg": res}, ensure_ascii=False))

        if p == "/api/learn_save":
            # 依頼者が承認・編集した学びをプレイブックに追記
            if not current_id:
                return self._send(400, json.dumps({"ok": False, "msg": "会議がありません"}, ensure_ascii=False))
            m = read_meta(current_id)
            mtype = (m.get("mtype") or "").strip()
            text = str((b or {}).get("text", "")).strip()
            if not mtype:
                return self._send(200, json.dumps({"ok": False, "msg": "用途が未設定です"}, ensure_ascii=False))
            ok = append_playbook(mtype, m.get("title", "会議"), text)
            return self._send(200, json.dumps({"ok": ok, "path": _playbook_path(mtype)}, ensure_ascii=False))

        if p == "/api/pickdir":
            # macOSネイティブのフォルダ選択ダイアログを開いてパスを返す（ブラウザでは絶対パスが取れないため）
            try:
                r = subprocess.run(["osascript",
                                    "-e", 'tell application "System Events" to activate',
                                    "-e", 'POSIX path of (choose folder with prompt "背景フォルダを選択してください")'],
                                   capture_output=True, text=True, timeout=300)
                path = (r.stdout or "").strip().rstrip("/")
                if r.returncode == 0 and path:
                    return self._send(200, json.dumps({"ok": True, "path": path}, ensure_ascii=False))
                return self._send(200, json.dumps({"ok": False, "msg": "キャンセル"}, ensure_ascii=False))
            except Exception as e:
                return self._send(200, json.dumps({"ok": False, "msg": "ダイアログを開けませんでした: %r" % e}, ensure_ascii=False))

        if p == "/api/import_notes":
            # 事前メモ（Claude Code/Codex等で残した背景ファイル・フォルダ）を読み、準備チャットへ注入してブリーフに反映
            if not current_id:
                return self._send(400, json.dumps({"ok": False, "msg": "会議がありません"}, ensure_ascii=False))
            body = b or {}
            path = str(body.get("path", "")).strip()
            if not path:
                kind = "folder" if body.get("kind") == "folder" else "file"
                osa = ('POSIX path of (choose folder with prompt "取り込む事前メモのフォルダを選択してください")'
                       if kind == "folder" else
                       'POSIX path of (choose file with prompt "取り込む事前メモ（ファイル）を選択してください")')
                try:
                    # launchd配下のosascriptダイアログは他ウィンドウの背面に出て「無反応」に見える
                    # （2026-07-17 実障害）。System Eventsをactivateして最前面に出す
                    r = subprocess.run(["osascript",
                                        "-e", 'tell application "System Events" to activate',
                                        "-e", osa], capture_output=True, text=True, timeout=300)
                    path = (r.stdout or "").strip().rstrip("/")
                    if r.returncode != 0 or not path:
                        return self._send(200, json.dumps({"ok": False, "msg": "キャンセル"}, ensure_ascii=False))
                except Exception as e:
                    return self._send(200, json.dumps({"ok": False, "msg": "ダイアログを開けませんでした: %r" % e}, ensure_ascii=False))
            text, used = _read_import_notes(path)
            if not text:
                return self._send(200, json.dumps({"ok": False, "msg": "読み込めるテキストがありませんでした（対応形式：md/txt/json/yaml/csv）"}, ensure_ascii=False))
            message = ("以下は依頼主が事前に用意した背景メモ（%s）の全文です。\n"
                       "replyは依頼主が「AIが正しく理解したか」を答え合わせできる中規模のレポートにしてください：\n"
                       "1) 案件の全体像（2〜3文） 2) 把握した重要な事実・数字・関係者（箇条書きで5件前後） "
                       "3) 解釈に自信がない点・確認したい質問（最大3件・番号付き）。\n"
                       "あわせて着地点・仮説・会議で聞くこと・懸念を準備ボードとbriefへ反映してください。\n---\n%s"
                       % (used or path, text))
            ok, result = strategy_chat(current_id, message)
            return self._send(200, json.dumps({"ok": ok, **(result if ok else {"msg": result})}, ensure_ascii=False))

        if p == "/api/explore":
            # 背景フォルダを読み直す（手動再探索）
            if not current_id:
                return self._send(400, json.dumps({"ok": False, "msg": "会議がありません"}, ensure_ascii=False))
            for fn in ("context.json", "research.json"):
                try: os.remove(os.path.join(sdir(current_id), fn))
                except FileNotFoundError: pass
            if hasattr(queue_lookups, "_seen"):
                queue_lookups._seen.pop(current_id, None)
            explore_project(current_id)
            return self._send(200, json.dumps({"ok": True}, ensure_ascii=False))

        if p == "/api/assist_verify":
            # AIサポートの疑問をWeb検索で裏取り（オンデマンド・時間がかかる）
            q = (b or {}).get("q", "").strip()   # ボディは読み済み。再読み禁止
            if not q:
                return self._send(400, json.dumps({"ok": False, "msg": "問いがありません"}, ensure_ascii=False))
            ok, ans = assist_verify(q)
            return self._send(200, json.dumps({"ok": ok, "answer": ans}, ensure_ascii=False))

        return self._send(404, "not found", "text/plain; charset=utf-8")


def main():
    global current_id, recording
    # 起動時：前回の会議を復元。無ければ最新、それも無ければ新規作成。
    saved = load_state()
    if is_session(saved):
        current_id = saved
    else:
        ss = list_sessions()
        current_id = ss[0]["id"] if ss else new_session("")
    save_state()

    # 音声文字起こしとAI整理を分離。長時間話し続けてもdata.json更新を止めない。
    if recover_pending_chunks():
        recording = True
        _caffeinate(True)
    threading.Thread(target=chunk_worker, daemon=True).start()
    threading.Thread(target=analysis_worker, daemon=True).start()
    threading.Thread(target=active_view_worker, daemon=True).start()
    threading.Thread(target=detail_worker, daemon=True).start()
    threading.Thread(target=live_diarization_worker, daemon=True).start()
    threading.Thread(target=analysis_watchdog, daemon=True).start()
    try:
        tp, dp = os.path.join(sdir(current_id), "transcript.txt"), os.path.join(sdir(current_id), "data.json")
        if os.path.isfile(tp) and (not os.path.isfile(dp) or os.path.getmtime(tp) > os.path.getmtime(dp)):
            request_analysis(current_id)
    except Exception:
        pass
    threading.Thread(target=lookup_worker, daemon=True).start()   # 背景フォルダの自動下調べ係

    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)

    def shutdown(*_):
        # signal handlerとserve_foreverは同じメインスレッド。ここで直接shutdownすると
        # 待ち合わせが自己デッドロックになるため、別スレッドから停止させる。
        try: threading.Thread(target=srv.shutdown, daemon=True).start()
        except Exception: pass

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print("════════════════════════════════════════════")
    print(" 議事ライブ整理  ｜ http://127.0.0.1:%d/" % PORT)
    print(" 会議データ: %s" % SESS)
    _asr = ("mlx:%s" % MLX_MODEL.split("/")[-1]) if ASR_BACKEND == "mlx" else "whisper-cli"
    print(" 文字起こし=%s / 整理=%s / スライド=%s   停止: Ctrl+C" % (_asr, CLAUDE_MODEL, SLIDE_MODEL))
    print("════════════════════════════════════════════")
    srv.serve_forever()


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--live-mtg-helper":
        helper = os.path.basename(sys.argv[2])
        if helper not in {"make-mindmap.py"}:
            raise SystemExit("unknown helper")
        runpy.run_path(os.path.join(SCRIPT_DIR, helper), run_name="__main__")
    else:
        main()
