# DuckTrack 录制轨迹数据说明

> 日期：2026-08-20  
> 适用范围：本机标注器录制并停止后落盘的结果目录（如 `output/recording-YYYY-MM-DD_HH-MM-SS/`）  
> 对照样例：`output/recording-2026-08-20_17-10-27`  
> 说明：以当前源码（含 v3 快捷键/悬停、v4 子任务分层划分能力）为准；标注/模型划分会改写部分字段，但**录制刚结束**时的结构如下。

---

## 1. 一次录制会产出什么

停止录制后，会在独立目录中写入：

```text
recording-YYYY-MM-DD_HH-MM-SS/
├── trajectory.json          # 主数据：步骤序列 + 子任务覆盖（标注/上传用）
├── events.jsonl             # 原始事件流（一行一个 JSON）
├── metadata.json            # 环境与录制元信息
├── frames/                  # 截图（webp）
│   ├── event_XXXXXX_{before,after,settled}.webp
│   ├── change_*.webp        # 自适应变更帧（可选）
│   └── changes.jsonl        # 变更帧索引（可选）
├── obj/                     # 预留/辅助目录（多数步骤不依赖）
└── upload_status.json       # 若走云端上传后才会有
```

| 文件/目录 | 是否必有 | 作用 |
|---|---|---|
| `trajectory.json` | 是 | **主结果**：有序步骤 + 默认/已编辑子任务；标注页、模型划分、上传都读它 |
| `events.jsonl` | 是 | 更细粒度的原始输入事件；导出 `trajectory` 时会合并/合成 |
| `metadata.json` | 是 | 分辨率、显示器、起止时间、采集模式等 |
| `frames/` | 是（有截图配置时） | 事件三帧截图等 |
| `obj/` | 常有空目录 | 历史/辅助路径；当前自适应截图主路径在 `frames/` |
| `upload_status.json` | 否 | 上传进度与状态缓存 |

云端会话侧还可能出现：

- `trajectory.annotated.json`：逐步模型标注后  
- `trajectory.reviewed.json`：人工审阅保存后  

字段族与本地 `trajectory.json` 相同。

---

## 2. `metadata.json`（录制环境）

典型字段：

| 字段 | 含义 |
|---|---|
| `system` / `node` / `release` / `version` / `machine` / `processor` | 操作系统与主机信息 |
| `id` | 机器节点标识 |
| `screen_width` / `screen_height` | 主屏分辨率 |
| `monitors[]` | 各显示器：`id`（如 `display-0`）、`x/y/width/height`、`primary` |
| `virtual_desktop` | 虚拟桌面包围盒（多屏时用于归一化坐标） |
| `scroll_direction` | 滚轮方向约定 |
| `start_time` / `stop_time` | 录制起止墙钟时间 |
| `capture_mode` | 截图模式，当前常见 `adaptive_event_frames` |
| `video_capture_backend` / `video_timing` | 连续视频采集相关；默认常关闭 |

---

## 3. `trajectory.json` 顶层结构

```json
{
  "query": "",
  "application": "<主机名>",
  "platform_type": "windows",
  "url": null,
  "sub_tasks": [ /* 子任务段 */ ],
  "sub_task_metadata": { /* 切分状态 */ },
  "trajectory": [ /* 步骤数组，下标从 0 起 */ ]
}
```

| 字段 | 含义 |
|---|---|
| `query` | 整条轨迹总任务描述；录制导出时常为空 |
| `application` | 录制机器标识（多为主机名） |
| `platform_type` | 平台，如 `windows` |
| `url` | 预留；桌面轨迹多为 `null` |
| `trajectory` | **步骤列表**（核心）。子任务的 `start_step`/`end_step` 引用此数组下标 |
| `sub_tasks` | 按意图切分的多段覆盖；刚录完通常为 **1 段**，覆盖全部非 `terminate` 步骤 |
| `sub_task_metadata` | 切分来源、状态、撤销备份等 |

