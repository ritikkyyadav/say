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
fn reveal_path(path: String) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .arg("-R")
            .arg(&path)
            .spawn()
            .map_err(|e| e.to_string())?;
    }
    #[cfg(target_os = "windows")]
    {
        std::process::Command::new("explorer")
            .arg("/select,")
            .arg(&path)
            .spawn()
            .map_err(|e| e.to_string())?;
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        // No portable "reveal + select"; open the containing directory.
        let dir = std::path::Path::new(&path)
            .parent()
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or(path.clone());
        std::process::Command::new("xdg-open")
            .arg(&dir)
            .spawn()
            .map_err(|e| e.to_string())?;
    }
    Ok(())
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
    // Reuse the existing engine process if it is still alive.
    // PID check is more reliable than file-age: avoids double-spawning when
    // the engine is healthy but started more than a few seconds ago.
    if sidecar::is_engine_alive() {
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
            reveal_logs_dir,
            reveal_path,
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
