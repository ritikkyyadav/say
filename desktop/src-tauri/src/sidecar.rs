// Engine sidecar process manager.
//
// macOS launches SayEngine.app through LaunchServices to preserve TCC identity.
// Linux execs the bundled PyInstaller onedir engine/SayEngine and owns its Child.
// In dev, SAY_ENGINE_BIN takes precedence over either bundled location.

use std::env;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant, SystemTime};
#[cfg(target_os = "linux")]
use std::{
    os::fd::{AsRawFd, FromRawFd, OwnedFd},
    process::Child,
};

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
    #[allow(dead_code)]
    engine_app: PathBuf,
    // Unlike LaunchServices on macOS, a direct Linux launch gives us a child
    // handle. Keep it even before runtime.json exists: closing during model
    // initialization must not leave an invisible, untracked engine behind.
    #[cfg(target_os = "linux")]
    child: Option<Child>,
}

impl Sidecar {
    pub fn kill(&mut self) -> io::Result<()> {
        #[cfg(target_os = "linux")]
        {
            if let Some(child) = self.child.as_mut() {
                terminate_owned_child(child)?;
            }
            self.child = None;
        }
        // LaunchServices detaches the macOS engine, so runtime.json remains
        // the source of its PID. Do not change that launch/ownership model.
        #[cfg(not(target_os = "linux"))]
        shutdown_engine();
        Ok(())
    }
}

#[cfg(unix)]
fn valid_pid(pid: i64) -> Option<libc::pid_t> {
    let pid = libc::pid_t::try_from(pid).ok()?;
    // In particular, never turn a stale/overflowing i64 into a process-group
    // signal (kill(0, ...) or kill(-1, ...)).
    (pid > 0).then_some(pid)
}

#[cfg(unix)]
fn pid_alive(pid: i64) -> bool {
    let Some(pid) = valid_pid(pid) else {
        return false;
    };
    let result = unsafe { libc::kill(pid, 0) };
    result == 0 || io::Error::last_os_error().raw_os_error() == Some(libc::EPERM)
}

#[cfg(not(unix))]
fn pid_alive(_pid: i64) -> bool {
    false
}

/// Does `pid` look like a Say engine running in sidecar mode?
///
/// Guards against stale runtime.json PIDs. On Linux require the exact bundled
/// executable basename and a separate --no-menubar argument; a substring of an
/// unrelated program's path or arguments is not proof of engine identity.
#[cfg(target_os = "linux")]
fn is_say_engine(pid: i64) -> bool {
    let Some(pid) = valid_pid(pid) else {
        return false;
    };
    let proc_dir = PathBuf::from("/proc").join(pid.to_string());
    let Ok(exe) = fs::read_link(proc_dir.join("exe")) else {
        return false;
    };
    let Ok(cmdline) = fs::read(proc_dir.join("cmdline")) else {
        return false;
    };
    linux_engine_identity(&exe, &cmdline)
}

#[cfg(any(target_os = "linux", test))]
fn linux_engine_identity(exe: &Path, cmdline: &[u8]) -> bool {
    if exe.file_name() != Some(std::ffi::OsStr::new("SayEngine")) {
        return false;
    }
    let mut args = cmdline.split(|byte| *byte == 0);
    let Some(argv0) = args.next() else {
        return false;
    };
    argv0.rsplit(|byte| *byte == b'/').next() == Some(&b"SayEngine"[..])
        && args.any(|arg| arg == b"--no-menubar")
}

