use once_cell::sync::Lazy;
use reqwest::blocking::Client;
use serde_json::Value;
use std::fs;
use std::path::Path;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

static BACKEND_CHILD: Lazy<Mutex<Option<Child>>> = Lazy::new(|| Mutex::new(None));
static RECORDING_STATE: Lazy<Mutex<String>> = Lazy::new(|| Mutex::new("idle".to_string()));
static LAST_BACKEND_ENTRY: Lazy<Mutex<Option<PathBuf>>> = Lazy::new(|| Mutex::new(None));
static LAST_BACKEND_LAUNCHER: Lazy<Mutex<Option<Vec<String>>>> = Lazy::new(|| Mutex::new(None));
static LAST_BACKEND_BINARY: Lazy<Mutex<Option<PathBuf>>> = Lazy::new(|| Mutex::new(None));
static BACKEND_START_LOCK: Lazy<Mutex<()>> = Lazy::new(|| Mutex::new(()));
static ACTIVE_RECORDING_PATH: Lazy<Mutex<Option<String>>> = Lazy::new(|| Mutex::new(None));
static START_ERROR: Lazy<Mutex<Option<String>>> = Lazy::new(|| Mutex::new(None));

const BACKEND_START_WAIT_STEPS: usize = 35;
const BACKEND_START_WAIT_MS: u64 = 100;
const BACKEND_MAX_ATTEMPTS: usize = 8;
const BACKEND_LOG_TAIL_CHARS: usize = 5000;

fn set_recording_state(state: &str) {
  if let Ok(mut guard) = RECORDING_STATE.lock() {
    *guard = state.to_string();
  }
}

fn set_start_error(err: Option<String>) {
  if let Ok(mut guard) = START_ERROR.lock() {
    *guard = err;
  }
}

fn set_active_recording_path(path: Option<String>) {
  if let Ok(mut guard) = ACTIVE_RECORDING_PATH.lock() {
    *guard = path;
  }
}

fn backend_base_url() -> String {
  std::env::var("DUCKTRACK_BACKEND_URL").unwrap_or_else(|_| "http://127.0.0.1:18765".to_string())
}

fn cloud_base_url() -> String {
  std::env::var("DUCKTRACK_CLOUD_URL")
    .unwrap_or_else(|_| "http://118.145.32.133:20027".to_string())
    .trim_end_matches('/')
    .to_string()
}

fn cloud_token() -> String {
  std::env::var("DUCKTRACK_CLOUD_TOKEN").unwrap_or_default()
}

fn cloud_request(client: &Client, method: reqwest::Method, path: &str) -> reqwest::blocking::RequestBuilder {
  let request = client.request(method, format!("{}{}", cloud_base_url(), path));
  let token = cloud_token();
  if token.is_empty() {
    request
  } else {
    request.bearer_auth(token)
  }
}

fn percent_encode_path(path: &str) -> String {
  let normalized = path.replace('\\', "/");
  let mut encoded = String::new();
  for byte in normalized.as_bytes() {
    match *byte {
      b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' | b'/' => {
        encoded.push(*byte as char);
      }
      value => encoded.push_str(&format!("%{:02X}", value)),
    }
  }
  encoded
}

fn cloud_cache_root() -> PathBuf {
  std::env::var("LOCALAPPDATA")
    .map(PathBuf::from)
    .unwrap_or_else(|_| std::env::temp_dir())
    .join("DuckTrack")
    .join("cloud-cache")
}

fn backend_client(timeout: Duration, connect_timeout: Duration) -> Result<Client, String> {
  Client::builder()
    .timeout(timeout)
    .connect_timeout(connect_timeout)
    .build()
    .map_err(|e| e.to_string())
}

fn health_client() -> Result<Client, String> {
  backend_client(Duration::from_millis(600), Duration::from_millis(200))
}

fn api_client() -> Result<Client, String> {
  backend_client(Duration::from_secs(30), Duration::from_millis(1200))
}

fn stop_api_client() -> Result<Client, String> {
  // Stopping capture can take noticeably longer due to flush/export work.
  backend_client(Duration::from_secs(180), Duration::from_millis(1200))
}

fn long_api_client() -> Result<Client, String> {
  // Model sub-task split / annotation-style calls may take several minutes.
  backend_client(Duration::from_secs(1800), Duration::from_secs(5))
}

fn is_backend_alive() -> bool {
  let url = format!("{}/api/recordings", backend_base_url());
  if let Ok(client) = health_client() {
    if let Ok(resp) = client.get(url).send() {
      return resp.status().is_success();
    }
  }
  false
}

pub fn shutdown_backend_process() {
  if is_backend_alive() {
    if let Ok(client) = stop_api_client() {
      let _ = client
        .post(format!("{}/api/recordings/stop", backend_base_url()))
        .send();
    }
  }

  if let Ok(mut guard) = BACKEND_CHILD.lock() {
    if let Some(mut proc) = guard.take() {
      let _ = proc.kill();
      let _ = proc.wait();
    }
  }

  set_recording_state("idle");
  set_active_recording_path(None);
}

fn env_flag(name: &str, default: bool) -> bool {
  match std::env::var(name) {
    Ok(v) => match v.trim().to_ascii_lowercase().as_str() {
      "1" | "true" | "yes" | "y" | "on" => true,
      "0" | "false" | "no" | "n" | "off" => false,
      _ => default,
    },
    Err(_) => default,
  }
}

fn backend_log_dir() -> PathBuf {
  let dir = std::env::temp_dir().join("DuckTrack").join("backend-logs");
  let _ = fs::create_dir_all(&dir);
  dir
}

