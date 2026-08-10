<template>
  <div class="app-root">
    <aside class="sidebar">
      <div class="sidebar-brand">
        <span class="brand-mark">DuckTrack</span>
        <span class="brand-sub">标注器</span>
      </div>
      <button
        v-if="enteredAnnotator"
        type="button"
        class="sidebar-nav-btn"
        @click="enteredAnnotator = false"
      >
        <span class="sidebar-nav-icon">←</span>
        返回工作流
      </button>
      <div v-if="enteredAnnotator" class="sidebar-steps">
        <div class="sidebar-section-label">子任务 / 步骤</div>
        <div class="subtask-toolbar">
          <button
            class="btn primary"
            type="button"
            :disabled="subtaskSplitBusy || !trajectoryData"
            title="用模型整轨划分子任务（覆盖当前分段，可撤销）"
            @click="runModelSplitSubtasks"
          >
            {{ subtaskSplitBusy ? "划分中…" : "模型划分子任务" }}
          </button>
          <button
            class="btn"
            type="button"
            :disabled="subtaskSplitBusy || !canUndoModelSplit"
            title="恢复一键划分前的子任务分段"
            @click="undoModelSplitSubtasks"
          >
            撤销模型划分
          </button>
        </div>
        <div class="step-list">
          <template v-for="group in sidebarGroups" :key="group.key">
            <div
              class="subtask-group-header"
              :class="{ active: group.subTask && selectedSubTaskId === group.subTask.id }"
              @click="group.subTask && selectSubTask(group.subTask.id)"
            >
              <span class="subtask-group-title">{{ group.title }}</span>
              <span class="subtask-group-range" v-if="group.subTask">
                步骤 {{ group.subTask.start_step }}–{{ group.subTask.end_step }}
              </span>
            </div>
            <div
              v-for="entry in group.entries"
              :key="entry.idx"
              class="step-item"
              :class="{ active: entry.viewIdx === selectedViewIndex }"
              @click="selectViewIndex(entry.viewIdx)"
            >
              {{ formatStepLabel(steps[entry.idx]) }}
            </div>
          </template>
        </div>
      </div>
      <p v-else class="sidebar-hint">加载录制目录后，在右侧完成标注与保存。</p>
    </aside>

    <div class="main-column">
      <header class="topbar">
        <div class="dir-input-wrap">
          <input
            class="dir-input"
            v-model="directoryInput"
            placeholder="录制目录路径…"
          />
        </div>
        <button class="btn" @click="browseRecordingDir">浏览</button>
        <button class="btn" @click="browseVideoFile">视频</button>
        <button class="btn" @click="pickRecordingDir">加载</button>
        <button class="btn primary" :disabled="!dirty" @click="saveAnnotations">保存</button>
        <span class="status-pill" v-if="status">{{ status }}</span>
        <span class="spacer"></span>
        <span class="recording-tag" v-if="recordingName">{{ recordingName }}</span>
      </header>

      <main v-if="!enteredAnnotator" class="workflow-main">
        <div class="workflow-inner">
          <h1 class="workflow-hero">开始一次标注</h1>
          <p class="workflow-lead">先录制桌面操作，再为每一步写下自然语言说明。</p>
          <div class="workflow-grid">
            <div class="workflow-card">
              <h2>1 · 录制</h2>
              <p>直接在当前应用内控制录制，不再启动独立录制窗口。</p>
              <div class="workflow-actions recorder-controls">
                <button
                  class="btn primary"
                  type="button"
                  :disabled="recorderBusy || recorderState !== 'idle'"
                  @click="startRecording"
                >
                  开始
                </button>
                <button
                  class="btn"
                  type="button"
                  :disabled="recorderBusy || recorderState !== 'recording'"
                  @click="pauseRecording"
                >
                  暂停
                </button>
                <button
                  class="btn"
                  type="button"
                  :disabled="recorderBusy || recorderState !== 'paused'"
                  @click="resumeRecording"
                >
                  继续
                </button>
                <button
                  class="btn danger-btn"
                  type="button"
                  :disabled="recorderBusy || recorderState === 'idle' || recorderState === 'starting'"
                  @click="stopRecording"
                >
                  停止并加载
                </button>
              </div>
              <p class="recording-path" v-if="activeRecordingPath">
                当前录制目录：{{ activeRecordingPath }}
              </p>
            </div>
            <div class="workflow-card">
              <h2>2 · 标注</h2>
              <p>选择录制目录，对齐视频并填写每步说明。</p>
              <div class="workflow-actions">
                <button class="btn" type="button" @click="browseRecordingDir">选择目录</button>
                <button class="btn primary" type="button" @click="enterAnnotator">进入标注</button>
              </div>
            </div>
            <div class="workflow-card cloud-workflow-card">
              <h2>3 · 云端回流</h2>
              <p>加载 Qwen 自动标注结果，在原标注器中回放、删除和编辑。</p>
              <div class="cloud-session-row">
                <select class="cloud-session-select" v-model="selectedCloudSession">
                  <option value="">选择云端会话</option>
                  <option v-for="session in cloudSessions" :key="session.session_id" :value="session.session_id">
                    {{ session.session_id }} · {{ session.annotation_state || "unknown" }}
                  </option>
                </select>
                <button class="btn" type="button" :disabled="cloudBusy" @click="refreshCloudSessions">刷新</button>
                <button class="btn primary" type="button" :disabled="cloudBusy || !selectedCloudSession" @click="loadCloudSession">
                  加载云端会话
                </button>
                <button class="btn" type="button" :disabled="cloudBusy || !selectedCloudSession" @click="triggerCloudAnnotation">
                  重新标注
                </button>
              </div>
            </div>
          </div>
        </div>
      </main>

      <main v-else class="annotator-main">
      <section class="right-panel">
        <div class="zoom-row">
          <span class="offset-label">视频缩放</span>
          <button class="btn zoom-btn" @click="setZoomMode('fit')">适配窗口</button>
          <button class="btn zoom-btn" @click="setZoomMode('100')">100%</button>
          <button class="btn zoom-btn" @click="setZoomMode('150')">150%</button>
          <button class="btn zoom-btn" @click="setZoomMode('200')">200%</button>
          <span class="offset-label frame-label">关键帧</span>
          <button class="btn zoom-btn" :class="{ primary: screenshotFrameMode === 'before' }" @click="screenshotFrameMode = 'before'">操作前</button>
          <button class="btn zoom-btn" :class="{ primary: screenshotFrameMode === 'after' }" @click="screenshotFrameMode = 'after'">操作后 250ms</button>
          <button class="btn zoom-btn" :class="{ primary: screenshotFrameMode === 'settled' }" @click="screenshotFrameMode = 'settled'">稳定态 ~1s</button>
        </div>
        <div class="video-wrap">
          <div v-if="showScreenshotPreview" class="image-stage" ref="imageStageEl">
            <img ref="previewImageEl" class="preview-image" :src="previewSrc" :style="{ transform: `scale(${zoomScale})` }" alt="步骤截图预览" @load="updateScreenshotOverlay" />
            <div class="overlay-layer" :style="{ transform: `scale(${zoomScale})`, transformOrigin: 'center center' }">
              <svg v-if="trailPathD" class="trail-svg" viewBox="0 0 100 100" preserveAspectRatio="none">
                <path class="trail-line-shadow" :d="trailPathD" pathLength="1" />
                <path class="trail-line" :d="trailPathD" pathLength="1" />
              </svg>
              <div
                v-if="dragPreviewLine"
                class="drag-preview-line"
                :style="{
                  left: `${dragPreviewLine.left}px`, top: `${dragPreviewLine.top}px`,
                  width: `${dragPreviewLine.width}px`, transform: `rotate(${dragPreviewLine.angle}rad)`,
                }"
              ></div>
              <div
                v-if="selectedActionMarker"
                class="action-marker"
                :style="{ left: `${selectedActionMarker.x}px`, top: `${selectedActionMarker.y}px` }"
              >
                <span class="action-ring"></span><span class="action-core"></span>
              </div>
            </div>
          </div>
          <div v-else-if="!videoSrc" class="video-empty-tip">未加载视频，请先选择录制目录并点击加载</div>
          <div v-else class="video-stage" ref="videoStageEl" :style="{ transform: `scale(${zoomScale})` }">
            <video
              ref="videoEl"
              class="video"
              :src="videoSrc"
              muted
              playsinline
              preload="metadata"
              @error="onVideoError"
              @loadedmetadata="onVideoLoadedMetadata"
              @timeupdate="updateOverlayByVideoTime"
            ></video>
            <div class="overlay-layer">
              <svg v-if="trailPathD" class="trail-svg" viewBox="0 0 100 100" preserveAspectRatio="none">
                <path class="trail-line-shadow" :d="trailPathD" pathLength="1" />
                <path class="trail-line" :d="trailPathD" pathLength="1" />
              </svg>
              <div
                v-if="dragPreviewLine"
                class="drag-preview-line"
                :style="{
                  left: `${dragPreviewLine.left}px`,
                  top: `${dragPreviewLine.top}px`,
                  width: `${dragPreviewLine.width}px`,
                  transform: `rotate(${dragPreviewLine.angle}rad)`,
                }"
              ></div>
              <div
                v-for="p in trailPoints"
                :key="p.id"
                class="trail-dot"
                :class="{ 'trail-dot-hot': p.hot }"
                :style="{
                  left: `${p.x}px`,
                  top: `${p.y}px`,
                  opacity: `${p.opacity}`,
                }"
              ></div>
              <div
                v-if="selectedActionMarker"
                class="action-marker"
                :style="{ left: `${selectedActionMarker.x}px`, top: `${selectedActionMarker.y}px` }"
              >
                <span class="action-ring"></span>
                <span class="action-core"></span>
              </div>
              <div
                v-if="cursorPoint"
                class="cursor-marker"
                :style="{ left: `${cursorPoint.x}px`, top: `${cursorPoint.y}px` }"
              ></div>
              <div v-if="previewChipText" class="preview-chip">
                {{ previewChipText }}
              </div>
              <div v-if="actionHintText" class="action-hint">
                {{ actionHintText }}
              </div>
            </div>
          </div>
        </div>

        <div class="offset-row" v-if="hasTimeline">
          <label class="offset-label">视频偏移(s)</label>
          <input
            class="offset-input"
            type="number"
            step="0.05"
            min="-10"
            max="10"
            v-model.number="manualOffsetS"
          />
        </div>

        <div class="detail-card">
          <div class="panel-title small">EVENT DETAIL</div>
          <div class="detail-content" v-if="selectedStep">
            <div v-html="formatStepDetailHtml(selectedStep)"></div>
          </div>
        </div>

        <div class="instruction-card">
          <div class="panel-title small">SUB-TASK</div>
          <div class="subtask-edit" v-if="selectedSubTask">
            <label class="annotation-field wide">
              <span>子任务意图 (instruction)</span>
              <input v-model="selectedSubTaskInstruction" placeholder="填写该段操作意图" />
            </label>
            <div class="subtask-meta">
              #{{ (selectedSubTask.index ?? 0) + 1 }} ·
              source={{ selectedSubTask.source || "manual" }} ·
              步骤 {{ selectedSubTask.start_step }}–{{ selectedSubTask.end_step }}
            </div>
            <div class="subtask-actions">
              <button class="btn" type="button" @click="splitSubTaskAtSelected" :disabled="!canSplitAtSelected">
                从此步拆分子任务
              </button>
              <button class="btn" type="button" @click="mergeSelectedSubTaskWithPrev" :disabled="!canMergeSelectedSubTask">
                与上一段合并
              </button>
            </div>
          </div>
          <div v-else class="subtask-empty">当前无子任务信息（旧录制可保存后自动补全单段）。</div>

          <div class="panel-title small">STEP INSTRUCTION</div>
          <div v-if="canDeleteSelected" class="wait-review-row">
            <span class="wait-tip">
              {{ isSelectedWait ? "当前是 wait 步骤，请判断是否需要删除。" : "如发现误记录的点击/操作，可删除当前步骤。" }}
            </span>
            <button class="btn danger-btn" @click="removeCurrentStep">删除当前步骤</button>
          </div>
          <textarea
            class="textarea"
            v-model="selectedStepInstruction"
            placeholder="请填写这一步的自然语言动作描述"
            :disabled="!selectedStep"
          ></textarea>
          <div class="annotation-fields" v-if="selectedStep">
            <label class="annotation-field wide">
              <span>操作意图</span>
              <input v-model="selectedIntent" placeholder="Qwen 推断的局部意图" />
            </label>
            <label class="annotation-field wide">
              <span>操作结果</span>
              <input v-model="selectedResult" placeholder="界面中可观察到的变化" />
            </label>
            <label class="annotation-field">
              <span>置信度</span>
              <input v-model.number="selectedConfidence" type="number" min="0" max="1" step="0.05" />
            </label>
            <label class="annotation-field">
              <span>风险</span>
              <select v-model="selectedRisk">
                <option value="low">low</option><option value="medium">medium</option>
                <option value="high">high</option><option value="critical">critical</option>
              </select>
            </label>
            <label class="annotation-check">
              <input v-model="selectedNeedsReview" type="checkbox" />
              <span>需要复核</span>
            </label>
          </div>

          <div class="nav-row">
            <button class="btn" @click="goPrev" :disabled="selectedViewIndex <= 0">上一个</button>
            <div class="nav-center">
              {{ viewRangeText }}
            </div>
            <button
              class="btn"
              @click="goNext"
              :disabled="selectedViewIndex >= filteredIndices.length - 1"
            >
              下一个
            </button>
          </div>
        </div>
      </section>
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { convertFileSrc, invoke } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";

