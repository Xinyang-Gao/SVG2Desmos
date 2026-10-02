#!/usr/bin/env python3
"""
将 SVG 路径转换为数学表达式（参数方程）

该脚本读取一个 SVG 文件，提取所有路径，并对每个线段（直线、二次/三次 Bézier 曲线、椭圆弧）
输出相应的参数方程，参数 t ∈ [0, 1]。所有线段的紧凑坐标表达式 (x(t), y(t))
会集中显示在输出末尾，每行一条。

新增功能：使用 --fourier 选项可将指定路径拟合为傅里叶级数（周期函数），
输出可直接复制到 Desmos 中使用。添加 --fit-all-paths 可对所有路径分别拟合，
并在输出末尾聚合所有路径的坐标表达式。添加 --split-discontinuities 可自动将
路径内部的不连续点断开，分别拟合每个连续段。

椭圆弧现已支持精确参数方程，基于 SVG 弧的中心点参数化方法。
支持 --precision 控制输出小数位数（默认 4）。
支持 --flip-y 将 Y 轴翻转（适用于 Desmos 等数学坐标系），默认开启。

核心逻辑说明：
    * 所有生成函数均接受显式的 ``precision`` 参数，不再依赖可变全局状态，
      因此命令行与 GUI 可以安全地并行/在工作线程中调用。
    * ``build_segment_report`` 与 ``build_fourier_report`` 是命令行与 GUI
      共用的报告构建器，保证两种入口的输出格式完全一致。
    * 报告构建器接受 ``lang``（zh/en）与 ``path_indices``（多路径子集并保留
      原始编号）参数：GUI 的语言切换与多选路径都由它们支撑，缺省值保持
      原有中文单路径输出不变。

依赖项：svgpathtools, numpy（傅里叶模式需要）
用法：python svg_to_function.py input.svg [-o output.txt] [--fourier N] [--path-index idx ...] [--fit-all-paths] [--split-discontinuities] [--samples N] [--precision N] [--lang {zh,en}] [--no-flip-y]
"""
import argparse
import math
import sys
from typing import List, Optional, Tuple, Union

try:
    from svgpathtools import (
        svg2paths,
        Line,
        QuadraticBezier,
        CubicBezier,
        Arc,
        Path,
    )
except ImportError:
    print(
        "错误：未安装 svgpathtools！请运行：pip install svgpathtools", file=sys.stderr
    )
    sys.exit(1)

# 傅里叶模式需要 numpy
try:
    import numpy as np
except ImportError:
    np = None

# 默认输出精度（保留该全局变量以兼容旧代码；新代码请显式传入 precision）
PRECISION = 4

# 傅里叶系数过滤阈值
_COEFF_EPS = 1e-12


# --- 报告文案（命令行默认中文；GUI 可传 lang="en" 输出英文报告）---

_REPORT_TEXT = {
    "zh": {
        "no_paths": "未找到路径段",
        "path_header": "路径 {index}:",
        "seg_header": "第 {index} 段({kind}):",
        "unknown_seg_header": "第 {index} 段(未知类型):",
        "coords_header": "各线段坐标表达式 (每行一条):",
        "label.path": "路径 {index}",
        "label.path_seg": "路径{index}段{sub}",
        "fourier_header": "{label} 傅里叶级数拟合 (谐波数 N={n}, 采样点={samples}):",
        "desmos_header": "可直接复制到 Desmos 的坐标表达式:",
        "fourier_coords_header": "各连续段坐标表达式 (每行一条，按顺序对应上面各段):",
        "err.no_fit": "没有成功拟合任何连续段",
        "err.index_range": "路径索引 {index} 超出范围（共 {total} 条路径）",
        "err.need_numpy": "傅里叶模式需要 numpy，请运行：pip install numpy",
        "err.harmonics": "谐波次数必须 >= 1",
        "err.samples": "采样点数必须 >= 2",
        "err.prefix": "错误：",
        "cli.read_fail": "读取 SVG 时出错：{exc}",
        "cli.no_paths": "未找到任何路径",
        "cli.flip": "已应用 Y 轴翻转 (SVG → 数学坐标系)",
        "cli.written": "输出已写入 {path}",
        "warn.alias": (
            "警告：谐波数 {n} 大于等于采样点数 {samples} 的一半，"
            "可能发生混叠。建议增加采样点数或减少谐波数。"
        ),
        "warn.fit_failed": "警告：{label} 拟合失败：{exc}",
        "kind.line": "线段",
        "kind.quad": "二次贝塞尔",
        "kind.cubic": "三次贝塞尔",
        "kind.arc": "椭圆弧",
        "kind.unknown": "未知类型",
    },
    "en": {
        "no_paths": "No path segments found",
        "path_header": "Path {index}:",
        "seg_header": "Segment {index} ({kind}):",
        "unknown_seg_header": "Segment {index} (unknown type):",
        "coords_header": "Coordinate expressions (one per line):",
        "label.path": "Path {index}",
        "label.path_seg": "Path {index}.{sub}",
        "fourier_header": "{label} Fourier series fit (harmonics N={n}, samples={samples}):",
        "desmos_header": "Coordinate expressions ready to paste into Desmos:",
        "fourier_coords_header": (
            "Coordinate expressions for each block (one per line, in the order above):"
        ),
        "err.no_fit": "No block was fitted successfully",
        "err.index_range": "Path index {index} is out of range ({total} paths)",
        "err.need_numpy": "Fourier mode requires numpy, run: pip install numpy",
        "err.harmonics": "Harmonics must be >= 1",
        "err.samples": "Sample points must be >= 2",
        "err.prefix": "Error: ",
        "cli.read_fail": "Error reading SVG: {exc}",
        "cli.no_paths": "No paths found",
        "cli.flip": "Y-axis flip applied (SVG → math coordinates)",
        "cli.written": "Output written to {path}",
        "warn.alias": (
            "Warning: harmonics {n} >= samples/2 ({samples}) may alias; "
            "increase the sample count or reduce the harmonics."
        ),
        "warn.fit_failed": "Warning: fitting {label} failed: {exc}",
        "kind.line": "line",
        "kind.quad": "quadratic Bezier",
        "kind.cubic": "cubic Bezier",
        "kind.arc": "elliptical arc",
        "kind.unknown": "unknown type",
    },
}