fn backend_log_paths(attempt: usize) -> (PathBuf, PathBuf) {
  let ts = SystemTime::now()
    .duration_since(UNIX_EPOCH)
    .unwrap_or_default()
    .as_millis();
  let base = format!("backend-{}-{}", ts, attempt);
  (
    backend_log_dir().join(format!("{}-out.log", base)),
    backend_log_dir().join(format!("{}-err.log", base)),
  )
}

fn tail_text(mut text: String) -> String {
  if text.chars().count() <= BACKEND_LOG_TAIL_CHARS {
    return text;
  }

  let chars: Vec<char> = text.drain(..).collect();
  chars[chars.len().saturating_sub(BACKEND_LOG_TAIL_CHARS)..]
    .iter()
    .collect()
}

fn read_log_tail(path: &Path) -> String {
  match fs::read_to_string(path) {
    Ok(text) => tail_text(text.trim().to_string()),
    Err(_) => String::new(),
  }
}

fn format_attempt_logs(stdout_log: &Path, stderr_log: &Path) -> String {
  let stdout = read_log_tail(stdout_log);
  let stderr = read_log_tail(stderr_log);
  let mut parts = Vec::<String>::new();
  if !stderr.is_empty() {
    parts.push(format!("stderr:\n{}", stderr));
  }
  if !stdout.is_empty() {
    parts.push(format!("stdout:\n{}", stdout));
  }
  if parts.is_empty() {
    format!(
      "no backend output captured (stdout log: {}; stderr log: {})",
      stdout_log.display(),
      stderr_log.display()
    )
  } else {
    parts.join("\n")
  }
}

fn candidate_roots() -> Vec<PathBuf> {
  let mut roots: Vec<PathBuf> = Vec::new();

  if let Ok(cwd) = std::env::current_dir() {
    let mut cur = Some(cwd);
    for _ in 0..7 {
      if let Some(p) = cur {
        roots.push(p.clone());
        cur = p.parent().map(|q| q.to_path_buf());
      } else {
        break;
      }
    }
  }

  if let Ok(exe_path) = std::env::current_exe() {
    if let Some(exe_dir) = exe_path.parent() {
      let mut cur = Some(exe_dir.to_path_buf());
      for _ in 0..7 {
        if let Some(p) = cur {
          roots.push(p.clone());
          cur = p.parent().map(|q| q.to_path_buf());
        } else {
          break;
        }
      }
    }
  }

  let mut deduped: Vec<PathBuf> = Vec::new();
  let mut seen = std::collections::HashSet::<PathBuf>::new();
  for p in roots {
    let key = fs::canonicalize(&p).unwrap_or_else(|_| p.clone());
    if seen.insert(key) {
      deduped.push(p);
    }
  }
  deduped
}

fn find_backend_executable_candidates() -> Vec<PathBuf> {
  let mut candidates: Vec<PathBuf> = Vec::new();
  for root in candidate_roots() {
    candidates.push(root.join("DuckTrack.exe"));
    candidates.push(root.join("ducktrack-backend.exe"));
    candidates.push(root.join("backend").join("DuckTrack.exe"));
    candidates.push(root.join("backend").join("ducktrack-backend.exe"));
    candidates.push(root.join("dist").join("DuckTrack.exe"));
    candidates.push(root.join("dist").join("ducktrack-backend.exe"));
  }

  if let Ok(guard) = LAST_BACKEND_BINARY.lock() {
    if let Some(last) = guard.as_ref() {
      candidates.insert(0, last.clone());
    }
  }

  let mut resolved_unique: Vec<PathBuf> = Vec::new();
  let mut seen = std::collections::HashSet::<PathBuf>::new();
  for p in candidates.into_iter().filter(|p| p.exists()) {
    let key = fs::canonicalize(&p).unwrap_or_else(|_| p.clone());
    if seen.insert(key) {
      resolved_unique.push(p);
    }
  }

  resolved_unique
}

fn find_python_backend_entry() -> Vec<PathBuf> {
  let mut candidates: Vec<PathBuf> = Vec::new();
  if let Ok(cwd) = std::env::current_dir() {
    candidates.push(cwd.join("..").join("main.py"));
    candidates.push(cwd.join("DuckTrack").join("main.py"));
    candidates.push(cwd.join("..").join("DuckTrack").join("main.py"));
    candidates.push(cwd.join("main.py"));
  }
  if let Ok(exe_path) = std::env::current_exe() {
    if let Some(exe_dir) = exe_path.parent() {
      candidates.push(exe_dir.join("main.py"));
      candidates.push(exe_dir.join("..").join("main.py"));
      candidates.push(exe_dir.join("..").join("DuckTrack").join("main.py"));
      candidates.push(exe_dir.join("..").join("..").join("DuckTrack").join("main.py"));
      candidates.push(exe_dir.join("..").join("..").join("..").join("main.py"));
    }
  }

  let mut resolved_unique: Vec<PathBuf> = Vec::new();
  let mut seen = std::collections::HashSet::<PathBuf>::new();
  for p in candidates.into_iter().filter(|p| p.exists()) {
    let key = fs::canonicalize(&p).unwrap_or_else(|_| p.clone());
    if seen.insert(key) {
      resolved_unique.push(p);
    }
  }
  resolved_unique
}

