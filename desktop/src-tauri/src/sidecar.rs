// Engine sidecar process manager.
//
// Looks for the bundled `say-engine` binary inside the .app's Resources
// directory and launches it with `--server --no-menubar`. In dev,
// `SAY_ENGINE_BIN` overrides the search.

use std::env;
use std::fs;
use std::io;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant, SystemTime};

use serde::Deserialize;
use tauri::{AppHandle, Manager};

#[derive(Debug, Deserialize, Clone)]
pub struct RuntimeInfo {
    pub host: String,
    pub port: u16,
    pub token: String,
    #[serde(default)]
    pub pid: Option<i64>,
}

pub struct Sidecar {
    // The engine is launched via LaunchServices (`open`), which detaches it
    // from this process, so we have no child handle. We track it through the
    // pid the engine records in runtime.json and signal that on shutdown.
    #[allow(dead_code)]
    engine_app: PathBuf,
}

impl Sidecar {
    /// Graceful shutdown: SIGTERM the engine pid from runtime.json, wait up to
    /// 3s for it to flush its SQLite store and release the single-instance
    /// lock, then SIGKILL as a last resort.
    pub fn kill(&mut self) -> io::Result<()> {
        let Some(pid) = read_runtime_info().and_then(|i| i.pid) else {
            return Ok(());
        };
        let pid = pid as libc::pid_t;
        #[cfg(unix)]
        {
            if unsafe { libc::kill(pid, 0) } != 0 {
                return Ok(()); // already gone
            }
            unsafe { libc::kill(pid, libc::SIGTERM) };
            let deadline = Instant::now() + Duration::from_secs(3);
            loop {
                if unsafe { libc::kill(pid, 0) } != 0 {
                    return Ok(()); // exited
                }
                if Instant::now() >= deadline {
                    unsafe { libc::kill(pid, libc::SIGKILL) };
                    return Ok(());
                }
                std::thread::sleep(Duration::from_millis(100));
            }
        }
        #[cfg(not(unix))]
        {
            Ok(())
        }
    }
}

impl Drop for Sidecar {
    fn drop(&mut self) {
        let _ = self.kill();
    }
}

pub fn app_data_dir() -> PathBuf {
    if cfg!(target_os = "macos") {
        if let Some(home) = dirs::home_dir() {
            return home
                .join("Library")
                .join("Application Support")
                .join("Say");
        }
    } else if cfg!(target_os = "windows") {
        if let Some(roaming) = dirs::config_dir() {
            return roaming.join("Say");
        }
    } else if let Some(data) = dirs::data_dir() {
        return data.join("Say");
    }
    PathBuf::from(".")
}

pub fn logs_dir() -> PathBuf {
    if cfg!(target_os = "macos") {
        if let Some(home) = dirs::home_dir() {
            return home.join("Library").join("Logs").join("Say");
        }
    }
    app_data_dir().join("logs")
}

pub fn runtime_path() -> PathBuf {
    app_data_dir().join("runtime.json")
}

pub fn read_runtime_info() -> Option<RuntimeInfo> {
    let p = runtime_path();
    let bytes = fs::read(&p).ok()?;
    serde_json::from_slice(&bytes).ok()
}

#[allow(dead_code)]
pub fn is_runtime_fresh(max_age: Duration) -> bool {
    let p = runtime_path();
    let Ok(meta) = fs::metadata(&p) else {
        return false;
    };
    let Ok(modified) = meta.modified() else {
        return false;
    };
    let Ok(age) = SystemTime::now().duration_since(modified) else {
        return false;
    };
    age <= max_age && read_runtime_info().is_some()
}

/// Check whether the engine process recorded in runtime.json is still alive.
/// Uses kill(pid, 0) on Unix — no signal is sent; it just checks existence.
/// Returns false if runtime.json is absent, has no pid, or the process is gone.
pub fn is_engine_alive() -> bool {
    let Some(info) = read_runtime_info() else {
        return false;
    };
    let Some(pid) = info.pid else {
        return false;
    };
    #[cfg(unix)]
    {
        (unsafe { libc::kill(pid as libc::pid_t, 0) }) == 0
    }
    #[cfg(not(unix))]
    {
        // On non-Unix we fall back to the 60-second freshness heuristic.
        is_runtime_fresh(Duration::from_secs(60))
    }
}

/// Candidate paths for the engine **.app bundle** (not the inner binary).
fn engine_app_paths(app: &AppHandle) -> Vec<PathBuf> {
    let mut out = Vec::new();
    if let Ok(resource_dir) = app.path().resource_dir() {
        out.push(resource_dir.join("OceanusEngine.app"));
    }
    if let Ok(exe) = env::current_exe() {
        if let Some(contents) = exe.parent().and_then(|p| p.parent()) {
            out.push(contents.join("Resources").join("OceanusEngine.app"));
        }
    }
    out
}

pub fn spawn(app: &AppHandle) -> io::Result<Sidecar> {
    // Dev override: a bare engine binary, exec'd directly. Only used when
    // SAY_ENGINE_BIN is set (the packaged app always goes through LaunchServices
    // below). In dev the Terminal already holds the TCC grants, so attribution
    // is not a concern.
    if let Ok(custom) = env::var("SAY_ENGINE_BIN") {
        Command::new(custom)
            .arg("--no-menubar")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()?;
        return Ok(Sidecar { engine_app: PathBuf::from("SAY_ENGINE_BIN") });
    }

    let engine_app = engine_app_paths(app)
        .into_iter()
        .find(|p| p.exists())
        .ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::NotFound,
                "OceanusEngine.app not found in bundle resources",
            )
        })?;

    // Launch through LaunchServices (`open`) instead of exec'ing the inner
    // Mach-O directly. A directly-exec'd child inherits THIS shell's TCC
    // "responsible process" identity, so the engine's own Accessibility /
    // Input-Monitoring grants never take effect (AXIsProcessTrusted stays
    // false and the Fn CGEventTap can't be created). `open -n` registers the
    // engine as its own app (parent = launchd) with a clean code identity
    // that matches what the user granted, so Fn works.
    let status = Command::new("/usr/bin/open")
        .arg("-n")
        .arg(&engine_app)
        .arg("--args")
        .arg("--no-menubar")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()?;
    if !status.success() {
        return Err(io::Error::new(
            io::ErrorKind::Other,
            format!("`open -n {}` exited with {status}", engine_app.display()),
        ));
    }
    Ok(Sidecar { engine_app })
}