云端逐步标注后还可能出现 `annotation_metadata`（与子任务无关）。

---

## 4. 单步（`trajectory[]`）字段

每一步表示「某一时刻的一次可执行动作 + 截图引用」。

| 字段 | 录制刚结束时 | 含义 |
|---|---|---|
| `action` | 有 | `[{"name": "<动作名>", "parameters": {...}}]`，通常一项 |
| `elapsed_time` | 有 | 相对本段录制起点的秒数 |
| `width` / `height` | 有 | 该步坐标系逻辑分辨率（与归一化坐标配套） |
| `observation` | 常有 | 主观察截图相对路径（多为 `frames/..._before.webp`） |
| `objects` | 常有 | 辅助路径；实现上常与 before 相同 |
| `frames` | 视动作 | `{before, after, settled}` 三帧路径 |
| `step_instruction` | 多为 `""` | 本步操作说明（人工/云端逐步标注后填写） |
| `rationale` | 多为 `null` | 本步意图/原因 |
| `screenshot_caption` | 多为 `null` | 操作结果/界面变化描述 |
| `reference_idx` | 多为 `null` | 预留引用 |
| `annotation` | 录制时通常无 | 逐步完整标注对象（云端/审阅后） |

坐标约定：

- `x` / `y`（及拖拽中的 `start_x` 等）：相对 **虚拟桌面** 的 **0～1** 归一化坐标  
- `screen_x` / `screen_y`：像素坐标  
- `monitor_id`：所属显示器（如 `display-0`）

末尾通常有一步 `terminate`，表示录制结束；**不计入**子任务覆盖范围。

---

## 5. 支持记录的动作（`action[].name`）及详细参数

以下为 **导出到 `trajectory.json` 的动作名**（标注页侧栏中文名见括号）。底层 `events.jsonl` 可能使用不同的原始名（如 `left_click`），见第 7 节。

### 5.0 坐标与显示器（多数鼠标动作共用）

归一化以 `metadata.json` 的 **`virtual_desktop`** 为参考矩形（多屏时是包围所有显示器的大矩形；单屏时通常就是该屏）：

```text
x = clamp( (screen_x - virtual_desktop.x) / virtual_desktop.width , 0, 1 )
y = clamp( (screen_y - virtual_desktop.y) / virtual_desktop.height, 0, 1 )
```

步骤上的 `width` / `height` 即该虚拟桌面宽高（样例中为 1920×1080）。

| 字段 | 类型 | 含义 |
|---|---|---|
| `x` / `y` | `number`（0～1） | 光标相对虚拟桌面的归一化位置。`(0,0)` 为桌面左上，`(1,1)` 为右下。标注页侧栏展示的就是这两维。 |
| `screen_x` / `screen_y` | `number`（像素） | 同一位置的屏幕像素坐标，与系统/采集器给出的绝对坐标一致（未再按单屏裁剪）。 |
| `monitor_id` | `string` | 该像素落在哪块屏，对应 `metadata.monitors[].id`，如 `"display-0"`。点在屏外或无法匹配时可能为 `null`。 |

点击类动作记录的是 **按下/单击发生时** 的位置；滚动记录的是 **该组合并段最后一次滚轮事件** 的位置；悬停记录的是 **静止锚点**（开始 dwell 时的位置，允许约 8px 抖动）。

### 5.1 鼠标

| 动作名 | 中文 | `parameters` | 备注 |
|---|---|---|---|
| `click` | 左键单击 | `x, y, screen_x, screen_y, monitor_id` | 含义见 §5.0 |
| `rightClick` | 右键单击 | 同上 | |
| `doubleClick` | 左键双击 | 同上 | 约 0.35s 内同键两次抬起合成 |
| `doubleRightClick` | 右键双击 | 同上 | |
| `dragTo` | 拖拽 | 见下表 | 由 `drag_start` / `drag_move` / `drag_end` 合成 |
| `scroll` | 滚轮滚动 | 见下表 | 约 0.25s 内连续滚轮合并为一步 |
| `hover` | 鼠标悬停 | 见下表 | 静止满阈值（默认 **5s**）才记 |