fn python_launchers() -> Vec<Vec<String>> {
  let mut launchers: Vec<Vec<String>> = Vec::new();
  let mut has_explicit_launcher = false;

  if let Ok(py) = std::env::var("DUCKTRACK_BACKEND_PYTHON") {
    let py = py.trim();
    if !py.is_empty() {
      launchers.push(vec![py.to_string()]);
      has_explicit_launcher = true;
    }
  }

  if let Ok(venv) = std::env::var("VIRTUAL_ENV") {
    let venv_py = PathBuf::from(venv).join("Scripts").join("python.exe");
    if venv_py.exists() {
      launchers.push(vec![venv_py.to_string_lossy().to_string()]);
      has_explicit_launcher = true;
    }
  }

  // Prefer repository-local virtual environments even when app is launched
  // outside an activated shell (double-clicked exe, task scheduler, etc.).
  for root in candidate_roots() {
    let local_venv_py = root.join(".venv").join("Scripts").join("python.exe");
    if local_venv_py.exists() {
      launchers.push(vec![local_venv_py.to_string_lossy().to_string()]);
      has_explicit_launcher = true;
      break;
    }

    let local_venv_py_alt = root.join("venv").join("Scripts").join("python.exe");
    if local_venv_py_alt.exists() {
      launchers.push(vec![local_venv_py_alt.to_string_lossy().to_string()]);
      has_explicit_launcher = true;
      break;
    }
  }

  if has_explicit_launcher {
    let mut deduped: Vec<Vec<String>> = Vec::new();
    let mut seen = std::collections::HashSet::<String>::new();
    for launcher in launchers {
      let key = launcher.join("\u{1f}");
      if seen.insert(key) {
        deduped.push(launcher);
      }
    }
    return deduped;
  }

  if let Ok(conda_py) = std::env::var("CONDA_PYTHON_EXE") {
    let conda_py = conda_py.trim();
    if !conda_py.is_empty() && PathBuf::from(conda_py).exists() {
      launchers.push(vec![conda_py.to_string()]);
    }
  }

  if let Ok(conda_prefix) = std::env::var("CONDA_PREFIX") {
    let conda_env_py = PathBuf::from(conda_prefix).join("python.exe");
    if conda_env_py.exists() {
      launchers.push(vec![conda_env_py.to_string_lossy().to_string()]);
    }
  }

  launchers.push(vec!["python".to_string()]);
  launchers.push(vec!["py".to_string(), "-3".to_string()]);

  let mut deduped: Vec<Vec<String>> = Vec::new();
  let mut seen = std::collections::HashSet::<String>::new();
  for launcher in launchers {
    let key = launcher.join("\u{1f}");
    if seen.insert(key) {
      deduped.push(launcher);
    }
  }
  deduped
}

fn spawn_backend_child(
  launcher: &[String],
  entry: &Path,
  attempt: usize,
) -> Option<(Child, PathBuf, PathBuf)> {
  if launcher.is_empty() {
    return None;
  }

  let mut cmd = Command::new(&launcher[0]);
  for arg in launcher.iter().skip(1) {
    cmd.arg(arg);
  }
  cmd.arg(entry);
  cmd.env("DUCKTRACK_BACKEND_PORT", "18765");

  #[cfg(windows)]
  if !cfg!(debug_assertions) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x08000000;
    cmd.creation_flags(CREATE_NO_WINDOW);
  }

  // When launching a conda/venv python by absolute path (without activating
  // the shell), Windows may miss runtime DLL search paths (e.g. Library\\bin).
  // Prepend interpreter-local paths to PATH for this child process only.
  let launcher_path = PathBuf::from(&launcher[0]);
  if launcher_path.is_absolute() {
    if let Some(py_home) = launcher_path.parent() {
      let mut extras = Vec::<String>::new();
      extras.push(py_home.to_string_lossy().to_string());
      extras.push(py_home.join("Scripts").to_string_lossy().to_string());
      extras.push(py_home.join("Library").join("bin").to_string_lossy().to_string());
      extras.push(py_home.join("DLLs").to_string_lossy().to_string());

      let existing = std::env::var("PATH").unwrap_or_default();
      let mut combined = extras.join(";");
      if !existing.is_empty() {
        combined.push(';');
        combined.push_str(&existing);
      }
      cmd.env("PATH", combined);
    }
  }

  if cfg!(debug_assertions) {
    cmd.stdout(Stdio::inherit()).stderr(Stdio::inherit());
  } else {
    let (stdout_log, stderr_log) = backend_log_paths(attempt);
    let stdout_file = fs::File::create(&stdout_log).ok()?;
    let stderr_file = fs::File::create(&stderr_log).ok()?;
    cmd.stdout(Stdio::from(stdout_file));
    cmd.stderr(Stdio::from(stderr_file));
    return cmd.spawn().ok().map(|child| (child, stdout_log, stderr_log));
  }

  let (stdout_log, stderr_log) = backend_log_paths(attempt);
  cmd.spawn().ok().map(|child| (child, stdout_log, stderr_log))
}

fn spawn_backend_executable(exe: &Path, attempt: usize) -> Option<(Child, PathBuf, PathBuf)> {
  let mut cmd = Command::new(exe);
  let (stdout_log, stderr_log) = backend_log_paths(attempt);
  let stdout_file = fs::File::create(&stdout_log).ok()?;
  let stderr_file = fs::File::create(&stderr_log).ok()?;
  cmd.stdout(Stdio::from(stdout_file));
  cmd.stderr(Stdio::from(stderr_file));
  cmd.env("DUCKTRACK_BACKEND_PORT", "18765");

  #[cfg(windows)]
  {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x08000000;
    cmd.creation_flags(CREATE_NO_WINDOW);
  }

  cmd.spawn().ok().map(|child| (child, stdout_log, stderr_log))
}