def _norm_lang(lang: Optional[str]) -> str:
    """把任意语言标识归一化为 'zh' / 'en'（核心模块自包含，不依赖 i18n）。"""
    if isinstance(lang, str) and lang.strip().lower().replace("_", "-").startswith("en"):
        return "en"
    return "zh"


def _rt(key: str, lang: Optional[str] = None, **kwargs) -> str:
    """
    取报告文案并插值。

    缺 key 回退中文表，再回退 key 本身；格式化失败返回原始模板，绝不抛异常。
    """
    code = _norm_lang(lang)
    table = _REPORT_TEXT.get(code) or _REPORT_TEXT["zh"]
    text = table.get(key) or _REPORT_TEXT["zh"].get(key) or key
    if not kwargs:
        return text
    try:
        return text.format(**kwargs)
    except Exception:
        return text


# --- 数值格式化 ---


def _fmt(value: float, precision: Optional[int] = None) -> str:
    """按指定精度格式化数字，去掉多余的尾随零与小数点（如 3.5000 -> 3.5）。"""
    if precision is None:
        precision = PRECISION
    return f"{value:.{precision}f}".rstrip('0').rstrip('.')


def _format_coefficient(coeff: float) -> str:
    """格式化系数，使用全局精度（兼容旧接口）。"""
    return _fmt(coeff, PRECISION)


# --- 线段表达式生成 ---


def _line_expressions(seg: Line, precision: int) -> Tuple[str, str]:
    """直线段：线性插值。"""
    start, end = seg.start, seg.end
    dx = end.real - start.real
    dy = end.imag - start.imag
    x_expr = f"{_fmt(start.real, precision)} + ({_fmt(dx, precision)})*t"
    y_expr = f"{_fmt(start.imag, precision)} + ({_fmt(dy, precision)})*t"
    return x_expr, y_expr


def _quadratic_bezier_expressions(seg: QuadraticBezier, precision: int) -> Tuple[str, str]:
    """二次 Bézier：伯恩斯坦多项式。"""
    start, control, end = seg.start, seg.control, seg.end
    x_expr = (
        f"(1-t)^2*{_fmt(start.real, precision)} + "
        f"2*(1-t)*t*{_fmt(control.real, precision)} + "
        f"t^2*{_fmt(end.real, precision)}"
    )
    y_expr = (
        f"(1-t)^2*{_fmt(start.imag, precision)} + "
        f"2*(1-t)*t*{_fmt(control.imag, precision)} + "
        f"t^2*{_fmt(end.imag, precision)}"
    )
    return x_expr, y_expr


def _cubic_bezier_expressions(seg: CubicBezier, precision: int) -> Tuple[str, str]:
    """三次 Bézier：伯恩斯坦多项式。"""
    start, c1, c2, end = seg.start, seg.control1, seg.control2, seg.end
    x_expr = (
        f"(1-t)^3*{_fmt(start.real, precision)} + "
        f"3*(1-t)^2*t*{_fmt(c1.real, precision)} + "
        f"3*(1-t)*t^2*{_fmt(c2.real, precision)} + "
        f"t^3*{_fmt(end.real, precision)}"
    )
    y_expr = (
        f"(1-t)^3*{_fmt(start.imag, precision)} + "
        f"3*(1-t)^2*t*{_fmt(c1.imag, precision)} + "
        f"3*(1-t)*t^2*{_fmt(c2.imag, precision)} + "
        f"t^3*{_fmt(end.imag, precision)}"
    )
    return x_expr, y_expr


