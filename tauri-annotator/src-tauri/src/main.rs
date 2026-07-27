#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

#[path = "../../src/backend_commands.rs"]
mod backend_commands;

fn app_log_path() -> std::path::PathBuf {
  std::env::var("LOCALAPPDATA")
    .map(std::path::PathBuf::from)
    .unwrap_or_else(|_| std::env::temp_dir())
    .join("DuckTrack-OneClick")
    .join("frontend.log")
}

fn app_log_line(message: &str) {
  use std::io::Write;
  let path = app_log_path();
  if let Some(parent) = path.parent() {
    let _ = std::fs::create_dir_all(parent);
  }
  let line = format!("{:?} {}\n", std::time::SystemTime::now(), message);
  let _ = std::fs::OpenOptions::new()
    .create(true)
    .append(true)
    .open(path)
    .and_then(|mut f| f.write_all(line.as_bytes()));
}

fn show_startup_error(message: &str) {
  #[cfg(windows)]
  {
    let escaped = message.replace('\'', "''");
    let script = format!(
      "Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show('{}','DuckTrack') | Out-Null",
      escaped
    );
    let _ = std::process::Command::new("powershell")
      .arg("-NoProfile")
      .arg("-ExecutionPolicy")
      .arg("Bypass")
      .arg("-Command")
      .arg(script)
      .status();
  }
}

#[tauri::command]
fn minimize_main_window(app: tauri::AppHandle) -> Result<String, String> {
  use tauri::Manager;
  let window = app
    .get_webview_window("main")
    .ok_or_else(|| "main window not found".to_string())?;
  window.minimize().map_err(|e| e.to_string())?;

  // On some Windows setups minimize may not visually apply; fall back to hide.
  std::thread::sleep(std::time::Duration::from_millis(80));
  let minimized = window.is_minimized().map_err(|e| e.to_string())?;
  if minimized {
    Ok("minimized".to_string())
  } else {
    window.hide().map_err(|e| e.to_string())?;
    Ok("hidden".to_string())
  }
}

#[tauri::command]
fn show_main_window(app: tauri::AppHandle) -> Result<(), String> {
  use tauri::Manager;
  let window = app
    .get_webview_window("main")
    .ok_or_else(|| "main window not found".to_string())?;
  let _ = window.show();
  let _ = window.unminimize();
  let _ = window.set_focus();
  Ok(())
}

#[tauri::command]
fn show_floating_controls(_app: tauri::AppHandle) -> Result<(), String> {
  // Disabled: the main window now owns recording controls.
  Ok(())
}

#[tauri::command]
fn hide_floating_controls(app: tauri::AppHandle) -> Result<(), String> {
  use tauri::Manager;
  if let Some(window) = app.get_webview_window("floating") {
    let _ = window.hide();
  }
  Ok(())
}

fn main() {
  std::panic::set_hook(Box::new(|info| {
    app_log_line(&format!("panic: {}", info));
  }));
  app_log_line("frontend starting");

  let result = tauri::Builder::default()
    .invoke_handler(tauri::generate_handler![
      minimize_main_window,
      show_main_window,
      show_floating_controls,
      hide_floating_controls,
      backend_commands::ensure_backend_running,
      backend_commands::backend_get_recording_state,
      backend_commands::backend_get_active_recording_path,
      backend_commands::backend_get_start_error,
      backend_commands::backend_begin_start_recording,
      backend_commands::backend_start_recording,
      backend_commands::backend_pause_recording,
      backend_commands::backend_resume_recording,
      backend_commands::backend_stop_recording,
      backend_commands::backend_list_recordings,
      backend_commands::cloud_list_sessions,
      backend_commands::cloud_download_session,
      backend_commands::cloud_save_reviewed,
      backend_commands::cloud_trigger_annotation,
      backend_commands::read_text_file,
      backend_commands::file_size_bytes,
      backend_commands::write_text_file,
      backend_commands::get_latest_mp4,
      backend_commands::select_folder,
      backend_commands::select_video_file
    ])
    .on_window_event(|window, event| {
      if window.label() == "main" {
        if let tauri::WindowEvent::CloseRequested { .. } = event {
          backend_commands::shutdown_backend_process();
        }
      }
    })
    .run(tauri::generate_context!())
    .map_err(|err| err.to_string());

  if let Err(err) = result {
    app_log_line(&format!("frontend startup failed: {}", err));
    show_startup_error(&format!(
      "DuckTrack startup failed:\n{}\n\nLog: {}",
      err,
      app_log_path().display()
    ));
  }
}