fn start_native_backend_process() -> Result<String, String> {
  let exe_candidates = find_backend_executable_candidates();
  if exe_candidates.is_empty() {
    return Err("cannot find DuckTrack.exe backend for native startup".to_string());
  }

  let mut attempt_errors: Vec<String> = Vec::new();
  for (idx, exe) in exe_candidates.into_iter().enumerate() {
    let Some((child, stdout_log, stderr_log)) = spawn_backend_executable(&exe, idx + 1) else {
      attempt_errors.push(format!("failed to spawn native backend: {}", exe.display()));
      continue;
    };

    let mut child_exited_early = false;
    {
      let mut guard = BACKEND_CHILD.lock().map_err(|e| e.to_string())?;
      *guard = Some(child);
    }

    for _ in 0..BACKEND_START_WAIT_STEPS {
      if is_backend_alive() {
        if let Ok(mut guard) = LAST_BACKEND_BINARY.lock() {
          *guard = Some(exe.clone());
        }
        return Ok(format!("backend started from {} (native exe)", exe.display()));
      }

      {
        let mut guard = BACKEND_CHILD.lock().map_err(|e| e.to_string())?;
        if let Some(proc) = guard.as_mut() {
          match proc.try_wait() {
            Ok(Some(status)) => {
              child_exited_early = true;
              attempt_errors.push(format!(
                "native backend exited early (status={}) from {}\n{}",
                status,
                exe.display(),
                format_attempt_logs(&stdout_log, &stderr_log)
              ));
              *guard = None;
              break;
            }
            Ok(None) => {}
            Err(err) => {
              child_exited_early = true;
              attempt_errors.push(format!(
                "native backend state check failed from {}: {}",
                exe.display(),
                err
              ));
              *guard = None;
              break;
            }
          }
        }
      }

      thread::sleep(Duration::from_millis(BACKEND_START_WAIT_MS));
    }

    if child_exited_early {
      continue;
    }

    attempt_errors.push(format!(
      "native backend did not become healthy within {}ms from {}\n{}",
      BACKEND_START_WAIT_STEPS as u64 * BACKEND_START_WAIT_MS,
      exe.display(),
      format_attempt_logs(&stdout_log, &stderr_log)
    ));

    {
      let mut guard = BACKEND_CHILD.lock().map_err(|e| e.to_string())?;
      if let Some(mut proc) = guard.take() {
        let _ = proc.kill();
        let _ = proc.wait();
      }
    }
  }

  Err(format!(
    "failed to start native backend DuckTrack.exe: {}",
    attempt_errors.join("\n")
  ))
}

fn parent_has_model_url() -> bool {
  for key in ["DUCKTRACK_QWEN_URL", "DUCKTRACK_SUBTASK_URL"] {
    if let Ok(value) = std::env::var(key) {
      if !value.trim().is_empty() {
        return true;
      }
    }
  }
  false
}

fn backend_splitter_configured() -> bool {
  let Ok(client) = health_client() else {
    return false;
  };
  let url = format!("{}/api/subtask_splitter/status", backend_base_url());
  let Ok(response) = client.get(url).send() else {
    return false;
  };
  if !response.status().is_success() {
    return false;
  }
  response
    .json::<Value>()
    .ok()
    .and_then(|body| body.get("configured").and_then(|v| v.as_bool()))
    .unwrap_or(false)
}

fn stop_tracked_backend_child() {
  if let Ok(mut guard) = BACKEND_CHILD.lock() {
    if let Some(mut proc) = guard.take() {
      let _ = proc.kill();
      let _ = proc.wait();
    }
  }
}

#[cfg(windows)]
fn kill_backend_port_listeners() {
  use std::os::windows::process::CommandExt;
  const CREATE_NO_WINDOW: u32 = 0x08000000;
  let Ok(output) = Command::new("cmd")
    .args(["/C", "netstat -ano | findstr :18765 | findstr LISTENING"])
    .creation_flags(CREATE_NO_WINDOW)
    .output()
  else {
    return;
  };
  let text = String::from_utf8_lossy(&output.stdout);
  let mut pids = std::collections::BTreeSet::new();
  for line in text.lines() {
    if let Some(pid) = line.split_whitespace().last() {
      if pid.chars().all(|c| c.is_ascii_digit()) {
        pids.insert(pid.to_string());
      }
    }
  }
  for pid in pids {
    let _ = Command::new("taskkill")
      .args(["/F", "/PID", &pid])
      .creation_flags(CREATE_NO_WINDOW)
      .output();
  }
}

#[cfg(not(windows))]
fn kill_backend_port_listeners() {}

fn restart_stale_unconfigured_backend() -> bool {
  if !is_backend_alive() {
    return false;
  }
  if !parent_has_model_url() {
    return false;
  }
  if backend_splitter_configured() {
    return false;
  }
  stop_tracked_backend_child();
  kill_backend_port_listeners();
  // Give the port a moment to release
  thread::sleep(Duration::from_millis(300));
  true
}