**`dragTo` 参数**

| 字段 | 类型 | 含义 |
|---|---|---|
| `start_x` / `start_y` | 0～1 | **按下并开始拖**时的归一化坐标 |
| `end_x` / `end_y` | 0～1 | **松开**时的归一化坐标 |
| `start_monitor_id` / `end_monitor_id` | `string` | 起点、终点所在显示器 |
| `button` | `string` | 拖拽用的鼠标键，常见 `"left"`；右键拖则为 `"right"` |
| `duration` | `number`（秒） | 从 `drag_start` 到 `drag_end` 的时长（保留 3 位小数）。**不是**悬停的 duration。 |
| `path` | `{x, y}[]` | 拖拽过程中的归一化折线：起点 + 途中采样点（约每 50ms 一点）+ 终点。用于回放轨迹，不是像素折线。 |

若录制中途只有 `drag_start` 没有 `drag_end`，仍会写出一步 `dragTo`，此时终点等于起点、`duration` 为 `0`。

**`scroll` 参数**（位置字段同 §5.0，另加）

| 字段 | 类型 | 含义 |
|---|---|---|
| `clicks` | `number` | 本合并段内纵向滚轮增量 **`dy` 的累加**。Windows 上常见每次滚格为 ±1；**负值多为向下**，正值多为向上。横向滚轮 `dx` **不写入** trajectory。 |

**`hover` 参数**（位置字段同 §5.0，另加）

| 字段 | 类型 | 含义 |
|---|---|---|
| `duration` | `number`（秒） | **实际停留时长**：从光标停下开始，到移开/点击/滚动/按键/停止录制为止。阈值（默认 5s，环境变量 `DUCKTRACK_HOVER_DWELL_S`）只决定「要不要记」，不把 duration 写成固定 5。抖动容差默认约 8 像素（`DUCKTRACK_HOVER_JITTER_PX`）。 |

点击类 JSON 样例：

```json
{
  "name": "click",
  "parameters": {
    "x": 0.8151,
    "y": 0.1083,
    "screen_x": 1565,
    "screen_y": 117,
    "monitor_id": "display-0"
  }
}
```

### 5.2 键盘

| 动作名 | 中文 | `parameters` | 备注 |
|---|---|---|---|
| `type` | 文字输入 | `text` | 连续可打印字符合并成一段；**仅 Shift+字母仍记为 type** |
| `hotkey` | 快捷键 | `keys` | Ctrl/Alt/Win（可加 Shift）+ 主键 |
| `keyDown` | 按键按下 | `key` | 特殊键或未合成热键的修饰键 |
| `keyUp` | 按键释放 | `key` | 与 `keyDown` 成对 |

| 字段 | 出现在 | 类型 | 含义 |
|---|---|---|---|
| `text` | `type` | `string` | 本段合并后的输入内容，按键序拼接，如 `"zzzzzz"`、`"first"`。不含 Enter/Backspace（那些走 `keyDown`/`keyUp`）。 |
| `keys` | `hotkey` | `string[]` | 组合键列表。修饰键顺序固定为 `ctrl` → `alt` → `shift` → `win`，最后是主键（小写字母或符号）。例：`["ctrl","a"]`、`["ctrl","alt","a"]`。左右 Ctrl 都归一成 `"ctrl"`。 |
| `key` | `keyDown` / `keyUp` | `string` | 单个按键名，来自系统键名（如 `"enter"`、`"backspace"`、`"esc"`）。未并入热键的修饰键也可能是 `"ctrl_l"` 这类原始名。 |

### 5.3 录制控制

| 动作名 | 中文 | `parameters` | 备注 |
|---|---|---|---|
| `pause` | 录制暂停 | `{}`（无字段） | UI 点暂停时写入；暂停期间不采新操作 |
| `resume` | 录制恢复 | `{}`（无字段） | |
| `terminate` | 结束 | `status` | 停止录制后自动追加，通常为最后一步 |

