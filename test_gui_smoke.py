# -*- coding: utf-8 -*-
"""
GUI 自动化冒烟测试。

用法: python test_gui_smoke.py

不显示窗口地构建界面，驱动真实控件（路径列表、预览、分段/傅里叶两种模式的
后台生成、剪贴板等），输出通过/失败统计；全部通过时退出码为 0。
优先使用仓库内的 sprite.svg，缺失时自动创建临时 SVG 夹具。
"""
import os
import sys
import tempfile
import time
import tkinter as tk

import svg_to_function_gui as gui
from svgpathtools import svg2paths

# sprite.svg 缺失时的备选夹具（3 条路径：闭合矩形 / 开放曲线 / 圆形弧）
_FIXTURE_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40">
  <path d="M5 5 L35 5 L35 35 L5 35 Z"/>
  <path d="M5 20 C 10 5, 30 5, 35 20"/>
  <path d="M10 30 A 8 8 0 1 1 26 30 A 8 8 0 1 1 10 30"/>
</svg>"""

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f" :: {detail}"))


def fixture_path():
    """返回 (SVG 路径, 结束后需删除的临时路径或 None)。"""
    if os.path.exists("sprite.svg"):
        return "sprite.svg", None
    fd, path = tempfile.mkstemp(suffix=".svg", prefix="svg2desmos_fixture_")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(_FIXTURE_SVG)
    return path, path


def pump(root, predicate, timeout=60.0):
    """驱动 Tk 事件循环直到 predicate() 为真或超时。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        root.update()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def main():
    svg_file, temp_svg = fixture_path()
    root = tk.Tk()
    root.withdraw()
    app = gui.SVGMathGUI(root)

    # 1) 预加载文件（绕过文件对话框）
    paths, _ = svg2paths(svg_file)
    app.paths = paths
    app.filepath = svg_file
    app.file_label.config(text=os.path.basename(svg_file))
    app._refresh_path_list()
    check("路径列表条目数", len(app.path_tree.get_children()) == len(paths))
    check("路径索引 spinbox 上限", str(app.path_spinbox.cget("to")) == str(len(paths) - 1),
          str(app.path_spinbox.cget("to")))

    # 2) 预览绘制（withdraw 状态下 winfo_width 为 1，打桩为固定尺寸）
    app.preview_canvas.winfo_width = lambda: 500
    app.preview_canvas.winfo_height = lambda: 300
    app._draw_preview()
    items = app.preview_canvas.find_all()
    check("预览画布有绘制内容", len(items) > 0, f"items={len(items)}")

    # 点击路径 1 -> 高亮切换
    app.path_tree.selection_set("1")
    app.path_tree.event_generate("<<TreeviewSelect>>")
    root.update()
    check("树选择同步到索引控件", app.selected_path_idx.get() == 1, app.selected_path_idx.get())
    app._draw_preview()
    items_sel = app.preview_canvas.find_all()
    check("重绘后仍有内容", len(items_sel) > 0)

    # 3) 分段模式生成
    app.current_mode.set("segments")
    app._update_control_states()
    app.clear_output()
    app.generate()
    ok = pump(root, lambda: not app._busy and bool(app._output_content()), timeout=60)
    text = app._output_content()
    check("分段模式生成完成", ok, text[:80])
    check("分段输出含坐标汇总", "各线段坐标表达式" in text)
    check("分段输出含段落标记", "第 0 段(" in text)

    # 4) 傅里叶模式：拟合所有路径
    app.current_mode.set("fourier")
    app.fit_all_paths.set(True)
    app._update_control_states()
    check("拟合所有路径时索引禁用", str(app.path_spinbox.cget("state")) == "disabled",
          str(app.path_spinbox.cget("state")))
    app.clear_output()
    app.generate()
    ok = pump(root, lambda: not app._busy and "傅里叶级数拟合" in app._output_content(), timeout=120)
    text = app._output_content()
    check("傅里叶全路径拟合完成", ok, text[:120])
    check("傅里叶输出含聚合坐标", "各连续段坐标表达式" in text)
    check("傅里叶输出无双重符号", "+ +" not in text and "-  " not in text, text[:200])

    # 5) 傅里叶单路径 + 分割不连续点
    app.fit_all_paths.set(False)
    app.split_discont.set(True)
    app.selected_path_idx.set(1)
    app.clear_output()
    app.generate()
    ok = pump(root, lambda: not app._busy and "傅里叶级数拟合" in app._output_content(), timeout=120)
    check("傅里叶单路径分割拟合完成", ok, app._output_content()[:120])

    # 6) 参数健壮性：非法输入回退默认值
    app.precision.set(-3)  # IntVar 允许，读取时夹取
    check("精度夹取", app._read_int(app.precision, 4, 0, 10) == 0,
          app._read_int(app.precision, 4, 0, 10))

    # 7) 空路径保护（打桩 messagebox，避免弹窗阻塞测试）
    warned = []
    original_warning = gui.messagebox.showwarning
    gui.messagebox.showwarning = lambda title, msg: warned.append(msg)
    try:
        app.paths = []
        app.generate()
        root.update()
    finally:
        gui.messagebox.showwarning = original_warning
    check("无路径时给出警告而非异常", not app._busy and len(warned) == 1, str(warned))

    # 8) 清空 / 复制
    app.paths = paths
    app.clear_output()
    check("清空输出", app._output_content() == "")
    app._set_output("hello")
    app.copy_output()
    root.update()
    try:
        clip = root.clipboard_get()
    except Exception as exc:
        clip = f"<clipboard error: {exc}>"
    check("复制到剪贴板", clip == "hello", clip)

    root.destroy()
    if temp_svg:
        try:
            os.remove(temp_svg)
        except OSError:
            pass
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