def _arc_center_parameters(seg: Arc) -> Optional[Tuple[float, ...]]:
    """
    SVG 椭圆弧的中心点参数化（https://www.w3.org/TR/SVG/implnote.html#ArcImplementationNotes）。

    返回 (cx, cy, rx, ry, phi, theta1, delta_theta)；
    若半径退化为 0（等价于直线）返回 None。
    """
    start, end = seg.start, seg.end
    rx, ry = seg.radius.real, seg.radius.imag
    if rx == 0 or ry == 0:
        return None

    # svgpathtools 的 Arc.rotation 单位是“度”，而所有三角计算需要弧度
    phi = math.radians(seg.rotation)
    cos_phi = math.cos(phi)
    sin_phi = math.sin(phi)

    # 中点转换到椭圆坐标系
    x1, y1 = start.real, start.imag
    x2, y2 = end.real, end.imag
    dx = (x1 - x2) / 2.0
    dy = (y1 - y2) / 2.0
    x1p = cos_phi * dx + sin_phi * dy
    y1p = -sin_phi * dx + cos_phi * dy

    # 半径过小时按比例放大
    lambda_val = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lambda_val > 1:
        sqrt_lambda = math.sqrt(lambda_val)
        rx *= sqrt_lambda
        ry *= sqrt_lambda

    # 椭圆坐标系下的中心
    sign = 1 if seg.large_arc == seg.sweep else -1
    numerator = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    if numerator < 0:
        numerator = 0
    denominator = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    if denominator == 0:
        denominator = 1e-12
    factor = sign * math.sqrt(numerator / denominator)
    cxp = factor * (rx * y1p / ry)
    cyp = factor * (-ry * x1p / rx)

    # 全局坐标下的中心
    cx = cos_phi * cxp - sin_phi * cyp + (x1 + x2) / 2.0
    cy = sin_phi * cxp + cos_phi * cyp + (y1 + y2) / 2.0

    # 起始角与扫过角
    def angle(u, v) -> float:
        return math.atan2(u[0] * v[1] - u[1] * v[0], u[0] * v[0] + u[1] * v[1])

    ux = (x1p - cxp) / rx
    uy = (y1p - cyp) / ry
    vx = (-x1p - cxp) / rx
    vy = (-y1p - cyp) / ry

    theta1 = angle((1, 0), (ux, uy))
    delta_theta = angle((ux, uy), (vx, vy)) % (2 * math.pi)
    if not seg.sweep and delta_theta > 0:
        delta_theta -= 2 * math.pi
    elif seg.sweep and delta_theta < 0:
        delta_theta += 2 * math.pi

    return cx, cy, rx, ry, phi, theta1, delta_theta


def _arc_expressions(seg: Arc, precision: int) -> Tuple[str, str]:
    """椭圆弧：中心点参数化，t ∈ [0,1] 映射到角度 [theta1, theta1+delta]。"""
    params = _arc_center_parameters(seg)
    if params is None:
        # 退化情况：半径为 0，等价于直线
        return _line_expressions(Line(seg.start, seg.end), precision)

    cx, cy, rx, ry, phi, theta1, delta_theta = params
    angle_expr = f"({_fmt(theta1, precision)} + t*{_fmt(delta_theta, precision)})"
    cos_angle = f"cos({angle_expr})"
    sin_angle = f"sin({angle_expr})"
    cos_phi_f = _fmt(math.cos(phi), precision)
    sin_phi_f = _fmt(math.sin(phi), precision)

    x_expr = (
        f"{_fmt(cx, precision)} + {_fmt(rx, precision)}*{cos_phi_f}*{cos_angle}"
        f" - {_fmt(ry, precision)}*{sin_phi_f}*{sin_angle}"
    )
    y_expr = (
        f"{_fmt(cy, precision)} + {_fmt(rx, precision)}*{sin_phi_f}*{cos_angle}"
        f" + {_fmt(ry, precision)}*{cos_phi_f}*{sin_angle}"
    )
    return x_expr, y_expr


# --- 路径处理（Y 轴翻转）---


def _flip_point(z: complex) -> complex:
    """返回 Y 坐标取反后的点。"""
    return complex(z.real, -z.imag)


