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

依赖项：svgpathtools, numpy（傅里叶模式需要）
用法：python svg_to_function.py input.svg [-o output.txt] [--fourier N] [--path-index idx] [--fit-all-paths] [--split-discontinuities] [--samples N] [--precision N] [--no-flip-y]
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

# 线段类型 → 显示名称
_SEGMENT_KINDS = (
    (Line, "线段"),
    (QuadraticBezier, "二次贝塞尔"),
    (CubicBezier, "三次贝塞尔"),
    (Arc, "椭圆弧"),
)


def _segment_kind(seg) -> str:
    for cls, name in _SEGMENT_KINDS:
        if isinstance(seg, cls):
            return name
    return "未知类型"


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
    seg: SegmentType, seg_index: int, precision: Optional[int] = None
) -> Tuple[str, str]:
    """
    返回该线段参数方程的主要描述字符串和紧凑坐标表达式字符串。
    返回格式: (main_str, coord_str)
    """
    if precision is None:
        precision = PRECISION

    kind = _segment_kind(seg)
    if kind == "未知类型":
        main_str = f"第 {seg_index} 段(未知类型):\n {seg}\n--------------------"
        return main_str, "Unknown segment type"

    x_expr, y_expr = _segment_expressions(seg, precision)

    main_str = "\n".join(
        [
            f"第 {seg_index} 段({kind}):",
            f" x(t) = {x_expr}",
            f" y(t) = {y_expr}",
            " t ∈ [0, 1]",
            "--------------------",
        ]
    )
    return main_str, f"({x_expr}, {y_expr})"


def process_paths(
    paths: List[Path], precision: Optional[int] = None
) -> Tuple[List[str], List[str]]:
    """处理路径列表，返回主输出和坐标表达式列表"""
    output_lines: List[str] = []
    coordinate_expressions: List[str] = []

    for path_idx, path in enumerate(paths):
        output_lines.append(f"路径 {path_idx}:")
        for seg_idx, seg in enumerate(path):
            main_str, coord_str = segment_to_expression(seg, seg_idx, precision)
            output_lines.append(main_str)
            coordinate_expressions.append(coord_str)
        output_lines.append("")  # 路径之间空行

    return output_lines, coordinate_expressions


def build_segment_report(
    paths: List[Path], precision: Optional[int] = None
) -> str:
    """构建分段表达式报告文本（命令行与 GUI 共用）。"""
    if not paths or all(len(p) == 0 for p in paths):
        return "未找到路径段"

    output_lines, coordinate_expressions = process_paths(paths, precision)
    if coordinate_expressions:
        output_lines.extend(["", "各线段坐标表达式 (每行一条):"])
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
    paths: List[Path], split: bool, tolerance: float = 1e-6
) -> List[Tuple[str, Path]]:
    """
    根据是否分割，将输入路径列表转换为连续段列表。
    返回列表，每个元素为 (label, path)，label 如 "路径0" 或 "路径0段1"。
    """
    segments: List[Tuple[str, Path]] = []
    for path_idx, path in enumerate(paths):
        if split:
            subpaths = split_path_at_discontinuities(path, tolerance)
            if len(subpaths) == 1:
                segments.append((f"路径{path_idx}", subpaths[0]))
            else:
                for sub_idx, subpath in enumerate(subpaths):
                    segments.append((f"路径{path_idx}段{sub_idx}", subpath))
        else:
            segments.append((f"路径{path_idx}", path))
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
    path: Path, n_harmonics: int = 5, num_samples: int = 1000, precision: int = 4
) -> Tuple[str, str, str]:
    """
    对路径进行傅里叶级数拟合，返回 (x_expr, y_expr, coord_expr) 字符串，
    可直接复制到 Desmos 中使用。
    """
    if np is None:
        raise ImportError("傅里叶模式需要 numpy，请运行：pip install numpy")

    if n_harmonics < 1:
        raise ValueError("谐波次数必须 >= 1")
    if num_samples < 2:
        raise ValueError("采样点数必须 >= 2")

    # Nyquist 检查
    if n_harmonics >= num_samples / 2:
        print(
            f"警告：谐波数 {n_harmonics} 大于等于采样点数 {num_samples} 的一半，"
            f"可能发生混叠。建议增加采样点数或减少谐波数。",
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
    paths: List[Path], fit_all: bool, path_index: int, split: bool
) -> Tuple[List[Tuple[str, Path]], bool]:
    """
    选择需要拟合的段。返回 (segments, is_multi_format)。
    is_multi_format 决定输出使用“多段聚合”格式还是“单段”格式。
    """
    if fit_all:
        return collect_continuous_segments(paths, split), True

    if path_index < 0 or path_index >= len(paths):
        raise ValueError(
            f"路径索引 {path_index} 超出范围（共 {len(paths)} 条路径）"
        )

    if split:
        subpaths = split_path_at_discontinuities(paths[path_index])
        if len(subpaths) > 1:
            segments = [
                (f"路径{path_index}段{sub_idx}", subpath)
                for sub_idx, subpath in enumerate(subpaths)
            ]
            return segments, True

    return [(f"路径 {path_index}", paths[path_index])], False