fn start_backend_process() -> Result<String, String> {
  let _start_guard = BACKEND_START_LOCK.lock().map_err(|e| e.to_string())?;

  let restarted = restart_stale_unconfigured_backend();
  if is_backend_alive() {
    return Ok(if restarted {
      "backend already running after restart check".to_string()
    } else {
      "backend already running".to_string()
    });
  }

  if env_flag("DUCKTRACK_PREFER_NATIVE_BACKEND", false) {
    if let Ok(msg) = start_native_backend_process() {
      return Ok(msg);
    }
  }

  let mut entries = find_python_backend_entry();
  if entries.is_empty() {
    return start_native_backend_process();
  }
  if let Ok(guard) = LAST_BACKEND_ENTRY.lock() {
    if let Some(last_entry) = guard.as_ref() {
      if let Some(idx) = entries.iter().position(|p| p == last_entry) {
        let preferred = entries.remove(idx);
        entries.insert(0, preferred);
      }
    }
  }

  let mut launchers = python_launchers();
  if launchers.is_empty() {
    return Err("no available python launcher found for backend startup".to_string());
  }
  if let Ok(guard) = LAST_BACKEND_LAUNCHER.lock() {
    if let Some(last_launcher) = guard.as_ref() {
      if let Some(idx) = launchers.iter().position(|l| l == last_launcher) {
        let preferred = launchers.remove(idx);
        launchers.insert(0, preferred);
      }
    }
  }

  let entries_display = entries
    .iter()
    .map(|p| p.display().to_string())
    .collect::<Vec<String>>()
    .join(", ");
  let launchers_display = launchers
    .iter()
    .map(|l| l.join(" "))
    .collect::<Vec<String>>()
    .join(", ");

  let mut attempts = 0usize;
  let mut attempt_errors: Vec<String> = Vec::new();
  'attempts: for entry in entries {
    for launcher in &launchers {
      attempts += 1;
      if attempts > BACKEND_MAX_ATTEMPTS {
        break 'attempts;
      }

      let Some((child, stdout_log, stderr_log)) = spawn_backend_child(launcher, &entry, attempts) else {
        attempt_errors.push(format!(
          "attempt {} failed to spawn: {} {}",
          attempts,
          launcher.join(" "),
          entry.display()
        ));
        continue;
      };

      let mut child_exited_early = false;
      {
        let mut guard = BACKEND_CHILD.lock().map_err(|e| e.to_string())?;
        *guard = Some(child);
      }

      // Python + uvicorn + imports can take several seconds on Windows.
      // Give one candidate enough startup time before killing and trying next.
      for _ in 0..BACKEND_START_WAIT_STEPS {
        if is_backend_alive() {
          if let Ok(mut guard) = LAST_BACKEND_ENTRY.lock() {
            *guard = Some(entry.clone());
          }
          if let Ok(mut guard) = LAST_BACKEND_LAUNCHER.lock() {
            *guard = Some(launcher.clone());
          }
          return Ok(format!(
            "backend started from {} (launcher: {})",
            entry.display(),
            launcher.join(" ")
          ));
        }

        {
          let mut guard = BACKEND_CHILD.lock().map_err(|e| e.to_string())?;
          if let Some(proc) = guard.as_mut() {
            match proc.try_wait() {
              Ok(Some(status)) => {
                child_exited_early = true;
                attempt_errors.push(format!(
                  "attempt {} exited early (status={}) from {} via {}\n{}",
                  attempts,
                  status,
                  entry.display(),
                  launcher.join(" "),
                  format_attempt_logs(&stdout_log, &stderr_log)
                ));
                eprintln!(
                  "ducktrack backend exited early (status={}) from {} via {}",
                  status,
                  entry.display(),
                  launcher.join(" ")
                );
                *guard = None;
                break;
              }
              Ok(None) => {}
              Err(err) => {
                child_exited_early = true;
                attempt_errors.push(format!(
                  "attempt {} state check failed from {} via {}: {}",
                  attempts,
                  entry.display(),
                  launcher.join(" "),
                  err
                ));
                eprintln!(
                  "ducktrack backend state check failed from {} via {}: {}",
                  entry.display(),
                  launcher.join(" "),
                  err
                );
                *guard = None;
                break;
              }
            }
          }
        }

        thread::sleep(Duration::from_millis(BACKEND_START_WAIT_MS));
      }

      if child_exited_early {
        continue;
      }

      attempt_errors.push(format!(
        "attempt {} did not become healthy within {}ms from {} via {}\n{}",
        attempts,
        BACKEND_START_WAIT_STEPS as u64 * BACKEND_START_WAIT_MS,
        entry.display(),
        launcher.join(" "),
        format_attempt_logs(&stdout_log, &stderr_log)
      ));

      {
        let mut guard = BACKEND_CHILD.lock().map_err(|e| e.to_string())?;
        if let Some(mut proc) = guard.take() {
          let _ = proc.kill();
          let _ = proc.wait();
        }
      }
    }
  }

  if env_flag("DUCKTRACK_ALLOW_NATIVE_BACKEND", false) {
    if let Ok(msg) = start_native_backend_process() {
      return Ok(msg);
    }
  }

  Err(format!(
    "failed to start backend process after {} attempts (main.py candidates: {}; python launchers: {}). \
Please run `pip install -r requirements.txt` in your active env (missing `uvicorn` or `PyQt6.sip` is common).\n\n{}",
    attempts.min(BACKEND_MAX_ATTEMPTS),
    entries_display,
    launchers_display,
    attempt_errors.join("\n\n")
  ))
}