def transform_path(path: Path, flip_y: bool = True) -> Path:
    """
    对路径中的所有点进行 Y 坐标翻转（如果 flip_y 为 True）。
    对于 Arc，同时取反旋转角与 sweep 标志，保证镜像后的弧几何形状正确。
    """
    if not flip_y:
        return path
    new_segments = []
    for seg in path:
        if isinstance(seg, Line):
            new_segments.append(Line(_flip_point(seg.start), _flip_point(seg.end)))
        elif isinstance(seg, QuadraticBezier):
            new_segments.append(
                QuadraticBezier(_flip_point(seg.start), _flip_point(seg.control), _flip_point(seg.end))
            )
        elif isinstance(seg, CubicBezier):
            new_segments.append(
                CubicBezier(
                    _flip_point(seg.start),
                    _flip_point(seg.control1),
                    _flip_point(seg.control2),
                    _flip_point(seg.end),
                )
            )
        elif isinstance(seg, Arc):
            # Y 轴镜像变换下：端点 y 取反、旋转角取反（R(-φ)·M = M·R(φ)）、
            # 扫掠方向取反，large_arc 与半径不变。
            # 必须使用关键字参数：svgpathtools 的 Arc 构造签名为
            # (start, radius, rotation, large_arc, sweep, end)，不同版本顺序曾有变化。
            new_segments.append(
                Arc(
                    start=_flip_point(seg.start),
                    radius=seg.radius,
                    rotation=-seg.rotation,
                    large_arc=seg.large_arc,
                    sweep=not seg.sweep,
                    end=_flip_point(seg.end),
                )
            )
        else:
            # 未知类型，原样复制（通常不会发生）
            new_segments.append(seg)
    return Path(*new_segments)


# --- 线段 → 表达式 ---


SegmentType = Union[Line, QuadraticBezier, CubicBezier, Arc]

# 线段类型 → 文案 key（实际名称由 _REPORT_TEXT 按语言给出）
_SEGMENT_KINDS = (
    (Line, "kind.line"),
    (QuadraticBezier, "kind.quad"),
    (CubicBezier, "kind.cubic"),
    (Arc, "kind.arc"),
)


def _segment_kind(seg, lang: Optional[str] = None) -> str:
    for cls, key in _SEGMENT_KINDS:
        if isinstance(seg, cls):
            return _rt(key, lang)
    return _rt("kind.unknown", lang)


def _segment_expressions(seg: SegmentType, precision: int) -> Tuple[str, str]:
    """返回线段的 (x(t), y(t)) 表达式字符串。"""
    if isinstance(seg, Line):
        return _line_expressions(seg, precision)
    if isinstance(seg, QuadraticBezier):
        return _quadratic_bezier_expressions(seg, precision)
    if isinstance(seg, CubicBezier):
        return _cubic_bezier_expressions(seg, precision)
    if isinstance(seg, Arc):
        return _arc_expressions(seg, precision)
    raise TypeError(f"不支持的线段类型: {type(seg)!r}")


def segment_to_expression(
    seg: SegmentType,
    seg_index: int,
    precision: Optional[int] = None,
    lang: Optional[str] = None,
) -> Tuple[str, str]:
    """
    返回该线段参数方程的主要描述字符串和紧凑坐标表达式字符串。
    返回格式: (main_str, coord_str)
    """
    if precision is None:
        precision = PRECISION

    kind = _segment_kind(seg, lang)
    if kind == _rt("kind.unknown", lang):
        main_str = (
            _rt("unknown_seg_header", lang, index=seg_index)
            + f"\n {seg}\n--------------------"
        )
        return main_str, "Unknown segment type"

    x_expr, y_expr = _segment_expressions(seg, precision)

    main_str = "\n".join(
        [
            _rt("seg_header", lang, index=seg_index, kind=kind),
            f" x(t) = {x_expr}",
            f" y(t) = {y_expr}",
            " t ∈ [0, 1]",
            "--------------------",
        ]
    )
    return main_str, f"({x_expr}, {y_expr})"


def process_paths(
    paths: List[Path],
    precision: Optional[int] = None,
    path_indices: Optional[List[int]] = None,
    lang: Optional[str] = None,
) -> Tuple[List[str], List[str]]:
    """
    处理路径列表，返回主输出和坐标表达式列表。

    path_indices 给出要处理的路径编号（取位于 ``paths``，报告中保留这些编号），
    缺省时按当前位置处理全部路径；编号越界抛 ValueError。
    """
    output_lines: List[str] = []
    coordinate_expressions: List[str] = []

    selected = range(len(paths)) if path_indices is None else path_indices
    for display_idx in selected:
        display_idx = int(display_idx)
        if display_idx < 0 or display_idx >= len(paths):
            raise ValueError(
                _rt("err.index_range", lang, index=display_idx, total=len(paths))
            )
        path = paths[display_idx]
        output_lines.append(_rt("path_header", lang, index=display_idx))
        for seg_idx, seg in enumerate(path):
            main_str, coord_str = segment_to_expression(
                seg, seg_idx, precision, lang=lang
            )
            output_lines.append(main_str)
            coordinate_expressions.append(coord_str)
        output_lines.append("")  # 路径之间空行

    return output_lines, coordinate_expressions