type Step = any;
type TrailPoint = { id: string; x: number; y: number; opacity: number; hot?: boolean };
type ScreenPoint = { x: number; y: number };
type DragPreviewLine = { left: number; top: number; width: number; angle: number };

const ACTION_CN: Record<string, string> = {
  click: "左键单击",
  rightClick: "右键单击",
  doubleClick: "左键双击",
  doubleRightClick: "右键双击",
  dragTo: "拖拽",
  moveTo: "移动到",
  scroll: "滚轮滚动",
  type: "文字输入",
  keyDown: "按键按下",
  keyUp: "按键释放",
  pause: "录制暂停",
  resume: "录制恢复",
  wait: "等待",
  terminate: "结束",
};

// Parsing very large events.jsonl on the UI thread can freeze the app.
// If file exceeds this threshold, skip timeline mapping and keep screenshot mode.
const MAX_TIMELINE_EVENTS_FILE_BYTES = 6 * 1024 * 1024;

const videoEl = ref<HTMLVideoElement | null>(null);
const videoStageEl = ref<HTMLDivElement | null>(null);
const imageStageEl = ref<HTMLDivElement | null>(null);
const previewImageEl = ref<HTMLImageElement | null>(null);
const dirty = ref(false);
const status = ref("");
const recorderBusy = ref(false);
const recorderState = ref<"idle" | "starting" | "recording" | "paused">("idle");
const activeRecordingPath = ref("");
const cloudSessions = ref<any[]>([]);
const selectedCloudSession = ref("");
const activeCloudSession = ref("");
const cloudBusy = ref(false);
let backendWarmupPromise: Promise<void> | null = null;
async function warmupBackend(silent = true): Promise<void> {
  if (backendWarmupPromise) {
    return backendWarmupPromise;
  }
  backendWarmupPromise = (async () => {
    try {
      await invokeWithTimeout<string>("ensure_backend_running", undefined, 15000);
    } catch (e: any) {
      if (!silent) {
        status.value = `后端预热失败：${e?.message || String(e)}`;
      }
      throw e;
    }
  })();
  try {
    await backendWarmupPromise;
  } finally {
    backendWarmupPromise = null;
  }
}

async function minimizeWindowAfterStart() {
  try {
    await invoke<string>("minimize_main_window");
    return;
  } catch (_e) {
    // Fall back to JS-side minimize.
  }
  try {
    await getCurrentWindow().minimize();
  } catch (_e) {
    // Ignore minimization errors to avoid breaking recording flow.
  }
}

function onVideoError() {
  const v = videoEl.value;
  const mediaErr = v?.error;
  const code = mediaErr?.code ?? "unknown";
  status.value = `视频加载失败（code=${code}）`;
  clearActionPreviewOverlay();
}

function onVideoLoadedMetadata() {
  updateOverlayByVideoTime();
}

function clearActionPreviewOverlay() {
  trailPoints.value = [];
  trailPathD.value = "";
  cursorPoint.value = null;
  selectedActionMarker.value = null;
  dragPreviewLine.value = null;
  previewChipText.value = "";
  actionHintText.value = "";
  previewWindow.value = null;
}

const recordingPath = ref<string | null>(null);
const directoryInput = ref("");
const enteredAnnotator = ref(false);
const trajectoryPath = computed(() => {
  if (!recordingPath.value) return null;
  return `${recordingPath.value}\\trajectory.json`;
});

function normalizePathForAsset(path: string): string {
  return path.replace(/\\/g, "/");
}

const mp4Path = ref<string | null>(null);
const videoSrc = computed(() => {
  if (!mp4Path.value) return "";
  return convertFileSrc(normalizePathForAsset(mp4Path.value));
});

const recordingName = computed(() => {
  if (!recordingPath.value) return "";
  return recordingPath.value.split(/[/\\]/).pop() || "";
});

const trajectoryData = ref<any>(null);
const screenshotFrameMode = ref<"before" | "after" | "settled">("before");
const subtaskSplitBusy = ref(false);
const steps = computed<Step[]>(() => trajectoryData.value?.trajectory || []);
const subTasks = computed<any[]>(() =>
  Array.isArray(trajectoryData.value?.sub_tasks) ? trajectoryData.value.sub_tasks : []
);
const canUndoModelSplit = computed(() => {
  const meta = trajectoryData.value?.sub_task_metadata;
  if (!meta || typeof meta !== "object") return false;
  if (meta.previous_snapshot && typeof meta.previous_snapshot === "object") return true;
  return Array.isArray(meta.previous_sub_tasks);
});

const filteredIndices = computed(() => {
  const list = steps.value || [];
  return list.map((_, i) => i);
});

type SidebarGroup = {
  key: string;
  title: string;
  subTask: any | null;
  entries: Array<{ idx: number; viewIdx: number }>;
};

const sidebarGroups = computed<SidebarGroup[]>(() => {
  const indices = filteredIndices.value;
  const tasks = subTasks.value;
  if (!tasks.length) {
    return [
      {
        key: "all",
        title: "全部步骤",
        subTask: null,
        entries: indices.map((idx, viewIdx) => ({ idx, viewIdx })),
      },
    ];
  }
  const groups: SidebarGroup[] = tasks.map((task: any, i: number) => {
    const titleText = (task.instruction || "").trim() || "（未填写意图）";
    return {
      key: task.id || `st_${i}`,
      title: `子任务 #${(task.index ?? i) + 1}：${titleText}`,
      subTask: task,
      entries: [] as Array<{ idx: number; viewIdx: number }>,
    };
  });
  const unmatched: Array<{ idx: number; viewIdx: number }> = [];
  indices.forEach((idx, viewIdx) => {
    const step = steps.value[idx];
    const name = step?.action?.[0]?.name;
    if (name === "terminate") {
      unmatched.push({ idx, viewIdx });
      return;
    }
    const group = groups.find(
      (g) => g.subTask && idx >= g.subTask.start_step && idx <= g.subTask.end_step
    );
    if (group) group.entries.push({ idx, viewIdx });
    else unmatched.push({ idx, viewIdx });
  });
  if (unmatched.length) {
    groups.push({
      key: "other",
      title: "其他 / terminate",
      subTask: null,
      entries: unmatched,
    });
  }
  return groups;
});

