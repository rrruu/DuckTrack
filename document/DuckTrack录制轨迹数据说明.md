# DuckTrack 录制轨迹数据说明

> 日期：2026-09-02（相对 2026-08-20 增补 **v5**；同日补充 UIA 开启/依赖/实录样例）  
> 适用范围：本机标注器录制并停止后落盘的结果目录（如 `output/recording-YYYY-MM-DD_HH-MM-SS/`）  
> 对照样例：  
> - v4 及以前结构参考：`output/recording-2026-08-20_17-10-27`  
> - **v5** 多应用 + 前台上下文（未开 UIA）：`output/recording-2026-09-01_22-31-27`  
> - **v5 + UIA**：`output/recording-2026-09-02_19-42-02`、`output/recording-2026-09-02_19-48-24`  
> 说明：以当前源码为准（含 v3 快捷键/悬停、v4 子任务分层划分、**v5 身份溯源 + 前台应用上下文 + 可选 UIA**）。标注/模型划分会改写部分字段，但**录制刚结束**时的结构如下。  
> 文中凡标注 **（v5新增）** 的字段，在 v4 及更早录制中通常不存在；旧轨迹加载时按缺失处理即可。

---

## 1. 一次录制会产出什么

停止录制后，会在独立目录中写入：

```text
recording-YYYY-MM-DD_HH-MM-SS/
├── trajectory.json          # 主数据：步骤序列 + 子任务覆盖（标注/上传用）
├── events.jsonl             # 原始事件流（一行一个 JSON）
├── provenance.json          # （v5新增）step ↔ source_event 轻量审计映射（默认可关）
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
| `provenance.json` | 否（v5 默认生成） | **（v5新增）** 每步 `step_id` ↔ `source_event_ids` 摘要，便于审计 |
| `metadata.json` | 是 | 分辨率、显示器、起止时间、采集模式等 |
| `frames/` | 是（有截图配置时） | 事件三帧截图等 |
| `obj/` | 常有空目录 | 历史/辅助路径；当前自适应截图主路径在 `frames/` |
| `upload_status.json` | 否 | 上传进度与状态缓存 |

云端会话侧还可能出现：

- `trajectory.annotated.json`：逐步模型标注后  
- `trajectory.reviewed.json`：人工审阅保存后  

字段族与本地 `trajectory.json` 相同（v5 新字段原样保留）。

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
| `model` | 电脑硬件型号（如 WMI/`sysctl` 读到的 Product/Model）；采集失败或未开相关采集时常见 `"Unknown"`（Windows 上完整型号常需 `DUCKTRACK_COLLECT_WMI=1`） |
| `scroll_direction` | 滚轮方向约定 |
| `start_time` / `stop_time` | 录制起止墙钟时间 |
| `capture_mode` | 截图模式，当前常见 `adaptive_event_frames` |
| `video_capture_backend` / `video_timing` | 连续视频采集相关；默认常关闭 |
| `schema_version` | **（v5新增）** 如 `ducktrack-metadata-v5`（开启前台上下文或 UIA 时写入） |
| `recorder_features` | **（v5新增）** 本段录制开启了哪些 v5 能力，见下表 |
| `uia_last_error` | **（v5新增，可选）** 开启了 UIA 但采集失败时的简要错误（如缺少 `comtypes`）；成功录制通常无此字段 |

**`recorder_features`（v5新增）**

| 字段 | 含义 | 默认 |
|---|---|---|
| `event_ids` | `events.jsonl` 是否写入 `event_id` | `true` |
| `step_ids` | `trajectory` 是否写入 `step_id` / `step_seq` / `source_event_ids` 等 | `true` |
| `provenance` | 是否写出 `provenance.json` | `true` |
| `foreground_context` | 是否采集前台窗口 `context` | `true` |
| `context_exe_path` | `context` 是否含完整 `exe_path` | `false` |
| `uia` | 是否对 click 采样 UI Automation（仅表示**开关已开**，不保证每条 click 都有 `context.uia`） | `false` |

对应环境变量见 §11。验证 UIA 是否真正落盘：看 click 步/`events` 是否含 `context.uia`（见 §4.2、§9.3）。

---

## 3. `trajectory.json` 顶层结构

**v5 导出样例结构：**

```json
{
  "schema_version": "ducktrack-trajectory-v5",
  "query": "",
  "host_name": "DESKTOP-CU287P3",
  "primary_application": {
    "process_name": "notepad.exe",
    "window_title": "新建文本文档 (6).txt - 记事本",
    "app_display_name": "...",
    "app_version": "..."
  },
  "application": "notepad.exe",
  "platform_type": "windows",
  "url": null,
  "sub_tasks": [ /* 子任务段 */ ],
  "sub_task_metadata": { /* 切分状态 */ },
  "trajectory": [ /* 步骤数组，下标从 0 起 */ ]
}
```

| 字段 | 含义 |
|---|---|
| `schema_version` | **（v5新增）** 轨迹 schema，当前为 `ducktrack-trajectory-v5` |
| `query` | 整条轨迹总任务描述；录制导出时常为空 |
| `host_name` | **（v5新增）** 录制机器主机名（原 v4 `application` 的真实语义迁到此字段） |
| `primary_application` | **（v5新增）** 整轨「主应用」摘要（按带 `context` 的可操作步中，`process_name` 出现次数最多者）；常见子字段：`process_name`、`window_title`、`app_display_name`、`app_version` |
| `application` | **语义已变（v5）**：现写入主应用 `process_name`（兼容旧读者）；**v4 及以前**此处多为主机名。新代码请优先读 `primary_application` / `host_name` |
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
| `step_id` | 有（默认） | **（v5新增）** 稳定步骤 ID，如 `stp_<hex>`；删步/重排后不回收 |
| `step_seq` | 有（默认） | **（v5新增）** 导出时轨迹内从 0 递增的可读序号（与 `trajectory[]` 下标、`sub_tasks.start_step`/`end_step` 一致）；对应 event 侧的 `event_seq` |
| `source_event_ids` | 有（默认） | **（v5新增）** 合成该步的全部 `event_id`（按时间序）；1:1 时长度为 1 |
| `source_event_seq` | 常有 | **（v5新增）** 与 `source_event_ids` 对应的 `event_seq`，便于人读 |
| `merge_rule` | 有（默认） | **（v5新增）** 合并规则名，见 §7.3 |
| `context` | 常有 | **（v5新增）** 前台应用/窗口上下文（及可选 UIA），见 §4.1 |

坐标约定：

- `x` / `y`（及拖拽中的 `start_x` 等）：相对 **虚拟桌面** 的 **0～1** 归一化坐标  
- `screen_x` / `screen_y`：像素坐标  
- `monitor_id`：所属显示器（如 `display-0`）

末尾通常有一步 `terminate`，表示录制结束；**不计入**子任务覆盖范围。`terminate` 的 `merge_rule` 为 `terminate_append`，`source_event_ids` 多为 `[]`。

### 4.1 `context` 对象（v5新增）

挂在 **events.jsonl 行** 与 **trajectory step** 上。导出合并步时，一般取源事件中**最后一个非空** `context`（如 `drag_end`、末个 `key_input`）。

| 字段 | 含义 |
|---|---|
| `process_name` | 前台进程名，如 `notepad.exe`、`QQMusic.exe` |
| `process_id` | Windows PID |
| `exe_path` | 可执行文件完整路径（默认不写；`DUCKTRACK_CONTEXT_EXE_PATH=1` 开启） |
| `app_display_name` | 文件版本 ProductName（若可读） |
| `app_version` | 文件版本号（若可读） |
| `window_title` | 前台窗口标题 |
| `window_handle` | HWND 十六进制字符串 |
| `window_rect` | 窗口屏幕矩形 `{left, top, right, bottom}` |
| `client_rect` | 客户区矩形（屏幕坐标） |
| `monitor_id` | 窗口中心所在显示器 id |
| `dpi_scale` | 该窗口有效 DPI 缩放，如 `1.0`、`1.25` |
| `uia` | **（v5新增，可选）** UI Automation 控件快照；仅当 `DUCKTRACK_UIA=1` 且为 click 类事件时写入，见 §4.2 |

**采集说明：** `drag_move` 不重复采 `context`（由 `drag_end` 携带）。个别事件可能采不到前台窗口，则该行/该步无 `context`。

样例（记事本操作步）：

```json
"context": {
  "process_name": "notepad.exe",
  "process_id": 29348,
  "app_display_name": "Microsoft® Windows® Operating System",
  "app_version": "10.0.19041.4522 (WinBuild.160101.0800)",
  "window_title": "新建文本文档 (6).txt - 记事本",
  "window_handle": "0x400fae",
  "window_rect": { "left": 314, "top": 123, "right": 1455, "bottom": 826 },
  "client_rect": { "left": 323, "top": 186, "right": 1446, "bottom": 817 },
  "monitor_id": "display-0",
  "dpi_scale": 1.25
}
```

### 4.2 `context.uia`（v5新增，默认关闭）

开启 `DUCKTRACK_UIA=1` 后，对 `left_click` / `right_click` / 双击在点击坐标做 `ElementFromPoint`，写入：

| 字段 | 含义 |
|---|---|
| `control_type` | 控件类型名，如 `Button`、`Edit`、`ListItem`、`Document`、`Pane` |
| `automation_id` | AutomationId（**可空**；很多桌面程序不提供） |
| `name` | 控件 Name（**可空**） |
| `bounding_rectangle` | 控件屏幕矩形 `{left, top, right, bottom}` |

样例（Chrome 网页按钮）：

```json
"uia": {
  "control_type": "Button",
  "name": "VENT",
  "automation_id": "btn-vent",
  "bounding_rectangle": { "left": 322, "top": 259, "right": 425, "bottom": 293 }
}
```

**如何开启**

1. 在 `start_annotator.bat` 中增加（或确认已有）：`set "DUCKTRACK_UIA=1"`  
2. **完全退出**后再启动标注器（后端进程需重新继承环境变量）  
3. 依赖：标注器使用的 `.venv` 需安装 **`comtypes`**（已列入 `requirements-unified.txt`；缺包时常见现象是 `recorder_features.uia=true` 但没有任何 `context.uia`）  
4. 验收：`metadata.json` 中 `recorder_features.uia=true`，且 click 的 event/step 出现 `context.uia`；若失败可查看可选字段 `uia_last_error`

**采样范围与局限**

- **只采 click 类**（左/右/双击）；`type`、`scroll`、`drag_move`、`press`/`release` 等不写 `uia`  
- 导出时 `trajectory` 的 click 步从源事件继承 `context.uia`  
- 部分点击只能落到粗控件（如整页 `Document` / `RootWebArea`、大块 `Pane`），`name` 可能为空——取决于目标应用是否暴露细粒度 Automation 树  
- 默认关闭的原因：性能、体积、隐私与部分应用 UIA 支持差。窗口级 `context`（v5-B）默认开启即可识别多应用切换  

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

### 7.1 原始事件 → 导出步骤

`events.jsonl` 是原始流（每行一个事件）。**event** = 钩子抓到的细粒度输入；**trajectory step** = 停止录制后合并/合成的标准动作。

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

标注与下游任务一般以 **`trajectory.json` 为准**；需要更细时序或调试合并逻辑时再查 `events.jsonl`。v5 可通过 step 上的 `source_event_ids` 精确反查。

### 7.2 `events.jsonl` 行字段

每一行是一个 JSON 对象。字段分两类：**公共字段**（多数行都有）与 **随 `action` 变化的专用字段**。并非每一行都含全部字段。

#### 7.2.1 公共字段

| 字段 | 是否常见 | 含义 |
|---|---|---|
| `time_stamp` | 几乎总有 | `time.perf_counter()` 高性能时钟（秒，浮点）；**不是**墙钟日期时间 |
| `action` | 总有 | 原始动作名，见 §7.1 |
| `event_id` | 默认有 | **（v5新增）** 稳定 ID，如 `evt_<hex>`（`recorder_drop_stats` 等特殊行可能无） |
| `event_seq` | 默认有 | **（v5新增）** 录制内从 0 递增的可读序号 |
| `observation` | 常见 | 主观察截图相对路径（多为 `frames/event_XXXXXX_before.webp`；键盘类常复用上一张） |
| `objects` | 常见 | 辅助截图路径；实现上常与 `observation` 相同 |
| `frames` | 视动作 | 新截三帧时有：`{before, after, settled}`；键盘类 / `drag_move` 等常无新三帧 |
| `context` | 常见 | **（v5新增）** 前台窗口上下文，结构见 §4.1；`drag_move` 通常不采；个别行可能采不到 |

#### 7.2.2 随 `action` 变化的字段

| `action` | 专用字段 | 说明 |
|---|---|---|
| `left_click` / `right_click` | `x`, `y`, `button` | 抬起时像素坐标；`button` 为 `"left"` / `"right"` 等。开 UIA 时 `context.uia` 可能有值 |
| `double_left_click` / `double_right_click` | `x`, `y`, `button` | 同上；由短时两次抬起合成的原始双击事件 |
| `scroll` | `x`, `y`, `dx`, `dy` | 光标位置；滚轮增量（纵向多为 `dy`，横向 `dx`） |
| `drag_start` | `button`, `start_x`, `start_y`, `x`, `y` | 按下并开始拖时的起点；`x`/`y` 为当前点（可与起点相同或略移） |
| `drag_move` | `button`, `x`, `y` | 拖拽途中采样点；**通常无** `context`、**通常无**新 `frames` |
| `drag_end` | `button`, `x`, `y` | 松开位置 |
| `key_input` | `text` | 可打印字符（长度多为 1）；无坐标 |
| `press` / `release` | `name` | 键名，如 `"enter"`、`"ctrl_l"`、`"backspace"`；可打印字符一般不单独写 press/release |
| `hover` | `x`, `y`, `duration` | 长停留结束时写出；`duration` 为实际停留秒数；截图可能已预置 |
| `pause` / `resume` | （无坐标类字段） | 仅公共字段 + 可能的截图/`context` |
| `sub_task_boundary` | `sub_task_id`, `sub_task_index`, `instruction` | 录中切分子任务边界；主流程默认不产生 |
| `recorder_drop_stats` | `dropped_events` | 队列丢弃统计；常写在文件末尾，**通常无** `event_id`/`observation` |

说明：

- `x` / `y`（及 `start_x` / `start_y`）在 **events** 里是**屏幕像素**，不是 trajectory 里的 0～1 归一化坐标。  
- `button` 只出现在鼠标相关事件上。  
- `text` 只出现在 `key_input`；`name` 只出现在 `press`/`release`（不要与 `context` 里窗口/控件的 name 混淆）。  
- `dx` / `dy` 只出现在 `scroll`。  
- 开 UIA 后，click 类行的 `context` 内可能多 `uia` 子对象（§4.2）；这不是顶层独立字段。

#### 7.2.3 行结构示意

单击（含 v5 字段）示意：

```json
{
  "time_stamp": 180500.12,
  "action": "left_click",
  "x": 1045,
  "y": 866,
  "button": "left",
  "event_id": "evt_...",
  "event_seq": 7,
  "observation": "frames/event_000007_before.webp",
  "objects": "frames/event_000007_before.webp",
  "frames": {
    "before": "frames/event_000007_before.webp",
    "after": "frames/event_000007_after.webp",
    "settled": "frames/event_000007_settled.webp"
  },
  "context": {
    "process_name": "explorer.exe",
    "window_title": "Program Manager",
    "dpi_scale": 1.25,
    "uia": { "control_type": "ListItem", "name": "新建文本文档 (6).txt", "bounding_rectangle": {} }
  }
}
```

打字单键示意（常无新 `frames`，复用上一张图）：

```json
{
  "time_stamp": 180510.01,
  "action": "key_input",
  "text": "a",
  "event_id": "evt_...",
  "event_seq": 10,
  "observation": "frames/event_000009_before.webp",
  "objects": "frames/event_000009_before.webp",
  "context": { "process_name": "notepad.exe", "window_title": "..." }
}
```

### 7.3 `merge_rule`（v5新增）

| 值 | 含义 |
|---|---|
| `direct` | 无合并，原始事件 1:1 映射 |
| `type_buffer` | 连续 `key_input` → `type` |
| `scroll_group` | 短时连续 `scroll` 合并（单条 scroll 也可能记为 `direct`） |
| `drag_compose` | `drag_start` + `drag_move*` + `drag_end` → `dragTo` |
| `hotkey_chord` | 修饰键 + 主键 → `hotkey` |
| `hover_finalize` | 悬停结束写出 `hover` |
| `terminate_append` | 停止录制追加 `terminate` |

完整合并前后 JSON 样例见：`document/v5-轨迹字段样例.json`。

### 7.4 `provenance.json`（v5新增）

默认生成，结构示例：

```json
{
  "schema_version": "provenance-v1",
  "recording_id": "recording-2026-09-01_22-31-27",
  "steps": [
    {
      "step_id": "stp_...",
      "step_seq": 0,
      "step_index": 0,
      "action_name": "click",
      "merge_rule": "direct",
      "source_event_ids": ["evt_..."]
    }
  ]
}
```

与 `trajectory[]` 中对应字段应一致（`step_seq` 与数组下标相同；`step_index` 为兼容别名，取值相同）。可用 `DUCKTRACK_PROVENANCE_JSON=0` 关闭。

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
子任务仍用 **数组下标**（与各步 **`step_seq`** 相同）引用步骤；v5 的 `step_id` 用于审计/溯源，不替代 `start_step`/`end_step`。

---

## 9. 样例对照

### 9.1 v4 样例（已标注子任务）

目录：`output/recording-2026-08-20_17-10-27`

| 项 | 值 |
|---|---|
| 时长（墙钟） | 约 17:10:27 → 17:11:39 |
| `trajectory` 步数 | 39（下标 0～38；最后一步 `terminate`） |
| 出现的动作 | `click`×12，`type`×3，`hotkey`×2，`keyDown`/`keyUp`×8，`scroll`×2，`dragTo`/`doubleClick`/`rightClick`/`terminate`×1 |
| 未出现但支持 | `hover`、`pause`/`resume`、`doubleRightClick` 等 |
| 逐步自然语言 | 各步 `step_instruction` 仍为空，无 `annotation` 对象（**未做**云端逐步「操作意图/操作结果」标注） |
| v5 字段 | 本目录为 v4 期录制，**无** `step_id` / `context` / `primary_application` 等 |

`hotkey` 样例：`{"keys":["ctrl","a"]}`、`{"keys":["ctrl","s"]}`。  
`dragTo` 样例含 `path` 多点轨迹与 `duration`。  
`scroll` 样例含 `clicks: -3` / `-5`。

标注后子任务为 **6 段** 连续覆盖 `0`～`37`（不含 `terminate`）：

| `index` | `start_step`～`end_step` | `source` | `instruction` |
|---|---|---|---|
| 0 | 0～6 | `manual` | 打开记事本 |
| 1 | 7～7 | `model` | 拖拽文件窗口调整位置 |
| 2 | 8～11 | `model` | 在文本编辑器中右键点击并从上下文菜单中选择操作 |
| 3 | 12～32 | `model` | 在文本编辑器中编辑文档内容，包括全选删除、保存文件、输入新文本内容 |
| 4 | 33～36 | `model` | 关闭文本编辑器并处理保存确认对话框 |
| 5 | 37～37 | `model` | 打开DuckTrack应用程序 |

模型段：`model=anthropic/claude-sonnet-4`，`prompt_version=subtask-split-v2`。  
`sub_task_metadata`：`status=completed`，`splitter=qwen_subtask_v2`。

### 9.2 v5 样例（多应用 + 前台上下文）

目录：`output/recording-2026-09-01_22-31-27`

| 项 | 值 |
|---|---|
| 墙钟 | 约 22:31:27 → 22:32:40 |
| `events.jsonl` | 约 144 行；多数含 `event_id` / `event_seq` / `context` |
| `trajectory` 步数 | 99（含末尾 `terminate`） |
| `schema_version` | `ducktrack-trajectory-v5` |
| `host_name` | `DESKTOP-CU287P3` |
| `primary_application.process_name` | `notepad.exe`（记事本步数最多） |
| `application` | `notepad.exe`（兼容字段，非主机名） |
| `recorder_features.uia` | `false`（本段未开 UIA，故无 `context.uia`） |
| `provenance.json` | 有，与各步 `source_event_ids` 一致 |

按 `context.process_name` 的主线切换（step 下标）：

| 应用 | step 区间 |
|---|---|
| QQ音乐 (`QQMusic.exe`) | 2 |
| 标注器 (`ducktrack-annotator.exe`) | 3 |
| 桌面 (`explorer.exe`) | 4～9 |
| 记事本 (`notepad.exe`) | 10～80（主段） |
| 有道 (`YoudaoDict.exe`) | 83～91 |

其后还有少量回切桌面/标注器及无 `context` 的步；完整切换表可按逐步 `context.process_name` 连续分段得到。

### 9.3 v5 样例（开启 UIA）

目录（任选对照）：

- `output/recording-2026-09-02_19-42-02`：短录；**24/24** click 均有 `context.uia`  
- `output/recording-2026-09-02_19-48-24`：多应用（桌面 / Chem3D / Chrome SEM 等）；约 **43/45** click 有 `uia`

| 项 | 典型值（以 19-48-24 为例） |
|---|---|
| `recorder_features.uia` | `true` |
| `uia_last_error` | 无（采集正常） |
| click 覆盖 | 绝大多数 click event/step 含 `context.uia`；非 click 动作无 `uia` |
| 常见 `control_type` | `Button`、`ListItem`、`Edit`、`Menu`、`Document`、`Pane`、`Image` 等 |

可读性较好的例子：

| 场景 | `uia` 摘要 |
|---|---|
| 桌面点文件图标 | `ListItem`，`name`≈`新建文本文档 (6).txt` |
| Chem3D 工具栏 | `Button`，`name`=`Select`；右键 `Menu`/`PopupMenu` |
| Chem3D 视口 | `Pane`，`name`=`glWindow` |
| Chrome 功能按钮 | `Button`，`name`=`VENT`，`automation_id`=`btn-vent`（另有 OPEN/CLOSE/EVACUATE 等） |
| 网页空白/画布 | 常退化为 `Document`（如 `RootWebArea`）或大块 `Pane`/`Image` |

说明：§9.2 目录录制时 **未** 开 UIA；不要与本节混淆。若 `uia=true` 却完全没有 `context.uia`，优先检查 `.venv` 是否已安装 `comtypes`，并确认已重启标注器。

---

## 10. 下游怎么读这份数据

1. **看步骤序列**：读 `trajectory[]`，按 `elapsed_time` 与 `action[0].name` 理解操作流。  
2. **看意图切分**：读 `sub_tasks[]` 的 `start_step`～`end_step` 与 `instruction`（标注页或模型划分后才有具体文案）。  
3. **看界面证据**：用步骤的 `frames.before/after/settled` 或 `observation`。  
4. **看环境**：读 `metadata.json` 的分辨率与显示器布局；v5 可看 `recorder_features`。  
5. **看前台应用（v5）**：整轨看 `primary_application` / `host_name`；逐步看 `context.process_name`、`window_title`、`dpi_scale` 等。  
6. **调试合并 / 溯源（v5）**：用 `step_seq` 定位第几步；用 `source_event_ids` / `source_event_seq` 或 `provenance.json` 回查 `events.jsonl`。  
7. **控件级（v5 可选）**：仅当开启 UIA 且该步为 click 时读 `context.uia`；不要仅凭 `recorder_features.uia=true` 假定每步都有控件信息。

---

## 11. v5 相关环境变量（摘要）

| 变量 | 默认 | 说明 |
|---|---|---|
| `DUCKTRACK_EVENT_IDS` | `1` | **（v5新增）** events 写 `event_id` |
| `DUCKTRACK_STEP_IDS` | `1` | **（v5新增）** trajectory 写 `step_id` / `step_seq` / 溯源字段 |
| `DUCKTRACK_PROVENANCE_JSON` | `1` | **（v5新增）** 写出 `provenance.json` |
| `DUCKTRACK_FOREGROUND_CONTEXT` | `1` | **（v5新增）** 采集前台窗口 `context` |
| `DUCKTRACK_CONTEXT_EXE_PATH` | `0` | **（v5新增）** 是否写入完整 `exe_path` |
| `DUCKTRACK_UIA` | `0` | **（v5新增）** click 处采 UI Automation；改后需**重启**标注器；Windows 需 `comtypes` |

推荐在 `start_annotator.bat` 中设置，例如：

```bat
set "DUCKTRACK_UIA=1"
```

---

## 12. 相关文档

- `更新说明-v1.md` §6：原版 vs 子任务字段对照  
- `document/子任务节点功能实现方案.md`：子任务 schema 与校验  
- `更新说明-v3.md`：`hotkey` / `hover`  
- `更新说明-v4.md`：长轨粗切+细切划分（改写 `sub_tasks`，不改变录制动作集合）  
- `document/功能-v5-轨迹身份与应用上下文方案.md`：v5-A/B/C 方案与验收  
- `document/v5-轨迹字段样例.json`：合并前后字段 JSON 样例  