def build_segment_report(
    paths: List[Path],
    precision: Optional[int] = None,
    path_indices: Optional[List[int]] = None,
    lang: Optional[str] = None,
) -> str:
    """
    构建分段表达式报告文本（命令行与 GUI 共用）。

    path_indices 与 ``process_paths`` 同义：缺省输出全部路径，给定时只输出
    这些编号的路径（报告标题仍用原始编号）。
    """
    if not paths or all(len(p) == 0 for p in paths):
        return _rt("no_paths", lang)
    if path_indices is not None and len(path_indices) == 0:
        return _rt("no_paths", lang)

    output_lines, coordinate_expressions = process_paths(
        paths, precision, path_indices=path_indices, lang=lang
    )
    if coordinate_expressions:
        output_lines.extend(["", _rt("coords_header", lang)])
        output_lines.extend(coordinate_expressions)
    return "\n".join(output_lines)


# --- 不连续点分割 ---


def split_path_at_discontinuities(path: Path, tolerance: float = 1e-6) -> List[Path]:
    """
    将一个路径按照线段之间的不连续点（跳跃）分割成多个连续的子路径。
    返回子路径列表，每个子路径内的线段首尾相连（连续）。
    """
    if not path:
        return []

    subpaths: List[Path] = []
    current_segments = [path[0]]

    for i in range(1, len(path)):
        prev_seg, curr_seg = path[i - 1], path[i]
        if abs(curr_seg.start - prev_seg.end) > tolerance:
            # 不连续：结束当前子路径并开始新子路径
            subpaths.append(Path(*current_segments))
            current_segments = [curr_seg]
        else:
            current_segments.append(curr_seg)

    subpaths.append(Path(*current_segments))
    return subpaths


def collect_continuous_segments(
    paths: List[Path],
    split: bool,
    tolerance: float = 1e-6,
    lang: Optional[str] = None,
) -> List[Tuple[str, Path]]:
    """
    根据是否分割，将输入路径列表转换为连续段列表。
    返回列表，每个元素为 (label, path)，label 如 "路径 0" 或 "路径0段1"。
    """
    segments: List[Tuple[str, Path]] = []
    for path_idx, path in enumerate(paths):
        label = _rt("label.path", lang, index=path_idx)
        if split:
            subpaths = split_path_at_discontinuities(path, tolerance)
            if len(subpaths) == 1:
                segments.append((label, subpaths[0]))
            else:
                for sub_idx, subpath in enumerate(subpaths):
                    segments.append(
                        (_rt("label.path_seg", lang, index=path_idx, sub=sub_idx), subpath)
                    )
        else:
            segments.append((label, path))
    return segments


def is_path_closed(path: Path, tolerance: float = 1e-6) -> bool:
    """检查路径是否闭合（起点与终点距离小于容差）"""
    if not path:
        return False
    return abs(path[0].start - path[-1].end) < tolerance


# --- 傅里叶拟合 ---


def sample_path_points(path: Path, num_samples: int = 500) -> Tuple["np.ndarray", "np.ndarray"]:
    """
    沿路径均匀采样参数 t ∈ [0, 1)，返回 x 和 y 坐标数组。
    采样点不包括终点（终点与起点重合，用于周期函数）。
    """
    t_values = np.linspace(0, 1, num_samples, endpoint=False)
    points = [path.point(t) for t in t_values]
    x_vals = np.fromiter((p.real for p in points), dtype=float, count=num_samples)
    y_vals = np.fromiter((p.imag for p in points), dtype=float, count=num_samples)
    return x_vals, y_vals


def compute_fourier_coeffs(
    values: "np.ndarray", n_harmonics: int
) -> Tuple[float, List[float], List[float]]:
    """
    对实序列 values 计算傅里叶级数系数。
    返回 (a0, a_list, b_list)，其中 a_list 和 b_list 长度为 n_harmonics。
    重建公式: f(t) = a0/2 + Σ_{n=1}^{N} (a_n cos(n t) + b_n sin(n t))
    t ∈ [0, 2π)
    """
    count = len(values)
    coeffs = np.fft.rfft(values) / count

    a0 = 2 * coeffs[0].real  # 直流分量乘以2，使得 a0/2 为直流平均值

    # 可用的谐波数量（超出 Nyquist 的补 0）
    available = min(n_harmonics, len(coeffs) - 1)
    usable = coeffs[1 : available + 1]
    a = list(2 * usable.real) + [0.0] * (n_harmonics - available)
    b = list(-2 * usable.imag) + [0.0] * (n_harmonics - available)
    return float(a0), a, b