const selectedViewIndex = ref(0);
const selectedStepIndex = computed(() => filteredIndices.value[selectedViewIndex.value] ?? 0);
const selectedStep = computed<Step | null>(() => {
  const i = selectedStepIndex.value;
  return steps.value?.[i] || null;
});

function findSubTaskForStep(stepIndex: number): any | null {
  for (const task of subTasks.value) {
    if (stepIndex >= Number(task.start_step) && stepIndex <= Number(task.end_step)) {
      return task;
    }
  }
  return null;
}

const selectedSubTask = computed(() => findSubTaskForStep(selectedStepIndex.value));
const selectedSubTaskId = computed(() => selectedSubTask.value?.id || "");

const selectedSubTaskInstruction = computed<string>({
  get() {
    return selectedSubTask.value?.instruction || "";
  },
  set(v: string) {
    const task = selectedSubTask.value;
    if (!task || !trajectoryData.value) return;
    task.instruction = v;
    task.needs_review = !String(v || "").trim();
    if (!task.source) task.source = "manual";
    dirty.value = true;
  },
});

const canSplitAtSelected = computed(() => {
  const task = selectedSubTask.value;
  const idx = selectedStepIndex.value;
  if (!task) return false;
  const name = selectedStep.value?.action?.[0]?.name;
  if (name === "terminate") return false;
  return idx > Number(task.start_step) && idx <= Number(task.end_step);
});

const canMergeSelectedSubTask = computed(() => {
  const task = selectedSubTask.value;
  if (!task) return false;
  return Number(task.index) > 0;
});

function ensureSubTaskDefaults() {
  if (!trajectoryData.value) return;
  const list = trajectoryData.value.trajectory;
  if (!Array.isArray(list)) return;
  if (!Array.isArray(trajectoryData.value.sub_tasks) || !trajectoryData.value.sub_tasks.length) {
    let last = -1;
    for (let i = list.length - 1; i >= 0; i--) {
      if (list[i]?.action?.[0]?.name !== "terminate") {
        last = i;
        break;
      }
    }
    if (last >= 0) {
      trajectoryData.value.sub_tasks = [
        {
          id: `st_${Date.now().toString(16)}`,
          index: 0,
          instruction: "",
          start_step: 0,
          end_step: last,
          source: "manual",
          confidence: null,
          needs_review: true,
          model: null,
          prompt_version: null,
        },
      ];
    } else {
      trajectoryData.value.sub_tasks = [];
    }
  }
  if (!trajectoryData.value.sub_task_metadata || typeof trajectoryData.value.sub_task_metadata !== "object") {
    const hasMulti = Array.isArray(trajectoryData.value.sub_tasks) && trajectoryData.value.sub_tasks.length > 1;
    trajectoryData.value.sub_task_metadata = {
      status: hasMulti ? "manual_complete" : "none",
      splitter: hasMulti ? "manual" : "none",
      manual_boundary_count: hasMulti ? trajectoryData.value.sub_tasks.length - 1 : 0,
      split_at_unix: null,
      prompt_version: null,
      previous_sub_tasks: null,
      previous_snapshot: null,
    };
  }
}

function selectSubTask(id: string) {
  const task = subTasks.value.find((t) => t.id === id);
  if (!task) return;
  const viewIdx = filteredIndices.value.findIndex((idx) => idx === Number(task.start_step));
  if (viewIdx >= 0) selectViewIndex(viewIdx);
}

function newSubTaskId(): string {
  return `st_${Math.random().toString(16).slice(2)}${Date.now().toString(16)}`;
}

function splitSubTaskAtSelected() {
  const task = selectedSubTask.value;
  const idx = selectedStepIndex.value;
  if (!task || !trajectoryData.value || !canSplitAtSelected.value) return;
  const list = [...subTasks.value];
  const pos = list.findIndex((t) => t.id === task.id);
  if (pos < 0) return;
  const left = {
    ...task,
    end_step: idx - 1,
    source: "manual",
    model: null,
    prompt_version: null,
  };
  const right = {
    id: newSubTaskId(),
    index: 0,
    instruction: "",
    start_step: idx,
    end_step: Number(task.end_step),
    source: "manual",
    confidence: null,
    needs_review: true,
    model: null,
    prompt_version: null,
  };
  const next = [...list.slice(0, pos), left, right, ...list.slice(pos + 1)];
  next.forEach((t, i) => (t.index = i));
  trajectoryData.value.sub_tasks = next;
  const meta = trajectoryData.value.sub_task_metadata || {};
  meta.status = "manual_complete";
  meta.splitter = "manual";
  meta.manual_boundary_count = Math.max(Number(meta.manual_boundary_count || 0), next.length - 1);
  trajectoryData.value.sub_task_metadata = meta;
  dirty.value = true;
}

function mergeSelectedSubTaskWithPrev() {
  const task = selectedSubTask.value;
  if (!task || !trajectoryData.value || !canMergeSelectedSubTask.value) return;
  const list = [...subTasks.value];
  const pos = list.findIndex((t) => t.id === task.id);
  if (pos <= 0) return;
  const prev = list[pos - 1];
  const instruction = (prev.instruction || task.instruction || "").trim();
  const merged = {
    ...prev,
    instruction,
    end_step: Number(task.end_step),
    source: "manual",
    confidence: null,
    needs_review: !instruction,
    model: null,
    prompt_version: null,
  };
  const next = [...list.slice(0, pos - 1), merged, ...list.slice(pos + 1)];
  next.forEach((t, i) => (t.index = i));
  trajectoryData.value.sub_tasks = next;
  dirty.value = true;
}

function rebuildSubTasksAfterDelete(deletedIndex: number) {
  if (!trajectoryData.value) return;
  const next: any[] = [];
  for (const item of subTasks.value) {
    let start = Number(item.start_step);
    let end = Number(item.end_step);
    if (end < deletedIndex) {
      // unchanged
    } else if (start > deletedIndex) {
      start -= 1;
      end -= 1;
    } else {
      end -= 1;
      if (start > end) continue;
    }
    next.push({ ...item, start_step: start, end_step: end });
  }
  next.forEach((t, i) => (t.index = i));
  trajectoryData.value.sub_tasks = next;
}

async function runModelSplitSubtasks() {
  if (!trajectoryData.value || subtaskSplitBusy.value) return;
  subtaskSplitBusy.value = true;
  status.value = "正在调用模型划分子任务（整轨覆盖）…";
  try {
    await warmupBackend(true).catch(() => {});
    let updated: any = null;
    if (activeCloudSession.value) {
      updated = await invoke("cloud_split_subtasks", {
        sessionId: activeCloudSession.value,
        trajectory: trajectoryData.value,
      });
    } else {
      updated = await invoke("backend_split_subtasks", {
        trajectory: trajectoryData.value,
        recordingPath: recordingPath.value || null,
      });
    }
    if (!updated || typeof updated !== "object" || !Array.isArray(updated.trajectory)) {
      throw new Error("模型返回的轨迹无效");
    }
    trajectoryData.value = updated;
    ensureSubTaskDefaults();
    dirty.value = true;
    selectedViewIndex.value = 0;
    const visionMeta = updated?.sub_task_metadata || {};
    const modeHint =
      visionMeta.prompt_version === "subtask-split-v2"
        ? "（看图划分 v2）"
        : visionMeta.prompt_version === "subtask-split-v1"
          ? "（文本划分 v1）"
          : "";
    status.value = `模型划分完成：${(updated.sub_tasks || []).length} 个子任务${modeHint}（可撤销）`;
  } catch (e: any) {
    status.value = `模型划分失败：${e?.message || String(e)}`;
  } finally {
    subtaskSplitBusy.value = false;
  }
}

