// Engine sidecar process manager.
//
// Looks for the bundled `say-engine` binary inside the .app's Resources
// directory and launches it with `--server --no-menubar`. In dev,
// `SAY_ENGINE_BIN` overrides the search.

use std::env;
use std::fs;
use std::io;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::time::{Duration, SystemTime};

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
    child: Child,
}

impl Sidecar {
    /// Graceful shutdown: SIGTERM, wait up to 3s for the engine to
    /// flush its SQLite store and release the single-instance lock,
    /// then SIGKILL as a last resort.
    pub fn kill(&mut self) -> io::Result<()> {
        #[cfg(unix)]
        unsafe {
            libc::kill(self.child.id() as i32, libc::SIGTERM);
        }
        #[cfg(not(unix))]
        {
            let _ = self.child.kill();
        }

        let deadline = std::time::Instant::now() + Duration::from_secs(3);
        loop {
            match self.child.try_wait() {
                Ok(Some(_)) => return Ok(()),
                Ok(None) if std::time::Instant::now() >= deadline => break,
                Ok(None) => std::thread::sleep(Duration::from_millis(100)),
                Err(_) => break,
            }
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
        Ok(())
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

fn engine_binary_paths(app: &AppHandle) -> Vec<PathBuf> {
    let mut out = Vec::new();
    if let Ok(custom) = env::var("SAY_ENGINE_BIN") {
        out.push(PathBuf::from(custom));
    }
    if let Ok(resource_dir) = app.path().resource_dir() {
        // Engine bundled as a nested .app inside Resources/.
        out.push(resource_dir.join("SayEngine.app").join("Contents").join("MacOS").join("Say"));
        // Or as a flat one-dir PyInstaller artifact.
        out.push(resource_dir.join("say-engine"));
        out.push(resource_dir.join("resources").join("say-engine"));
    }
    if let Ok(exe) = env::current_exe() {
        if let Some(parent) = exe.parent() {
            // When the Tauri .app is run, exe lives in Contents/MacOS/.
            // The engine .app lives one level up in Contents/Resources/.
            if let Some(contents) = parent.parent() {
                out.push(
                    contents
                        .join("Resources")
                        .join("SayEngine.app")
                        .join("Contents")
                        .join("MacOS")
                        .join("Say"),
                );
            }
            out.push(parent.join("say-engine"));
        }
    }
    out
}

pub fn spawn(app: &AppHandle) -> io::Result<Sidecar> {
    let candidates = engine_binary_paths(app);
    let bin = candidates
        .into_iter()
        .find(|p| p.exists())
        .ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::NotFound,
                "say-engine binary not found in bundle resources or SAY_ENGINE_BIN",
            )
        })?;

    let child = Command::new(&bin)
        .arg("--no-menubar")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()?;
    Ok(Sidecar { child })
}