def format_fourier_series(
    prefix: str,
    a0: float,
    a_list: List[float],
    b_list: List[float],
    var: str = "t",
    precision: int = 4,
) -> str:
    """
    将傅里叶系数格式化为 Desmos 可读的表达式字符串。
    prefix: 表达式开头，例如 "x(t) = "

    每一项都带显式符号拼接，避免出现 "+ +" / "-  " 之类的非法或冗余记号；
    四舍五入后为 0 的项会被直接丢弃。
    """
    signed_terms: List[Tuple[str, bool]] = []  # (正文, 是否为负)

    def add(value: float, text: str) -> None:
        if abs(value) <= _COEFF_EPS:
            return
        rendered = _fmt(abs(value), precision)
        if rendered in ("", "0"):  # 精度不足导致该项渲染为 0，丢弃
            return
        signed_terms.append((rendered + text, value < 0))

    # 直流项 a0/2
    add(a0 / 2, "")

    for n, (a_n, b_n) in enumerate(zip(a_list, b_list), start=1):
        add(a_n, f" * cos({n} {var})")
        add(b_n, f" * sin({n} {var})")

    if not signed_terms:
        return prefix + "0"

    pieces: List[str] = []
    for i, (text, negative) in enumerate(signed_terms):
        if i == 0:
            pieces.append(("-" if negative else "") + text)
        else:
            pieces.append((" - " if negative else " + ") + text)
    return prefix + "".join(pieces)


def fourier_fit_path(
    path: Path,
    n_harmonics: int = 5,
    num_samples: int = 1000,
    precision: int = 4,
    lang: Optional[str] = None,
) -> Tuple[str, str, str]:
    """
    对路径进行傅里叶级数拟合，返回 (x_expr, y_expr, coord_expr) 字符串，
    可直接复制到 Desmos 中使用。
    """
    if np is None:
        raise ImportError(_rt("err.need_numpy", lang))

    if n_harmonics < 1:
        raise ValueError(_rt("err.harmonics", lang))
    if num_samples < 2:
        raise ValueError(_rt("err.samples", lang))

    # Nyquist 检查
    if n_harmonics >= num_samples / 2:
        print(
            _rt("warn.alias", lang, n=n_harmonics, samples=num_samples),
            file=sys.stderr,
        )

    x_vals, y_vals = sample_path_points(path, num_samples)

    a0_x, a_x, b_x = compute_fourier_coeffs(x_vals, n_harmonics)
    a0_y, a_y, b_y = compute_fourier_coeffs(y_vals, n_harmonics)

    x_pure = format_fourier_series("", a0_x, a_x, b_x, "t", precision)
    y_pure = format_fourier_series("", a0_y, a_y, b_y, "t", precision)
    return f"x(t) = {x_pure}", f"y(t) = {y_pure}", f"({x_pure}, {y_pure})"


# --- 傅里叶报告构建 ---


def _select_fourier_segments(
    paths: List[Path],
    fit_all: bool,
    path_index: int,
    split: bool,
    path_indices: Optional[List[int]] = None,
    lang: Optional[str] = None,
) -> Tuple[List[Tuple[str, Path]], bool]:
    """
    选择需要拟合的段。返回 (segments, is_multi_format)。
    is_multi_format 决定输出使用“多段聚合”格式还是“单段”格式。

    path_indices 为 GUI 多选时的原始路径编号列表：
        * None            → 使用 path_index（单条，旧行为）
        * [i]             → 等价于 path_index=i（保持单段输出格式）
        * [i, j, ...]     → 多条路径，使用聚合格式
    """
    if fit_all:
        return collect_continuous_segments(paths, split, lang=lang), True

    if path_indices is None:
        selected = [path_index]
    else:
        selected = [int(idx) for idx in path_indices]
        if not selected:
            selected = [path_index]

    for idx in selected:
        if idx < 0 or idx >= len(paths):
            raise ValueError(
                _rt("err.index_range", lang, index=idx, total=len(paths))
            )

    if len(selected) == 1:
        index = selected[0]
        if split:
            subpaths = split_path_at_discontinuities(paths[index])
            if len(subpaths) > 1:
                segments = [
                    (_rt("label.path_seg", lang, index=index, sub=sub_idx), subpath)
                    for sub_idx, subpath in enumerate(subpaths)
                ]
                return segments, True

        return [(_rt("label.path", lang, index=index), paths[index])], False

    # 多选：逐条收集，split=True 时在不连续点处断开
    segments = []
    for index in selected:
        label = _rt("label.path", lang, index=index)
        if split:
            subpaths = split_path_at_discontinuities(paths[index])
            if len(subpaths) > 1:
                segments.extend(
                    (
                        _rt("label.path_seg", lang, index=index, sub=sub_idx),
                        subpath,
                    )
                    for sub_idx, subpath in enumerate(subpaths)
                )
                continue
        segments.append((label, paths[index]))
    return segments, True