function undoModelSplitSubtasks() {
  if (!trajectoryData.value || !canUndoModelSplit.value) return;
  const meta = trajectoryData.value.sub_task_metadata || {};
  const snapshot = meta.previous_snapshot;
  if (snapshot && typeof snapshot === "object" && Array.isArray(snapshot.sub_tasks)) {
    const oldMeta = snapshot.sub_task_metadata && typeof snapshot.sub_task_metadata === "object"
      ? snapshot.sub_task_metadata
      : {};
    trajectoryData.value.sub_tasks = JSON.parse(JSON.stringify(snapshot.sub_tasks));
    trajectoryData.value.sub_task_metadata = {
      status: oldMeta.status || "none",
      splitter: oldMeta.splitter || "none",
      manual_boundary_count: Number(oldMeta.manual_boundary_count || 0),
      split_at_unix: oldMeta.split_at_unix ?? null,
      prompt_version: oldMeta.prompt_version ?? null,
      previous_sub_tasks: null,
      previous_snapshot: null,
    };
  } else if (Array.isArray(meta.previous_sub_tasks)) {
    const prev = JSON.parse(JSON.stringify(meta.previous_sub_tasks));
    trajectoryData.value.sub_tasks = prev;
    trajectoryData.value.sub_task_metadata = {
      status: prev.length > 1 ? "manual_complete" : "none",
      splitter: prev.length > 1 ? "manual" : "none",
      manual_boundary_count: prev.length > 1 ? prev.length - 1 : 0,
      split_at_unix: null,
      prompt_version: null,
      previous_sub_tasks: null,
      previous_snapshot: null,
    };
  } else {
    status.value = "没有可撤销的模型划分备份";
    return;
  }
  dirty.value = true;
  selectedViewIndex.value = 0;
  status.value = "已撤销模型划分，已恢复上一版子任务";
}
const previewPath = computed(() => {
  if (!recordingPath.value || !selectedStep.value) return "";
  const frames = selectedStep.value.frames || {};
  const relPath = frames[screenshotFrameMode.value] || selectedStep.value.observation || selectedStep.value.objects;
  if (!relPath) return "";
  const normalizedRel = String(relPath).replace(/\//g, "\\");
  return `${recordingPath.value}\\${normalizedRel}`;
});
const previewSrc = computed(() => {
  if (!previewPath.value) return "";
  return convertFileSrc(normalizePathForAsset(previewPath.value));
});
const showScreenshotPreview = computed(() => !hasTimeline.value && !!previewSrc.value);
const isSelectedWait = computed(() => {
  const s = selectedStep.value;
  if (!s) return false;
  return getActionOf(s).name === "wait";
});
const canDeleteSelected = computed(() => {
  const s = selectedStep.value;
  if (!s) return false;
  const name = getActionOf(s).name;
  // 终止步骤一般作为整体结束标记，默认不允许删。
  return name !== "terminate";
});

const selectedStepInstruction = computed<string>({
  get() {
    return selectedStep.value?.step_instruction || "";
  },
  set(v: string) {
    if (!selectedStep.value) return;
    selectedStep.value.step_instruction = v;
    selectedStep.value.annotation ||= {};
    selectedStep.value.annotation.step_instruction = v;
    dirty.value = true;
  },
});

function annotationField<T>(key: string, fallback: T) {
  return computed<T>({
    get() {
      return (selectedStep.value?.annotation?.[key] ?? fallback) as T;
    },
    set(value: T) {
      if (!selectedStep.value) return;
      selectedStep.value.annotation ||= {};
      selectedStep.value.annotation[key] = value;
      if (key === "intent") selectedStep.value.rationale = value;
      if (key === "result") selectedStep.value.screenshot_caption = value;
      dirty.value = true;
    },
  });
}
const selectedIntent = annotationField<string>("intent", "");
const selectedResult = annotationField<string>("result", "");
const selectedConfidence = annotationField<number>("confidence", 0);
const selectedRisk = annotationField<string>("risk_level", "low");
const selectedNeedsReview = annotationField<boolean>("needs_review", false);

const viewRangeText = computed(() => {
  const total = filteredIndices.value.length;
  const cur = total ? selectedViewIndex.value + 1 : 0;
  return `已选 ${cur} / ${total}`;
});

// Timeline mapping state
const hasTimeline = computed(() => timelineReady.value);
const timelineReady = ref(false);
let rawFirstTs = 0;
let videoStartOffsetS = 0;
let pauseSegments: Array<[number, number]> = [];
let trimPausesFromTimeline = true;

const manualOffsetS = ref(0);
const zoomMode = ref<"fit" | "100" | "150" | "200">("fit");
const zoomScale = computed(() => {
  if (zoomMode.value === "150") return 1.5;
  if (zoomMode.value === "200") return 2;
  return 1;
});
let previewTimer: number | null = null;
const previewBeforeS = 0.75;
const previewAfterS = 1.35;
const trailPoints = ref<TrailPoint[]>([]);
const trailPathD = ref("");
const cursorPoint = ref<TrailPoint | null>(null);
const selectedActionMarker = ref<ScreenPoint | null>(null);
const dragPreviewLine = ref<DragPreviewLine | null>(null);
const previewChipText = ref("");
const previewWindow = ref<{ start: number; end: number; focus: number } | null>(null);
const actionHintText = ref("");
const rawEvents = ref<any[]>([]);
const normalizedEvents = ref<Array<{ t: number; nx?: number; ny?: number; snx?: number; sny?: number }>>([]);
const screenWidth = ref(1);
const screenHeight = ref(1);

function setZoomMode(mode: "fit" | "100" | "150" | "200") {
  zoomMode.value = mode;
  updateOverlayByVideoTime();
}

function getActionOf(step: Step): { name: string; parameters: any } {
  const a = step?.action?.[0] || {};
  return { name: a.name || "unknown", parameters: a.parameters || {} };
}

function formatStepLabel(step: Step): string {
  const a = getActionOf(step);
  const name = ACTION_CN[a.name] || a.name;
  const p = a.parameters || {};
  const parts: string[] = [name];
  if (typeof p.x === "number" && typeof p.y === "number") {
    parts.push(`(${p.x.toFixed(4)}, ${p.y.toFixed(4)})`);
  }
  if (
    p.start_x != null &&
    p.start_y != null &&
    p.end_x != null &&
    p.end_y != null
  ) {
    parts.push(`(${p.start_x.toFixed(4)}, ${p.start_y.toFixed(4)}) -> (${p.end_x.toFixed(4)}, ${p.end_y.toFixed(4)})`);
  }
  if (typeof p.text === "string") parts.push(`"${p.text}"`);
  if (typeof p.key === "string") parts.push(`键=${p.key}`);
  if (a.name === "scroll" && typeof p.clicks === "number") parts.push(`clicks=${p.clicks}`);
  return parts.join("  ");
}

function pausedSecondsBefore(tRel: number): number {
  let total = 0;
  for (const [ps, rs] of pauseSegments) {
    if (tRel <= ps) break;
    if (tRel >= rs) total += rs - ps;
    else {
      total += tRel - ps;
      break;
    }
  }
  return total;
}

function mapElapsedToVideoSeconds(elapsedS: number): number {
  const pausedS = trimPausesFromTimeline ? pausedSecondsBefore(elapsedS) : 0;
  const corrected = elapsedS - videoStartOffsetS - pausedS + Number(manualOffsetS.value || 0);
  return Math.max(0, corrected);
}

function mapVideoToElapsed(videoS: number): number {
  // Approximate inverse of mapElapsedToVideoSeconds.
  let elapsed = videoS + videoStartOffsetS - Number(manualOffsetS.value || 0);
  for (let i = 0; i < (trimPausesFromTimeline ? 2 : 0); i++) {
    elapsed = videoS + videoStartOffsetS - Number(manualOffsetS.value || 0) + pausedSecondsBefore(elapsed);
  }
  return Math.max(0, elapsed);
}

function getVideoRenderRect() {
  const stage = videoStageEl.value || imageStageEl.value;
  const v = videoEl.value;
  const image = previewImageEl.value;
  if (!stage || (!v && !image)) return null;
  const cw = stage.clientWidth;
  const ch = stage.clientHeight;
  const vw = v?.videoWidth || image?.naturalWidth || 1;
  const vh = v?.videoHeight || image?.naturalHeight || 1;
  const videoAspect = vw / vh;
  const containerAspect = cw / ch;

  // Always use contain mapping for overlay, and scale video+overlay together.
  let rw = cw;
  let rh = ch;
  if (videoAspect > containerAspect) {
    rh = rw / videoAspect;
  } else {
    rw = rh * videoAspect;
  }
  return {
    left: (cw - rw) / 2,
    top: (ch - rh) / 2,
    width: rw,
    height: rh,
  };
}

function clamp01(v: number): number {
  return Math.max(0, Math.min(1, v));
}

function normToScreenPoint(nx: number, ny: number): ScreenPoint | null {
  const rect = getVideoRenderRect();
  if (!rect) return null;
  return {
    x: rect.left + clamp01(nx) * rect.width,
    y: rect.top + clamp01(ny) * rect.height,
  };
}

function buildTrailForElapsed(elapsedS: number): TrailPoint[] {
  const focus = previewWindow.value?.focus ?? Number(selectedStep.value?.elapsed_time || elapsedS);
  const windowS = previewWindow.value
    ? Math.max(0.3, previewWindow.value.end - previewWindow.value.start)
    : 2.2;
  const start = previewWindow.value?.start ?? Math.max(0, elapsedS - windowS);
  const end = previewWindow.value ? Math.min(previewWindow.value.end, elapsedS + 0.04) : elapsedS;
  const points: TrailPoint[] = [];
  const list = normalizedEvents.value || [];
  let idx = 0;
  for (const ev of list) {
    const t = Number(ev?.t || 0);
    if (t < start || t > end) continue;

    // Main pointer coordinates
    if (typeof ev.nx === "number" && typeof ev.ny === "number") {
      const p = normToScreenPoint(ev.nx, ev.ny);
      if (p) {
        const age = Math.max(0, end - t);
        const opacity = Math.max(0.16, 1 - age / windowS);
        const hot = Math.abs(t - focus) < 0.18;
        points.push({ id: `${idx++}-${t}-xy`, x: p.x, y: p.y, opacity, hot });
      }
    }

    // Drag start anchor
    if (typeof ev.snx === "number" && typeof ev.sny === "number") {
      const p = normToScreenPoint(ev.snx, ev.sny);
      if (p) {
        const age = Math.max(0, end - t);
        const opacity = Math.max(0.12, 1 - age / windowS);
        points.push({ id: `${idx++}-${t}-start`, x: p.x, y: p.y, opacity });
      }
    }
  }
  return points.slice(-260);
}

function buildTrailPath(points: TrailPoint[]): string {
  const stage = videoStageEl.value || imageStageEl.value;
  if (!stage || points.length < 2) return "";
  const w = stage.clientWidth || 1;
  const h = stage.clientHeight || 1;
  return points
    .map((p, i) => {
      const x = (p.x / w) * 100;
      const y = (p.y / h) * 100;
      return `${i === 0 ? "M" : "L"} ${x.toFixed(3)} ${y.toFixed(3)}`;
    })
    .join(" ");
}

function markerFromParams(params: any): ScreenPoint | null {
  if (typeof params?.x === "number" && typeof params?.y === "number") {
    return normToScreenPoint(params.x, params.y);
  }
  if (typeof params?.end_x === "number" && typeof params?.end_y === "number") {
    return normToScreenPoint(params.end_x, params.end_y);
  }
  if (typeof params?.start_x === "number" && typeof params?.start_y === "number") {
    return normToScreenPoint(params.start_x, params.start_y);
  }
  return null;
}

function buildSelectedActionMarker(): ScreenPoint | null {
  const step = selectedStep.value;
  if (!step) return null;
  return markerFromParams(getActionOf(step).parameters || {});
}

function buildSelectedDragLine(): DragPreviewLine | null {
  const step = selectedStep.value;
  if (!step) return null;
  const p = getActionOf(step).parameters || {};
  if (
    typeof p.start_x !== "number" ||
    typeof p.start_y !== "number" ||
    typeof p.end_x !== "number" ||
    typeof p.end_y !== "number"
  ) {
    return null;
  }
  const start = normToScreenPoint(p.start_x, p.start_y);
  const end = normToScreenPoint(p.end_x, p.end_y);
  if (!start || !end) return null;
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  return {
    left: start.x,
    top: start.y,
    width: Math.hypot(dx, dy),
    angle: Math.atan2(dy, dx),
  };
}

function getPreviewChipText(elapsedS: number): string {
  const step = selectedStep.value;
  if (!step || !previewWindow.value) return "";
  const action = getActionOf(step).name;
  const delta = elapsedS - previewWindow.value.focus;
  const sign = delta >= 0 ? "+" : "";
  return `动作预览 · ${ACTION_CN[action] || action} ${sign}${delta.toFixed(2)}s`;
}

function getActionHintForElapsed(elapsedS: number): string {
  const list = steps.value || [];
  let best: Step | null = null;
  for (const step of list) {
    const t = Number(step?.elapsed_time || 0);
    if (t <= elapsedS) best = step;
    else break;
  }
  if (!best) return "";
  const t = Number(best.elapsed_time || 0);
  if (Math.abs(elapsedS - t) > 0.45) return "";
  const action = getActionOf(best).name;
  return ACTION_CN[action] || action || "";
}

function updateOverlayByVideoTime() {
  const v = videoEl.value;
  if (!v || !hasTimeline.value) return;
  const elapsed = mapVideoToElapsed(v.currentTime || 0);
  trailPoints.value = buildTrailForElapsed(elapsed);
  trailPathD.value = buildTrailPath(trailPoints.value);
  cursorPoint.value = trailPoints.value.at(-1) || null;
  selectedActionMarker.value = buildSelectedActionMarker();
  dragPreviewLine.value = buildSelectedDragLine();
  previewChipText.value = getPreviewChipText(elapsed);
  actionHintText.value = getActionHintForElapsed(elapsed);
}

async function waitForSeek(video: HTMLVideoElement): Promise<void> {
  await new Promise<void>((resolve) => {
    const timer = window.setTimeout(() => {
      video.removeEventListener("seeked", onSeeked);
      resolve();
    }, 1200);
    const onSeeked = () => {
      window.clearTimeout(timer);
      video.removeEventListener("seeked", onSeeked);
      resolve();
    };
    video.addEventListener("seeked", onSeeked);
  });
}

async function playActionPreviewAt(elapsedS: number) {
  if (!videoEl.value) return;
  const v = videoEl.value;
  const startElapsed = Math.max(0, elapsedS - previewBeforeS);
  const endElapsed = Math.max(startElapsed + 0.45, elapsedS + previewAfterS);
  const startVideoS = mapElapsedToVideoSeconds(startElapsed);
  const endVideoS = mapElapsedToVideoSeconds(endElapsed);
  const maxVideoS = Number.isFinite(v.duration) ? v.duration : endVideoS;
  const safeEndVideoS = Math.min(Math.max(startVideoS + 0.45, endVideoS), maxVideoS || endVideoS);

  previewWindow.value = {
    start: startElapsed,
    end: mapVideoToElapsed(safeEndVideoS),
    focus: elapsedS,
  };

  try {
    v.pause();
    v.currentTime = startVideoS;
    await waitForSeek(v);
    updateOverlayByVideoTime();
  } catch {
    // ignore seek errors
  }

  try {
    await v.play();
  } catch {
    // autoplay restrictions
  }

  if (previewTimer) window.clearTimeout(previewTimer);
  const durationMs = Math.max(650, Math.min(3200, (safeEndVideoS - startVideoS) * 1000));
  previewTimer = window.setTimeout(() => {
    v.pause();
    try {
      v.currentTime = safeEndVideoS;
    } catch {
      // ignore seek errors
    }
    updateOverlayByVideoTime();
  }, durationMs);
}

function selectViewIndex(viewIdx: number) {
  selectedViewIndex.value = viewIdx;
  void onStepSelected();
}

async function onStepSelected() {
  if (!selectedStep.value) return;
  if (!hasTimeline.value) {
    trailPoints.value = buildSelectedDragTrail();
    trailPathD.value = buildTrailPath(trailPoints.value);
    selectedActionMarker.value = buildSelectedActionMarker();
    dragPreviewLine.value = buildSelectedDragLine();
    return;
  }
  const elapsed = Number(selectedStep.value.elapsed_time || 0);
  await playActionPreviewAt(elapsed);
}

function buildSelectedDragTrail(): TrailPoint[] {
  const step = selectedStep.value;
  if (!step) return [];
  const params = getActionOf(step).parameters || {};
  const path = Array.isArray(params.path) ? params.path : [];
  return path
    .map((point: any, index: number) => {
      if (typeof point?.x !== "number" || typeof point?.y !== "number") return null;
      const mapped = normToScreenPoint(point.x, point.y);
      return mapped ? { id: `drag-${index}`, x: mapped.x, y: mapped.y, opacity: 1, hot: index === path.length - 1 } : null;
    })
    .filter((point: TrailPoint | null): point is TrailPoint => !!point);
}

function updateScreenshotOverlay() {
  window.setTimeout(() => {
    trailPoints.value = buildSelectedDragTrail();
    trailPathD.value = buildTrailPath(trailPoints.value);
    selectedActionMarker.value = buildSelectedActionMarker();
    dragPreviewLine.value = buildSelectedDragLine();
  }, 0);
}

watch(manualOffsetS, () => {
  if (!hasTimeline.value) return;
  void onStepSelected();
});

watch(selectedViewIndex, () => {
  // Ensure overlay refresh when selecting a different step.
  window.setTimeout(() => updateOverlayByVideoTime(), 0);
});

function goPrev() {
  if (selectedViewIndex.value <= 0) return;
  selectViewIndex(selectedViewIndex.value - 1);
}
function goNext() {
  if (selectedViewIndex.value >= filteredIndices.value.length - 1) return;
  selectViewIndex(selectedViewIndex.value + 1);
}

function removeCurrentStep() {
  const step = selectedStep.value;
  if (!step) return;
  const idx = selectedStepIndex.value;
  const list = steps.value;
  if (!Array.isArray(list) || idx < 0 || idx >= list.length) return;
  list.splice(idx, 1);
  rebuildSubTasksAfterDelete(idx);
  dirty.value = true;
  if (selectedViewIndex.value >= filteredIndices.value.length) {
    selectedViewIndex.value = Math.max(0, filteredIndices.value.length - 1);
  }
}

function actionParamsToHtml(p: any): string {
  const entries = Object.entries(p || {});
  if (entries.length === 0) return "";
  return `<div style="margin-top:6px;">
    ${entries
      .map(
        ([k, v]) =>
          `<div style="display:flex; gap:10px; padding:2px 0;"><b style="color:#9ca3af; min-width:110px;">${k}</b><span style="color:#ececec;">${String(v)}</span></div>`
      )
      .join("")}
  </div>`;
}

function formatStepDetailHtml(step: Step | null): string {
  if (!step) return "";
  const a = getActionOf(step);
  const p = a.parameters || {};
  const cn = ACTION_CN[a.name] || a.name;
  const elapsed = step.elapsed_time ?? "";
  return `
    <div style="font-weight:700; color:#ececec;">${cn}</div>
    <div style="color:#9ca3af; margin-top:2px;">(${a.name})</div>
    <div style="color:#8e8ea0; margin-top:10px; font-size:12px;">elapsed_time: ${elapsed}</div>
    ${actionParamsToHtml(p)}
  `;
}

async function computeTimelineMapping() {
  if (!recordingPath.value) return;
  const eventsPath = `${recordingPath.value}\\events.jsonl`;
  const metadataPath = `${recordingPath.value}\\metadata.json`;

  try {
    const bytes = await invoke<number>("file_size_bytes", { path: eventsPath });
    if (Number.isFinite(Number(bytes)) && Number(bytes) > MAX_TIMELINE_EVENTS_FILE_BYTES) {
      rawEvents.value = [];
      normalizedEvents.value = [];
      pauseSegments = [];
      timelineReady.value = false;
      return;
    }
  } catch (_e) {
    // Best effort check; continue when file size cannot be read.
  }

  const eventsText: string = await invoke("read_text_file", { path: eventsPath });
  const lines = eventsText.split(/\r?\n/).filter(Boolean);
  const events = lines.map((l) => JSON.parse(l));
  if (!events.length) return;
  rawEvents.value = events;

  rawFirstTs = Number(events[0].time_stamp || 0);

  // Pause/resume segments (relative to rawFirstTs)
  pauseSegments = [];
  let pausedStart: number | null = null;
  for (const ev of events) {
    if (ev.action === "pause") {
      if (pausedStart == null) pausedStart = Number(ev.time_stamp || 0);
    } else if (ev.action === "resume" && pausedStart != null) {
      const resumeTs = Number(ev.time_stamp || 0);
      const pauseRel = pausedStart - rawFirstTs;
      const resumeRel = resumeTs - rawFirstTs;
      if (resumeRel > pauseRel) pauseSegments.push([pauseRel, resumeRel]);
      pausedStart = null;
    }
  }
  pauseSegments.sort((a, b) => a[0] - b[0]);

  const metadataText: string = await invoke("read_text_file", { path: metadataPath });
  const metadata = JSON.parse(metadataText);
  screenWidth.value = Number(metadata?.screen_width || 1);
  screenHeight.value = Number(metadata?.screen_height || 1);

  // Normalize trajectory coordinates with auto-correction:
  // Use whichever is larger between metadata screen size and observed max event coords.
  // This reduces systematic offset when DPI scaling causes coord-space mismatch.
  let maxX = 1;
  let maxY = 1;
  for (const ev of events) {
    if (typeof ev.x === "number") maxX = Math.max(maxX, ev.x);
    if (typeof ev.y === "number") maxY = Math.max(maxY, ev.y);
    if (typeof ev.start_x === "number") maxX = Math.max(maxX, ev.start_x);
    if (typeof ev.start_y === "number") maxY = Math.max(maxY, ev.start_y);
  }
  const normW = Math.max(1, Number(screenWidth.value || 1), maxX);
  const normH = Math.max(1, Number(screenHeight.value || 1), maxY);
  normalizedEvents.value = events.map((ev: any) => {
    const t = Number(ev?.time_stamp || 0) - rawFirstTs;
    const item: { t: number; nx?: number; ny?: number; snx?: number; sny?: number } = { t };
    if (typeof ev.x === "number" && typeof ev.y === "number") {
      item.nx = ev.x / normW;
      item.ny = ev.y / normH;
    }
    if (typeof ev.start_x === "number" && typeof ev.start_y === "number") {
      item.snx = ev.start_x / normW;
      item.sny = ev.start_y / normH;
    }
    return item;
  });

  trimPausesFromTimeline = true;
  const videoTiming = metadata?.video_timing || {};
  if (videoTiming && typeof videoTiming === "object" && Object.keys(videoTiming).length) {
    if (!Boolean(videoTiming.realtime ?? true)) {
      timelineReady.value = false;
      return;
    }

    trimPausesFromTimeline = Boolean(videoTiming.trim_pauses ?? true);
    const timingCandidates = Array.isArray(videoTiming.start_events) ? videoTiming.start_events : [];
    const validTimes = timingCandidates
      .map((t: any) => Number(t))
      .filter((t: number) => Number.isFinite(t));

    if (validTimes.length) {
      videoStartOffsetS = Math.min(...validTimes) - rawFirstTs;
      timelineReady.value = true;
      return;
    }
  }

  const obsTimings = metadata?.obs_record_state_timings || {};
  const allTimes: number[] = [];
  const recordCandidates: number[] = [];

  for (const [state, list] of Object.entries(obsTimings)) {
    const stateL = state.toLowerCase();
    if (!Array.isArray(list)) continue;
    for (const t of list) {
      const ft = Number(t);
      if (!Number.isFinite(ft)) continue;
      allTimes.push(ft);
      if (stateL.includes("record") || stateL.includes("start")) recordCandidates.push(ft);
    }
  }

  if (!allTimes.length) {
    timelineReady.value = false;
    return;
  }

  const obsStartTs = recordCandidates.length ? Math.min(...recordCandidates) : Math.min(...allTimes);
  videoStartOffsetS = obsStartTs - rawFirstTs;
  timelineReady.value = true;
}

async function pickRecordingDir() {
  status.value = "加载中...";
  timelineReady.value = false;
  dirty.value = false;
  selectedViewIndex.value = 0;
  recordingPath.value = null;
  trajectoryData.value = null;
  const prevMp4 = mp4Path.value;

  try {
    const dir = directoryInput.value?.trim();
    if (!dir) {
      status.value = "请先输入录制目录路径";
      return;
    }

    recordingPath.value = dir;

    const trajPath = `${dir}\\trajectory.json`;
    status.value = `读取 trajectory.json...`;
    const trajText: string = await invoke("read_text_file", { path: trajPath });
    trajectoryData.value = JSON.parse(trajText);
    ensureSubTaskDefaults();

    status.value = `查找 mp4...`;
    const foundMp4 = await invoke<string | null>("get_latest_mp4", { dir });
    if (foundMp4) {
      mp4Path.value = normalizePathForAsset(foundMp4);
      status.value = `已加载视频：${foundMp4.split(/[/\\]/).pop()}`;
    } else if (!mp4Path.value) {
      status.value = "未找到 mp4，视频预览将不可用。可点击“选择视频”手动指定。";
    } else {
      // keep previous selected video as fallback
      status.value = `未自动找到新视频，继续使用：${mp4Path.value.split(/[/\\]/).pop()}`;
    }

    status.value = `计算时间映射...`;
    await computeTimelineMapping();

    status.value = hasTimeline.value
      ? ""
      : previewSrc.value
        ? "当前录制使用步骤截图预览，视频时间轴不可用。"
        : "当前录制没有可用的视频时间轴。";
    if (filteredIndices.value.length) {
      void onStepSelected();
    } else {
      status.value = "trajectory.json 中没有可标注的步骤。";
    }
  } catch (e: any) {
    status.value = `加载失败：${e?.message || String(e)}`;
    // keep previous video source on load failure
    if (!mp4Path.value && prevMp4) {
      mp4Path.value = prevMp4;
    }
  }
}

async function browseRecordingDir() {
  try {
    const picked = await invoke<string | null>("select_folder", {
      defaultPath: directoryInput.value?.trim() || activeRecordingPath.value || undefined,
    });
    if (picked) {
      activeCloudSession.value = "";
      directoryInput.value = picked;
      status.value = `已选择目录：${picked}`;
    }
  } catch (e: any) {
    status.value = `打开文件夹选择失败：${e?.message || String(e)}`;
  }
}

async function browseVideoFile() {
  try {
    const picked = await invoke<string | null>("select_video_file", {
      defaultPath: recordingPath.value || directoryInput.value?.trim() || activeRecordingPath.value || undefined,
    });
    if (picked) {
      mp4Path.value = normalizePathForAsset(picked);
      status.value = `已手动选择视频：${picked.split(/[/\\]/).pop()}`;
      updateOverlayByVideoTime();
    }
  } catch (e: any) {
    status.value = `选择视频失败：${e?.message || String(e)}`;
  }
}

function enterAnnotator() {
  if (recorderState.value !== "idle") {
    status.value = "录制尚未结束，请先停止录制。";
    return;
  }
  if (!directoryInput.value.trim()) {
    status.value = "请先选择或输入录制目录，再进入标注。";
    return;
  }
  enteredAnnotator.value = true;
  // Auto-load once entering annotation mode to avoid blank screen.
  void pickRecordingDir();
}

async function invokeWithTimeout<T>(command: string, args?: Record<string, unknown>, timeoutMs = 15000): Promise<T> {
  let timer: number | undefined;
  const timeoutPromise = new Promise<never>((_, reject) => {
    timer = window.setTimeout(() => {
      reject(new Error(`命令超时（${timeoutMs}ms）：${command}`));
    }, timeoutMs);
  });
  try {
    return await Promise.race([invoke<T>(command, args), timeoutPromise]);
  } finally {
    if (timer) {
      window.clearTimeout(timer);
    }
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function waitForRecordingStart(maxWaitMs = 45000): Promise<string> {
  const begin = Date.now();
  let lastHintSec = -1;

  while (Date.now() - begin < maxWaitMs) {
    const state = await invoke<string>("backend_get_recording_state").catch(() => "starting");
    if (state === "recording") {
      const path = await invoke<string | null>("backend_get_active_recording_path").catch(() => null);
      return path || "";
    }
    if (state === "idle") {
      const err = await invoke<string | null>("backend_get_start_error").catch(() => null);
      throw new Error(err || "backend startup aborted");
    }

    const elapsedSec = Math.floor((Date.now() - begin) / 1000);
    if (elapsedSec !== lastHintSec) {
      status.value = `正在启动录制...${elapsedSec}s`;
      lastHintSec = elapsedSec;
    }

    await sleep(250);
  }

  throw new Error(`启动录制超时（${maxWaitMs}ms），可能是后端依赖或权限问题`);
}

async function waitForTrajectoryAndLoad(path: string) {
  const trajPath = `${path}\\trajectory.json`;
  const maxWaitS = 120;

  for (let elapsed = 0; elapsed < maxWaitS; elapsed += 1) {
    try {
      const bytes = await invoke<number>("file_size_bytes", { path: trajPath });
      if (Number.isFinite(Number(bytes)) && Number(bytes) > 2) {
        await pickRecordingDir();
        return;
      }
    } catch (_e) {
      // trajectory not ready yet
    }

    if (elapsed === 0 || (elapsed + 1) % 5 === 0) {
      status.value = `正在收尾并生成轨迹...${elapsed + 1}s`;
    }
    await sleep(1000);
  }

  status.value = "录制已停止，但轨迹仍在生成。可稍后点击“加载”。";
}

async function startRecording() {
  if (recorderBusy.value || recorderState.value !== "idle") return;
  const saveDir = await invoke<string | null>("select_folder", {
    defaultPath: directoryInput.value?.trim() || activeRecordingPath.value || undefined,
  }).catch((e: any) => {
    status.value = `选择保存目录失败：${e?.message || String(e)}`;
    return null;
  });
  if (!saveDir) {
    status.value = "已取消录制。";
    return;
  }

  recorderBusy.value = true;
  status.value = "正在启动录制...";
  try {
    recorderState.value = "starting";
    await invokeWithTimeout("backend_begin_start_recording", { recordingsDir: saveDir }, 5000);
    const path = await waitForRecordingStart(60000);
    recorderState.value = "recording";
    if (path) {
      activeRecordingPath.value = path;
      directoryInput.value = path;
    }
    status.value = path ? `录制中：${path}` : "录制已开始";
    recorderBusy.value = false;
  } catch (e: any) {
    recorderState.value = "idle";
    await invoke("hide_floating_controls").catch(() => {});
    await invoke("show_main_window").catch(() => {});
    status.value = `开始录制失败：${e?.message || String(e)}`;
  } finally {
    recorderBusy.value = false;
  }
}

async function pauseRecording() {
  if (recorderBusy.value || recorderState.value !== "recording") return;
  recorderBusy.value = true;
  status.value = "正在暂停录制...";
  try {
    await invokeWithTimeout("backend_pause_recording");
    recorderState.value = "paused";
    status.value = "录制已暂停";
  } catch (e: any) {
    status.value = `暂停录制失败：${e?.message || String(e)}`;
  } finally {
    recorderBusy.value = false;
  }
}

async function resumeRecording() {
  if (recorderBusy.value || recorderState.value !== "paused") return;
  recorderBusy.value = true;
  status.value = "正在恢复录制...";
  try {
    await invokeWithTimeout("backend_resume_recording");
    recorderState.value = "recording";
    status.value = "录制已恢复";
  } catch (e: any) {
    status.value = `恢复录制失败：${e?.message || String(e)}`;
  } finally {
    recorderBusy.value = false;
  }
}

async function stopRecording() {
  if (recorderBusy.value || recorderState.value === "idle" || recorderState.value === "starting") return;
  recorderBusy.value = true;
  status.value = "正在停止录制...";
  try {
    // Stop may take longer because capture backend needs to flush/export files.
    const path = await invokeWithTimeout<string>("backend_stop_recording", undefined, 180000);
    recorderState.value = "idle";
    await invoke("hide_floating_controls").catch(() => {});
    await invoke("show_main_window").catch(() => {});
    if (!path) {
      status.value = "录制已停止，请手动选择录制目录。";
      return;
    }
    activeRecordingPath.value = path;
    directoryInput.value = path;
    enteredAnnotator.value = true;
    status.value = "录制已停止，正在后台收尾...";
    void waitForTrajectoryAndLoad(path);
  } catch (e: any) {
    status.value = `停止录制失败：${e?.message || String(e)}`;
  } finally {
    recorderBusy.value = false;
  }
}

async function saveAnnotations() {
  if (!recordingPath.value || !trajectoryPath.value || !trajectoryData.value) return;
  status.value = "保存中...";
  try {
    const content = JSON.stringify(trajectoryData.value, null, 4);
    await invoke("write_text_file", { path: trajectoryPath.value, content });
    if (activeCloudSession.value) {
      await invoke("cloud_save_reviewed", {
        sessionId: activeCloudSession.value,
        trajectory: trajectoryData.value,
      });
    }
    dirty.value = false;
    status.value = `已保存：${trajectoryPath.value.split(/[/\\]/).pop()}`;
  } catch (e: any) {
    status.value = `保存失败：${e?.message || String(e)}`;
    return;
  }
  window.setTimeout(() => (status.value = ""), 1600);
}

async function refreshCloudSessions() {
  cloudBusy.value = true;
  status.value = "正在读取云端会话...";
  try {
    const result: any = await invoke("cloud_list_sessions");
    cloudSessions.value = Array.isArray(result?.sessions) ? result.sessions : [];
    if (!selectedCloudSession.value && cloudSessions.value.length) {
      selectedCloudSession.value = cloudSessions.value[0].session_id;
    }
    status.value = `已加载 ${cloudSessions.value.length} 个云端会话`;
  } catch (e: any) {
    status.value = `读取云端会话失败：${e?.message || String(e)}`;
  } finally {
    cloudBusy.value = false;
  }
}

async function loadCloudSession() {
  if (!selectedCloudSession.value || cloudBusy.value) return;
  cloudBusy.value = true;
  status.value = "正在下载云端轨迹和关键帧...";
  try {
    const cacheDir = await invoke<string>("cloud_download_session", {
      sessionId: selectedCloudSession.value,
    });
    activeCloudSession.value = selectedCloudSession.value;
    directoryInput.value = cacheDir;
    enteredAnnotator.value = true;
    await pickRecordingDir();
    status.value = `已加载云端会话：${activeCloudSession.value}`;
  } catch (e: any) {
    status.value = `加载云端会话失败：${e?.message || String(e)}`;
  } finally {
    cloudBusy.value = false;
  }
}

async function triggerCloudAnnotation() {
  if (!selectedCloudSession.value || cloudBusy.value) return;
  cloudBusy.value = true;
  status.value = "正在提交重新标注任务...";
  try {
    await invoke("cloud_trigger_annotation", { sessionId: selectedCloudSession.value });
    status.value = `已提交重新标注：${selectedCloudSession.value}`;
    await refreshCloudSessions();
  } catch (e: any) {
    status.value = `重新标注失败：${e?.message || String(e)}`;
  } finally {
    cloudBusy.value = false;
  }
}

onBeforeUnmount(() => {
  if (previewTimer) window.clearTimeout(previewTimer);
  void invoke("hide_floating_controls").catch(() => {});
});
</script>

<style scoped>
/* ChatGPT-like dark shell */
.app-root {
  --bg-sidebar: #202123;
  --bg-main: #343541;
  --bg-input: #40414f;
  --bg-hover: #2a2b32;
  --border-subtle: rgba(255, 255, 255, 0.08);
  --text: #ececec;
  --text-muted: #c5c5d2;
  --text-dim: #8e8ea0;
  --accent: #10a37f;
  --accent-hover: #0d8f6e;
  --danger-bg: rgba(239, 68, 68, 0.12);
  --danger-border: rgba(239, 68, 68, 0.35);
  --danger-text: #f87171;

  height: 100vh;
  width: 100vw;
  display: flex;
  overflow: hidden;
  background: var(--bg-main);
  color: var(--text);
}

.sidebar {
  width: 260px;
  flex-shrink: 0;
  background: var(--bg-sidebar);
  border-right: 1px solid var(--border-subtle);
  display: flex;
  flex-direction: column;
  padding: 12px 10px;
  box-sizing: border-box;
}

.sidebar-brand {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 10px 16px;
  border-bottom: 1px solid var(--border-subtle);
  margin-bottom: 12px;
}

.brand-mark {
  font-size: 15px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--text);
}

.brand-sub {
  font-size: 12px;
  color: var(--text-dim);
}

.sidebar-nav-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 10px 12px;
  margin-bottom: 8px;
  border: none;
  border-radius: 10px;
  background: transparent;
  color: var(--text);
  font-size: 14px;
  cursor: pointer;
  text-align: left;
  transition: background 0.15s ease;
}

.sidebar-nav-btn:hover {
  background: var(--bg-hover);
}

.sidebar-nav-icon {
  opacity: 0.7;
  font-size: 16px;
}

.sidebar-section-label {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-dim);
  padding: 4px 10px 8px;
}