| 字段 | 类型 | 含义 |
|---|---|---|
| `status` | `string` | 当前导出走正常结束，固定为 `"success"`。不是业务上「任务是否做对」。 |

### 5.4 不会自动写入轨迹的内容

| 名称 | 说明 |
|---|---|
| 普通鼠标移动 | 界面文案有「移动到」，但**不**把路过轨迹记成步骤 |
| 未达阈值的悬停 | 静止不足默认约 5 秒不记 `hover` |
| `wait` | 代码中有占位逻辑，**当前导出关闭**，不会插入等待步 |
| 浮窗录中切分边界 | 主流程不弹浮窗；`sub_task_boundary` 默认不会出现 |

---

## 6. 截图如何挂到步骤上

当前默认采集模式：`adaptive_event_frames`（自适应事件帧）。

- **会新截一组三帧**的典型动作：单击/右击/双击、拖拽起止、滚动、长悬停、暂停/恢复等。  
- 文件名形如：`frames/event_XXXXXX_{before,after,settled}.webp`  
  - `before`：操作时（或操作前）  
  - `after`：约 +250ms  
  - `settled`：约 +1s 相对稳定态  
- **常复用上一张图、不新截图**：连续 `type`、以及导出后的 `hotkey`、多数单独 `keyDown`/`keyUp`。因此快速打字时，侧栏多步可能看到同一张界面。

步骤里：

- `observation` / `objects` 通常指向 `before`  
- `frames` 给出完整三帧路径（若该事件拍过图）

---

## 7. `events.jsonl` 与 `trajectory` 的关系

`events.jsonl` 是原始流（每行一个事件），常见原始 `action`：

| 原始事件 | 导出到 trajectory |
|---|---|
| `left_click` / `right_click` | `click` / `rightClick` |
| `double_left_click` / `double_right_click` | `doubleClick` / `doubleRightClick` |
| `scroll`（可多条） | 合并为一条 `scroll` |
| `drag_start` + `drag_move*` + `drag_end` | 一条 `dragTo` |
| `hover` | `hover` |
| `key_input` | 并入 `type`，或在按住修饰键时合成 `hotkey` |
| `press` / `release` | `keyDown` / `keyUp`，或被热键/打字合并吃掉 |
| `pause` / `resume` | 同名步骤 |
| （停止录制） | 追加 `terminate` |

标注与下游任务一般以 **`trajectory.json` 为准**；需要更细时序或调试合并逻辑时再查 `events.jsonl`。

---

## 8. 子任务字段（录制导出即带默认值）

### 8.1 `sub_tasks[]`

```json
{
  "id": "st_...",
  "index": 0,
  "instruction": "",
  "start_step": 0,
  "end_step": 37,
  "source": "manual",
  "confidence": null,
  "needs_review": true,
  "model": null,
  "prompt_version": null
}
```

| 字段 | 含义 |
|---|---|
| `id` | 子任务唯一 ID |
| `index` | 段序号，从 0 起 |
| `instruction` | 子任务意图文案；刚录完常为空 |
| `start_step` / `end_step` | 覆盖 `trajectory` 的闭区间下标（**不含** `terminate`） |
| `source` | `manual` / `model` / `hybrid` |
| `confidence` / `needs_review` | 模型置信度与是否需复核 |
| `model` / `prompt_version` | 模型划分时写入 |

### 8.2 `sub_task_metadata`

| 字段 | 含义 |
|---|---|
| `status` | 如 `none`、`manual_complete`、`completed` 等 |
| `splitter` | 谁写的当前切分：`none` / `manual` / `qwen_subtask_v2` / `qwen_subtask_v3` 等 |
| `manual_boundary_count` | 录中/人工边界次数；默认导出常为 `0` |
| `split_at_unix` | 模型划分时间戳 |
| `prompt_version` | 如 `subtask-split-v2` / `subtask-split-v3` |
| `previous_sub_tasks` / `previous_snapshot` | 一键划分前的撤销备份 |