def _fit_segments(
    segments: List[Tuple[str, Path]],
    n_harmonics: int,
    samples: int,
    precision: int,
    skip_failures: bool,
    lang: Optional[str] = None,
) -> List[Tuple[str, str, str, str]]:
    """
    对多个段执行傅里叶拟合。
    返回 [(label, x_expr, y_expr, coord_expr), ...]。
    skip_failures=True 时单段失败仅告警并跳过；否则异常向上抛出。
    """
    results: List[Tuple[str, str, str, str]] = []
    for label, path in segments:
        try:
            x_expr, y_expr, coord_expr = fourier_fit_path(
                path, n_harmonics, samples, precision, lang=lang
            )
        except Exception as exc:
            if not skip_failures:
                raise
            print(_rt("warn.fit_failed", lang, label=label, exc=exc), file=sys.stderr)
            continue
        results.append((label, x_expr, y_expr, coord_expr))
    return results


def format_fourier_report(
    results: List[Tuple[str, str, str, str]],
    n_harmonics: int,
    samples: int,
    multi_format: bool,
    lang: Optional[str] = None,
) -> str:
    """把拟合结果渲染为报告文本。"""
    if not results:
        raise ValueError(_rt("err.no_fit", lang))

    if not multi_format:
        label, x_expr, y_expr, coord_expr = results[0]
        return "\n".join(
            [
                _rt("fourier_header", lang, label=label, n=n_harmonics, samples=samples),
                x_expr,
                y_expr,
                "t ∈ [0, 2π]",
                "",
                _rt("desmos_header", lang),
                coord_expr,
            ]
        )

    blocks = [
        "\n".join(
            [
                _rt("fourier_header", lang, label=label, n=n_harmonics, samples=samples),
                x_expr,
                y_expr,
                "t ∈ [0, 2π]",
                "",
                "---------------------",
            ]
        )
        for label, x_expr, y_expr, _ in results
    ]
    coord_lines = ["", _rt("fourier_coords_header", lang)]
    coord_lines.extend(coord for *_, coord in results)
    blocks.append("\n".join(coord_lines))
    return "\n".join(blocks)


def build_fourier_report(
    paths: List[Path],
    n_harmonics: int = 5,
    samples: int = 1000,
    precision: Optional[int] = None,
    fit_all: bool = False,
    path_index: int = 0,
    split: bool = False,
    path_indices: Optional[List[int]] = None,
    lang: Optional[str] = None,
) -> str:
    """
    构建傅里叶拟合报告文本（命令行与 GUI 共用）。

    fit_all=False 时只拟合 path_indices 指定的路径（这些编号取位于 ``paths``，
    缺省为单条 path_index）；split=True 时先在不连续点处断开。
    参数非法时抛出 ValueError/IndexError，缺少 numpy 时抛出 ImportError。
    """
    if np is None:
        raise ImportError(_rt("err.need_numpy", lang))
    if precision is None:
        precision = PRECISION

    segments, multi_format = _select_fourier_segments(
        paths, fit_all, path_index, split, path_indices=path_indices, lang=lang
    )
    results = _fit_segments(
        segments,
        n_harmonics,
        samples,
        precision,
        skip_failures=multi_format,
        lang=lang,
    )
    return format_fourier_report(results, n_harmonics, samples, multi_format, lang=lang)


# --- 兼容旧接口的输出包装 ---


def fourier_fit_multiple_segments(
    segments: List[Tuple[str, Path]],
    n_harmonics: int,
    samples: int,
    precision: int,
    output_file: Optional[str] = None,
    lang: Optional[str] = None,
) -> None:
    """对多个连续段进行傅里叶拟合并输出（兼容旧接口）。"""
    results = _fit_segments(
        segments, n_harmonics, samples, precision, skip_failures=True, lang=lang
    )
    text = format_fourier_report(results, n_harmonics, samples, multi_format=True, lang=lang)
    _emit(text, output_file, lang=lang)


def output_fourier_result_single(
    path: Path,
    n_harmonics: int,
    path_idx: int,
    samples: int,
    precision: int,
    output_file: Optional[str] = None,
    split: bool = False,
    lang: Optional[str] = None,
) -> None:
    """输出单条路径的傅里叶拟合结果（兼容旧接口）。"""
    if split:
        subpaths = split_path_at_discontinuities(path)
        if len(subpaths) > 1:
            segments = [
                (
                    _rt("label.path_seg", lang, index=path_idx, sub=sub_idx),
                    subpath,
                )
                for sub_idx, subpath in enumerate(subpaths)
            ]
            results = _fit_segments(
                segments, n_harmonics, samples, precision, skip_failures=True, lang=lang
            )
            text = format_fourier_report(
                results, n_harmonics, samples, multi_format=True, lang=lang
            )
            _emit(text, output_file, lang=lang)
            return

    _output_single_segment(
        path, path_idx, n_harmonics, samples, precision, output_file, lang=lang
    )