.subtask-toolbar {
  padding: 0 10px 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.subtask-toolbar .btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.subtask-group-header {
  margin: 6px 8px 4px;
  padding: 6px 8px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.04);
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.subtask-group-header.active {
  background: rgba(16, 163, 127, 0.18);
}

.subtask-group-title {
  font-size: 12px;
  color: #e5e7eb;
  font-weight: 600;
}

.subtask-group-range {
  font-size: 11px;
  color: #9ca3af;
}

.subtask-edit {
  margin-bottom: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.subtask-meta {
  font-size: 12px;
  color: #9ca3af;
}

.subtask-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.subtask-empty {
  font-size: 12px;
  color: #9ca3af;
  margin-bottom: 10px;
}

.sidebar-steps {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.sidebar-hint {
  margin: 12px 10px;
  font-size: 13px;
  line-height: 1.5;
  color: var(--text-dim);
}

.main-column {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: var(--bg-main);
}

.topbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  min-height: 52px;
  box-sizing: border-box;
  border-bottom: 1px solid var(--border-subtle);
  flex-shrink: 0;
}

.dir-input-wrap {
  flex: 1;
  min-width: 200px;
}

.dir-input {
  width: 100%;
  padding: 10px 14px;
  border: none;
  border-radius: 12px;
  font-size: 13px;
  background: var(--bg-input);
  color: var(--text);
  outline: none;
  box-shadow: 0 0 0 1px var(--border-subtle);
}

.dir-input::placeholder {
  color: var(--text-dim);
}

.dir-input:focus {
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.2);
}

.btn {
  padding: 8px 14px;
  border-radius: 10px;
  border: 1px solid var(--border-subtle);
  background: transparent;
  color: var(--text);
  font-size: 13px;
  cursor: pointer;
  transition:
    background 0.15s ease,
    border-color 0.15s ease;
}

.btn:hover:not(:disabled) {
  background: var(--bg-hover);
}

.btn.primary {
  background: var(--accent);
  border-color: transparent;
  color: #fff;
  font-weight: 500;
}

.btn.primary:hover:not(:disabled) {
  background: var(--accent-hover);
}

.btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.btn.full {
  width: 100%;
  justify-content: center;
}

.status-pill {
  font-size: 12px;
  color: var(--text-dim);
  max-width: min(52vw, 860px);
  overflow-wrap: anywhere;
  white-space: normal;
  line-height: 1.35;
}

.recording-tag {
  font-size: 12px;
  color: var(--text-muted);
  padding: 4px 10px;
  border-radius: 999px;
  background: var(--bg-input);
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.spacer {
  flex: 1;
}

.workflow-main {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 32px 24px 48px;
  box-sizing: border-box;
}

.workflow-inner {
  max-width: 720px;
  margin: 0 auto;
}

.workflow-hero {
  margin: 0 0 8px;
  font-size: 28px;
  font-weight: 600;
  letter-spacing: -0.03em;
  color: var(--text);
}

.workflow-lead {
  margin: 0 0 28px;
  font-size: 15px;
  color: var(--text-dim);
  line-height: 1.55;
}

.workflow-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.cloud-workflow-card {
  grid-column: 1 / -1;
}

.cloud-session-row {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}

.cloud-session-select {
  flex: 1;
  min-width: 260px;
  padding: 9px 12px;
  border: 1px solid var(--border-subtle);
  border-radius: 10px;
  background: var(--bg-main);
  color: var(--text);
}

@media (max-width: 700px) {
  .workflow-grid {
    grid-template-columns: 1fr;
  }
}

.workflow-card {
  background: var(--bg-input);
  border: 1px solid var(--border-subtle);
  border-radius: 16px;
  padding: 20px;
  box-shadow: 0 4px 24px rgba(0, 0, 0, 0.12);
}

.workflow-card h2 {
  margin: 0 0 10px;
  font-size: 16px;
  font-weight: 600;
  color: var(--text);
}

.workflow-card p {
  margin: 0 0 18px;
  color: var(--text-muted);
  font-size: 14px;
  line-height: 1.5;
}

.workflow-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.recorder-controls {
  margin-bottom: 10px;
}

.recording-path {
  margin: 0;
  padding: 8px 10px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.04);
  font-size: 12px;
  color: var(--text-dim);
  word-break: break-all;
}

.annotator-main {
  flex: 1;
  min-height: 0;
  display: flex;
  overflow: hidden;
}

.panel-title {
  padding: 0 0 8px;
  color: var(--text-dim);
  font-weight: 600;
  letter-spacing: 0.04em;
  font-size: 11px;
  text-transform: uppercase;
}

.panel-title.small {
  padding: 0 0 6px;
}

.step-list {
  flex: 1;
  overflow: auto;
  padding-right: 4px;
}

.step-item {
  padding: 10px 12px;
  margin-bottom: 4px;
  border-radius: 10px;
  border: 1px solid transparent;
  background: transparent;
  cursor: pointer;
  font-size: 13px;
  line-height: 1.35;
  color: var(--text-muted);
  user-select: none;
  transition:
    background 0.12s ease,
    color 0.12s ease;
}

.step-item:hover {
  background: var(--bg-hover);
  color: var(--text);
}

.step-item.active {
  background: var(--bg-main);
  color: var(--text);
  border-color: var(--border-subtle);
  box-shadow: inset 3px 0 0 0 var(--accent);
}

.right-panel {
  flex: 1;
  overflow-x: hidden;
  overflow-y: auto;
  padding: 12px 16px 16px;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}

.video-wrap {
  flex: 1 1 auto;
  /* 下方标注表单变高时，禁止把截图区挤成 0 高度 */
  min-height: min(42vh, 420px);
  height: min(48vh, 520px);
  max-height: 560px;
  background: #0d0d0d;
  border-radius: 12px;
  overflow: hidden;
  position: relative;
  border: 1px solid var(--border-subtle);
  flex-shrink: 0;
}

.video-empty-tip {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-dim);
  font-size: 14px;
  z-index: 2;
  pointer-events: none;
  padding: 16px;
  text-align: center;
}