录制刚结束时常见：`status=none`、`splitter=none`、整轨一段空意图 `sub_tasks`。

---

## 9. 样例对照（本机一次录制，已标注）

目录：`output/recording-2026-08-20_17-10-27`  


### 9.1 录制本身（未变）

| 项 | 值 |
|---|---|
| 时长（墙钟） | 约 17:10:27 → 17:11:39 |
| `trajectory` 步数 | 39（下标 0～38；最后一步 `terminate`） |
| 出现的动作 | `click`×12，`type`×3，`hotkey`×2，`keyDown`/`keyUp`×8，`scroll`×2，`dragTo`/`doubleClick`/`rightClick`/`terminate`×1 |
| 未出现但支持 | `hover`、`pause`/`resume`、`doubleRightClick` 等 |
| 逐步自然语言 | 各步 `step_instruction` 仍为空，无 `annotation` 对象（**未做**云端逐步「操作意图/操作结果」标注） |

`hotkey` 样例：`{"keys":["ctrl","a"]}`、`{"keys":["ctrl","s"]}`。  
`dragTo` 样例含 `path` 多点轨迹与 `duration`。  
`scroll` 样例含 `clicks: -3` / `-5`。

### 9.2 标注后的子任务

刚录完时是 **1 段空意图**（`0`～`37`）。当前为 **6 段连续覆盖** `0`～`37`（不含 `terminate` 下标 38）。

| `index` | `start_step`～`end_step` | `source` | `instruction` |
|---|---|---|---|
| 0 | 0～6 | `manual` | 打开记事本 |
| 1 | 7～7 | `model` | 拖拽文件窗口调整位置 |
| 2 | 8～11 | `model` | 在文本编辑器中右键点击并从上下文菜单中选择操作 |
| 3 | 12～32 | `model` | 在文本编辑器中编辑文档内容，包括全选删除、保存文件、输入新文本内容 |
| 4 | 33～36 | `model` | 关闭文本编辑器并处理保存确认对话框 |
| 5 | 37～37 | `model` | 打开DuckTrack应用程序 |

模型段（1～5）共同字段：`model=anthropic/claude-sonnet-4`，`prompt_version=subtask-split-v2`，`confidence` 约 0.87～0.95，`needs_review=false`。  
第 0 段为人工意图（`source=manual`，`model`/`prompt_version` 为空）。

`sub_task_metadata`（标注后）：

| 字段 | 值 |
|---|---|
| `status` | `completed` |
| `splitter` | `qwen_subtask_v2` |
| `prompt_version` | `subtask-split-v2` |
| `split_at_unix` | 有（本次一键划分时刻） |
| `previous_sub_tasks` / `previous_snapshot` | 有：划分前的默认单段空意图，可供「撤销模型划分」 |

目录中新增 `model_responses/`（划分调试，如 `subtask_split.json`）。步数与动作集合相对录制结束时 **未改**。

---

## 10. 下游怎么读这份数据

1. **看步骤序列**：读 `trajectory[]`，按 `elapsed_time` 与 `action[0].name` 理解操作流。  
2. **看意图切分**：读 `sub_tasks[]` 的 `start_step`～`end_step` 与 `instruction`（标注页或模型划分后才有具体文案）。  
3. **看界面证据**：用步骤的 `frames.before/after/settled` 或 `observation`。  
4. **看环境**：读 `metadata.json` 的分辨率与显示器布局。  
5. **调试合并**：对照 `events.jsonl` 原始事件与导出后的合成动作。

相关文档：

- `更新说明-v1.md` §6：原版 vs 子任务字段对照  
- `document/子任务节点功能实现方案.md`：子任务 schema 与校验  
- `更新说明-v3.md`：`hotkey` / `hover`  
- `更新说明-v4.md`：长轨粗切+细切划分（改写 `sub_tasks`，不改变录制动作集合）
