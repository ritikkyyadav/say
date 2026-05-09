// Tauri shell entrypoint. Spawns the Python `say-engine` sidecar, exposes
// `get_engine_endpoint` to the React app, and tears the sidecar down on
// quit. The hotkey + dictation logic lives entirely in the sidecar; the
// shell is just a window over its HTTP API.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod sidecar;

use std::sync::Mutex;
use std::time::Duration;

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
    // If the runtime file is fresh and reachable, reuse it; otherwise
    // spawn the bundled engine.
    if sidecar::is_runtime_fresh(Duration::from_secs(5)) {
        return;
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
            reveal_logs_dir
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
            if let RunEvent::ExitRequested { .. } = event {
                let state: State<EngineState> = app.state();
                let to_kill = state
                    .sidecar
                    .lock()
                    .ok()
                    .and_then(|mut g| g.take());
                if let Some(mut child) = to_kill {
                    let _ = child.kill();
                }
            }
        });
}

fn main() {
    run();
}
