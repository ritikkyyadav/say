// Tauri shell entrypoint. Spawns the Python `say-engine` sidecar, exposes
// `get_engine_endpoint` to the React app, and tears the sidecar down on
// quit. The hotkey + dictation logic lives entirely in the sidecar; the
// shell is just a window over its HTTP API.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod sidecar;

use std::sync::Mutex;

use serde::Serialize;
use tauri::{AppHandle, Manager, RunEvent, State, WindowEvent};

#[derive(Default)]
struct EngineState {
    sidecar: Mutex<Option<sidecar::Sidecar>>,
}

#[derive(Serialize, Clone)]
struct EngineEndpoint {
    host: String,
    port: u16,
    token: String,
}

#[tauri::command]
fn get_engine_endpoint() -> Option<EngineEndpoint> {
    let info = sidecar::read_runtime_info()?;
    Some(EngineEndpoint {
        host: info.host,
        port: info.port,
        token: info.token,
    })
}

/// Open System Settings to a specific Privacy pane.
///
/// Accepted values: "microphone", "accessibility", "input-monitoring".
/// Anything else falls back to the Privacy & Security root.
#[tauri::command]
fn open_privacy_settings(pane: String) -> Result<(), String> {
    let url = match pane.as_str() {
        "microphone" => "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
        "accessibility" => {
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
        }
        "input-monitoring" => {
            "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"
        }
        _ => "x-apple.systempreferences:com.apple.preference.security?Privacy",
    };
    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .arg(url)
            .spawn()
            .map_err(|e| e.to_string())?;
    }
    Ok(())
}

/// Reveal a specific file or folder in the system file manager, selecting it.
/// Used after exporting a recordings ZIP so the user can grab it immediately.
#[tauri::command]
fn reveal_logs_dir() -> Result<(), String> {
    let path = sidecar::logs_dir();
    let path_str = path.to_string_lossy().to_string();
    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .arg(&path_str)
            .spawn()
            .map_err(|e| e.to_string())?;
    }
    #[cfg(target_os = "windows")]
    {
        std::process::Command::new("explorer")
            .arg(&path_str)
            .spawn()
            .map_err(|e| e.to_string())?;
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        std::process::Command::new("xdg-open")
            .arg(&path_str)
            .spawn()
            .map_err(|e| e.to_string())?;
    }
    Ok(())
}

fn ensure_sidecar(app: &AppHandle, state: State<EngineState>) {
    // Every launch ends up with an engine this shell owns and can therefore
    // shut down. An engine already running is only reused when it is *ours*;
    // an orphan left over from a previous run is terminated first, because
    // adopting one is what used to make the problem permanent — the shell only
    // ever killed engines it had spawned, so an adopted engine survived the
    // quit, was adopted again next launch, and survived that quit too.
    match sidecar::engine_status() {
        sidecar::EngineStatus::Ours(_) => return,
        // Not ours to manage: another live shell's engine, or one a developer
        // started by hand. Leave it running and talk to it.
        sidecar::EngineStatus::Foreign(_) => return,
        sidecar::EngineStatus::Orphan(pid) => {
            eprintln!(
                "[say-desktop] terminating orphaned engine (pid {pid}) left by a previous run"
            );
            sidecar::terminate_engine(pid);
        }
        sidecar::EngineStatus::None => {}
    }
    let mut guard = state.sidecar.lock().unwrap();
    if guard.is_some() {
        return;
    }
    match sidecar::spawn(app) {
        Ok(child) => {
            *guard = Some(child);
        }
        Err(err) => {
            eprintln!("[say-desktop] could not start engine sidecar: {err}");
        }
    }
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(EngineState::default())
        .invoke_handler(tauri::generate_handler![
            get_engine_endpoint,
            reveal_logs_dir,
            open_privacy_settings
        ])
        .setup(|app| {
            let state: State<EngineState> = app.state();
            ensure_sidecar(&app.handle(), state);
            Ok(())
        })
        .on_window_event(|window, event| {
            // Cmd+W / clicking the close button hides the window but
            // leaves the engine running. Cmd+Q triggers full exit via
            // RunEvent::ExitRequested below.
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .build(tauri::generate_context!())
        .expect("failed to build Say desktop app")
        .run(|app, event| {
            // Both events, because neither is guaranteed: ExitRequested can be
            // consumed and Exit is the last thing we ever see. `shutdown_engine`
            // is idempotent — once the engine is gone the second call finds
            // nothing and returns immediately — so running it twice costs
            // nothing and missing it costs the user an invisible engine.
            //
            // Note this is only the *fast* path. It cannot run at all under
            // Force Quit (SIGKILL), which is why the engine watches our pid and
            // exits on its own. See `owner_watch.py`.
            if matches!(event, RunEvent::ExitRequested { .. } | RunEvent::Exit) {
                // Dropping the handle runs `Sidecar::drop`, which tears the
                // engine down too; the explicit call after it is what covers an
                // engine this shell adopted rather than spawned — the case that
                // previously had nobody responsible for it.
                let state: State<EngineState> = app.state();
                if let Ok(mut g) = state.sidecar.lock() {
                    drop(g.take());
                }
                sidecar::shutdown_engine();
            }
        });
}

fn main() {
    run();
}
