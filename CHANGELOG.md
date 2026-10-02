# Changelog

All notable changes to this project will be documented in this file.

This project adheres to [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/) and [Semantic Versioning](https://semver.org/).

## [Unreleased]

A refactor of the core module (no more global mutable state), a shared report builder so the CLI and the GUI emit identical output, and three correctness fixes in elliptical-arc handling. The GUI was rebuilt around a path preview, a path list, menus and shortcuts, with a fully thread-safe background computation pipeline. The latest iteration adds a bilingual (Chinese/English) interface — including the generated report — and multi-path selection.

**Compatibility notes:**

- Piecewise-mode output is **byte-identical** to the previous version (verified against the `git` original on `sun.svg` and `moon.svg`); Chinese (default) report text is unchanged as well.
- Report headings are localised (`lang="zh"` / `lang="en"`), and aggregated Fourier labels were normalised to `路径 0` (a space was added: `路径0` → `路径 0`).
- Fourier-mode output is now more compact: terms that round to zero and illegal doubled signs were dropped; every non-zero coefficient is unchanged.
- Output for elliptical arcs with a rotation angle differs from the previous release — the previous output was incorrect.
- Former public functions (`process_paths`, `segment_to_expression`, `fourier_fit_path`, `fourier_fit_multiple_segments`, `output_fourier_result_single`, …) and the `PRECISION` global remain available for backward compatibility; all new parameters are optional keywords with the old defaults.

### Added

**Core / CLI**

- `build_segment_report()` and `build_fourier_report()`, shared report builders used by both entry points; identical settings now produce identical output from the CLI and the GUI.
- CLI argument validation — `--precision` (0–10), `--samples` (≥ 2), `--fourier` (≥ 1), `--path-index` (≥ 0) — reported as standard argparse errors, plus explicit exit codes returned by `main()`.
- `lang="zh"|"en"` keyword threaded through the report builders (`segment_to_expression()`, `process_paths()`, `build_segment_report()`, `collect_continuous_segments()`, `format_fourier_report()`, `build_fourier_report()`, the fit warnings and error messages). The GUI passes its interface language; the CLI gained `--lang {zh,en}` (default `zh`, so existing output is unchanged).
- `path_indices=` keyword on `build_segment_report()` / `build_fourier_report()`: process a subset of `paths` while keeping each path's original index in the report (`ValueError` on out-of-range indexes). The CLI accepts several at once — `--path-index 0 2`.
- `i18n.py`: GUI string table plus language detection (`SVG2DESMOS_LANG` → `LANG`/`LC_ALL` → `locale.getlocale()` → preferred encoding, with `cp936`/`gbk`/`gb18030`/`big5` mapped to Chinese) and persistence (`~/.svg2desmos_gui.json`, relocatable via `SVG2DESMOS_CONFIG`); every read/write failure is tolerated.
- `test_core_math.py`: re-evaluates the generated parametric equations and compares them against svgpathtools' own `path.point(t)` for every segment type (line, quadratic/cubic Bézier, rotated elliptical arc), verifies that Y‑flipping is an exact mirror image, checks Fourier series reconstruction, and exercises the error paths. Runs without a display. Now also covers bilingual reports, `path_indices` subsets and the CLI language/multi-index options (41 checks).
- `test_gui_smoke.py`: builds the window without showing it and drives the real widgets — language switching, multi-path selection and the selection counter/buttons, path list, preview, background generation in both modes, clipboard, empty-selection warnings (53 checks). Falls back to a generated SVG fixture when `sprite.svg` is absent.

**GUI**

- *View → Language* menu (中文 / English) that switches menus, labels, list headings, status bar, dialogs **and the generated report** instantly; the preference is remembered between runs and the first launch follows the system language.
- Multi-path selection: the path list is `selectmode="extended"` (Ctrl/Shift-click), with **Select all** / **Clear** buttons and a `selected/total` counter; loading a file selects every path, generation converts exactly the selected paths (report keeps the original indexes), an empty selection prompts instead of producing an empty result, and the preview highlights all selected paths in blue.
- Path preview canvas: draws every path, highlights the selected path in blue, re-renders instantly when the Y‑flip option changes, shows dashed coordinate axes, and debounces redraws while resizing.
- Path list (Treeview) showing each path's segment count and open/closed state; clicking a row selects the target path and stays in sync with the target path index field.
- Menu bar (File / Edit / View / Help) and keyboard shortcuts: `Ctrl+O` open, `F5` generate, `Ctrl+S` save, `Ctrl+Shift+C` copy.
- Progress feedback: the generate button is disabled while computing, an indeterminate progress bar runs, and the status bar reports elapsed time and output length; the output area shows a character count and a word-wrap toggle.

### Changed

- The global mutable `PRECISION` was replaced by explicit `precision` parameters passed through every generator; the global is kept only as a default for backward compatibility. Worker threads can now run concurrently without racing shared state.
- Segment expression generation was unified behind a single `_segment_expressions(seg, precision)` entry point and one report-block builder, replacing three duplicated implementations.
- Arc center parameterization was extracted into `_arc_center_parameters()` with precomputed trigonometry; the unused helper function and redundant calculations were removed.
- Fourier coefficient computation is vectorized (rfft slicing instead of a per-harmonic Python loop) and samples are built with `np.fromiter`.
- Fourier result selection, fitting and rendering were split into `_select_fourier_segments()` / `_fit_segments()` / `format_fourier_report()`, giving single-segment and aggregated modes clearly separated failure handling.
- The GUI generation flow is now *parameter snapshot → worker thread → queue → main-thread polling*, so no Tk object is touched off the main thread (the previous implementation read `self.precision.get()` from a worker thread).
- The GUI reuses the shared report builders instead of assembling its own Fourier output, deleting roughly 70 lines of duplicated formatting logic.
- Control-state interlocks: Fourier options disable themselves while piecewise mode is active, the target path index disables when “fit all paths” is checked, and invalid numeric input falls back to defaults and is clamped to range.
- Missing-dependency dialogs are deferred until after the window is created (they were previously raised at import time, before any Tk root existed).
- Layout rebuilt with `PanedWindow`, plus a status-bar progress indicator, a window title that follows the loaded filename, a raised minimum size, and a case-insensitive copy shortcut.
- Generation scope: piecewise mode used to convert every path and Fourier mode a single index; both now convert **whatever is selected** in the path list (original indexes are kept in the report), while *fit all paths* still ignores the selection. The target index field now narrows the tree selection to exactly one row instead of merely tracking it.
- `README.md`: the GUI sections were rewritten in both languages and a testing section was added; the new bilingual/multi-select behaviour and CLI options are documented in both language sections.

### Fixed

- The result-polling `after` chain could die silently: an exception while rendering a result skipped the reschedule, leaving the window stuck on “computing” (spinning progress bar, generate button disabled) and every later `F5` ignored with no error shown. The loop now reschedules in `finally`, and errors are reported without wedging the pipeline.
- `Arc(...)` was constructed with positional arguments ordered as `(start, end, radius, rotation, large_arc, sweep)`, while svgpathtools' signature is `(start, radius, rotation, large_arc, sweep, end)`. **Any SVG containing an arc crashed with `TypeError` when Y‑flip was enabled.** The arc is now built from keyword arguments, which also stays correct across svgpathtools versions with different parameter orders.
- `Arc.rotation` is expressed in **degrees** but was passed straight into `math.cos` / `math.sin` as if it were radians, so every arc with a rotation angle produced equations that did not describe the curve (measured deviation 4.16 where an exact match is 0). Rotation angle 0 was unaffected. The angle is now converted with `math.radians()` for both the center parameterization and the trigonometric terms.
- Y‑flipping now negates the arc's rotation angle (φ → −φ), as required by the mirror identity `R(-φ)·M = M·R(φ)`; previously only the endpoints and the sweep flag were flipped, so rotated arcs mirrored into the wrong shape. The flipped geometry now deviates from the exact reflection by 0.0.
- Fourier series formatting emitted illegal or redundant notation — `+ + 0 * sin(2 t)` double signs, a doubled space after minus signs, and terms that round to zero such as `0 * cos(2 t)`. Signs are now applied explicitly and terms that render as zero are dropped.

[Unreleased]: https://github.com/Xinyang-Gao/SVG2Desmos/commits/main

---

# 更新日志

本项目的重大变更都将记录在此文件中。

本项目遵循 [Keep a Changelog 1.1.0](https://keepachangelog.com/zh-CN/1.1.0/) 与 [语义化版本控制](https://semver.org/lang/zh-CN/)。

## [未发布]

本次重构了核心模块（不再依赖全局可变状态），新增命令行与 GUI 共用的报告构建器以保证两种入口输出一致，并修复了三个椭圆弧相关的正确性问题；GUI 则围绕路径预览、路径列表、菜单与快捷键重做，计算流程改为完全线程安全的后台流水线。最新一轮新增中英双语界面（含生成的报告文本）与路径多选。

**兼容性说明：**

- 分段模式输出与旧版**逐字节一致**（已用 `git` 中的原版对 `sun.svg`、`moon.svg` 比对验证）；默认的中文报告文本同样保持不变。
- 报告标题支持本地化（`lang="zh"` / `lang="en"`）；聚合格式的傅里叶标签统一为 `路径 0`（补了一个空格：`路径0` → `路径 0`）。
- 傅里叶模式输出更简洁：舍入后为零的项与非法双符号已被移除，所有非零系数保持不变。
- 带旋转角的椭圆弧输出与旧版不同——旧版输出是错误的。
- 旧版公共函数（`process_paths`、`segment_to_expression`、`fourier_fit_path`、`fourier_fit_multiple_segments`、`output_fourier_result_single` 等）与 `PRECISION` 全局变量仍然保留；新增参数全部为可选关键字且默认值即旧行为。

### 新增

**核心 / 命令行**

- `build_segment_report()` 与 `build_fourier_report()`：命令行与 GUI 共用的报告构建器，相同参数下两种入口输出完全一致。
- CLI 参数校验：`--precision`（0–10）、`--samples`（≥ 2）、`--fourier`（≥ 1）、`--path-index`（≥ 0）非法时以标准 argparse 方式报错；`main()` 返回明确的退出码。
- 报告构建链路新增 `lang="zh"|"en"` 关键字（`segment_to_expression()`、`process_paths()`、`build_segment_report()`、`collect_continuous_segments()`、`format_fourier_report()`、`build_fourier_report()` 及拟合告警、错误消息），GUI 传入当前界面语言；CLI 新增 `--lang {zh,en}`（默认 `zh`，原有输出不变）。
- `build_segment_report()` / `build_fourier_report()` 新增 `path_indices=` 关键字：只处理给定 `paths` 的子集并在报告中保留原始编号（越界抛 `ValueError`）；CLI 可一次给出多个索引，如 `--path-index 0 2`。
- `i18n.py`：GUI 文案表 + 语言探测（`SVG2DESMOS_LANG` → `LANG`/`LC_ALL` → `locale.getlocale()` → 首选编码，`cp936`/`gbk`/`gb18030`/`big5` 判为中文）与偏好持久化（`~/.svg2desmos_gui.json`，可用 `SVG2DESMOS_CONFIG` 改路径），读写失败一律忽略。
- `test_core_math.py`：把生成的参数方程重新求值，与 svgpathtools 自身的 `path.point(t)` 对比，覆盖直线、二次/三次贝塞尔与旋转椭圆弧；校验 Y 翻转是精确镜像、傅里叶级数可重建路径，并覆盖参数校验的错误分支（无需显示器）。现同时覆盖中英双语报告、`path_indices` 子集与 CLI 语言/多索引选项（共 41 项检查）。
- `test_gui_smoke.py`：不显示窗口地构建界面并驱动真实控件——语言切换、路径多选与选择计数/按钮、路径列表、预览、两种模式的后台生成、剪贴板、空选警告（共 53 项检查）；缺少 `sprite.svg` 时自动生成临时 SVG 夹具。

**GUI**

- “视图 → 语言”菜单（中文 / English），即时切换菜单、控件文案、列表标题、状态栏、对话框**以及生成的报告文本**；偏好跨次启动保留，首次启动跟随系统语言。
- 路径多选：路径列表改为 `selectmode="extended"`（Ctrl/Shift 点击），并提供“全选 / 清除选择”按钮与“已选 n/total”计数；加载文件默认全选，生成只转换选中路径（报告保留原始编号），未选中时给出提示而不是生成空结果，预览把所有已选路径高亮为蓝色。
- 路径预览画布：绘制全部路径、当前选中路径以蓝色高亮、切换 Y 翻转即时重绘、虚线显示坐标轴、窗口缩放时防抖重绘。
- 路径列表（Treeview）：显示每条路径的线段数与闭合状态，点击某行即选中目标路径，并与“目标路径索引”保持同步。
- 菜单栏（文件 / 编辑 / 视图 / 帮助）与快捷键：`Ctrl+O` 打开、`F5` 生成、`Ctrl+S` 保存、`Ctrl+Shift+C` 复制。
- 进度反馈：计算期间禁用生成按钮、显示不确定进度条，状态栏报告耗时与输出字符数；输出区显示字符统计与“自动换行”开关。

### 变更

- 用显式 `precision` 参数替代全局可变 `PRECISION`，逐层传递到每个生成函数；全局变量仅保留为缺省值以兼容旧代码。工作线程可安全并发，不再竞争共享状态。
- 四种线段类型的表达式生成统一到单一入口 `_segment_expressions(seg, precision)` 与统一的报告块拼装，替代三处重复实现。
- 弧线中心点参数化拆分为独立函数 `_arc_center_parameters()`，三角函数预计算；删除未使用的辅助函数与冗余计算。
- 傅里叶系数计算向量化（用 rfft 切片替代逐谐波 Python 循环），采样点数组改用 `np.fromiter` 构造。
- 傅里叶结果的选择、拟合、渲染拆分为 `_select_fourier_segments()` / `_fit_segments()` / `format_fourier_report()`，单段与聚合模式的失败处理语义清晰。
- GUI 生成流程改为“参数快照 → 后台线程 → 队列回传 → 主线程轮询”，任何 Tk 对象都不会在主线程之外被触碰（旧实现曾在工作线程中读取 `self.precision.get()`）。
- GUI 复用共享报告构建器，不再自行拼装傅里叶输出，删除约 70 行重复的格式化逻辑。
- 控件状态联动：分段模式下傅里叶参数自动禁用；勾选“拟合所有路径”时目标路径索引自动禁用；数字输入非法时回退默认值并夹取范围。
- 依赖缺失的报错延迟到窗口创建之后弹出（旧代码在 import 阶段、Tk 根窗口尚不存在时就调用 `messagebox`）。
- 使用 `PanedWindow` 重做分栏布局，并加入状态栏进度条、窗口标题随文件名更新、提高窗口最小尺寸、复制快捷键大小写不敏感。
- 生成范围：分段模式原先转换全部路径、傅里叶模式只转换一个索引；现在两者都只转换**路径列表中被选中的路径**（报告保留原始编号），而“拟合所有路径”仍然忽略选择。目标路径索引现在会把列表选择收窄为单条，而不只是单向同步。
- `README.md`：中英文 GUI 说明全部重写，并新增测试章节；中英双语、多选与新的 CLI 选项已在两个语言章节中补充说明。

### 修复

- 结果轮询的 `after` 链可能“静默死亡”：渲染某条结果时抛异常会跳过重新排期，界面永远停在“正在计算”（进度条一直转、生成按钮禁用），之后按 `F5` 毫无反应也不报错。现在用 `finally` 重新排期，并在不卡死流水线的前提下提示错误。
- `Arc(...)` 按位置参数 `(start, end, radius, rotation, large_arc, sweep)` 传参，而 svgpathtools 的实际签名为 `(start, radius, rotation, large_arc, sweep, end)`。结果是**任何包含弧线的 SVG 在启用 Y 翻转时直接抛 `TypeError` 崩溃**。现改为关键字参数构造，同时对参数顺序不同的 svgpathtools 版本也保持正确。
- `Arc.rotation` 的单位是**度**，旧代码却直接将其代入 `math.cos` / `math.sin`（当作弧度）。因此只要弧线带旋转角，生成的参数方程就与真实曲线严重不符（实测偏差 4.16，精确值应为 0）；旋转角为 0 时恰好不受影响。现统一用 `math.radians()` 转换后再参与中心点参数化与三角函数计算。
- Y 翻转现在会取反弧线的旋转角（φ → −φ），这是镜像恒等式 `R(-φ)·M = M·R(φ)` 的要求；旧代码只翻转端点与 sweep 标志，导致旋转弧镜像后形状错误。修复后翻转结果与理论镜像的偏差为 0.0。
- 傅里叶级数拼装曾输出非法或冗余记号——`+ + 0 * sin(2 t)` 双重符号、减号后的双空格，以及舍入后为零的项（如 `0 * cos(2 t)`）。现改为显式符号拼接，并丢弃渲染结果为零的项。

[未发布]: https://github.com/Xinyang-Gao/SVG2Desmos/commits/main
