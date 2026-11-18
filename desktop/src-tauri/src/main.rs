// Tauri shell entrypoint. Spawns the Python `say-engine` sidecar, exposes
// `get_engine_endpoint` to the React app, and tears the sidecar down on
// quit. The hotkey + dictation logic lives entirely in the sidecar; the
// shell is just a window over its HTTP API.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

#[cfg(all(not(debug_assertions), not(feature = "custom-protocol")))]
compile_error!("Release builds require --features custom-protocol to embed the frontend.");

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

/// Open the platform's microphone/privacy settings, using fixed commands only.
/// macOS also supports Accessibility and Input Monitoring privacy panes.
#[tauri::command]
fn open_privacy_settings(pane: String) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        let url = match pane.as_str() {
            "microphone" => {
                "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
            }
            "accessibility" => {
                "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
            }
            "input-monitoring" => {
                "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"
            }
            _ => "x-apple.systempreferences:com.apple.preference.security?Privacy",
        };
        std::process::Command::new("open")
            .arg(url)
            .spawn()
            .map_err(|e| e.to_string())?;
    }
    #[cfg(target_os = "linux")]
    {
        return open_linux_privacy_settings(&pane);
    }
    #[cfg(not(any(target_os = "macos", target_os = "linux")))]
    let _ = pane;
    #[cfg(not(target_os = "linux"))]
    Ok(())
}

#[cfg(any(target_os = "linux", test))]
type SettingsCommand = (&'static str, &'static [&'static str]);

#[cfg(any(target_os = "linux", test))]
fn linux_settings_commands(pane: &str) -> Result<&'static [SettingsCommand], String> {
    match pane {
        "microphone" => Ok(&[
            ("pavucontrol", &[]),
            ("gnome-control-center", &["sound"]),
            ("systemsettings", &["kcm_pulseaudio"]),
        ]),
        "accessibility" | "input-monitoring" => Err(
            "Linux has no macOS-style Accessibility or Input Monitoring permission pane. \
             Configure your desktop's keyboard/input permissions manually; on Wayland, \
             global shortcuts and automatic text insertion may be restricted."
                .into(),
        ),
        _ => Err(
            "Unsupported settings pane. On Linux, use \"microphone\" to open sound settings."
                .into(),
        ),
    }
}

#[cfg(target_os = "linux")]
fn open_linux_privacy_settings(pane: &str) -> Result<(), String> {
    let mut failures = Vec::new();
    for (program, args) in linux_settings_commands(pane)? {
        match std::process::Command::new(program)
            .args(*args)
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
        {
            Ok(mut child) => {
                // Settings windows can live for hours. Do not block Tauri's
                // command thread, but do reap the launcher when it exits.
                std::thread::spawn(move || {
                    let _ = child.wait();
                });
                return Ok(());
            }
            Err(err) => failures.push(format!("{program}: {err}")),
        }
    }
    Err(format!(
        "Could not open microphone settings. Install pavucontrol, GNOME Settings, \
         or KDE System Settings, or open your desktop's Sound/Input settings manually. {}",
        failures.join("; ")
    ))
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
    let builder = tauri::Builder::default().plugin(tauri_plugin_shell::init());
    #[cfg(target_os = "linux")]
    let builder = builder.plugin(tauri_plugin_single_instance::init(|app, _, _| {
        if let Some(window) = app.get_webview_window("main") {
            let _ = window.show();
            let _ = window.unminimize();
            let _ = window.set_focus();
        }
    }));
    builder
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
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                // There is no Linux tray to recover a hidden window from.
                // A normal close must take the engine down via the exit hook.
                #[cfg(target_os = "linux")]
                window.app_handle().exit(0);
                // Preserve Cmd+W / close-to-hide on macOS and Windows;
                // Cmd+Q / explicit quit still reaches the exit hook below.
                #[cfg(not(target_os = "linux"))]
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

#[cfg(test)]
mod tests {
    use super::linux_settings_commands;

    #[test]
    fn linux_microphone_settings_use_only_fixed_programs_and_arguments() {
        assert_eq!(
            linux_settings_commands("microphone").unwrap(),
            &[
                ("pavucontrol", &[][..]),
                ("gnome-control-center", &["sound"][..]),
                ("systemsettings", &["kcm_pulseaudio"][..]),
            ]
        );
    }

    #[test]
    fn linux_rejects_unsupported_or_untrusted_settings_panes() {
        for pane in [
            "accessibility",
            "input-monitoring",
            "",
            "sound; touch /tmp/say",
            "../sound",
        ] {
            assert!(linux_settings_commands(pane).is_err(), "accepted {pane:?}");
        }
    }
}