// Preserve the macOS ps guard and bundle naming: LaunchServices does not give
// us a child handle or Linux's /proc filesystem.
#[cfg(all(unix, not(target_os = "linux")))]
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
/// input hooks and clear runtime.json, then SIGKILL as a last resort.
pub fn terminate_engine(pid: i64) {
    #[cfg(target_os = "linux")]
    terminate_linux_engine(pid);
    #[cfg(all(unix, not(target_os = "linux")))]
    {
        if !pid_alive(pid) || !is_say_engine(pid) {
            return;
        }
        let Some(raw_pid) = valid_pid(pid) else {
            return;
        };
        unsafe { libc::kill(raw_pid, libc::SIGTERM) };
        let deadline = Instant::now() + Duration::from_secs(3);
        loop {
            if !pid_alive(pid) || !is_say_engine(pid) {
                return; // exited cleanly, or the PID no longer names an engine
            }
            if Instant::now() >= deadline {
                unsafe { libc::kill(raw_pid, libc::SIGKILL) };
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

// A pidfd pins a runtime-discovered process's identity across /proc checks and
// signals. Rechecking /proc alone leaves a check/kill race with PID reuse.
// Fail closed if the kernel does not support pidfds; owned Child shutdown below
// does not need them, since an unreaped child PID cannot be reused.
#[cfg(target_os = "linux")]
struct LinuxProcess(OwnedFd);

#[cfg(target_os = "linux")]
impl LinuxProcess {
    fn open(pid: libc::pid_t) -> io::Result<Self> {
        let fd = unsafe { libc::syscall(libc::SYS_pidfd_open, pid, 0u32) };
        if fd < 0 {
            return Err(io::Error::last_os_error());
        }
        // SAFETY: pidfd_open returned a new descriptor owned by this caller.
        Ok(Self(unsafe { OwnedFd::from_raw_fd(fd as libc::c_int) }))
    }

    fn signal(&self, signal: libc::c_int) -> io::Result<()> {
        let result = unsafe {
            libc::syscall(
                libc::SYS_pidfd_send_signal,
                self.0.as_raw_fd(),
                signal,
                std::ptr::null::<libc::siginfo_t>(),
                0u32,
            )
        };
        if result < 0 {
            Err(io::Error::last_os_error())
        } else {
            Ok(())
        }
    }
}

#[cfg(target_os = "linux")]
fn terminate_linux_engine(pid: i64) {
    if !pid_alive(pid) || !is_say_engine(pid) {
        return;
    }
    let Some(raw_pid) = valid_pid(pid) else {
        return;
    };
    let process = match LinuxProcess::open(raw_pid) {
        Ok(process) => process,
        Err(err) => {
            eprintln!("[say-desktop] cannot safely open engine pid {pid} for shutdown: {err}");
            return;
        }
    };
    // A PID may have been recycled between the first check and pidfd_open.
    if !is_say_engine(pid) {
        return;
    }
    if let Err(err) = process.signal(libc::SIGTERM) {
        eprintln!("[say-desktop] could not terminate engine pid {pid}: {err}");
        return;
    }
    let deadline = Instant::now() + Duration::from_secs(3);
    loop {
        if !pid_alive(pid) || !is_say_engine(pid) {
            return;
        }
        if Instant::now() >= deadline {
            // Signal the pinned process, never a newly reused numeric PID.
            if let Err(err) = process.signal(libc::SIGKILL) {
                eprintln!("[say-desktop] could not kill engine pid {pid}: {err}");
            }
            return;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
}

#[cfg(target_os = "linux")]
fn terminate_owned_child(child: &mut Child) -> io::Result<()> {
    if child.try_wait()?.is_some() {
        return Ok(()); // try_wait also reaps an already-exited child
    }
    let pid = libc::pid_t::try_from(child.id())
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "invalid engine child PID"))?;
    // This is our unreaped child, not a PID read from a file. Even an arbitrary
    // SAY_ENGINE_BIN override is safe to terminate here, including during boot.
    if unsafe { libc::kill(pid, libc::SIGTERM) } != 0 {
        let err = io::Error::last_os_error();
        if err.raw_os_error() != Some(libc::ESRCH) {
            return Err(err);
        }
    }
    let deadline = Instant::now() + Duration::from_secs(3);
    loop {
        if child.try_wait()?.is_some() {
            return Ok(());
        }
        if Instant::now() >= deadline {
            child.kill()?;
            child.wait()?; // never leave a zombie behind after escalation
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(50));
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
        if let Err(err) = self.kill() {
            eprintln!("[say-desktop] could not shut down engine child: {err}");
        }
    }
}

pub fn app_data_dir() -> PathBuf {
    if cfg!(target_os = "macos") {
        if let Some(home) = dirs::home_dir() {
            return home.join("Library").join("Application Support").join("Say");
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

/// Candidate paths for the macOS **.app bundle** (not the inner binary).
#[cfg(not(target_os = "linux"))]
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

#[cfg(any(target_os = "linux", test))]
fn linux_engine_path(resource_dir: &Path) -> PathBuf {
    // The Linux resource map copies the CONTENTS of the PyInstaller onedir
    // directory here, preserving _internal/ beside the executable.
    resource_dir.join("engine").join("SayEngine")
}

fn engine_command(engine_path: &Path, owner_pid: &str) -> Command {
    let mut command = Command::new(engine_path);
    command
        .arg("--no-menubar")
        .arg("--owner-pid")
        .arg(owner_pid)
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    command
}

fn spawn_direct(engine_path: PathBuf, owner_pid: &str) -> io::Result<Sidecar> {
    let child = engine_command(&engine_path, owner_pid)
        .spawn()
        .map_err(|err| {
            io::Error::new(
                err.kind(),
                format!("could not launch {}: {err}", engine_path.display()),
            )
        })?;
    #[cfg(not(target_os = "linux"))]
    drop(child); // Preserve macOS's runtime.json-based developer override path.
    Ok(Sidecar {
        engine_app: engine_path,
        #[cfg(target_os = "linux")]
        child: Some(child),
    })
}

pub fn spawn(app: &AppHandle) -> io::Result<Sidecar> {
    let owner_pid = std::process::id().to_string();
    // Always honor the developer override, even if bundled resources exist.
    // On macOS the Terminal already holds TCC grants for this dev-only path.
    if let Some(custom) = env::var_os("SAY_ENGINE_BIN") {
        return spawn_direct(PathBuf::from(custom), &owner_pid);
    }

    spawn_bundled(app, &owner_pid)
}

#[cfg(target_os = "linux")]
fn spawn_bundled(app: &AppHandle, owner_pid: &str) -> io::Result<Sidecar> {
    let resources = app.path().resource_dir().map_err(|err| {
        io::Error::new(
            io::ErrorKind::NotFound,
            format!("engine resource directory: {err}"),
        )
    })?;
    spawn_direct(linux_engine_path(&resources), owner_pid)
}

#[cfg(not(target_os = "linux"))]
fn spawn_bundled(app: &AppHandle, owner_pid: &str) -> io::Result<Sidecar> {
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
        .arg(owner_pid)
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn linux_onedir_layout_keeps_the_executable_beside_internal_resources() {
        let resources = Path::new("resources with spaces");
        let executable = linux_engine_path(resources);
        assert_eq!(executable, resources.join("engine").join("SayEngine"));
        assert_eq!(
            executable.parent().unwrap().join("_internal"),
            resources.join("engine").join("_internal")
        );
    }

    #[test]
    fn direct_launch_passes_owner_and_sidecar_flags_without_a_shell() {
        let path = Path::new("engine with spaces/SayEngine");
        let command = engine_command(path, "12345");
        assert_eq!(command.get_program(), path.as_os_str());
        let args: Vec<_> = command.get_args().collect();
        assert_eq!(
            args,
            vec![
                std::ffi::OsStr::new("--no-menubar"),
                std::ffi::OsStr::new("--owner-pid"),
                std::ffi::OsStr::new("12345"),
            ]
        );
    }

    #[test]
    fn linux_runtime_identity_requires_exact_executable_and_flag() {
        let exe = Path::new("/opt/Say engine/SayEngine");
        assert!(linux_engine_identity(
            exe,
            b"/opt/Say engine/SayEngine\0--no-menubar\0"
        ));
        assert!(linux_engine_identity(exe, b"./SayEngine\0--no-menubar\0"));
        let invalid_cmdlines: &[&[u8]] = &[
            b"",
            b"SayEngine\0",
            b"SayEngine\0--no-menubar=true\0",
            b"SayEngine\0prefix--no-menubar\0",
            b"SayEngine\0a note containing --no-menubar\0",
            b"NotSayEngine\0--no-menubar\0",
        ];
        for cmdline in invalid_cmdlines {
            assert!(!linux_engine_identity(exe, cmdline));
        }
        for unrelated in [
            "/usr/bin/python3",
            "/tmp/NotSayEngine",
            "/tmp/SayEngine-helper",
            "/tmp/SayEngine (deleted)",
        ] {
            assert!(!linux_engine_identity(
                Path::new(unrelated),
                b"SayEngine\0--no-menubar\0"
            ));
        }
    }

    #[test]
    fn developer_runtime_does_not_acquire_an_owner_on_read() {
        let info: RuntimeInfo =
            serde_json::from_str(r#"{"host":"127.0.0.1","port":8765,"token":"test","pid":12345}"#)
                .unwrap();
        assert_eq!(info.pid, Some(12345));
        assert_eq!(info.owner_pid, None);
    }

    #[cfg(unix)]
    #[test]
    fn invalid_pids_cannot_become_process_group_signals() {
        for pid in [-1, 0, i64::from(libc::pid_t::MAX) + 1, i64::MAX] {
            assert_eq!(valid_pid(pid), None);
            assert!(!pid_alive(pid));
        }
        assert_eq!(valid_pid(42), Some(42));
    }

    #[cfg(target_os = "linux")]
    mod linux {
        use super::*;

        fn sleeping_sidecar() -> Sidecar {
            Sidecar {
                engine_app: PathBuf::from("/bin/sleep"),
                child: Some(
                    Command::new("/bin/sleep")
                        .arg("60")
                        .stdin(Stdio::null())
                        .stdout(Stdio::null())
                        .stderr(Stdio::null())
                        .spawn()
                        .expect("Linux lifecycle tests require /bin/sleep"),
                ),
            }
        }

        fn assert_reaped(pid: u32) {
            let mut status = 0;
            let result = unsafe { libc::waitpid(pid as libc::pid_t, &mut status, libc::WNOHANG) };
            let error = io::Error::last_os_error();
            assert_eq!(result, -1, "child {pid} was not reaped by Sidecar");
            assert_eq!(error.raw_os_error(), Some(libc::ECHILD));
        }

        #[test]
        fn drop_terminates_and_reaps_a_booting_child_without_runtime_json() {
            // No runtime file is created, read, or removed by this test. Also
            // proves owned overrides need not match the bundled engine name.
            let sidecar = sleeping_sidecar();
            let pid = sidecar.child.as_ref().unwrap().id();
            drop(sidecar);
            assert_reaped(pid);
        }

        #[test]
        fn child_shutdown_is_idempotent() {
            let mut sidecar = sleeping_sidecar();
            let pid = sidecar.child.as_ref().unwrap().id();
            sidecar.kill().unwrap();
            sidecar.kill().unwrap();
            assert!(sidecar.child.is_none());
            assert_reaped(pid);
        }

        #[test]
        fn an_already_reaped_child_is_not_signaled_again() {
            let mut sidecar = sleeping_sidecar();
            let child = sidecar.child.as_mut().unwrap();
            let pid = child.id();
            child.kill().unwrap();
            child.wait().unwrap();
            sidecar.kill().unwrap();
            assert_reaped(pid);
        }

        #[test]
        fn pidfd_stays_bound_to_the_original_process_after_reaping() {
            let mut sidecar = sleeping_sidecar();
            let child = sidecar.child.as_mut().unwrap();
            let process = LinuxProcess::open(child.id() as libc::pid_t)
                .expect("runtime PID shutdown requires Linux 5.3+ with pidfd syscalls allowed");
            process.signal(libc::SIGTERM).unwrap();
            child.wait().unwrap();
            assert_eq!(
                process.signal(0).unwrap_err().raw_os_error(),
                Some(libc::ESRCH)
            );
        }

        #[test]
        fn runtime_pid_guard_does_not_signal_an_unrelated_live_process() {
            let mut sidecar = sleeping_sidecar();
            let pid = i64::from(sidecar.child.as_ref().unwrap().id());
            assert!(!is_say_engine(pid));
            terminate_engine(pid);
            assert!(sidecar
                .child
                .as_mut()
                .unwrap()
                .try_wait()
                .unwrap()
                .is_none());
            // Sidecar's owned-child cleanup, not runtime PID signaling, is
            // responsible for stopping this deliberately unrelated executable.
            sidecar.kill().unwrap();
        }
    }
}