fn backend_post_json(path: &str, payload: Option<Value>) -> Result<Value, String> {
  let url = format!("{}{}", backend_base_url(), path);
  let client = if path.ends_with("/stop") {
    stop_api_client()?
  } else {
    api_client()?
  };

  let send_post = || {
    let request = client.post(&url);
    match payload.as_ref() {
      Some(value) => request.json(value).send(),
      None => request.send(),
    }
  };

  let resp = match send_post() {
    Ok(resp) => resp,
    Err(first_err) => {
      // Backend may be restarting between health-check and request.
      thread::sleep(Duration::from_millis(200));
      let _ = start_backend_process();
      send_post().map_err(|second_err| format!("{}; retry failed: {}", first_err, second_err))?
    }
  };

  if !resp.status().is_success() {
    let status = resp.status();
    let body = resp.text().unwrap_or_default();

    if path.ends_with("/start") && body.contains("Recording already in progress") {
      return Ok(serde_json::json!({ "recording_path": "" }));
    }

    if body.is_empty() {
      return Err(format!("backend error: {}", status));
    }
    return Err(format!("backend error {}: {}", status, body));
  }
  resp.json::<Value>().map_err(|e| e.to_string())
}

fn backend_post(path: &str) -> Result<Value, String> {
  backend_post_json(path, None)
}