def _fit_segments(
    segments: List[Tuple[str, Path]],
    n_harmonics: int,
    samples: int,
    precision: int,
    skip_failures: bool,
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
                path, n_harmonics, samples, precision
            )
        except Exception as exc:
            if not skip_failures:
                raise
            print(f"警告：{label} 拟合失败：{exc}", file=sys.stderr)
            continue
        results.append((label, x_expr, y_expr, coord_expr))
    return results


def format_fourier_report(
    results: List[Tuple[str, str, str, str]],
    n_harmonics: int,
    samples: int,
    multi_format: bool,
) -> str:
    """把拟合结果渲染为报告文本。"""
    if not results:
        raise ValueError("没有成功拟合任何连续段")

    if not multi_format:
        label, x_expr, y_expr, coord_expr = results[0]
        return "\n".join(
            [
                f"{label} 傅里叶级数拟合 (谐波数 N={n_harmonics}, 采样点={samples}):",
                x_expr,
                y_expr,
                "t ∈ [0, 2π]",
                "",
                "可直接复制到 Desmos 的坐标表达式:",
                coord_expr,
            ]
        )

    blocks = [
        "\n".join(
            [
                f"{label} 傅里叶级数拟合 (谐波数 N={n_harmonics}, 采样点={samples}):",
                x_expr,
                y_expr,
                "t ∈ [0, 2π]",
                "",
                "---------------------",
            ]
        )
        for label, x_expr, y_expr, _ in results
    ]
    coord_lines = ["", "各连续段坐标表达式 (每行一条，按顺序对应上面各段):"]
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
) -> str:
    """
    构建傅里叶拟合报告文本（命令行与 GUI 共用）。

    fit_all=False 时只拟合 path_index 指定的路径；split=True 时先在不连续点
    处断开。参数非法时抛出 ValueError/IndexError，缺少 numpy 时抛出 ImportError。
    """
    if np is None:
        raise ImportError("傅里叶模式需要 numpy，请运行：pip install numpy")
    if precision is None:
        precision = PRECISION

    segments, multi_format = _select_fourier_segments(paths, fit_all, path_index, split)
    results = _fit_segments(
        segments,
        n_harmonics,
        samples,
        precision,
        skip_failures=multi_format,
    )
    return format_fourier_report(results, n_harmonics, samples, multi_format)


# --- 兼容旧接口的输出包装 ---


def fourier_fit_multiple_segments(
    segments: List[Tuple[str, Path]],
    n_harmonics: int,
    samples: int,
    precision: int,
    output_file: Optional[str] = None,
) -> None:
    """对多个连续段进行傅里叶拟合并输出（兼容旧接口）。"""
    results = _fit_segments(segments, n_harmonics, samples, precision, skip_failures=True)
    text = format_fourier_report(results, n_harmonics, samples, multi_format=True)
    _emit(text, output_file)


