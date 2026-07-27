import { invoke } from "@tauri-apps/api/core";

type RecordingState = "idle" | "starting" | "recording" | "paused";

const app = document.getElementById("app");
if (!app) {
  throw new Error("floating root container missing");
}

app.innerHTML = `
  <div class="wrap">
    <div class="title">DuckTrack Recorder</div>
    <div id="state" class="state">状态：加载中...</div>
    <div class="actions">
      <button id="startBtn" class="btn primary">开始</button>
      <button id="toggleBtn" class="btn primary" style="display:none">暂停</button>
      <button id="stopBtn" class="btn danger" style="display:none">停止</button>
      <button id="mainBtn" class="btn">主窗口</button>
    </div>
  </div>
`;

const style = document.createElement("style");
style.textContent = `
  :root { color-scheme: dark; }
  html, body {
    margin: 0;
    padding: 0;
    background: rgba(30, 32, 46, 0.95);
    font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    color: #f5f7ff;
    user-select: none;
    overflow: hidden;
  }
  .wrap {
    box-sizing: border-box;
    width: 100vw;
    height: 100vh;
    padding: 10px 12px;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 10px;
    backdrop-filter: blur(6px);
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .title {
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 0.2px;
  }
  .state {
    font-size: 12px;
    color: #cfd6ff;
    min-height: 18px;
  }
  .actions {
    margin-top: auto;
    display: flex;
    gap: 8px;
  }
  .btn {
    border: none;
    border-radius: 8px;
    padding: 7px 12px;
    font-size: 12px;
    cursor: pointer;
    background: #4b5563;
    color: #fff;
  }
  .btn.primary {
    background: #10a37f;
    font-weight: 600;
  }
  .btn.danger {
    background: #ef4444;
    font-weight: 600;
  }
  .btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
`;
document.head.appendChild(style);

const stateEl = document.getElementById("state") as HTMLDivElement;
const startBtn = document.getElementById("startBtn") as HTMLButtonElement;
const toggleBtn = document.getElementById("toggleBtn") as HTMLButtonElement;
const stopBtn = document.getElementById("stopBtn") as HTMLButtonElement;
const mainBtn = document.getElementById("mainBtn") as HTMLButtonElement;

let state: RecordingState = "idle";
let busy = false;

const stateTextMap: Record<RecordingState, string> = {
  idle: "空闲",
  starting: "启动中",
  recording: "录制中",
  paused: "已暂停",
};

function render() {
  stateEl.textContent = `状态：${stateTextMap[state]}`;

  // Show/hide buttons based on state
  const isIdle = state === "idle";
  const isStarting = state === "starting";
  const isActive = state === "recording" || state === "paused";

  startBtn.style.display = isIdle || isStarting ? "" : "none";
  toggleBtn.style.display = isActive ? "" : "none";
  stopBtn.style.display = isActive ? "" : "none";

  startBtn.disabled = busy || isStarting;
  startBtn.textContent = isStarting ? "启动中..." : "开始";

  toggleBtn.disabled = busy;
  toggleBtn.textContent = state === "paused" ? "继续" : "暂停";

  stopBtn.disabled = busy;
  mainBtn.disabled = busy;
}

function setBusy(next: boolean) {
  busy = next;
  render();
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function waitForRecordingStart(maxWaitMs = 60000): Promise<string> {
  const begin = Date.now();
  while (Date.now() - begin < maxWaitMs) {
    const s = await invoke<string>("backend_get_recording_state").catch(() => "starting");
    if (s === "recording") {
      const path = await invoke<string | null>("backend_get_active_recording_path").catch(() => null);
      return path || "";
    }
    if (s === "idle") {
      const err = await invoke<string | null>("backend_get_start_error").catch(() => null);
      throw new Error(err || "backend startup aborted");
    }
    const elapsed = Math.floor((Date.now() - begin) / 1000);
    stateEl.textContent = `状态：启动中...${elapsed}s`;
    await sleep(250);
  }
  throw new Error("启动录制超时");
}

async function refreshState() {
  try {
    const s = await invoke<string>("backend_get_recording_state");
    if (s === "recording" || s === "paused" || s === "idle" || s === "starting") {
      state = s;
      render();
    }
  } catch {
    // Keep previous state on read failure.
  }
}

// -- Start recording --
startBtn.addEventListener("click", async () => {
  if (busy || state !== "idle") return;
  setBusy(true);
  try {
    state = "starting";
    render();
    await invoke("backend_begin_start_recording", { recordingsDir: null });
    const path = await waitForRecordingStart(60000);
    state = "recording";
    stateEl.textContent = path ? `状态：录制中` : `状态：录制中`;
    render();
  } catch (e) {
    state = "idle";
    stateEl.textContent = `状态：启动失败 (${String(e)})`;
    render();
  } finally {
    setBusy(false);
  }
});

// -- Pause / Resume toggle --
toggleBtn.addEventListener("click", async () => {
  if (busy || (state !== "recording" && state !== "paused")) return;
  setBusy(true);
  try {
    if (state === "recording") {
      await invoke("backend_pause_recording");
      state = "paused";
    } else {
      await invoke("backend_resume_recording");
      state = "recording";
    }
    render();
  } catch (e) {
    stateEl.textContent = `状态：操作失败 (${String(e)})`;
  } finally {
    setBusy(false);
    void refreshState();
  }
});

// -- Stop recording --
stopBtn.addEventListener("click", async () => {
  if (busy || (state !== "recording" && state !== "paused")) return;
  setBusy(true);
  stateEl.textContent = "状态：正在停止...";
  try {
    await invoke("backend_stop_recording");
    state = "idle";
    render();
    await invoke("show_main_window").catch(() => {});
    await invoke("hide_floating_controls").catch(() => {});
  } catch (e) {
    stateEl.textContent = `状态：停止失败 (${String(e)})`;
    render();
  } finally {
    setBusy(false);
    void refreshState();
  }
});

// -- Show main window --
mainBtn.addEventListener("click", async () => {
  if (busy) return;
  setBusy(true);
  try {
    await invoke("show_main_window");
    await invoke("hide_floating_controls");
  } finally {
    setBusy(false);
  }
});

render();
void refreshState();
const timer = window.setInterval(() => {
  void refreshState();
}, 1500);
window.addEventListener("beforeunload", () => window.clearInterval(timer));
