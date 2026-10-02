# -*- coding: utf-8 -*-
"""
核心算法正确性测试（无需显示器）。

用法: python test_core_math.py

把生成的参数方程/傅里叶级数重新求值，与 svgpathtools 自身的 path.point(t)
对比，覆盖直线、二次/三次贝塞尔、椭圆弧（含旋转与非等半径）以及 Y 翻转。
"""
import math
import os
import re
import sys
import tempfile

from svgpathtools import svg2paths

import svg_to_function as core

# 覆盖所有线段类型，且弧带旋转角与不等半径
_FIXTURE = """<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
  <path d="M0 0 L10 0 L10 10 Z"/>
  <path d="M0 20 C 5 10, 15 10, 20 20 S 30 30, 20 20 Q 15 25 10 20 T 0 20"/>
  <path d="M20 40 A 8 12 35 1 0 40 60 A 8 12 35 1 0 20 40 Z"/>
  <path d="M10 0 A 10 10 0 1 1 -10 0 A 10 10 0 1 1 10 0 Z"/>
</svg>"""

_T_SAMPLES = (0.0, 0.17, 0.33, 0.5, 0.67, 0.83, 1.0)
failures = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f" :: {detail}"))
    if not cond:
        failures.append(name)


def to_python(expr: str) -> str:
    """把 Desmos 表达式转成可 eval 的 Python：^ -> **，'2 t' -> '2*t'。"""
    expr = expr.replace("^", "**")
    return re.sub(r"(\d)\s+(t)\b", r"\1*\2", expr)


def evaluate(expr: str, t: float) -> float:
    return float(
        eval(to_python(expr), {"__builtins__": {}},  # noqa: S307
             {"t": t, "cos": math.cos, "sin": math.sin})
    )


def load_fixture():
    fd, path = tempfile.mkstemp(suffix=".svg")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(_FIXTURE)
    paths, _ = svg2paths(path)
    os.unlink(path)
    return paths


def test_segment_expressions():
    """每种线段的 (x(t), y(t)) 在若干 t 处应与 svgpathtools 的采样一致。"""
    for flip in (False, True):
        paths = [core.transform_path(p, flip_y=flip) for p in load_fixture()]
        worst = 0.0
        kinds = set()
        for path in paths:
            for seg in path:
                kinds.add(type(seg).__name__)
                x_expr, y_expr = core._segment_expressions(seg, precision=10)
                for t in _T_SAMPLES:
                    expected = seg.point(t)
                    worst = max(
                        worst,
                        abs(evaluate(x_expr, t) - expected.real),
                        abs(evaluate(y_expr, t) - expected.imag),
                    )
        check(f"线段表达式求值一致 (flip_y={flip})", worst < 1e-6,
              f"max dev={worst:.3e}, kinds={sorted(kinds)}")


def test_flip_is_exact_mirror():
    """翻转必须是关于 x 轴的精确镜像（含旋转椭圆弧）。"""
    for orig, flipped in zip(load_fixture(), [core.transform_path(p, True) for p in load_fixture()]):
        for t in _T_SAMPLES:
            a, b = orig.point(t), flipped.point(t)
            if abs(a.conjugate() - b) > 1e-9:
                check("Y 翻转为精确镜像", False, f"t={t}: {a} -> {b}")
                return
    check("Y 翻转为精确镜像", True)


def test_flip_off_is_identity():
    paths = load_fixture()
    ok = all(core.transform_path(p, flip_y=False) is p for p in paths)
    check("flip_y=False 不改变路径", ok)


def test_segment_report():
    paths = [core.transform_path(p, flip_y=True) for p in load_fixture()]
    report = core.build_segment_report(paths, precision=4)
    check("分段报告含各类线段", all(
        tag in report for tag in ("线段)", "二次贝塞尔)", "三次贝塞尔)", "椭圆弧)")
    ), report[:120])
    check("分段报告含坐标汇总", "各线段坐标表达式 (每行一条):" in report)
    check("空路径报告", core.build_segment_report([]) == "未找到路径段")


def test_fourier_report():
    paths = [core.transform_path(p, flip_y=True) for p in load_fixture()]
    report = core.build_fourier_report(
        paths, n_harmonics=8, samples=2000, precision=8, fit_all=True
    )
    check("傅里叶报告无非法记号", "+ +" not in report and "-  " not in report, report[:200])
    check("傅里叶报告含聚合坐标", "各连续段坐标表达式" in report)

    # 单路径（圆形）拟合后应能高精度重建
    circle = core.transform_path(load_fixture()[3], flip_y=True)
    x_expr, y_expr, _ = core.fourier_fit_path(circle, n_harmonics=6, num_samples=2000, precision=8)
    x_pure = x_expr.split("= ", 1)[1]
    y_pure = y_expr.split("= ", 1)[1]
    worst = 0.0
    for frac in (0.0, 0.13, 0.37, 0.5, 0.71, 0.9):
        angle = frac * 2 * math.pi
        expected = circle.point(frac)
        worst = max(
            worst,
            abs(evaluate(x_pure, angle) - expected.real),
            abs(evaluate(y_pure, angle) - expected.imag),
        )
    check("傅里叶级数可重建圆形路径", worst < 1e-4, f"max dev={worst:.3e}")


def test_discontinuity_split():
    paths = load_fixture()
    # 人为构造不连续路径：把两条不同路径的线段拼在一起
    from svgpathtools import Path
    mixed = Path(*list(paths[0]), *list(paths[2]))
    parts = core.split_path_at_discontinuities(mixed)
    check("不连续点被切分", len(parts) == 2, f"{len(parts)} 段")
    check("连续路径不被切分", len(core.split_path_at_discontinuities(paths[0])) == 1)


def test_invalid_params():
    circle = load_fixture()[3]
    for kwargs in ({"n_harmonics": 0}, {"num_samples": 1}):
        try:
            core.fourier_fit_path(circle, **kwargs)
            check(f"非法参数 {sorted(kwargs)} 抛出异常", False)
        except ValueError:
            check(f"非法参数 {sorted(kwargs)} 抛出异常", True)
    try:
        core.build_fourier_report(load_fixture(), path_index=99, fit_all=False)
        check("越界路径索引抛出异常", False)
    except ValueError:
        check("越界路径索引抛出异常", True)


def main() -> int:
    test_segment_expressions()
    test_flip_is_exact_mirror()
    test_flip_off_is_identity()
    test_segment_report()
    test_fourier_report()
    test_discontinuity_split()
    test_invalid_params()
    print(f"\n{'ALL PASS' if not failures else 'FAILURES: ' + ', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
