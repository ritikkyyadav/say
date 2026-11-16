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
    /// The shell process that launched this engine as a sidecar. Absent when
    /// the engine was started by hand (dev / menu-bar mode), which is how we
    /// tell "nobody's to kill" apart from "orphaned and should not exist".
    #[serde(default)]
    pub owner_pid: Option<i64>,
}

/// Who, if anyone, the engine recorded in runtime.json belongs to.
#[derive(Debug, PartialEq, Eq)]
pub enum EngineStatus {
    /// No engine is running.
    None,
    /// Launched by this shell.
    Ours(i64),
    /// A sidecar engine whose owner is gone (or was never recorded) — it
    /// outlived the app that started it and must not still be listening.
    Orphan(i64),
    /// Someone else's engine: another live shell's, or one a developer started
    /// by hand. Not ours to kill.
    Foreign(i64),
}

pub struct Sidecar {
    // The engine is launched via LaunchServices (`open`), which detaches it
    // from this process, so we have no child handle. We track it through the
    // pid the engine records in runtime.json and signal that on shutdown.
    #[allow(dead_code)]
    engine_app: PathBuf,
}

impl Sidecar {
    pub fn kill(&mut self) -> io::Result<()> {
        shutdown_engine();
        Ok(())
    }
}

#[cfg(unix)]
fn pid_alive(pid: i64) -> bool {
    pid > 0 && unsafe { libc::kill(pid as libc::pid_t, 0) } == 0
}

#[cfg(not(unix))]
fn pid_alive(_pid: i64) -> bool {
    false
}

/// Does `pid` look like a Say engine running in sidecar mode?
///
/// Guards against pid reuse: the number comes from runtime.json, which may name
/// a process that exited long ago and had its pid recycled onto something
/// unrelated. Signalling that would be someone else's crash. `--no-menubar`
/// exists only for the sidecar path — nobody launches it by hand — so its
/// presence, together with the bundle name, identifies the engine positively
/// rather than by a number we merely hope is still right.
#[cfg(unix)]
fn is_say_engine(pid: i64) -> bool {
    let Ok(out) = Command::new("/bin/ps")
        .arg("-o")
        .arg("command=")
        .arg("-p")
        .arg(pid.to_string())
        .output()
    else {
        return false;
    };
    let cmd = String::from_utf8_lossy(&out.stdout);
    cmd.contains("--no-menubar") && cmd.contains("Say")
}

#[cfg(not(unix))]
fn is_say_engine(_pid: i64) -> bool {
    false
}

/// Classify the engine recorded in runtime.json.
pub fn engine_status() -> EngineStatus {
    let Some(info) = read_runtime_info() else {
        return EngineStatus::None;
    };
    let Some(pid) = info.pid else {
        return EngineStatus::None;
    };
    if !pid_alive(pid) {
        return EngineStatus::None;
    }
    let ours = std::process::id() as i64;
    match info.owner_pid {
        Some(owner) if owner == ours => EngineStatus::Ours(pid),
        // Owner recorded but dead: the app it belonged to is gone and this
        // engine should have gone with it.
        Some(owner) if !pid_alive(owner) => EngineStatus::Orphan(pid),
        Some(_) => EngineStatus::Foreign(pid),
        // No owner recorded. A sidecar engine (--no-menubar) always has one
        // now, so a sidecar without one predates this and is an orphan from a
        // previous build; anything else is a hand-started engine we leave be.
        None if is_say_engine(pid) => EngineStatus::Orphan(pid),
        None => EngineStatus::Foreign(pid),
    }
}

/// SIGTERM the engine, wait up to 3s for it to flush its SQLite store, release
/// the Fn-key override and clear runtime.json, then SIGKILL as a last resort.
pub fn terminate_engine(pid: i64) {
    #[cfg(unix)]
    {
        if !pid_alive(pid) || !is_say_engine(pid) {
            return;
        }
        let pid = pid as libc::pid_t;
        unsafe { libc::kill(pid, libc::SIGTERM) };
        let deadline = Instant::now() + Duration::from_secs(3);
        loop {
            if unsafe { libc::kill(pid, 0) } != 0 {
                return; // exited cleanly
            }
            if Instant::now() >= deadline {
                unsafe { libc::kill(pid, libc::SIGKILL) };
                return;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
    }
    #[cfg(not(unix))]
    {
        let _ = pid;
    }
}

/// Take down the engine this shell is responsible for.
///
/// Deliberately keyed on runtime.json rather than on a handle we happen to
/// hold: the shell used to kill only an engine it had spawned *itself*, so an
/// engine adopted at launch was killed by nobody — and since a surviving engine
/// is exactly what gets adopted next time, one orphan made every later quit
/// leave another. Reading the file each time means "whatever is ours right now",
/// which is the only version of this that cannot drift.
pub fn shutdown_engine() {
    match engine_status() {
        EngineStatus::Ours(pid) | EngineStatus::Orphan(pid) => terminate_engine(pid),
        EngineStatus::Foreign(_) | EngineStatus::None => {}
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

/// Candidate paths for the engine **.app bundle** (not the inner binary).
fn engine_app_paths(app: &AppHandle) -> Vec<PathBuf> {
    let mut out = Vec::new();
    if let Ok(resource_dir) = app.path().resource_dir() {
        out.push(resource_dir.join("SayEngine.app"));
    }
    if let Ok(exe) = env::current_exe() {
        if let Some(contents) = exe.parent().and_then(|p| p.parent()) {
            out.push(contents.join("Resources").join("SayEngine.app"));
        }
    }
    out
}

pub fn spawn(app: &AppHandle) -> io::Result<Sidecar> {
    // Dev override: a bare engine binary, exec'd directly. Only used when
    // SAY_ENGINE_BIN is set (the packaged app always goes through LaunchServices
    // below). In dev the Terminal already holds the TCC grants, so attribution
    // is not a concern.
    let owner_pid = std::process::id().to_string();

    if let Ok(custom) = env::var("SAY_ENGINE_BIN") {
        Command::new(custom)
            .arg("--no-menubar")
            .arg("--owner-pid")
            .arg(&owner_pid)
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
                "SayEngine.app not found in bundle resources",
            )
        })?;

    // Launch through LaunchServices (`open`) instead of exec'ing the inner
    // Mach-O directly. A directly-exec'd child inherits THIS shell's TCC
    // "responsible process" identity, so the engine's own Accessibility /
    // Input-Monitoring grants never take effect (AXIsProcessTrusted stays
    // false and the Fn CGEventTap can't be created). `open -n` registers the
    // engine as its own app (parent = launchd) with a clean code identity
    // that matches what the user granted, so Fn works.
    // `--owner-pid` is what makes the engine mortal. `open -n` reparents it to
    // launchd, so it has no other way to know whose app it is serving — and a
    // Force Quit kills this shell outright, leaving no shell-side code to run.
    // The engine watches the pid and exits on its own when it goes.
    let status = Command::new("/usr/bin/open")
        .arg("-n")
        .arg(&engine_app)
        .arg("--args")
        .arg("--no-menubar")
        .arg("--owner-pid")
        .arg(&owner_pid)
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
