#!/usr/bin/env python3
"""
SVG 路径数学表达式生成器 - 图形界面

特性:
    * 中英双语界面: "视图 → 语言" 即时切换, 首次启动按系统语言自动探测并记住选择;
      生成的报告文本也跟随界面语言
    * 路径列表支持多选(Ctrl/Shift 点击, 或用"全选/清除选择"), 选中的路径即为生成
      范围; 预览中已选路径以蓝色高亮
    * 左侧控制面板 + 右侧预览/输出的分栏布局
    * SVG 路径预览画布(支持 Y 轴翻转实时预览)
    * 生成过程在后台线程执行, 通过队列回传结果, 不阻塞界面、无 Tk 跨线程调用
    * 菜单栏与快捷键: Ctrl+O 打开 / F5 生成 / Ctrl+S 保存 / Ctrl+Shift+C 复制

依赖: svgpathtools, numpy(傅里叶模式), tkinter(内置)
"""

import os
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from typing import List, Tuple

import i18n as tr

# --- 依赖检查（延迟报错：只有创建窗口后才弹对话框）---
# 每项为 (模块名, 异常文本, 缺失时给出的 pip 命令；空串表示提示"同一目录")
_LOAD_ERRORS: List[Tuple[str, str, str]] = []

try:
    from svgpathtools import svg2paths
except Exception as exc:  # pragma: no cover
    svg2paths = None
    _LOAD_ERRORS.append(("svgpathtools", str(exc), "pip install svgpathtools"))

try:
    import numpy as np  # noqa: F401  （仅用于检测傅里叶模式是否可用）
except ImportError:
    np = None

try:
    import svg_to_function as core
except Exception as exc:  # pragma: no cover
    core = None
    _LOAD_ERRORS.append(("svg_to_function", str(exc), ""))


def _dependency_text(lang: str) -> str:
    """按指定语言拼装依赖缺失提示。"""
    parts = []
    for name, exc, cmd in _LOAD_ERRORS:
        if cmd:
            parts.append(tr.t(lang, "err.dep_pip", name=name, exc=exc, cmd=cmd))
        else:
            parts.append(tr.t(lang, "err.dep_dir", name=name, exc=exc))
    return "\n\n".join(parts)


