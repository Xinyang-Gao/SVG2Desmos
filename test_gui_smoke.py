# -*- coding: utf-8 -*-
"""
GUI 自动化冒烟测试。

用法: python test_gui_smoke.py

不显示窗口地构建界面，驱动真实控件（中英双语切换、路径多选、路径列表、预览、
分段/傅里叶两种模式的后台生成、剪贴板等），输出通过/失败统计；全部通过时
退出码为 0。优先使用仓库内的 sprite.svg，缺失时自动创建临时 SVG 夹具。
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


def select(app, root, *iids):
    """按给定 iid 设置树选择并派发事件（模拟 Ctrl/Shift 多选）。"""
    if iids:
        app.path_tree.selection_set(*iids)
    else:
        app.path_tree.selection_set()
    app.path_tree.event_generate("<<TreeviewSelect>>")
    root.update()


def main():
    svg_file, temp_svg = fixture_path()
    root = tk.Tk()
    root.withdraw()
    app = gui.SVGMathGUI(root, lang="zh")

    # 生成失败会弹模态错误框并阻塞测试，改为收集后统一断言
    errors = []
    original_error = gui.messagebox.showerror
    gui.messagebox.showerror = lambda title, msg: errors.append(f"{title}: {msg}")

    # ============ 1) 中英双语切换 ============
    check("构造时指定中文", app.lang == "zh", app.lang)
    check("中文生成按钮文案",
          app.generate_btn.cget("text") == "生成表达式  (F5)",
          app.generate_btn.cget("text"))
    menus = [w for w in root.children.values() if isinstance(w, tk.Menu)]
    check("菜单栏已创建", len(menus) == 1, f"{len(menus)}")
    menubar = menus[0] if menus else None
    if menubar is not None:
        check("中文菜单栏", menubar.entrycget(0, "label") == "文件",
              menubar.entrycget(0, "label"))

    check("切换到英文", app.set_language("en", persist=False) and app.lang == "en",
          app.lang)
    check("英文生成按钮文案",
          app.generate_btn.cget("text") == "Generate expressions  (F5)",
          app.generate_btn.cget("text"))
    check("英文列表表头", app.path_tree.heading("segs", "text") == "Segments",
          app.path_tree.heading("segs", "text"))
    if menubar is not None:
        check("英文菜单栏", menubar.entrycget(0, "label") == "File",
              menubar.entrycget(0, "label"))
        view_menu = menubar.nametowidget(menubar.entrycget(2, "menu"))
        check("英文视图菜单", menubar.entrycget(2, "label") == "View",
              menubar.entrycget(2, "label"))
        check("英文语言子菜单", view_menu.entrycget(0, "label") == "Language",
              view_menu.entrycget(0, "label"))
        lang_menu = view_menu.nametowidget(view_menu.entrycget(0, "menu"))
        check("语言单选项标签", lang_menu.entrycget(1, "label") == "English",
              lang_menu.entrycget(1, "label"))
    check("状态栏跟随语言", app.status_var.get() == "Ready", app.status_var.get())

    check("切回中文",
          app.set_language("zh", persist=False) and app.lang == "zh"
          and app.generate_btn.cget("text").startswith("生成表达式"),
          app.generate_btn.cget("text"))
    if menubar is not None:
        check("中文菜单栏恢复", menubar.entrycget(0, "label") == "文件",
              menubar.entrycget(0, "label"))

    # ============ 2) 预加载文件（绕过文件对话框） ============
    paths, _ = svg2paths(svg_file)
    app.paths = paths
    app.filepath = svg_file
    app._update_file_label()
    app._refresh_path_list()
    check("路径列表条目数", len(app.path_tree.get_children()) == len(paths))
    check("路径索引 spinbox 上限", str(app.path_spinbox.cget("to")) == str(len(paths) - 1),
          str(app.path_spinbox.cget("to")))
    check("加载后默认全选", app._selected_indices() == list(range(len(paths))),
          app._selected_indices())
    check("已选计数标签",
          f"{len(paths)}/{len(paths)}" in app.selection_label.cget("text"),
          app.selection_label.cget("text"))
    check("文件名标签", app.file_label.cget("text") == os.path.basename(svg_file),
          app.file_label.cget("text"))

    # ============ 3) 预览绘制 ============
    # （withdraw 状态下 winfo_width 为 1，打桩为固定尺寸）
    app.preview_canvas.winfo_width = lambda: 500
    app.preview_canvas.winfo_height = lambda: 300
    app._draw_preview()
    items = app.preview_canvas.find_all()
    check("预览画布有绘制内容", len(items) > 0, f"items={len(items)}")

    # ============ 4) 路径多选 ============
    select(app, root, "1")
    check("单选同步到索引控件", app.selected_path_idx.get() == 1,
          app.selected_path_idx.get())
    check("单选取值", app._selected_indices() == [1], app._selected_indices())
    app._draw_preview()
    check("重绘后仍有内容", len(app.preview_canvas.find_all()) > 0)

    select(app, root, "0", "2")
    check("多选两条路径", app._selected_indices() == [0, 2], app._selected_indices())
    check("多选后计数标签", f"2/{len(paths)}" in app.selection_label.cget("text"),
          app.selection_label.cget("text"))
    app._draw_preview()
    check("多选预览仍有内容", len(app.preview_canvas.find_all()) > 0)

    app._select_all_paths()
    check("全选按钮", app._selected_indices() == list(range(len(paths))),
          app._selected_indices())
    app._clear_selection()
    check("清除选择按钮", app._selected_indices() == [], app._selected_indices())
    app._select_all_paths()

    # 目标路径索引 spinbox → 只选中该条
    app.selected_path_idx.set(3)
    app._sync_tree_from_spinbox()
    check("索引控件收窄为单选", app._selected_indices() == [3],
          app._selected_indices())

    # ============ 5) 分段模式：只输出选中路径 ============
    select(app, root, "0", "2")
    app.current_mode.set("segments")
    app._update_control_states()
    app.clear_output()
    app.generate()
    ok = pump(root, lambda: not app._busy and bool(app._output_content()), timeout=60)
    text = app._output_content()
    check("分段模式生成完成", ok, text[:80])
    check("分段输出含坐标汇总", "各线段坐标表达式" in text)
    check("分段输出含段落标记", "第 0 段(" in text)
    check("分段只输出选中路径",
          "路径 0:" in text and "路径 2:" in text and "路径 1:" not in text,
          text[:200])

    # ============ 6) 空选择 / 无路径 的警告 ============
    warned = []
    original_warning = gui.messagebox.showwarning
    gui.messagebox.showwarning = lambda title, msg: warned.append(msg)
    try:
        app._clear_selection()
        app.clear_output()
        app.generate()
        root.update()
        check("未选路径时给出警告而非生成",
              not app._busy and len(warned) == 1, str(warned))
        check("空选警告文案",
              bool(warned) and "至少一条" in warned[0], str(warned))

        app.paths = []
        app.generate()
        root.update()
        check("无路径时给出警告而非异常",
              not app._busy and len(warned) == 2, str(warned))
    finally:
        gui.messagebox.showwarning = original_warning

    # ============ 7) 英文报告 ============
    app.paths = paths
    app._refresh_path_list()
    app.set_language("en", persist=False)
    select(app, root, "1")
    app.clear_output()
    app.generate()
    ok = pump(root, lambda: not app._busy and bool(app._output_content()), timeout=60)
    text = app._output_content()
    check("英文分段报告生成", ok and "Path 1:" in text, text[:120])
    check("英文报告坐标汇总",
          "Coordinate expressions (one per line):" in text, text[-200:])
    check("英文报告不含中文标题",
          "路径" not in text and "各线段坐标表达式" not in text, text[:80])
    app.set_language("zh", persist=False)

    # ============ 8) 傅里叶模式：拟合所有路径 ============
    app.current_mode.set("fourier")
    app.fit_all_paths.set(True)
    app._update_control_states()
    check("拟合所有路径时索引禁用", str(app.path_spinbox.cget("state")) == "disabled",
          str(app.path_spinbox.cget("state")))
    app.clear_output()
    app.generate()
    ok = pump(root, lambda: not app._busy and "傅里叶级数拟合" in app._output_content(),
              timeout=120)
    text = app._output_content()
    check("傅里叶全路径拟合完成", ok, text[:120])
    check("傅里叶输出含聚合坐标", "各连续段坐标表达式" in text)
    check("傅里叶输出无双重符号", "+ +" not in text and "-  " not in text, text[:200])

    # ============ 9) 傅里叶多选路径 ============
    app.fit_all_paths.set(False)
    select(app, root, "0", "2")
    app.clear_output()
    app.generate()
    ok = pump(root, lambda: not app._busy and "傅里叶级数拟合" in app._output_content(),
              timeout=120)
    text = app._output_content()
    check("傅里叶多选拟合完成", ok,
          f"busy={app._busy} out={text[:80]!r} errors={errors}")
    check("傅里叶多选含两条标签",
          "路径 0 傅里叶级数拟合" in text
          and "路径 2 傅里叶级数拟合" in text
          and "路径 1 傅里叶级数拟合" not in text,
          text[:240])

    # ============ 10) 傅里叶单路径 + 分割不连续点 ============
    app.split_discont.set(True)
    app.selected_path_idx.set(1)
    app._sync_tree_from_spinbox()
    check("单路径选中", app._selected_indices() == [1], app._selected_indices())
    app.clear_output()
    app.generate()
    ok = pump(root, lambda: not app._busy and "傅里叶级数拟合" in app._output_content(),
              timeout=120)
    check("傅里叶单路径分割拟合完成", ok,
          f"busy={app._busy} out={app._output_content()[:80]!r} errors={errors}")

    # ============ 11) 参数健壮性：非法输入回退默认值 ============
    app.precision.set(-3)  # IntVar 允许，读取时夹取
    check("精度夹取", app._read_int(app.precision, 4, 0, 10) == 0,
          app._read_int(app.precision, 4, 0, 10))

    # ============ 12) 清空 / 统计 / 复制 ============
    app.paths = paths
    app._refresh_path_list()
    app.clear_output()
    check("清空输出", app._output_content() == "")
    check("清空后统计归零", app.stats_label.cget("text") == "",
          app.stats_label.cget("text"))
    app._set_output("hello")
    check("字符统计", "5" in app.stats_label.cget("text"),
          app.stats_label.cget("text"))
    app.copy_output()
    root.update()
    try:
        clip = root.clipboard_get()
    except Exception as exc:
        clip = f"<clipboard error: {exc}>"
    check("复制到剪贴板", clip == "hello", clip)

    # 全程不应出现生成失败的错误弹窗
    gui.messagebox.showerror = original_error
    check("生成过程无错误弹窗", not errors, str(errors)[:300])

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
