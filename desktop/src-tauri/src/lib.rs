use std::fs;
use std::process::Command;
use std::sync::Mutex;
use std::thread;
use std::time::Duration;
use tauri::{Manager, RunEvent, WindowEvent};
use tauri_plugin_shell::{process::CommandChild, ShellExt};

const BACKEND_PORT: &str = "18777";

struct BackendProcess {
    child: CommandChild,
    pid: u32,
}

struct BackendState(Mutex<Option<BackendProcess>>);

fn stop_backend(state: &BackendState) {
    if let Ok(mut guard) = state.0.lock() {
        if let Some(process) = guard.take() {
            // PyInstaller onefileはブートローダーの子としてPython本体を起動する。
            // 親だけkillするとサーバーが孤児化するため、先にプロセスツリーを止める。
            #[cfg(unix)]
            {
                let _ = Command::new("pkill")
                    .args(["-TERM", "-P", &process.pid.to_string()])
                    .status();
                thread::sleep(Duration::from_millis(350));
            }
            #[cfg(target_os = "windows")]
            {
                let _ = Command::new("taskkill")
                    .args(["/PID", &process.pid.to_string(), "/T", "/F"])
                    .status();
            }
            let _ = process.child.kill();
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(BackendState(Mutex::new(None)))
        .setup(|app| {
            let data_dir = app.path().app_data_dir()?;
            fs::create_dir_all(&data_dir)?;

            let mut command = app
                .shell()
                .sidecar("live-mtg-backend")?
                .env("LIVE_MTG_DESKTOP", "1")
                .env("RUN", data_dir.to_string_lossy().to_string())
                .env(
                    "MEETINGS_DIR",
                    data_dir.join("meetings").to_string_lossy().to_string(),
                )
                .env(
                    "DRIVE_SYNC_DIR",
                    data_dir.join("meetings").to_string_lossy().to_string(),
                )
                .env(
                    "PROFILE_MD",
                    data_dir.join("profile.md").to_string_lossy().to_string(),
                )
                .env(
                    "PLAYBOOK_DIR",
                    data_dir.join("playbooks").to_string_lossy().to_string(),
                )
                .env("PORT", BACKEND_PORT);

            #[cfg(target_os = "windows")]
            {
                command = command.env("ASR_BACKEND", "cpp");
            }
            #[cfg(target_os = "macos")]
            {
                command = command.env("ASR_BACKEND", "mlx");
            }

            let (_events, child) = command.spawn()?;
            let pid = child.pid();
            app.state::<BackendState>()
                .0
                .lock()
                .unwrap()
                .replace(BackendProcess { child, pid });
            Ok(())
        })
        .on_window_event(|window, event| {
            if matches!(event, WindowEvent::Destroyed) {
                stop_backend(&window.state::<BackendState>());
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building LiveMTG");

    app.run(|handle, event| {
        if matches!(event, RunEvent::Exit) {
            stop_backend(&handle.state::<BackendState>());
        }
    });
}