.video-stage {
  position: absolute;
  inset: 0;
  transform-origin: center center;
}

.image-stage {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 12px;
  box-sizing: border-box;
  overflow: auto;
}

.video {
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
}

.preview-image {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  display: block;
  transform-origin: center center;
}

.frame-label {
  margin-left: 10px;
}

.overlay-layer {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.trail-svg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  overflow: visible;
}

.trail-line,
.trail-line-shadow {
  fill: none;
  vector-effect: non-scaling-stroke;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.trail-line-shadow {
  stroke: rgba(0, 0, 0, 0.72);
  stroke-width: 7;
}

.trail-line {
  stroke: rgba(255, 48, 48, 0.94);
  stroke-width: 3;
  stroke-dasharray: 1;
  animation: trailFlow 1.2s linear infinite;
}

.trail-dot {
  position: absolute;
  width: 7px;
  height: 7px;
  margin-left: -3.5px;
  margin-top: -3.5px;
  border-radius: 999px;
  background: rgba(16, 163, 127, 0.95);
  box-shadow: 0 0 8px rgba(16, 163, 127, 0.45);
}

.trail-dot-hot {
  width: 11px;
  height: 11px;
  margin-left: -5.5px;
  margin-top: -5.5px;
  background: rgba(255, 210, 77, 0.96);
  box-shadow:
    0 0 0 2px rgba(0, 0, 0, 0.5),
    0 0 14px rgba(255, 210, 77, 0.72);
}

.cursor-marker {
  position: absolute;
  width: 18px;
  height: 18px;
  margin-left: -9px;
  margin-top: -9px;
  border-radius: 999px;
  border: 2px solid rgba(255, 255, 255, 0.94);
  background: rgba(16, 163, 127, 0.35);
  box-shadow:
    0 0 0 2px rgba(16, 163, 127, 0.8),
    0 0 16px rgba(16, 163, 127, 0.8);
  animation: cursorPulse 0.9s ease-in-out infinite;
}

.action-marker {
  position: absolute;
  width: 34px;
  height: 34px;
  margin-left: -17px;
  margin-top: -17px;
}

.action-ring,
.action-core {
  position: absolute;
  inset: 0;
  border-radius: 999px;
}

.action-ring {
  border: 3px solid rgba(255, 210, 77, 0.94);
  box-shadow:
    0 0 0 2px rgba(0, 0, 0, 0.5),
    0 0 18px rgba(255, 210, 77, 0.68);
  animation: actionRing 1.05s ease-out infinite;
}

.action-core {
  inset: 12px;
  background: rgba(255, 210, 77, 0.98);
  box-shadow: 0 0 10px rgba(0, 0, 0, 0.65);
}

.drag-preview-line {
  position: absolute;
  height: 4px;
  margin-top: -2px;
  transform-origin: 0 50%;
  border-radius: 999px;
  background: linear-gradient(90deg, rgba(255, 48, 48, 0.28), rgba(255, 48, 48, 0.96));
  box-shadow:
    0 0 0 1px rgba(0, 0, 0, 0.35),
    0 0 12px rgba(255, 48, 48, 0.55);
}

.preview-chip {
  position: absolute;
  left: 14px;
  top: 12px;
  max-width: min(420px, calc(100% - 28px));
  padding: 7px 10px;
  border-radius: 8px;
  background: rgba(32, 33, 35, 0.78);
  color: rgba(236, 236, 236, 0.95);
  font-size: 12px;
  font-weight: 600;
  line-height: 1.25;
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.12);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.action-hint {
  position: absolute;
  right: 16px;
  bottom: 14px;
  font-size: 34px;
  font-weight: 800;
  color: #10a37f;
  text-shadow:
    0 0 2px #000,
    0 0 8px #000,
    0 0 14px rgba(0, 0, 0, 0.85);
  letter-spacing: 1px;
}

@keyframes trailFlow {
  from {
    stroke-dashoffset: 1;
  }
  to {
    stroke-dashoffset: 0;
  }
}

@keyframes cursorPulse {
  0%,
  100% {
    transform: scale(0.9);
  }
  50% {
    transform: scale(1.18);
  }
}

@keyframes actionRing {
  from {
    transform: scale(0.7);
    opacity: 1;
  }
  to {
    transform: scale(1.35);
    opacity: 0.25;
  }
}

.zoom-row {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 10px;
  flex-wrap: wrap;
  flex-shrink: 0;
}

.zoom-btn {
  padding: 5px 12px;
  font-size: 12px;
}

.offset-row {
  display: flex;
  gap: 10px;
  align-items: center;
  margin: 8px 0;
  flex: 0 0 auto;
}

.offset-label {
  font-size: 12px;
  color: var(--text-dim);
}

.offset-input {
  width: 140px;
  padding: 8px 10px;
  border: none;
  border-radius: 10px;
  background: var(--bg-input);
  color: var(--text);
  font-size: 13px;
  box-shadow: 0 0 0 1px var(--border-subtle);
}

.detail-card,
.instruction-card {
  margin-top: 10px;
  background: var(--bg-input);
  border: 1px solid var(--border-subtle);
  border-radius: 16px;
  padding: 14px 16px;
  flex: 0 0 auto;
}

/* Composer-like instruction area */
.instruction-card {
  border-radius: 20px;
  padding-bottom: 16px;
}

.detail-content {
  padding-top: 4px;
  max-height: 110px;
  overflow: auto;
}

.detail-content :deep(*) {
  color: inherit;
}

.textarea {
  width: 100%;
  min-height: 88px;
  max-height: 140px;
  resize: vertical;
  padding: 14px 16px;
  border: none;
  border-radius: 14px;
  font-size: 15px;
  line-height: 1.45;
  box-sizing: border-box;
  background: var(--bg-main);
  color: var(--text);
  outline: none;
  box-shadow: inset 0 0 0 1px var(--border-subtle);
}

.textarea::placeholder {
  color: var(--text-dim);
}

.textarea:focus {
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.18);
}