def _output_single_segment(
    path: Path,
    label: Union[int, str],
    n_harmonics: int,
    samples: int,
    precision: int,
    output_file: Optional[str],
    lang: Optional[str] = None,
) -> None:
    """输出单个连续段的拟合结果（不分段，不聚合）。"""
    try:
        x_expr, y_expr, coord_expr = fourier_fit_path(
            path, n_harmonics, samples, precision, lang=lang
        )
    except ImportError as exc:
        print(_rt("err.prefix", lang) + str(exc), file=sys.stderr)
        sys.exit(1)

    label_str = (
        _rt("label.path", lang, index=label) if isinstance(label, int) else str(label)
    )
    text = format_fourier_report(
        [(label_str, x_expr, y_expr, coord_expr)],
        n_harmonics,
        samples,
        multi_format=False,
        lang=lang,
    )
    _emit(text, output_file, lang=lang)


def _emit(
    text: str, output_file: Optional[str], lang: Optional[str] = None
) -> None:
    """统一的输出写入：写文件或打印到标准输出。"""
    if output_file:
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(text)
        print(_rt("cli.written", lang, path=output_file))
    else:
        print(text)


# --- Main ---


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="将 SVG 路径转换为数学参数方程，支持分段表达式或傅里叶级数拟合"
    )
    parser.add_argument("input", help="输入 SVG 文件")
    parser.add_argument("-o", "--output", help="输出文件（默认: 标准输出）")
    parser.add_argument(
        "--fourier",
        type=int,
        nargs="?",
        const=5,
        default=None,
        help="使用傅里叶级数拟合，可选指定谐波次数（默认 5）",
    )
    parser.add_argument(
        "--path-index",
        type=int,
        nargs="+",
        default=[0],
        metavar="IDX",
        help="傅里叶模式下选择的路径索引，可指定多个（默认 0）",
    )
    parser.add_argument("--fit-all-paths", action="store_true",
                        help="傅里叶模式下对所有路径分别拟合（覆盖 --path-index 设置）")
    parser.add_argument("--split-discontinuities", action="store_true",
                        help="傅里叶模式下自动将路径内部的不连续点断开，分别拟合每个连续段")
    parser.add_argument("--samples", type=int, default=1000,
                        help="傅里叶模式下的采样点数（默认 1000）")
    parser.add_argument("--precision", type=int, default=4,
                        help="输出表达式的小数精度（默认 4）")
    parser.add_argument("--lang", choices=("zh", "en"), default="zh",
                        help="输出报告的语言（默认 zh）")
    parser.add_argument("--flip-y", action="store_true", default=True,
                        help="翻转 Y 坐标（适用于 Desmos 等数学坐标系，默认开启）")
    parser.add_argument("--no-flip-y", dest="flip_y", action="store_false",
                        help="禁用 Y 轴翻转，保留 SVG 原始坐标系")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    lang = args.lang

    if not 0 <= args.precision <= 10:
        parser.error("--precision 必须在 0 到 10 之间")
    if args.samples < 2:
        parser.error("--samples 必须 >= 2")
    if args.fourier is not None and args.fourier < 1:
        parser.error("--fourier 谐波次数必须 >= 1")

    # --path-index 支持一个或多个索引
    path_indices = [int(idx) for idx in args.path_index] or [0]
    if any(idx < 0 for idx in path_indices):
        parser.error("--path-index 不能为负数")

    # 读取 SVG
    try:
        paths, _ = svg2paths(args.input)
    except Exception as exc:
        print(_rt("cli.read_fail", lang, exc=exc), file=sys.stderr)
        return 1

    if not paths:
        print(_rt("cli.no_paths", lang), file=sys.stderr)
        return 1

    # 应用 Y 轴翻转（如果需要）
    if args.flip_y:
        paths = [transform_path(p, flip_y=True) for p in paths]
        print(_rt("cli.flip", lang), file=sys.stderr)

    try:
        if args.fourier is not None:
            text = build_fourier_report(
                paths,
                n_harmonics=args.fourier,
                samples=args.samples,
                precision=args.precision,
                fit_all=args.fit_all_paths,
                path_index=path_indices[0],
                split=args.split_discontinuities,
                path_indices=path_indices if len(path_indices) > 1 else None,
                lang=lang,
            )
        else:
            text = build_segment_report(
                paths, precision=args.precision, lang=lang
            )
    except (ValueError, IndexError) as exc:
        print(_rt("err.prefix", lang) + str(exc), file=sys.stderr)
        return 1

    _emit(text, args.output, lang=lang)
    return 0


if __name__ == "__main__":
    sys.exit(main())
