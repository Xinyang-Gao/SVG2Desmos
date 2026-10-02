# SVG to Desmos

Convert SVG paths into mathematical expressions (parametric equations), supporting piecewise exact expressions and Fourier series fitting. The generated expressions can be copied directly into mathematical graphing tools such as [Desmos](https://www.desmos.com/calculator).

## Features

- **Piecewise parametric equations**  
  Convert each segment (line, quadratic/cubic Bézier, elliptical arc) into an exact parametric equation, outputting `(x(t), y(t))` expressions with `t ∈ [0,1]`.

- **Fourier series fitting**  
  Fit the entire path (or consecutive segments) into a Fourier series, outputting trigonometric expressions for `x(t)` and `y(t)` with `t ∈ [0, 2π]`. Ideal for converting closed or complex curves into compact periodic functions.

- **Automatic discontinuity handling**  
  Automatically break paths at internal discontinuities (jumps) and fit each continuous segment separately.

- **Coordinate flip**  
  Y‑axis is flipped by default (SVG → math coordinate system) so that the graph appears upright in Desmos; can be disabled with an option.

- **Graphical User Interface (GUI)**  
  Provides a Tkinter‑based visual tool, no command‑line experience required.

## Dependencies

### Command line version (`svg_to_function.py`)
```bash
pip install svgpathtools numpy
```

### GUI version (`svg_to_function_gui.py`)
Requires Tkinter (comes with Python) in addition to the above dependencies.

## Command‑line tool usage

```bash
python svg_to_function.py input.svg [-o output.txt] [options]
```

### Basic arguments

| Argument | Description |
|----------|-------------|
| `input.svg` | Input SVG file path |
| `-o`, `--output` | Output file (default: stdout) |
| `--precision N` | Decimal precision (default: 4) |
| `--flip-y` / `--no-flip-y` | Enable/disable Y‑axis flipping (default: enabled) |

### Piecewise mode (default)

No extra options – outputs exact parametric equations per original segment.

Example:
```bash
python svg_to_function.py heart.svg --precision 3
```

### Fourier mode

Enable with `--fourier [N]` where `N` is the number of harmonics (default: 5).

| Option | Description |
|--------|-------------|
| `--fourier [N]` | Enable Fourier fitting, optionally specify number of harmonics |
| `--path-index idx` | Select which path to fit (default: 0) |
| `--fit-all-paths` | Fit all paths in the SVG separately (overrides `--path-index`) |
| `--split-discontinuities` | Automatically split at discontinuities, fit each continuous segment |
| `--samples N` | Number of sampling points (default: 1000) |

Examples:
```bash
# Fit path 0 with 10 harmonics
python svg_to_function.py logo.svg --fourier 10

# Fit all paths, split discontinuities, save to file
python svg_to_function.py icon.svg --fourier 8 --fit-all-paths --split-discontinuities -o fourier.txt
```

## GUI usage

Launch the graphical interface:
```bash
python svg_to_function_gui.py
```

Interface description:
- **File** – open an SVG file (`Ctrl+O`).
- **Path list** – one row per path with its segment count and open/closed state; clicking a row selects the target path and keeps the index field in sync.
- **Preview** – draws every path and highlights the selected one in blue; flipping the Y axis re-renders instantly, and dashed lines mark the coordinate axes.
- **Output mode** – switch between “Piecewise expressions” (exact) and “Fourier series fitting”. Fourier-only controls are disabled while piecewise mode is active.
- **Fourier parameters** – harmonic order, sample points, discontinuity splitting, fit-all-paths (the target index is disabled when “fit all” is checked).
- **Decimal precision** – controls the number of decimal places in the output.
- **Flip Y coordinate** – checked by default (Desmos ready).
- **Generate expressions** (`F5`) – runs in a background thread with a progress bar, so the window never freezes; the status bar reports elapsed time and output length.
- **Copy to clipboard** (`Ctrl+Shift+C`) / **Save to file** (`Ctrl+S`) / **Clear output** – export or reset the result; “Wrap lines” toggles word wrap.

The GUI and the command line share the same report builders, so both produce identical output for identical settings.

## Automated tests

```bash
python test_core_math.py   # core math correctness (no display needed)
python test_gui_smoke.py   # GUI smoke test
```

- `test_core_math.py` re-evaluates the generated parametric equations and compares them against `svgpathtools`' own `path.point(t)` for every segment type (line, quadratic/cubic Bézier, rotated elliptical arc), verifies that Y‑flipping is an exact mirror image, checks the Fourier series reconstruction, and exercises the error paths.
- `test_gui_smoke.py` builds the window without showing it, then drives the real widgets (path list, preview, background generation in both modes, clipboard) and prints a pass/fail summary.

## Output format description

### Piecewise mode example output
```
Path 0:
Segment 0 (elliptical arc):
 x(t) = 0 + 2*cos(0)*cos(0 + t*6.283) - 1*sin(0)*sin(0 + t*6.283)
 y(t) = 0 + 2*sin(0)*cos(0 + t*6.283) + 1*cos(0)*sin(0 + t*6.283)
 t ∈ [0, 1]
--------------------
...
Coordinates (one parametric pair per line):
(0 + 2*cos(0)*cos(0 + t*6.283) - 1*sin(0)*sin(0 + t*6.283), 0 + 2*sin(0)*cos(0 + t*6.283) + 1*cos(0)*sin(0 + t*6.283))
...
```

### Fourier mode example output
```
Path 0 Fourier series fit (harmonics N=5):
x(t) = 1.23 + 0.5 * cos(1 t) - 0.2 * sin(1 t) + 0.1 * cos(2 t) ...
y(t) = 0.5 + 0.3 * cos(1 t) + 0.4 * sin(1 t) - 0.05 * cos(2 t) ...
t ∈ [0, 2π]
Coordinates:
(1.23 + 0.5 * cos(1 t) - 0.2 * sin(1 t) + 0.1 * cos(2 t) ..., 0.5 + 0.3 * cos(1 t) + 0.4 * sin(1 t) - 0.05 * cos(2 t) ...)
```

## Notes

1. **Elliptical arc conversion** – the script implements exact centre parameterisation for SVG elliptical arcs, supporting arbitrary rotation, large‑arc flags, and sweep flags.
2. **Fourier sampling** – paths are sampled uniformly in the parameter `t` (not arc length). For non‑uniformly parameterised paths, increasing the number of sampling points improves accuracy.
3. **Performance** – Fourier mode computation grows with harmonic order and sample count. Be patient with complex paths.
4. **Desmos compatibility** – Y‑axis flipping is enabled by default; expressions can be copied directly into Desmos’s expression line (using `t` as the parameter variable).
5. **Discontinuity detection** – discontinuities are detected by comparing the distance between consecutive segment end/start points (tolerance 1e‑6).

## License (MIT)

This script is free to use. Suggestions and pull requests are welcome.

---

# 中文
# SVG to Desmos

将 SVG 路径转换为数学表达式（参数方程），支持分段精确表达式和傅里叶级数拟合。生成的表达式可直接复制到 [Desmos](https://www.desmos.com/calculator) 等数学绘图工具中使用。

## 功能特点

- **分段参数方程**  
  将每个线段（直线、二次/三次贝塞尔曲线、椭圆弧）转换为精确的参数方程，输出 `(x(t), y(t))` 表达式，`t ∈ [0,1]`。

- **傅里叶级数拟合**  
  将整个路径（或连续段）拟合为傅里叶级数，输出 `x(t)` 和 `y(t)` 的三角函数表达式，`t ∈ [0, 2π]`。非常适合将闭合路径或复杂曲线转换为简洁的周期函数。

- **自动处理不连续点**  
  支持将路径内部的不连续点（跳跃）自动断开，分别拟合每个连续段。

- **坐标系翻转**  
  默认翻转 Y 轴（SVG 坐标系 → 数学坐标系），使图形在 Desmos 中正向显示，可通过选项关闭。

- **图形界面 (GUI)**  
  提供基于 Tkinter 的可视化工具，无需命令行操作。

## 安装依赖

### 命令行版本 (`svg_to_function.py`)
```bash
pip install svgpathtools numpy
```

### GUI 版本 (`svg_to_function_gui.py`)
额外需要 Tkinter（Python 自带，无需安装），以及上述依赖。

## 命令行工具用法

```bash
python svg_to_function.py input.svg [-o output.txt] [选项]
```

### 基本参数

| 参数 | 说明 |
|------|------|
| `input.svg` | 输入的 SVG 文件路径 |
| `-o`, `--output` | 输出文件（默认输出到标准输出） |
| `--precision N` | 小数精度（默认 4） |
| `--flip-y` / `--no-flip-y` | 是否翻转 Y 轴（默认启用） |

### 分段模式（默认）

无额外选项时，按原始线段输出精确参数方程。

示例：
```bash
python svg_to_function.py heart.svg --precision 3
```

### 傅里叶模式

使用 `--fourier [N]` 启用，`N` 为谐波次数（默认 5）。

| 选项 | 说明 |
|------|------|
| `--fourier [N]` | 启用傅里叶拟合，可选指定谐波次数 |
| `--path-index idx` | 选择要拟合的路径索引（默认 0） |
| `--fit-all-paths` | 对 SVG 中所有路径分别拟合（覆盖 `--path-index`） |
| `--split-discontinuities` | 自动分割不连续点，分别拟合每个连续段 |
| `--samples N` | 采样点数（默认 1000） |

示例：
```bash
# 拟合路径 0，谐波 10 次
python svg_to_function.py logo.svg --fourier 10

# 拟合所有路径，分割不连续点，输出到文件
python svg_to_function.py icon.svg --fourier 8 --fit-all-paths --split-discontinuities -o fourier.txt
```

## GUI 工具用法

直接运行图形界面：
```bash
python svg_to_function_gui.py
```

界面说明：
- **文件**：打开 SVG 文件（`Ctrl+O`）。
- **路径列表**：每行显示一条路径的线段数量与闭合状态；点击某行即选中目标路径，并与“目标路径索引”联动。
- **预览**：绘制全部路径，当前选中路径以蓝色高亮；勾选/取消“翻转 Y 坐标”会即时重绘，虚线表示坐标轴。
- **输出模式**：切换“分段表达式（精确）”或“傅里叶级数拟合”；分段模式下傅里叶参数自动禁用。
- **傅里叶参数**：谐波次数、采样点数、分割不连续点、拟合所有路径（勾选后“目标路径索引”自动禁用）。
- **小数精度**：控制输出数字的小数位数。
- **翻转 Y 坐标**：默认勾选，适配 Desmos。
- **生成表达式**（`F5`）：在后台线程中执行并显示进度条，界面不会卡死；状态栏显示耗时与输出字符数。
- **复制到剪贴板**（`Ctrl+Shift+C`）/ **保存到文件**（`Ctrl+S`）/ **清空输出**：导出或重置结果；“自动换行”控制输出区换行方式。

GUI 与命令行共用同一套报告构建函数，相同参数下两者输出完全一致。

## 自动化测试

```bash
python test_core_math.py   # 核心算法正确性（无需显示器）
python test_gui_smoke.py   # GUI 冒烟测试
```

- `test_core_math.py`：把生成的参数方程重新求值，与 svgpathtools 自身的 `path.point(t)` 对比，覆盖直线、二次/三次贝塞尔与旋转椭圆弧；同时校验 Y 翻转是精确镜像、傅里叶级数可重建路径，并覆盖参数校验的错误分支。
- `test_gui_smoke.py`：不显示窗口地构建界面，驱动真实控件（路径列表、预览、两种模式的后台生成、剪贴板），输出通过/失败统计。

## 输出格式说明

### 分段模式输出示例
```
路径 0:
第 0 段(椭圆弧):
 x(t) = 0 + 2*cos(0)*cos(0 + t*6.283) - 1*sin(0)*sin(0 + t*6.283)
 y(t) = 0 + 2*sin(0)*cos(0 + t*6.283) + 1*cos(0)*sin(0 + t*6.283)
 t ∈ [0, 1]
--------------------
...
各线段坐标表达式 (每行一条):
(0 + 2*cos(0)*cos(0 + t*6.283) - 1*sin(0)*sin(0 + t*6.283), 0 + 2*sin(0)*cos(0 + t*6.283) + 1*cos(0)*sin(0 + t*6.283))
...
```

### 傅里叶模式输出示例
```
路径 0 傅里叶级数拟合 (谐波数 N=5):
x(t) = 1.23 + 0.5 * cos(1 t) - 0.2 * sin(1 t) + 0.1 * cos(2 t) ...
y(t) = 0.5 + 0.3 * cos(1 t) + 0.4 * sin(1 t) - 0.05 * cos(2 t) ...
t ∈ [0, 2π]
坐标表达式:
(1.23 + 0.5 * cos(1 t) - 0.2 * sin(1 t) + 0.1 * cos(2 t) ..., 0.5 + 0.3 * cos(1 t) + 0.4 * sin(1 t) - 0.05 * cos(2 t) ...)
```

## 注意事项

1. **椭圆弧转换**：脚本实现了 SVG 椭圆弧的精确中心参数化，支持任意旋转、大弧标志和 sweep 标志。
2. **傅里叶拟合的采样**：路径沿长度均匀采样（按参数 `t` 而非弧长）。对于非匀速参数化的路径，建议增加采样点数以提高拟合精度。
3. **性能**：傅里叶模式计算量随谐波次数和采样点数增加。处理复杂路径时请耐心等待。
4. **Desmos 兼容性**：默认启用 Y 轴翻转，表达式可直接复制到 Desmos 的“表达式”栏中（使用 `t` 作为参数变量）。
5. **不连续点检测**：通过比较相邻线段起点/终点距离（容差 1e-6）判断是否连续。

## 许可证(MIT)

本脚本自由使用。如有建议和改进，欢迎提交 issue 和 PR。
