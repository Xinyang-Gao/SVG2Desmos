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

依赖项：svgpathtools, numpy（傅里叶模式需要）
用法：python svg_to_function.py input.svg [-o output.txt] [--fourier N] [--path-index idx] [--fit-all-paths] [--split-discontinuities] [--samples N] [--precision N]
"""
import argparse
import sys
from typing import List, Tuple, Union, Optional
import math

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

# 全局精度设置
PRECISION = 4


# --- Helper Functions for Expression Generation ---


def _format_coefficient(coeff: float) -> str:
    """格式化系数，使用全局精度"""
    return f"{coeff:.{PRECISION}f}".rstrip('0').rstrip('.')


def _line_to_expression(seg: Line, seg_index: int) -> Tuple[str, str]:
    start = seg.start
    end = seg.end
    dx = end.real - start.real
    dy = end.imag - start.imag

    x_expr = f"{_format_coefficient(start.real)} + ({_format_coefficient(dx)})*t"
    y_expr = f"{_format_coefficient(start.imag)} + ({_format_coefficient(dy)})*t"

    main_parts = [
        f"第 {seg_index} 段(线段):",
        f" x(t) = {x_expr}",
        f" y(t) = {y_expr}",
        " t ∈ [0, 1]",
        "--------------------",
    ]
    return "\n".join(main_parts), f"({x_expr}, {y_expr})"


def _quadratic_bezier_to_expression(
    seg: QuadraticBezier, seg_index: int
) -> Tuple[str, str]:
    start = seg.start
    control = seg.control
    end = seg.end

    x_expr = (
        f"(1-t)^2*{_format_coefficient(start.real)} + "
        f"2*(1-t)*t*{_format_coefficient(control.real)} + "
        f"t^2*{_format_coefficient(end.real)}"
    )
    y_expr = (
        f"(1-t)^2*{_format_coefficient(start.imag)} + "
        f"2*(1-t)*t*{_format_coefficient(control.imag)} + "
        f"t^2*{_format_coefficient(end.imag)}"
    )

    main_parts = [
        f"第 {seg_index} 段(二次贝塞尔):",
        f" x(t) = {x_expr}",
        f" y(t) = {y_expr}",
        " t ∈ [0, 1]",
        "--------------------",
    ]
    return "\n".join(main_parts), f"({x_expr}, {y_expr})"


def _cubic_bezier_to_expression(seg: CubicBezier, seg_index: int) -> Tuple[str, str]:
    start = seg.start
    c1 = seg.control1
    c2 = seg.control2
    end = seg.end

    x_expr = (
        f"(1-t)^3*{_format_coefficient(start.real)} + "
        f"3*(1-t)^2*t*{_format_coefficient(c1.real)} + "
        f"3*(1-t)*t^2*{_format_coefficient(c2.real)} + "
        f"t^3*{_format_coefficient(end.real)}"
    )
    y_expr = (
        f"(1-t)^3*{_format_coefficient(start.imag)} + "
        f"3*(1-t)^2*t*{_format_coefficient(c1.imag)} + "
        f"3*(1-t)*t^2*{_format_coefficient(c2.imag)} + "
        f"t^3*{_format_coefficient(end.imag)}"
    )

    main_parts = [
        f"第 {seg_index} 段(三次贝塞尔):",
        f" x(t) = {x_expr}",
        f" y(t) = {y_expr}",
        " t ∈ [0, 1]",
        "--------------------",
    ]
    return "\n".join(main_parts), f"({x_expr}, {y_expr})"


def _arc_to_expression(seg: Arc, seg_index: int) -> Tuple[str, str]:
    """
    将 SVG 椭圆弧转换为精确参数方程。
    使用中心点参数化方法。
    """
    # 提取弧参数
    start = seg.start
    end = seg.end
    radius = seg.radius
    rotation = seg.rotation  # 弧度
    large_arc = seg.large_arc
    sweep = seg.sweep

    rx, ry = radius.real, radius.imag
    if rx == 0 or ry == 0:
        # 退化情况：直线
        return _line_to_expression(Line(start, end), seg_index)

    phi = rotation

    # 辅助函数：将点从椭圆坐标系变换到笛卡尔坐标系
    def transform_point(xe, ye):
        cos_phi = math.cos(phi)
        sin_phi = math.sin(phi)
        x = cos_phi * xe - sin_phi * ye
        y = sin_phi * xe + cos_phi * ye
        return x, y

    # 计算中心点 (cx, cy) 和起始/结束角度
    # 参考 SVG 规范：https://www.w3.org/TR/SVG/implnote.html#ArcImplementationNotes
    x1, y1 = start.real, start.imag
    x2, y2 = end.real, end.imag

    # 中点转换到椭圆坐标系
    dx = (x1 - x2) / 2.0
    dy = (y1 - y2) / 2.0
    cos_phi = math.cos(phi)
    sin_phi = math.sin(phi)
    x1p = cos_phi * dx + sin_phi * dy
    y1p = -sin_phi * dx + cos_phi * dy

    # 确保 rx, ry 足够大
    lambda_val = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lambda_val > 1:
        sqrt_lambda = math.sqrt(lambda_val)
        rx *= sqrt_lambda
        ry *= sqrt_lambda

    # 计算中心点
    sign = 1 if large_arc == sweep else -1
    numerator = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    if numerator < 0:
        numerator = 0
    denominator = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    if denominator == 0:
        denominator = 1e-12
    factor = sign * math.sqrt(numerator / denominator)
    cxp = factor * (rx * y1p / ry)
    cyp = factor * (-ry * x1p / rx)

    # 中心点的全局坐标
    cx = cos_phi * cxp - sin_phi * cyp + (x1 + x2) / 2.0
    cy = sin_phi * cxp + cos_phi * cyp + (y1 + y2) / 2.0

    # 起始角和终止角
    def angle(u, v):
        dot = u[0] * v[0] + u[1] * v[1]
        det = u[0] * v[1] - u[1] * v[0]
        return math.atan2(det, dot)

    ux = (x1p - cxp) / rx
    uy = (y1p - cyp) / ry
    vx = (-x1p - cxp) / rx
    vy = (-y1p - cyp) / ry

    theta1 = angle((1, 0), (ux, uy))
    delta_theta = angle((ux, uy), (vx, vy)) % (2 * math.pi)
    if not sweep and delta_theta > 0:
        delta_theta -= 2 * math.pi
    elif sweep and delta_theta < 0:
        delta_theta += 2 * math.pi

    theta2 = theta1 + delta_theta

    # 生成参数方程：t ∈ [0,1] 映射到角度 [theta1, theta2]
    # 参数化表达式使用中心点 + 椭圆旋转
    # x(t) = cx + rx * cos(phi) * cos(angle(t)) - ry * sin(phi) * sin(angle(t))
    # y(t) = cy + rx * sin(phi) * cos(angle(t)) + ry * cos(phi) * sin(angle(t))
    # 其中 angle(t) = theta1 + t * delta_theta

    t_expr = "t"
    angle_expr = f"({_format_coefficient(theta1)} + t*{_format_coefficient(delta_theta)})"
    cos_angle = f"cos({angle_expr})"
    sin_angle = f"sin({angle_expr})"
    cos_phi_f = _format_coefficient(math.cos(phi))
    sin_phi_f = _format_coefficient(math.sin(phi))
    rx_f = _format_coefficient(rx)
    ry_f = _format_coefficient(ry)
    cx_f = _format_coefficient(cx)
    cy_f = _format_coefficient(cy)

    x_expr = f"{cx_f} + {rx_f}*{cos_phi_f}*{cos_angle} - {ry_f}*{sin_phi_f}*{sin_angle}"
    y_expr = f"{cy_f} + {rx_f}*{sin_phi_f}*{cos_angle} + {ry_f}*{cos_phi_f}*{sin_angle}"

    main_parts = [
        f"第 {seg_index} 段(椭圆弧):",
        f" x(t) = {x_expr}",
        f" y(t) = {y_expr}",
        " t ∈ [0, 1]",
        "--------------------",
    ]
    return "\n".join(main_parts), f"({x_expr}, {y_expr})"


# --- Main Processing Logic for Segments ---

SegmentType = Union[Line, QuadraticBezier, CubicBezier, Arc]


def segment_to_expression(seg: SegmentType, seg_index: int) -> Tuple[str, str]:
    """
    返回该线段参数方程的主要描述字符串和紧凑坐标表达式字符串。
    返回格式: (main_str, coord_str)
    """
    if isinstance(seg, Line):
        return _line_to_expression(seg, seg_index)
    elif isinstance(seg, QuadraticBezier):
        return _quadratic_bezier_to_expression(seg, seg_index)
    elif isinstance(seg, CubicBezier):
        return _cubic_bezier_to_expression(seg, seg_index)
    elif isinstance(seg, Arc):
        return _arc_to_expression(seg, seg_index)
    else:
        main_str = f"第 {seg_index} 段(未知类型):\n {seg}\n--------------------"
        coord_str = "Unknown segment type"
        return main_str, coord_str


def process_paths(paths: List[Path]) -> Tuple[List[str], List[str]]:
    """处理路径列表，返回主输出和坐标表达式列表"""
    output_lines: List[str] = []
    coordinate_expressions: List[str] = []

    for path_idx, path in enumerate(paths):
        output_lines.append(f"路径 {path_idx}:")
        for seg_idx, seg in enumerate(path):
            main_str, coord_str = segment_to_expression(seg, seg_idx)
            output_lines.append(main_str)
            coordinate_expressions.append(coord_str)

        output_lines.append("")  # 路径之间空行

    return output_lines, coordinate_expressions


# --- Path Splitting at Discontinuities ---


def split_path_at_discontinuities(path: Path, tolerance: float = 1e-6) -> List[Path]:
    """
    将一个路径按照线段之间的不连续点（跳跃）分割成多个连续的子路径。
    返回子路径列表，每个子路径内的线段首尾相连（连续）。
    """
    if not path:
        return []
    
    subpaths = []
    current_segments = []
    
    # 第一个线段直接加入
    current_segments.append(path[0])
    
    for i in range(1, len(path)):
        prev_seg = path[i-1]
        curr_seg = path[i]
        # 检查当前线段的起点与上一线段的终点是否连续
        if abs(curr_seg.start - prev_seg.end) > tolerance:
            # 不连续，结束当前子路径并开始新子路径
            if current_segments:
                subpaths.append(Path(*current_segments))
            current_segments = [curr_seg]
        else:
            current_segments.append(curr_seg)
    
    if current_segments:
        subpaths.append(Path(*current_segments))
    
    return subpaths


def collect_continuous_segments(paths: List[Path], split: bool, tolerance: float = 1e-6) -> List[Tuple[str, Path]]:
    """
    根据是否分割，将输入路径列表转换为连续段列表。
    返回列表，每个元素为 (label, path)，label 如 "路径0" 或 "路径0段1"。
    """
    segments = []
    for path_idx, path in enumerate(paths):
        if split:
            subpaths = split_path_at_discontinuities(path, tolerance)
            if len(subpaths) == 1:
                # 无间断，仍标记为单一段
                segments.append((f"路径{path_idx}", subpaths[0]))
            else:
                for sub_idx, subpath in enumerate(subpaths):
                    segments.append((f"路径{path_idx}段{sub_idx}", subpath))
        else:
            segments.append((f"路径{path_idx}", path))
    return segments


# --- Fourier Fitting Functions ---


def is_path_closed(path: Path, tolerance: float = 1e-6) -> bool:
    """检查路径是否闭合（起点与终点距离小于容差）"""
    if not path:
        return False
    start = path[0].start
    end = path[-1].end
    return abs(start - end) < tolerance


def sample_path_points(path: Path, num_samples: int = 500) -> Tuple[np.ndarray, np.ndarray]:
    """
    沿路径均匀采样参数 t ∈ [0, 1)，返回 x 和 y 坐标数组。
    采样点不包括终点（终点与起点重合，用于周期函数）。
    """
    t_values = np.linspace(0, 1, num_samples, endpoint=False)
    points = [path.point(t) for t in t_values]
    x_vals = np.array([p.real for p in points])
    y_vals = np.array([p.imag for p in points])
    return x_vals, y_vals


def compute_fourier_coeffs(values: np.ndarray, n_harmonics: int) -> Tuple[float, List[float], List[float]]:
    """
    对实序列 values 计算傅里叶级数系数。
    返回 (a0, a_list, b_list)，其中 a_list 和 b_list 长度为 n_harmonics。
    重建公式: f(t) = a0/2 + Σ_{n=1}^{N} (a_n cos(n t) + b_n sin(n t))
    t ∈ [0, 2π)
    """
    N = len(values)
    # 使用 rfft 得到正频率系数
    coeffs = np.fft.rfft(values) / N
    
    a0 = 2 * coeffs[0].real  # 直流分量乘以2，使得 a0/2 为直流平均值
    
    a = []
    b = []
    for n in range(1, n_harmonics + 1):
        if n < len(coeffs):
            c = coeffs[n]
            a_n = 2 * c.real
            b_n = -2 * c.imag
        else:
            a_n = 0.0
            b_n = 0.0
        a.append(a_n)
        b.append(b_n)
    
    return a0, a, b


def format_fourier_series(prefix: str, a0: float, a_list: List[float], b_list: List[float], 
                           var: str = "t", precision: int = 4) -> str:
    """
    将傅里叶系数格式化为 Desmos 可读的表达式字符串。
    prefix: 表达式开头，例如 "x(t) = "
    """
    def fmt(x: float) -> str:
        return f"{x:.{precision}f}".rstrip('0').rstrip('.')
    
    terms = []
    # 直流项 a0/2
    dc = a0 / 2
    if abs(dc) > 1e-8:
        terms.append(fmt(dc))
    
    for n, (a_n, b_n) in enumerate(zip(a_list, b_list), start=1):
        if abs(a_n) < 1e-8 and abs(b_n) < 1e-8:
            continue
        term_parts = []
        if abs(a_n) > 1e-8:
            term_parts.append(f"{fmt(a_n)} * cos({n} {var})")
        if abs(b_n) > 1e-8:
            sign = "+" if b_n >= 0 else "-"
            term_parts.append(f"{sign} {fmt(abs(b_n))} * sin({n} {var})")
        if term_parts:
            # 合并同一谐波项
            combined = term_parts[0]
            for part in term_parts[1:]:
                combined += " " + part
            terms.append(combined)
    
    if not terms:
        return prefix + "0"
    
    expr = " + ".join(terms).replace("+ -", "- ")
    return prefix + expr


def fourier_fit_path(path: Path, n_harmonics: int = 5, num_samples: int = 1000, precision: int = 4) -> Tuple[str, str, str]:
    """
    对路径进行傅里叶级数拟合，返回 (x_expr, y_expr, coord_expr) 字符串，
    可直接复制到 Desmos 中使用。
    """
    if np is None:
        raise ImportError("傅里叶模式需要 numpy，请运行：pip install numpy")
    
    # Nyquist 检查
    if n_harmonics >= num_samples / 2:
        print(f"警告：谐波数 {n_harmonics} 大于等于采样点数 {num_samples} 的一半，可能发生混叠。建议增加采样点数或减少谐波数。", file=sys.stderr)
    
    # 采样路径点
    x_vals, y_vals = sample_path_points(path, num_samples)
    
    # 计算傅里叶系数
    a0_x, a_x, b_x = compute_fourier_coeffs(x_vals, n_harmonics)
    a0_y, a_y, b_y = compute_fourier_coeffs(y_vals, n_harmonics)
    
    # 生成表达式
    x_expr = format_fourier_series("x(t) = ", a0_x, a_x, b_x, "t", precision)
    y_expr = format_fourier_series("y(t) = ", a0_y, a_y, b_y, "t", precision)
    
    # 提取纯表达式部分（不带 "x(t) = " 前缀）用于紧凑坐标格式
    x_pure = x_expr.split("= ", 1)[1]
    y_pure = y_expr.split("= ", 1)[1]
    
    return x_expr, y_expr, f"({x_pure}, {y_pure})"


def fourier_fit_multiple_segments(segments: List[Tuple[str, Path]], n_harmonics: int, samples: int,
                                   precision: int, output_file: Optional[str] = None) -> None:
    """
    对多个连续段进行傅里叶拟合，输出详细信息并在末尾聚合坐标表达式。
    segments: 列表，每个元素为 (label, path)，label 如 "路径0" 或 "路径0段1"
    """
    results = []  # 每个元素为 (label, x_expr, y_expr, coord_expr)
    
    for label, path in segments:
        try:
            x_expr, y_expr, coord_expr = fourier_fit_path(path, n_harmonics, samples, precision)
            results.append((label, x_expr, y_expr, coord_expr))
        except Exception as e:
            print(f"警告：{label} 拟合失败：{e}", file=sys.stderr)
            continue
    
    if not results:
        print("没有成功拟合任何连续段", file=sys.stderr)
        return
    
    # 构建输出字符串
    output_parts = []
    
    # 详细块
    for label, x_expr, y_expr, coord_expr in results:
        block = [
            f"{label} 傅里叶级数拟合 (谐波数 N={n_harmonics}, 采样点={samples}):",
            x_expr,
            y_expr,
            "t ∈ [0, 2π]",
            "",
            "---------------------",
        ]
        output_parts.append("\n".join(block))
    
    # 聚合坐标表达式（类似分段模式的末尾汇总）
    coord_lines = ["", "各连续段坐标表达式 (每行一条，按顺序对应上面各段):"]
    for _, _, _, coord_expr in results:
        coord_lines.append(coord_expr)
    output_parts.append("\n".join(coord_lines))
    
    full_output = "\n".join(output_parts)
    
    if output_file:
        with open(output_file, "w", encoding='utf-8') as f:
            f.write(full_output)
        print(f"输出已写入 {output_file}")
    else:
        print(full_output)


def output_fourier_result_single(path: Path, n_harmonics: int, path_idx: int, 
                                 samples: int, precision: int, output_file: Optional[str] = None,
                                 split: bool = False) -> None:
    """
    输出单条路径的傅里叶拟合结果。
    如果 split=True，则先分割该路径为连续段，然后对每段拟合（类似多段模式）。
    如果 split=False，则整体拟合并直接输出。
    """
    if split:
        subpaths = split_path_at_discontinuities(path)
        if len(subpaths) == 1:
            # 实际没有断开，仍按单段处理
            _output_single_segment(path, path_idx, n_harmonics, samples, precision, output_file)
        else:
            segments = [(f"路径{path_idx}段{sub_idx}", subpath) for sub_idx, subpath in enumerate(subpaths)]
            fourier_fit_multiple_segments(segments, n_harmonics, samples, precision, output_file)
    else:
        _output_single_segment(path, path_idx, n_harmonics, samples, precision, output_file)


def _output_single_segment(path: Path, label: Union[int, str], n_harmonics: int, samples: int, precision: int, output_file: Optional[str]) -> None:
    """内部函数：输出单个连续段的拟合结果（不分段，不聚合）"""
    try:
        x_expr, y_expr, coord_expr = fourier_fit_path(path, n_harmonics, samples, precision)
    except ImportError as e:
        print(f"错误：{e}", file=sys.stderr)
        sys.exit(1)
    
    if isinstance(label, int):
        label_str = f"路径 {label}"
    else:
        label_str = str(label)
    
    lines = [
        f"{label_str} 傅里叶级数拟合 (谐波数 N={n_harmonics}, 采样点={samples}):",
        x_expr,
        y_expr,
        "t ∈ [0, 2π]",
        "",
        "可直接复制到 Desmos 的坐标表达式:",
        coord_expr,
    ]
    output_text = "\n".join(lines)
    
    if output_file:
        with open(output_file, "w", encoding='utf-8') as f:
            f.write(output_text)
        print(f"输出已写入 {output_file}")
    else:
        print(output_text)


# --- Main ---


def main():
    parser = argparse.ArgumentParser(
        description="将 SVG 路径转换为数学参数方程，支持分段表达式或傅里叶级数拟合"
    )
    parser.add_argument("input", help="输入 SVG 文件")
    parser.add_argument("-o", "--output", help="输出文件（默认: 标准输出）")
    parser.add_argument("--fourier", type=int, nargs='?', const=5, default=None,
                        help="使用傅里叶级数拟合，可选指定谐波次数（默认 5）")
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
    args = parser.parse_args()
    
    # 设置全局精度
    global PRECISION
    PRECISION = args.precision
    
    # 读取 SVG
    try:
        paths, attributes = svg2paths(args.input)
    except Exception as e:
        print(f"读取 SVG 时出错：{e}", file=sys.stderr)
        sys.exit(1)
    
    if not paths:
        print("未找到任何路径", file=sys.stderr)
        sys.exit(1)
    
    # 傅里叶模式
    if args.fourier is not None:
        if args.fit_all_paths:
            # 收集所有连续段
            segments = collect_continuous_segments(paths, args.split_discontinuities)
            fourier_fit_multiple_segments(segments, args.fourier, args.samples, args.precision, args.output)
        else:
            # 单路径模式
            if args.path_index >= len(paths):
                print(f"错误：路径索引 {args.path_index} 超出范围（共 {len(paths)} 条路径）", file=sys.stderr)
                sys.exit(1)
            output_fourier_result_single(
                paths[args.path_index], args.fourier, args.path_index, args.samples,
                args.precision, output_file=args.output, split=args.split_discontinuities
            )
        return
    
    # 原有分段模式
    output_lines, coordinate_expressions = process_paths(paths)
    
    if not paths or all(len(p) == 0 for p in paths):
        output_lines = ["未找到路径段"]
    
    if coordinate_expressions:
        output_lines.extend(["", "各线段坐标表达式 (每行一条):"])
        output_lines.extend(coordinate_expressions)
    
    output_text = "\n".join(output_lines)
    
    if args.output:
        with open(args.output, "w", encoding='utf-8') as f:
            f.write(output_text)
        print(f"输出已写入 {args.output}")
    else:
        print(output_text)


if __name__ == "__main__":
    main()