fn backend_get(path: &str) -> Result<Value, String> {
  let url = format!("{}{}", backend_base_url(), path);
  let client = api_client()?;

  let resp = match client.get(&url).send() {
    Ok(resp) => resp,
    Err(first_err) => {
      thread::sleep(Duration::from_millis(200));
      let _ = start_backend_process();
      client
        .get(&url)
        .send()
        .map_err(|second_err| format!("{}; retry failed: {}", first_err, second_err))?
    }
  };

  if !resp.status().is_success() {
    let status = resp.status();
    let body = resp.text().unwrap_or_default();
    if body.is_empty() {
      return Err(format!("backend error: {}", status));
    }
    return Err(format!("backend error {}: {}", status, body));
  }
  resp.json::<Value>().map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn ensure_backend_running() -> Result<String, String> {
  tauri::async_runtime::spawn_blocking(start_backend_process)
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
pub async fn backend_get_recording_state() -> Result<String, String> {
  let guard = RECORDING_STATE.lock().map_err(|e| e.to_string())?;
  Ok(guard.clone())
}

#[tauri::command]
pub async fn backend_get_active_recording_path() -> Result<Option<String>, String> {
  let guard = ACTIVE_RECORDING_PATH.lock().map_err(|e| e.to_string())?;
  Ok(guard.clone())
}

#[tauri::command]
pub async fn backend_get_start_error() -> Result<Option<String>, String> {
  let guard = START_ERROR.lock().map_err(|e| e.to_string())?;
  Ok(guard.clone())
}

fn start_recording_blocking(recordings_dir: Option<String>) -> Result<String, String> {
  set_recording_state("starting");
  set_start_error(None);

  let _ = start_backend_process();
  let payload = recordings_dir
    .filter(|s| !s.trim().is_empty())
    .map(|dir| serde_json::json!({ "recordings_dir": dir }));
  let data = match backend_post_json("/api/recordings/start", payload) {
    Ok(data) => data,
    Err(err) => {
      set_recording_state("idle");
      set_start_error(Some(err.clone()));
      return Err(err);
    }
  };

  let path = data
    .get("recording_path")
    .and_then(|v| v.as_str())
    .unwrap_or("")
    .to_string();
  set_active_recording_path(Some(path.clone()));
  set_recording_state("recording");
  set_start_error(None);
  Ok(path)
}

#[tauri::command]
pub async fn backend_begin_start_recording(recordings_dir: Option<String>) -> Result<(), String> {
  let current = {
    let guard = RECORDING_STATE.lock().map_err(|e| e.to_string())?;
    guard.clone()
  };

  if current == "recording" || current == "paused" || current == "starting" {
    return Ok(());
  }

  set_recording_state("starting");
  set_start_error(None);

  tauri::async_runtime::spawn(async move {
    let result = tauri::async_runtime::spawn_blocking(|| {
      let _ = start_backend_process();
      let payload = recordings_dir
        .filter(|s| !s.trim().is_empty())
        .map(|dir| serde_json::json!({ "recordings_dir": dir }));
      backend_post_json("/api/recordings/start", payload)
    })
    .await;

    match result {
      Ok(Ok(data)) => {
        let path = data
          .get("recording_path")
          .and_then(|v| v.as_str())
          .unwrap_or("")
          .to_string();
        set_active_recording_path(Some(path));
        set_recording_state("recording");
        set_start_error(None);
      }
      Ok(Err(err)) => {
        set_recording_state("idle");
        set_start_error(Some(err));
      }
      Err(err) => {
        set_recording_state("idle");
        set_start_error(Some(err.to_string()));
      }
    }
  });

  Ok(())
}

#[tauri::command]
pub async fn backend_start_recording(recordings_dir: Option<String>) -> Result<String, String> {
  tauri::async_runtime::spawn_blocking(move || start_recording_blocking(recordings_dir))
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
pub async fn backend_pause_recording() -> Result<(), String> {
  tauri::async_runtime::spawn_blocking(|| backend_post("/api/recordings/pause"))
    .await
    .map_err(|e| e.to_string())??;
  set_recording_state("paused");
  Ok(())
}

#[tauri::command]
pub async fn backend_resume_recording() -> Result<(), String> {
  tauri::async_runtime::spawn_blocking(|| backend_post("/api/recordings/resume"))
    .await
    .map_err(|e| e.to_string())??;
  set_recording_state("recording");
  Ok(())
}

#[tauri::command]
pub async fn backend_mark_sub_task_boundary(instruction: Option<String>) -> Result<Value, String> {
  let payload = Some(serde_json::json!({
    "instruction": instruction.unwrap_or_default(),
  }));
  tauri::async_runtime::spawn_blocking(move || {
    backend_post_json("/api/recordings/sub_task_boundary", payload)
  })
  .await
  .map_err(|e| e.to_string())?
}

#[tauri::command]
pub async fn backend_get_sub_task_state() -> Result<Value, String> {
  tauri::async_runtime::spawn_blocking(|| backend_get("/api/recordings/sub_task_state"))
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
pub async fn backend_stop_recording() -> Result<String, String> {
  let data = tauri::async_runtime::spawn_blocking(|| backend_post("/api/recordings/stop"))
    .await
    .map_err(|e| e.to_string())??;
  let path = data
    .get("recording_path")
    .and_then(|v| v.as_str())
    .unwrap_or("")
    .to_string();
  set_active_recording_path(Some(path.clone()));
  set_recording_state("idle");
  set_start_error(None);
  Ok(path)
}

#[tauri::command]
pub async fn backend_list_recordings() -> Result<Vec<String>, String> {
  let data = tauri::async_runtime::spawn_blocking(|| backend_get("/api/recordings"))
    .await
    .map_err(|e| e.to_string())??;
  let list = data
    .get("recordings")
    .and_then(|v| v.as_array())
    .ok_or_else(|| "invalid backend response".to_string())?;

  Ok(
    list
      .iter()
      .filter_map(|v| v.as_str().map(|s| s.to_string()))
      .collect(),
  )
}

#[tauri::command]
pub async fn cloud_list_sessions() -> Result<Value, String> {
  tauri::async_runtime::spawn_blocking(|| {
    let client = api_client()?;
    let response = cloud_request(&client, reqwest::Method::GET, "/api/v1/sessions")
      .send()
      .map_err(|e| e.to_string())?;
    if !response.status().is_success() {
      return Err(format!("cloud error {}: {}", response.status(), response.text().unwrap_or_default()));
    }
    response.json::<Value>().map_err(|e| e.to_string())
  })
  .await
  .map_err(|e| e.to_string())?
}

#[tauri::command]
pub async fn cloud_download_session(session_id: String) -> Result<String, String> {
  tauri::async_runtime::spawn_blocking(move || {
    if session_id.is_empty()
      || !session_id
        .chars()
        .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '-' | '_' | '.'))
    {
      return Err("invalid cloud session id".to_string());
    }
    let client = stop_api_client()?;
    let trajectory_response = cloud_request(
      &client,
      reqwest::Method::GET,
      &format!("/api/v1/sessions/{}/trajectory", session_id),
    )
    .send()
    .map_err(|e| e.to_string())?;
    if !trajectory_response.status().is_success() {
      return Err(format!(
        "cloud error {}: {}",
        trajectory_response.status(),
        trajectory_response.text().unwrap_or_default()
      ));
    }
    let response: Value = trajectory_response.json().map_err(|e| e.to_string())?;
    let trajectory = response
      .get("trajectory")
      .cloned()
      .ok_or_else(|| "cloud trajectory response is invalid".to_string())?;
    let metadata = response.get("metadata").cloned().unwrap_or_else(|| serde_json::json!({}));
    let cache_dir = cloud_cache_root().join(&session_id);
    fs::create_dir_all(&cache_dir).map_err(|e| e.to_string())?;
    fs::write(
      cache_dir.join("trajectory.json"),
      serde_json::to_vec_pretty(&trajectory).map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())?;
    fs::write(
      cache_dir.join("metadata.json"),
      serde_json::to_vec_pretty(&metadata).map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())?;
    fs::write(
      cache_dir.join("cloud_session.json"),
      serde_json::to_vec_pretty(&response).map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())?;

    let mut files = std::collections::HashSet::<String>::new();
    files.insert("events.jsonl".to_string());
    files.insert("frames/changes.jsonl".to_string());
    if let Some(steps) = trajectory.get("trajectory").and_then(|value| value.as_array()) {
      for step in steps {
        for key in ["observation", "objects"] {
          if let Some(path) = step.get(key).and_then(|value| value.as_str()) {
            files.insert(path.to_string());
          }
        }
        if let Some(frames) = step.get("frames").and_then(|value| value.as_object()) {
          for value in frames.values() {
            if let Some(path) = value.as_str() {
              files.insert(path.to_string());
            }
          }
        }
      }
    }

    for relative in files {
      let normalized = relative.replace('\\', "/");
      if normalized.split('/').any(|part| part == "..") {
        continue;
      }
      let target = cache_dir.join(&normalized);
      if target.is_file() {
        continue;
      }
      if let Some(parent) = target.parent() {
        fs::create_dir_all(parent).map_err(|e| e.to_string())?;
      }
      let path = format!(
        "/api/v1/sessions/{}/files/{}",
        session_id,
        percent_encode_path(&normalized)
      );
      let response = cloud_request(&client, reqwest::Method::GET, &path)
        .send()
        .map_err(|e| e.to_string())?;
      if response.status().is_success() {
        let bytes = response.bytes().map_err(|e| e.to_string())?;
        fs::write(target, &bytes).map_err(|e| e.to_string())?;
      }
    }
    Ok(cache_dir.to_string_lossy().to_string())
  })
  .await
  .map_err(|e| e.to_string())?
}

#[tauri::command]
pub async fn cloud_save_reviewed(session_id: String, trajectory: Value) -> Result<(), String> {
  tauri::async_runtime::spawn_blocking(move || {
    let client = stop_api_client()?;
    let response = cloud_request(
      &client,
      reqwest::Method::PUT,
      &format!("/api/v1/sessions/{}/reviewed", session_id),
    )
    .json(&trajectory)
    .send()
    .map_err(|e| e.to_string())?;
    if !response.status().is_success() {
      return Err(format!("cloud error {}: {}", response.status(), response.text().unwrap_or_default()));
    }
    Ok(())
  })
  .await
  .map_err(|e| e.to_string())?
}

#[tauri::command]
pub async fn cloud_trigger_annotation(session_id: String) -> Result<(), String> {
  tauri::async_runtime::spawn_blocking(move || {
    let client = api_client()?;
    let response = cloud_request(
      &client,
      reqwest::Method::POST,
      &format!("/api/v1/sessions/{}/annotate", session_id),
    )
    .send()
    .map_err(|e| e.to_string())?;
    if !response.status().is_success() {
      return Err(format!("cloud error {}: {}", response.status(), response.text().unwrap_or_default()));
    }
    Ok(())
  })
  .await
  .map_err(|e| e.to_string())?
}

#[tauri::command]
pub async fn backend_split_subtasks(
  trajectory: Value,
  recording_path: Option<String>,
) -> Result<Value, String> {
  tauri::async_runtime::spawn_blocking(move || {
    let client = long_api_client()?;
    let url = format!("{}/api/trajectory/split_subtasks", backend_base_url());
    let mut request = client.post(url);
    if let Some(ref path) = recording_path {
      if !path.trim().is_empty() {
        request = request.query(&[("recording_dir", path)]);
      }
    }
    let response = request
      .json(&trajectory)
      .send()
      .map_err(|e| e.to_string())?;
    let status = response.status();
    let text = response.text().map_err(|e| e.to_string())?;
    if !status.is_success() {
      return Err(format!("backend error {}: {}", status, text));
    }
    serde_json::from_str(&text).map_err(|e| e.to_string())
  })
  .await
  .map_err(|e| e.to_string())?
}

#[tauri::command]
pub async fn cloud_split_subtasks(session_id: String, trajectory: Option<Value>) -> Result<Value, String> {
  tauri::async_runtime::spawn_blocking(move || {
    let client = long_api_client()?;
    let path = format!("/api/v1/sessions/{}/split_subtasks", session_id);
    let mut request = cloud_request(&client, reqwest::Method::POST, &path);
    if let Some(payload) = trajectory {
      request = request.json(&payload);
    }
    let response = request.send().map_err(|e| e.to_string())?;
    let status = response.status();
    let text = response.text().map_err(|e| e.to_string())?;
    if !status.is_success() {
      return Err(format!("cloud error {}: {}", status, text));
    }
    serde_json::from_str(&text).map_err(|e| e.to_string())
  })
  .await
  .map_err(|e| e.to_string())?
}

#[tauri::command]
pub async fn read_text_file(path: String) -> Result<String, String> {
  tauri::async_runtime::spawn_blocking(move || fs::read_to_string(&path).map_err(|e| e.to_string()))
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
pub async fn file_size_bytes(path: String) -> Result<u64, String> {
  tauri::async_runtime::spawn_blocking(move || {
    let meta = fs::metadata(&path).map_err(|e| e.to_string())?;
    Ok(meta.len())
  })
  .await
  .map_err(|e| e.to_string())?
}

#[tauri::command]
pub async fn write_text_file(path: String, content: String) -> Result<(), String> {
  tauri::async_runtime::spawn_blocking(move || fs::write(&path, content).map_err(|e| e.to_string()))
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
pub async fn get_latest_mp4(dir: String) -> Result<Option<String>, String> {
  tauri::async_runtime::spawn_blocking(move || {
    let p = Path::new(&dir);
    if !p.exists() {
      return Ok(None);
    }

    let preferred = p.join("screen_recording.mp4");
    if preferred.is_file() {
      return Ok(Some(preferred.to_string_lossy().to_string()));
    }

    let mut candidates: Vec<(std::time::SystemTime, String)> = Vec::new();
    let mut stack = vec![p.to_path_buf()];
    while let Some(cur) = stack.pop() {
      for entry in fs::read_dir(&cur).map_err(|e| e.to_string())? {
        let entry = entry.map_err(|e| e.to_string())?;
        let path = entry.path();
        if path.is_dir() {
          stack.push(path);
          continue;
        }
        if !path.is_file() {
          continue;
        }
        let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("").to_lowercase();
        if ext != "mp4" {
          continue;
        }

        let meta = fs::metadata(&path).map_err(|e| e.to_string())?;
        let modified = meta.modified().unwrap_or(std::time::UNIX_EPOCH);
        candidates.push((modified, path.to_string_lossy().to_string()));
      }
    }

    candidates.sort_by_key(|(t, _)| *t);
    match candidates.last() {
      Some((_, s)) => Ok(Some(s.clone())),
      None => Ok(None),
    }
  })
  .await
  .map_err(|e| e.to_string())?
}

#[tauri::command]
pub fn select_folder(default_path: Option<String>) -> Result<Option<String>, String> {
  let mut dialog = rfd::FileDialog::new();
  if let Some(path) = default_path {
    let p = PathBuf::from(path);
    if p.exists() {
      dialog = dialog.set_directory(p);
    }
  }
  let picked = dialog.pick_folder();
  Ok(picked.map(|p| p.to_string_lossy().to_string()))
}

#[tauri::command]
pub fn select_video_file(default_path: Option<String>) -> Result<Option<String>, String> {
  let mut dialog = rfd::FileDialog::new().add_filter("Video", &["mp4", "mkv", "mov", "avi"]);
  if let Some(path) = default_path {
    let p = PathBuf::from(path);
    if p.exists() {
      dialog = dialog.set_directory(p);
    }
  }
  let picked = dialog.pick_file();
  Ok(picked.map(|p| p.to_string_lossy().to_string()))
}