class SVGMathGUI:
    """SVG → Desmos 表达式生成器主窗口。"""

    POLL_MS = 100          # 结果队列轮询间隔
    REDRAW_MS = 120        # 预览画布防抖间隔
    PREVIEW_SAMPLES = 400  # 每条路径的预览采样上限

    def __init__(self, root: tk.Tk, lang: str = None):
        self.root = root
        self.lang = tr.normalize(lang) if lang else tr.load_language()

        self.root.title(self.t("app.title"))
        self.root.geometry("1100x780")
        self.root.minsize(940, 700)

        # 数据
        self.paths = []            # 原始路径（未翻转）
        self.filepath = ""
        self._busy = False
        self._results: "queue.Queue[tuple]" = queue.Queue()
        self._redraw_job = None
        self._updating_ui = False   # 避免控件联动时的递归触发

        # i18n 注册表: (widget, key, 插值参数) / (menu, 序号, key)
        self._i18n_widgets: List[tuple] = []
        self._i18n_menu: List[tuple] = []
        self._status_key = "status.ready"
        self._status_args: dict = {}
        self._stats_chars = 0

        # 变量
        self.lang_var = tk.StringVar(value=self.lang)
        self.current_mode = tk.StringVar(value="segments")
        self.fourier_harmonics = tk.IntVar(value=5)
        self.fourier_samples = tk.IntVar(value=1000)
        self.precision = tk.IntVar(value=4)
        self.fit_all_paths = tk.BooleanVar(value=False)
        self.split_discont = tk.BooleanVar(value=False)
        self.selected_path_idx = tk.IntVar(value=0)
        self.flip_y = tk.BooleanVar(value=True)
        self.auto_wrap = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="")

        self._build_menu()
        self._build_ui()
        self._bind_shortcuts()
        self._apply_language()   # 首次按语言填充全部文案

        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)
        self.root.after(self.POLL_MS, self._poll_results)

    # ---------------- i18n ----------------

    def t(self, key: str, **kwargs) -> str:
        """当前语言下的界面文案。"""
        return tr.t(self.lang, key, **kwargs)

    def _reg(self, widget, key: str, **kwargs):
        """注册一个随语言切换更新 text 的控件，并返回该控件本身。"""
        self._i18n_widgets.append((widget, key, kwargs))
        return widget

    def _reg_menu(self, menu: tk.Menu, key: str, entry_kwargs: dict = None,
                  kind: str = "command"):
        """向菜单追加一个需要翻译的条目并注册（实际文案由 _apply_language 填充）。"""
        if kind == "cascade":
            menu.add_cascade(label="", **(entry_kwargs or {}))
        elif kind == "checkbutton":
            menu.add_checkbutton(label="", **(entry_kwargs or {}))
        else:
            menu.add_command(label="", **(entry_kwargs or {}))
        self._i18n_menu.append((menu, menu.index("end"), key))

    def _apply_language(self):
        """把当前语言刷到所有控件、菜单、标题与状态栏上。"""
        for widget, key, kwargs in self._i18n_widgets:
            try:
                widget.configure(text=self.t(key, **kwargs))
            except tk.TclError:
                pass
        for menu, index, key in self._i18n_menu:
            try:
                menu.entryconfig(index, label=self.t(key))
            except tk.TclError:
                pass

        self.lang_var.set(self.lang)
        self._apply_tree_headings()
        if self.paths:
            self._refresh_path_list(keep_selection=True)
        self._update_file_label()
        self._update_title()
        self._update_stats()
        self._update_selection_label()
        self._set_status(self._status_key, **self._status_args)
        self._schedule_redraw()

    def set_language(self, lang: str, persist: bool = True) -> bool:
        """切换界面语言并立即生效；返回语言是否发生变化。"""
        code = tr.normalize(lang)
        changed = code != self.lang
        self.lang = code
        if persist:
            tr.save_language(code)
        self._apply_language()
        return changed

    def _apply_tree_headings(self):
        try:
            self.path_tree.heading("#0", text="#")
            self.path_tree.heading("segs", text=self.t("tree.segs"))
            self.path_tree.heading("state", text=self.t("tree.state"))
        except tk.TclError:
            pass

    def _set_status(self, key: str, **args):
        """记录状态文案，语言切换时可原样重渲染。"""
        self._status_key = key
        self._status_args = dict(args)
        self.status_var.set(self.t(key, **args))

    def _update_title(self):
        base = self.t("app.title")
        name = os.path.basename(self.filepath) if self.filepath else ""
        self.root.title(f"{base} — {name}" if name else base)

    def _update_file_label(self):
        text = os.path.basename(self.filepath) if self.filepath else self.t("file.none")
        self.file_label.config(text=text)

    def _update_stats(self):
        if self._stats_chars:
            self.stats_label.config(text=self.t("stats.chars", n=self._stats_chars))
        else:
            self.stats_label.config(text="")

    def _update_selection_label(self):
        if not hasattr(self, "selection_label"):
            return
        total = len(self.paths)
        if not total:
            self.selection_label.config(text="")
        else:
            self.selection_label.config(
                text=self.t("paths.selected", n=len(self._selected_indices()),
                            total=total)
            )

    # ---------------- UI 构建 ----------------

    def _build_menu(self):
        menubar = tk.Menu(self.root, tearoff=0)

        file_menu = tk.Menu(menubar, tearoff=0)
        self._reg_menu(file_menu, "menu.file.open",
                       {"accelerator": "Ctrl+O", "command": self.load_svg})
        self._reg_menu(file_menu, "menu.file.save",
                       {"accelerator": "Ctrl+S", "command": self.save_output})
        file_menu.add_separator()
        self._reg_menu(file_menu, "menu.file.exit", {"command": self.root.destroy})

        edit_menu = tk.Menu(menubar, tearoff=0)
        self._reg_menu(edit_menu, "menu.edit.copy_all",
                       {"accelerator": "Ctrl+Shift+C", "command": self.copy_output})
        self._reg_menu(edit_menu, "menu.edit.clear", {"command": self.clear_output})
        edit_menu.add_separator()
        self._reg_menu(edit_menu, "menu.edit.wrap", {
            "variable": self.auto_wrap,
            "command": self._toggle_wrap,
        }, kind="checkbutton")

        view_menu = tk.Menu(menubar, tearoff=0)
        lang_menu = tk.Menu(view_menu, tearoff=0)
        lang_menu.add_radiobutton(
            label=tr.LANG_NATIVE["zh"], variable=self.lang_var, value="zh",
            command=lambda: self.set_language("zh"),
        )
        lang_menu.add_radiobutton(
            label=tr.LANG_NATIVE["en"], variable=self.lang_var, value="en",
            command=lambda: self.set_language("en"),
        )
        self._reg_menu(view_menu, "menu.view.language", {"menu": lang_menu},
                       kind="cascade")

        help_menu = tk.Menu(menubar, tearoff=0)
        self._reg_menu(help_menu, "menu.help.about", {"command": self._show_about})

        self._reg_menu(menubar, "menu.file", {"menu": file_menu}, kind="cascade")
        self._reg_menu(menubar, "menu.edit", {"menu": edit_menu}, kind="cascade")
        self._reg_menu(menubar, "menu.view", {"menu": view_menu}, kind="cascade")
        self._reg_menu(menubar, "menu.help", {"menu": help_menu}, kind="cascade")

        self.root.config(menu=menubar)

    def _build_ui(self):
        paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        # ---- 左侧控制面板 ----
        left = ttk.Frame(paned, width=320)
        paned.add(left, weight=0)

        # 文件
        file_frame = self._reg(
            ttk.LabelFrame(left, text="", padding=6), "frame.file"
        )
        file_frame.pack(fill=tk.X, padx=6, pady=(6, 4))
        self._reg(
            ttk.Button(file_frame, text="", command=self.load_svg), "btn.open_svg"
        ).pack(fill=tk.X)
        self.file_label = self._reg(
            ttk.Label(file_frame, text="", wraplength=280, foreground="#555"),
            "file.none",
        )
        self.file_label.pack(fill=tk.X, pady=(4, 0))

        # 路径列表（支持多选）
        path_frame = self._reg(
            ttk.LabelFrame(left, text="", padding=6), "frame.paths"
        )
        path_frame.pack(fill=tk.X, padx=6, pady=4)
        tree_wrap = ttk.Frame(path_frame)
        tree_wrap.pack(fill=tk.X)
        self.path_tree = ttk.Treeview(
            tree_wrap,
            columns=("segs", "state"),
            show="tree headings",
            height=6,
            selectmode="extended",
        )
        self.path_tree.column("#0", width=42, anchor=tk.CENTER, stretch=False)
        self.path_tree.column("segs", width=70, anchor=tk.CENTER, stretch=False)
        self.path_tree.column("state", width=70, anchor=tk.CENTER, stretch=False)
        tree_scroll = ttk.Scrollbar(tree_wrap, orient=tk.VERTICAL,
                                    command=self.path_tree.yview)
        self.path_tree.configure(yscrollcommand=tree_scroll.set)
        self.path_tree.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.path_tree.bind("<<TreeviewSelect>>", self._on_path_selected)

        sel_row = ttk.Frame(path_frame)
        sel_row.pack(fill=tk.X, pady=(4, 0))
        self._reg(
            ttk.Button(sel_row, text="", command=self._select_all_paths),
            "btn.select_all",
        ).pack(side=tk.LEFT)
        self._reg(
            ttk.Button(sel_row, text="", command=self._clear_selection),
            "btn.clear_sel",
        ).pack(side=tk.LEFT, padx=(4, 0))
        self.selection_label = ttk.Label(sel_row, text="", foreground="#666")
        self.selection_label.pack(side=tk.RIGHT)

        # 输出模式
        mode_frame = self._reg(ttk.LabelFrame(left, text="", padding=6), "frame.mode")
        mode_frame.pack(fill=tk.X, padx=6, pady=4)
        self._reg(ttk.Radiobutton(
            mode_frame, text="", variable=self.current_mode,
            value="segments", command=self._update_control_states,
        ), "mode.segments").pack(anchor=tk.W)
        self._reg(ttk.Radiobutton(
            mode_frame, text="", variable=self.current_mode,
            value="fourier", command=self._update_control_states,
        ), "mode.fourier").pack(anchor=tk.W)

        # 傅里叶参数
        self.fourier_frame = self._reg(
            ttk.LabelFrame(left, text="", padding=6), "frame.fourier"
        )
        self.fourier_frame.pack(fill=tk.X, padx=6, pady=4)

        self._reg(ttk.Label(self.fourier_frame, text=""),
                  "fourier.harmonics").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.harmonics_spin = ttk.Spinbox(
            self.fourier_frame, from_=1, to=50,
            textvariable=self.fourier_harmonics, width=7,
        )
        self.harmonics_spin.grid(row=0, column=1, sticky=tk.E, padx=6, pady=2)

        self._reg(ttk.Label(self.fourier_frame, text=""),
                  "fourier.samples").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.samples_spin = ttk.Spinbox(
            self.fourier_frame, from_=100, to=20000,
            textvariable=self.fourier_samples, width=7, increment=100,
        )
        self.samples_spin.grid(row=1, column=1, sticky=tk.E, padx=6, pady=2)

        self._reg(ttk.Label(self.fourier_frame, text=""),
                  "fourier.path_index").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.path_spinbox = ttk.Spinbox(
            self.fourier_frame, from_=0, to=0,
            textvariable=self.selected_path_idx, width=7,
        )
        self.path_spinbox.grid(row=2, column=1, sticky=tk.E, padx=6, pady=2)
        self.path_spinbox.bind("<KeyRelease>", lambda e: self._sync_tree_from_spinbox())
        self.path_spinbox.configure(command=self._sync_tree_from_spinbox)

        self.split_check = self._reg(ttk.Checkbutton(
            self.fourier_frame, text="", variable=self.split_discont,
        ), "fourier.split")
        self.split_check.grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=2)
        self.fit_all_check = self._reg(ttk.Checkbutton(
            self.fourier_frame, text="", variable=self.fit_all_paths,
            command=self._update_control_states,
        ), "fourier.fit_all")
        self.fit_all_check.grid(row=4, column=0, columnspan=2, sticky=tk.W, pady=2)
        self.fourier_frame.columnconfigure(1, weight=1)

        # 通用参数
        common_frame = self._reg(ttk.LabelFrame(left, text="", padding=6),
                                 "frame.common")
        common_frame.pack(fill=tk.X, padx=6, pady=4)
        self._reg(ttk.Label(common_frame, text=""),
                  "common.precision").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.precision_spin = ttk.Spinbox(
            common_frame, from_=0, to=10, textvariable=self.precision, width=7
        )
        self.precision_spin.grid(row=0, column=1, sticky=tk.E, padx=6, pady=2)
        self.flip_check = self._reg(ttk.Checkbutton(
            common_frame, text="", variable=self.flip_y,
            command=self._schedule_redraw,
        ), "common.flip")
        self.flip_check.grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=2)
        common_frame.columnconfigure(1, weight=1)

        # 生成按钮
        self.generate_btn = self._reg(
            ttk.Button(left, text="", command=self.generate), "btn.generate"
        )
        self.generate_btn.pack(fill=tk.X, padx=6, pady=(8, 6))

        # ---- 右侧：预览 + 输出 ----
        right = ttk.PanedWindow(paned, orient=tk.VERTICAL)
        paned.add(right, weight=1)

        preview_frame = self._reg(ttk.LabelFrame(right, text="", padding=4),
                                  "frame.preview")
        right.add(preview_frame, weight=1)
        self.preview_canvas = tk.Canvas(preview_frame, background="#ffffff",
                                        highlightthickness=0)
        self.preview_canvas.pack(fill=tk.BOTH, expand=True)
        self.preview_canvas.bind("<Configure>", self._on_canvas_configure)

        output_frame = self._reg(ttk.LabelFrame(right, text="", padding=4),
                                 "frame.output")
        right.add(output_frame, weight=3)
        self.output_text = scrolledtext.ScrolledText(
            output_frame, wrap=tk.WORD, font=("Consolas", 10)
        )
        self.output_text.pack(fill=tk.BOTH, expand=True)

        btn_row = ttk.Frame(output_frame)
        btn_row.pack(fill=tk.X, pady=(6, 0))
        self._reg(ttk.Button(btn_row, text="", command=self.copy_output),
                  "btn.copy").pack(side=tk.LEFT, padx=(0, 4))
        self._reg(ttk.Button(btn_row, text="", command=self.save_output),
                  "btn.save").pack(side=tk.LEFT, padx=(0, 4))
        self._reg(ttk.Button(btn_row, text="", command=self.clear_output),
                  "btn.clear").pack(side=tk.LEFT, padx=(0, 4))
        self._reg(ttk.Checkbutton(
            btn_row, text="", variable=self.auto_wrap, command=self._toggle_wrap
        ), "opt.wrap").pack(side=tk.RIGHT)
        self.stats_label = ttk.Label(btn_row, text="", foreground="#666")
        self.stats_label.pack(side=tk.RIGHT, padx=8)

        # ---- 状态栏 ----
        status_frame = ttk.Frame(self.root, padding=(6, 2))
        status_frame.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Label(status_frame, textvariable=self.status_var, anchor=tk.W).pack(
            side=tk.LEFT, fill=tk.X, expand=True
        )
        self.progress = ttk.Progressbar(status_frame, mode="indeterminate", length=160)
        # 仅在计算时 pack

        self._update_control_states()

    def _bind_shortcuts(self):
        self.root.bind_all("<Control-o>", lambda e: self.load_svg())
        self.root.bind_all("<Control-s>", lambda e: self.save_output())
        self.root.bind_all("<Control-Shift-C>", lambda e: self.copy_output())
        self.root.bind_all("<Control-Shift-c>", lambda e: self.copy_output())
        self.root.bind_all("<F5>", lambda e: self.generate())

    # ---------------- 文件与路径 ----------------

    def load_svg(self):
        """加载 SVG 文件。"""
        if svg2paths is None:
            messagebox.showerror(
                self.t("dlg.dependency"),
                self.t("err.dep_pip", name="svgpathtools", exc="-",
                       cmd="pip install svgpathtools"),
            )
            return
        filepath = filedialog.askopenfilename(
            title=self.t("dlg.open_svg"),
            filetypes=[
                (self.t("type.svg"), "*.svg"),
                (self.t("type.all"), "*.*"),
            ],
        )
        if not filepath:
            return
        try:
            paths, _ = svg2paths(filepath)
        except Exception as exc:
            messagebox.showerror(self.t("dlg.error"), self.t("err.open_fail", exc=exc))
            self._set_status("status.load_failed")
            return
        if not paths:
            messagebox.showwarning(self.t("dlg.warn"), self.t("err.no_paths"))
            return

        self.paths = paths
        self.filepath = filepath
        self._update_file_label()
        self._update_title()
        self._refresh_path_list()          # 加载后默认全选
        self._schedule_redraw()
        self._set_status("status.loaded", n=len(paths))

    def _refresh_path_list(self, keep_selection: bool = False):
        """
        重建左侧路径列表。

        keep_selection=True 时保留原有选择（语言切换用）；否则重建后默认全选，
        保证"界面上看到选中的路径就是会被生成的路径"。
        """
        previous = set(self._selected_indices()) if keep_selection else set()
        active = self._current_path_index() if keep_selection else 0

        self._updating_ui = True
        try:
            self.path_tree.delete(*self.path_tree.get_children())
            for i, path in enumerate(self.paths):
                closed = core.is_path_closed(path) if core is not None else False
                state = self.t("state.closed" if closed else "state.open")
                self.path_tree.insert(
                    "", tk.END, iid=str(i), text=str(i), values=(len(path), state)
                )
            self.path_spinbox.configure(to=max(0, len(self.paths) - 1))

            iids = [str(i) for i in range(len(self.paths))]
            if keep_selection:
                chosen = [iid for iid in iids if int(iid) in previous]
            else:
                chosen = iids

            if chosen:
                chosen_set = {int(iid) for iid in chosen}
                new_active = active if active in chosen_set else int(chosen[0])
                self.path_tree.selection_set(*chosen)
                self.path_tree.focus(str(new_active))
                self.path_tree.see(str(new_active))
                self.selected_path_idx.set(new_active)
            else:
                self.selected_path_idx.set(0)
        finally:
            self._updating_ui = False
        self._update_selection_label()

    def _selected_indices(self) -> List[int]:
        """当前选中的路径索引（排序去重，剔除越界项）。"""
        result: List[int] = []
        for iid in self.path_tree.selection():
            try:
                idx = int(iid)
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(self.paths):
                result.append(idx)
        return sorted(set(result))

    def _current_path_index(self) -> int:
        """当前活动的路径索引（越界时归一化）。"""
        try:
            idx = int(self.selected_path_idx.get())
        except Exception:
            idx = 0
        if not self.paths:
            return 0
        return max(0, min(len(self.paths) - 1, idx))

    def _on_path_selected(self, _event=None):
        if self._updating_ui:
            return
        selection = self.path_tree.selection()
        if selection:
            focus = self.path_tree.focus()
            if focus.isdigit() and focus in selection:
                active = int(focus)
            else:
                active = int(sorted(selection, key=lambda s: int(s))[-1])
            self._updating_ui = True
            try:
                self.selected_path_idx.set(active)
            finally:
                self._updating_ui = False
        self._update_selection_label()
        self._schedule_redraw()

    def _sync_tree_from_spinbox(self, *_args):
        """目标路径索引 → 只选中该条路径。"""
        if self._updating_ui or not self.paths:
            return
        idx = self._current_path_index()
        self._updating_ui = True
        try:
            self.selected_path_idx.set(idx)
            iid = str(idx)
            if self.path_tree.exists(iid):
                self.path_tree.selection_set(iid)
                self.path_tree.focus(iid)
                self.path_tree.see(iid)
        finally:
            self._updating_ui = False
        self._update_selection_label()
        self._schedule_redraw()

    def _select_all_paths(self):
        """选中全部路径。"""
        if not self.paths:
            return
        self._updating_ui = True
        try:
            self.path_tree.selection_set(*[str(i) for i in range(len(self.paths))])
        finally:
            self._updating_ui = False
        self._update_selection_label()
        self._schedule_redraw()

    def _clear_selection(self):
        """清空选择（此时生成会提示先选路径）。"""
        if not self.paths:
            return
        self._updating_ui = True
        try:
            self.path_tree.selection_set()
        finally:
            self._updating_ui = False
        self._update_selection_label()
        self._schedule_redraw()

    # ---------------- 预览画布 ----------------

    def _on_canvas_configure(self, _event):
        self._schedule_redraw()

    def _schedule_redraw(self):
        """防抖重绘预览（窗口缩放/选项变化时调用）。"""
        if self._redraw_job is not None:
            try:
                self.root.after_cancel(self._redraw_job)
            except Exception:
                pass
        self._redraw_job = self.root.after(self.REDRAW_MS, self._draw_preview)

    def _draw_preview(self):
        """在画布上绘制所有路径，已选路径以蓝色高亮。"""
        self._redraw_job = None
        canvas = self.preview_canvas
        canvas.delete("all")
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width < 30 or height < 30:
            return
        if core is None or not self.paths:
            canvas.create_text(
                width / 2, height / 2, text=self.t("preview.empty"),
                fill="#999999", font=("Microsoft YaHei UI", 11),
            )
            return

        flip = self.flip_y.get()
        selected = set(self._selected_indices())

        # 采样所有路径，同时计算包围盒
        polylines = []  # (是否已选, [(x, y), ...])
        min_x = min_y = float("inf")
        max_x = max_y = float("-inf")
        for i, path in enumerate(self.paths):
            transformed = core.transform_path(path, flip) if flip else path
            for sub in core.split_path_at_discontinuities(transformed):
                if not sub:
                    continue
                n = max(40, min(self.PREVIEW_SAMPLES, 6 * len(sub)))
                pts = [sub.point(k / (n - 1)) for k in range(n)]
                polylines.append((i in selected, pts))
                for p in pts:
                    if p.real < min_x:
                        min_x = p.real
                    if p.real > max_x:
                        max_x = p.real
                    if p.imag < min_y:
                        min_y = p.imag
                    if p.imag > max_y:
                        max_y = p.imag

        if not polylines:
            return
        if max_x - min_x < 1e-9:
            min_x -= 1
            max_x += 1
        if max_y - min_y < 1e-9:
            min_y -= 1
            max_y += 1

        pad = 16
        span_x = max_x - min_x
        span_y = max_y - min_y
        scale = min((width - 2 * pad) / span_x, (height - 2 * pad) / span_y)
        # 居中
        offset_x = pad + ((width - 2 * pad) - span_x * scale) / 2
        offset_y = pad + ((height - 2 * pad) - span_y * scale) / 2

        def to_canvas(px: float, py: float) -> Tuple[float, float]:
            return (
                offset_x + (px - min_x) * scale,
                offset_y + (max_y - py) * scale,
            )

        # 坐标轴（翻转后的数学坐标系中显示原点）
        if min_x < 0 < max_x:
            x0, _ = to_canvas(0, max_y)
            canvas.create_line(x0, 0, x0, height, fill="#e3e8ef", dash=(4, 4))
        if min_y < 0 < max_y:
            _, y0 = to_canvas(min_x, 0)
            canvas.create_line(0, y0, width, y0, fill="#e3e8ef", dash=(4, 4))

        # 先画未选中，再画已选中（保证高亮在最上层）
        for want_selected in (False, True):
            for is_selected, pts in polylines:
                if is_selected != want_selected:
                    continue
                flat = []
                for p in pts:
                    cx, cy = to_canvas(p.real, p.imag)
                    flat.extend((cx, cy))
                if len(flat) >= 4:
                    canvas.create_line(
                        *flat,
                        fill="#2563eb" if is_selected else "#aab4c3",
                        width=2 if is_selected else 1,
                        capstyle=tk.ROUND,
                    )

    # ---------------- 参数与状态 ----------------

    def _read_int(self, var, default: int, lo: int, hi: int) -> int:
        """安全读取整数控件（输入非法时回退到默认值并夹取范围）。"""
        try:
            value = int(var.get())
        except Exception:
            value = default
        return max(lo, min(hi, value))

    def _update_control_states(self):
        """根据当前模式启用/禁用相关控件。"""
        if not hasattr(self, "fourier_frame"):
            return
        fourier_on = self.current_mode.get() == "fourier"
        state = "normal" if fourier_on else "disabled"
        for child in self.fourier_frame.winfo_children():
            try:
                child.configure(state=state)
            except Exception:
                pass
        # 拟合所有路径时，目标路径索引无效
        if fourier_on and self.fit_all_paths.get():
            state = "disabled"
        try:
            self.path_spinbox.configure(state=state)
        except Exception:
            pass

    def _set_busy(self, busy: bool):
        self._busy = busy
        try:
            self.generate_btn.configure(state="disabled" if busy else "normal")
        except Exception:
            pass
        if busy:
            self.progress.pack(side=tk.RIGHT, padx=(8, 0))
            self.progress.start(12)
        else:
            try:
                self.progress.stop()
                self.progress.pack_forget()
            except Exception:
                pass

    # ---------------- 生成 ----------------

    def generate(self):
        """
        生成表达式：范围 = 路径列表中被选中的路径（"拟合所有路径"除外）。

        在后台线程执行，完成后通过队列回传。
        """
        if self._busy:
            return
        if core is None:
            messagebox.showerror(self.t("dlg.import_error"),
                                 _dependency_text(self.lang)
                                 or self.t("err.core_missing"))
            return
        if not self.paths:
            messagebox.showwarning(self.t("dlg.warn"), self.t("err.no_svg"))
            return

        mode = self.current_mode.get()
        fit_all = bool(self.fit_all_paths.get())
        indices = self._selected_indices()

        # 生成范围 = 列表中被选中的路径；"拟合所有路径"忽略选择。
        # 始终把完整路径列表交给核心模块，由 path_indices 挑选，
        # 这样报告里的编号就是 SVG 中的原始编号，且索引校验一致。
        if mode == "fourier" and fit_all:
            path_indices = None
        else:
            if not indices:
                messagebox.showwarning(self.t("dlg.warn"), self.t("err.no_selection"))
                return
            path_indices = indices

        params = {
            "mode": mode,
            "precision": self._read_int(self.precision, 4, 0, 10),
            "flip": self.flip_y.get(),
            "raw_paths": list(self.paths),
            "path_indices": path_indices,
            "lang": self.lang,
        }
        if mode == "fourier":
            params.update(
                harmonics=self._read_int(self.fourier_harmonics, 5, 1, 50),
                samples=self._read_int(self.fourier_samples, 1000, 2, 20000),
                split=bool(self.split_discont.get()),
                fit_all=fit_all,
                path_index=(indices[0] if indices else self._current_path_index()),
            )
            if np is None:
                messagebox.showerror(self.t("dlg.dependency"), self.t("err.numpy"))
                return

        self._set_busy(True)
        self._set_status("status.loading")
        threading.Thread(target=self._worker, args=(params,), daemon=True).start()

    @staticmethod
    def _build_text(params: dict) -> str:
        """工作线程：构造报告文本（不触碰任何 Tk 对象）。"""
        paths = params["raw_paths"]
        if params["flip"]:
            paths = [core.transform_path(p, flip_y=True) for p in paths]

        lang = params.get("lang")
        indices = params.get("path_indices")

        if params["mode"] == "segments":
            return core.build_segment_report(
                paths,
                precision=params["precision"],
                path_indices=indices,
                lang=lang,
            )

        return core.build_fourier_report(
            paths,
            n_harmonics=params["harmonics"],
            samples=params["samples"],
            precision=params["precision"],
            fit_all=params["fit_all"],
            path_index=params["path_index"],
            split=params["split"],
            path_indices=indices,
            lang=lang,
        )

    def _worker(self, params: dict):
        """后台线程入口：执行耗时计算并把结果放入队列。"""
        started = time.monotonic()
        try:
            text = self._build_text(params)
            self._results.put(("ok", text, time.monotonic() - started))
        except Exception as exc:
            self._results.put(("error", f"{type(exc).__name__}: {exc}",
                               time.monotonic() - started))

    def _poll_results(self):
        """
        主线程轮询结果队列，安全地更新 UI。

        任何单条结果的处理异常都不会终止轮询链（轮询靠 finally 重新排期）。
        """
        try:
            while True:
                try:
                    kind, payload, elapsed = self._results.get_nowait()
                except queue.Empty:
                    break
                self._set_busy(False)
                if kind == "ok":
                    try:
                        self._set_output(payload)
                        self._set_status("status.done",
                                         elapsed=f"{elapsed:.2f}", n=len(payload))
                    except Exception as exc:   # UI 更新失败也要给出反馈
                        messagebox.showerror(
                            self.t("dlg.gen_failed"),
                            f"{type(exc).__name__}: {exc}",
                        )
                        self._set_status("status.failed")
                else:
                    messagebox.showerror(self.t("dlg.gen_failed"), payload)
                    self._set_status("status.failed")
        finally:
            try:
                self.root.after(self.POLL_MS, self._poll_results)
            except (tk.TclError, RuntimeError):
                pass    # 窗口已销毁

    def _set_output(self, text: str):
        self.output_text.configure(state=tk.NORMAL)
        self.output_text.delete("1.0", tk.END)
        self.output_text.insert(tk.END, text)
        self._stats_chars = len(text)
        self._update_stats()

    # ---------------- 输出操作 ----------------

    def _output_content(self) -> str:
        return self.output_text.get("1.0", tk.END).rstrip("\n")

    def copy_output(self):
        content = self._output_content()
        if not content:
            messagebox.showinfo(self.t("dlg.info"), self.t("err.no_copy"))
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        self._set_status("status.copied")

    def save_output(self):
        content = self._output_content()
        if not content:
            messagebox.showinfo(self.t("dlg.info"), self.t("err.no_save"))
            return
        initial = ""
        if self.filepath:
            initial = os.path.splitext(os.path.basename(self.filepath))[0] + ".txt"
        filepath = filedialog.asksaveasfilename(
            title=self.t("dlg.save_output"),
            initialfile=initial,
            defaultextension=".txt",
            filetypes=[
                (self.t("type.txt"), "*.txt"),
                (self.t("type.all"), "*.*"),
            ],
        )
        if not filepath:
            return
        try:
            with open(filepath, "w", encoding="utf-8") as fh:
                fh.write(content)
            self._set_status("status.saved", path=filepath)
        except Exception as exc:
            messagebox.showerror(self.t("dlg.error"),
                                 self.t("err.save_fail", exc=exc))

    def clear_output(self):
        self._set_output("")
        self._stats_chars = 0
        self._update_stats()
        self._set_status("status.cleared")

    def _toggle_wrap(self):
        wrap = tk.WORD if self.auto_wrap.get() else tk.NONE
        self.output_text.configure(wrap=wrap)

    def _show_about(self):
        messagebox.showinfo(self.t("dlg.about"), self.t("about.body"))


def main() -> int:
    lang = tr.load_language()
    if _LOAD_ERRORS:
        # 依赖缺失：先弹错误对话框，再退出
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(tr.t(lang, "dlg.dependency"), _dependency_text(lang))
        root.destroy()
        return 1
    root = tk.Tk()
    SVGMathGUI(root, lang=lang)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