def output_fourier_result_single(
    path: Path,
    n_harmonics: int,
    path_idx: int,
    samples: int,
    precision: int,
    output_file: Optional[str] = None,
    split: bool = False,
) -> None:
    """输出单条路径的傅里叶拟合结果（兼容旧接口）。"""
    if split:
        subpaths = split_path_at_discontinuities(path)
        if len(subpaths) > 1:
            segments = [
                (f"路径{path_idx}段{sub_idx}", subpath)
                for sub_idx, subpath in enumerate(subpaths)
            ]
            results = _fit_segments(segments, n_harmonics, samples, precision, skip_failures=True)
            text = format_fourier_report(results, n_harmonics, samples, multi_format=True)
            _emit(text, output_file)
            return

    _output_single_segment(path, path_idx, n_harmonics, samples, precision, output_file)


def _output_single_segment(
    path: Path,
    label: Union[int, str],
    n_harmonics: int,
    samples: int,
    precision: int,
    output_file: Optional[str],
) -> None:
    """输出单个连续段的拟合结果（不分段，不聚合）。"""
    try:
        x_expr, y_expr, coord_expr = fourier_fit_path(path, n_harmonics, samples, precision)
    except ImportError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        sys.exit(1)

    label_str = f"路径 {label}" if isinstance(label, int) else str(label)
    text = "\n".join(
        [
            f"{label_str} 傅里叶级数拟合 (谐波数 N={n_harmonics}, 采样点={samples}):",
            x_expr,
            y_expr,
            "t ∈ [0, 2π]",
            "",
            "可直接复制到 Desmos 的坐标表达式:",
            coord_expr,
        ]
    )
    _emit(text, output_file)


def _emit(text: str, output_file: Optional[str]) -> None:
    """统一的输出写入：写文件或打印到标准输出。"""
    if output_file:
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"输出已写入 {output_file}")
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
    parser.add_argument("--path-index", type=int, default=0,
                        help="傅里叶模式下选择的路径索引（默认 0）")
    parser.add_argument("--fit-all-paths", action="store_true",
                        help="傅里叶模式下对所有路径分别拟合（覆盖 --path-index 设置）")
    parser.add_argument("--split-discontinuities", action="store_true",
                        help="傅里叶模式下自动将路径内部的不连续点断开，分别拟合每个连续段")
    parser.add_argument("--samples", type=int, default=1000,
                        help="傅里叶模式下的采样点数（默认 1000）")
    parser.add_argument("--precision", type=int, default=4,
                        help="输出表达式的小数精度（默认 4）")
    parser.add_argument("--flip-y", action="store_true", default=True,
                        help="翻转 Y 坐标（适用于 Desmos 等数学坐标系，默认开启）")
    parser.add_argument("--no-flip-y", dest="flip_y", action="store_false",
                        help="禁用 Y 轴翻转，保留 SVG 原始坐标系")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not 0 <= args.precision <= 10:
        parser.error("--precision 必须在 0 到 10 之间")
    if args.samples < 2:
        parser.error("--samples 必须 >= 2")
    if args.fourier is not None and args.fourier < 1:
        parser.error("--fourier 谐波次数必须 >= 1")
    if args.path_index < 0:
        parser.error("--path-index 不能为负数")

    # 读取 SVG
    try:
        paths, _ = svg2paths(args.input)
    except Exception as exc:
        print(f"读取 SVG 时出错：{exc}", file=sys.stderr)
        return 1

    if not paths:
        print("未找到任何路径", file=sys.stderr)
        return 1

    # 应用 Y 轴翻转（如果需要）
    if args.flip_y:
        paths = [transform_path(p, flip_y=True) for p in paths]
        print("已应用 Y 轴翻转 (SVG → 数学坐标系)", file=sys.stderr)

    try:
        if args.fourier is not None:
            text = build_fourier_report(
                paths,
                n_harmonics=args.fourier,
                samples=args.samples,
                precision=args.precision,
                fit_all=args.fit_all_paths,
                path_index=args.path_index,
                split=args.split_discontinuities,
            )
        else:
            text = build_segment_report(paths, precision=args.precision)
    except (ValueError, IndexError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    _emit(text, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
