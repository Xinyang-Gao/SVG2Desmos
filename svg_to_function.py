#!/usr/bin/env python3
"""
将 SVG 路径转换为数学表达式（参数方程）

该脚本读取一个 SVG 文件，提取所有路径，并对每个线段（直线、二次/三次 Bézier 曲线）
输出相应的参数方程，参数 t ∈ [0, 1]。所有线段的紧凑坐标表达式 (x(t), y(t)) 
会集中显示在输出末尾，每行一条。

依赖项：svgpathtools（pip install svgpathtools）
用法：python svg_to_function.py input.svg [-o output.txt]
"""
import argparse
import sys
from typing import List, Tuple, Union

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


# --- Helper Functions for Expression Generation ---
# 将每个几何体类型的处理逻辑分离出来


def _format_coefficient(coeff: float) -> str:
    """格式化系数，去除不必要的小数点后零"""
    return f"{coeff:.4f}".rstrip('0').rstrip('.')


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
    # Arc 处理较为复杂，这里仍返回占位符，但可以提示用户。
    # 也可以考虑调用 seg.split_cubic() 等方法近似转换。
    placeholder_text = "椭圆弧——表达式复杂，建议使用数值方法或近似"
    main_parts = [
        f"第 {seg_index} 段(弧):",
        f" {placeholder_text}",
        "--------------------",
    ]
    return "\n".join(main_parts), "Arc: complex expression, not provided"


# --- Main Processing Logic ---

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


def main():
    parser = argparse.ArgumentParser(
        description="将 SVG 路径转换为数学参数方程"
    )
    parser.add_argument("input", help="输入 SVG 文件")
    parser.add_argument("-o", "--output", help="输出文件 （默认: 标准输出）")
    args = parser.parse_args()

    try:
        paths, attributes = svg2paths(args.input)
    except Exception as e:
        print(f"读取 SVG 时出错：{e}", file=sys.stderr)
        sys.exit(1)

    # 处理所有路径
    output_lines, coordinate_expressions = process_paths(paths)

    if not paths or all(len(p) == 0 for p in paths):
        output_lines = ["未找到路径段"]

    # 添加最终的坐标表达式汇总
    if coordinate_expressions:
        output_lines.extend(["", "各线段坐标表达式 (每行一条):"])
        output_lines.extend(coordinate_expressions)

    output_text = "\n".join(output_lines)

    if args.output:
        with open(args.output, "w", encoding='utf-8') as f:  # 明确指定编码
            f.write(output_text)
        print(f"输出已写入 {args.output}")
    else:
        print(output_text)


if __name__ == "__main__":
    main()