.textarea:disabled {
  opacity: 0.5;
}

.annotation-fields {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto;
  gap: 10px;
  margin-top: 10px;
}

.annotation-field {
  display: flex;
  flex-direction: column;
  gap: 5px;
  color: var(--text-dim);
  font-size: 11px;
}

.annotation-field.wide {
  grid-column: span 3;
}

.annotation-field input,
.annotation-field select {
  min-width: 0;
  border: 1px solid var(--border-subtle);
  border-radius: 9px;
  padding: 8px 10px;
  color: var(--text);
  background: var(--bg-main);
}

.annotation-check {
  display: flex;
  align-items: end;
  gap: 6px;
  padding-bottom: 8px;
  color: var(--text-muted);
  font-size: 12px;
}

.wait-review-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 10px;
}

.wait-tip {
  font-size: 12px;
  color: var(--text-muted);
  line-height: 1.4;
}

.danger-btn {
  background: var(--danger-bg);
  border-color: var(--danger-border);
  color: var(--danger-text);
}

.danger-btn:hover:not(:disabled) {
  background: rgba(239, 68, 68, 0.2);
}

.nav-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-top: 12px;
}

.nav-center {
  flex: 1;
  text-align: center;
  font-size: 13px;
  color: var(--text-dim);
}
</style>
