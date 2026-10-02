#!/usr/bin/env python3
"""
SVG 路径数学表达式生成器 - 图形界面

特性:
    * 左侧控制面板 + 右侧预览/输出的分栏布局
    * SVG 路径预览画布(高亮当前选中路径, 支持 Y 轴翻转实时预览)
    * 路径列表(线段数/闭合状态), 点击即选中目标路径
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
from typing import Tuple

# --- 依赖检查（延迟报错：只有创建窗口后才弹对话框）---
_LOAD_ERRORS = []

try:
    from svgpathtools import svg2paths
except Exception as exc:  # pragma: no cover
    svg2paths = None
    _LOAD_ERRORS.append(f"svgpathtools：{exc}\n请运行 pip install svgpathtools")

try:
    import numpy as np  # noqa: F401  （仅用于检测傅里叶模式是否可用）
except ImportError:
    np = None

try:
    import svg_to_function as core
except Exception as exc:  # pragma: no cover
    core = None
    _LOAD_ERRORS.append(f"svg_to_function：{exc}\n请确认 svg_to_function.py 与本脚本在同一目录")


class SVGMathGUI:
    """SVG → Desmos 表达式生成器主窗口。"""

    POLL_MS = 100          # 结果队列轮询间隔
    REDRAW_MS = 120        # 预览画布防抖间隔
    PREVIEW_SAMPLES = 400  # 每条路径的预览采样上限

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("SVG 路径转数学表达式")
        self.root.geometry("1100x780")
        self.root.minsize(940, 700)

        # 数据
        self.paths = []            # 原始路径（未翻转）
        self.filepath = ""
        self._busy = False
        self._results: "queue.Queue[tuple]" = queue.Queue()
        self._redraw_job = None
        self._updating_ui = False   # 避免控件联动时的递归触发

        # 变量
        self.current_mode = tk.StringVar(value="segments")
        self.fourier_harmonics = tk.IntVar(value=5)
        self.fourier_samples = tk.IntVar(value=1000)
        self.precision = tk.IntVar(value=4)
        self.fit_all_paths = tk.BooleanVar(value=False)
        self.split_discont = tk.BooleanVar(value=False)
        self.selected_path_idx = tk.IntVar(value=0)
        self.flip_y = tk.BooleanVar(value=True)
        self.auto_wrap = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="就绪")

        self._build_menu()
        self._build_ui()
        self._bind_shortcuts()

        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)
        self.root.after(self.POLL_MS, self._poll_results)

    # ---------------- UI 构建 ----------------

    def _build_menu(self):
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="打开 SVG…", accelerator="Ctrl+O", command=self.load_svg)
        file_menu.add_command(label="保存输出…", accelerator="Ctrl+S", command=self.save_output)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.root.destroy)
        menubar.add_cascade(label="文件", menu=file_menu)

        edit_menu = tk.Menu(menubar, tearoff=0)
        edit_menu.add_command(label="复制全部输出", accelerator="Ctrl+Shift+C", command=self.copy_output)
        edit_menu.add_command(label="清空输出", command=self.clear_output)
        edit_menu.add_separator()
        edit_menu.add_checkbutton(label="自动换行", variable=self.auto_wrap, command=self._toggle_wrap)
        menubar.add_cascade(label="编辑", menu=edit_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="关于", command=self._show_about)
        menubar.add_cascade(label="帮助", menu=help_menu)

        self.root.config(menu=menubar)

    def _build_ui(self):
        paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        # ---- 左侧控制面板 ----
        left = ttk.Frame(paned, width=320)
        paned.add(left, weight=0)

        # 文件
        file_frame = ttk.LabelFrame(left, text="文件", padding=6)
        file_frame.pack(fill=tk.X, padx=6, pady=(6, 4))
        ttk.Button(file_frame, text="打开 SVG 文件", command=self.load_svg).pack(fill=tk.X)
        self.file_label = ttk.Label(file_frame, text="未加载文件", wraplength=280, foreground="#555")
        self.file_label.pack(fill=tk.X, pady=(4, 0))

        # 路径列表
        path_frame = ttk.LabelFrame(left, text="路径（点击选中目标路径）", padding=6)
        path_frame.pack(fill=tk.X, padx=6, pady=4)
        tree_wrap = ttk.Frame(path_frame)
        tree_wrap.pack(fill=tk.X)
        self.path_tree = ttk.Treeview(
            tree_wrap,
            columns=("segs", "state"),
            show="tree headings",
            height=6,
            selectmode="browse",
        )
        self.path_tree.heading("#0", text="#")
        self.path_tree.column("#0", width=42, anchor=tk.CENTER, stretch=False)
        self.path_tree.heading("segs", text="线段")
        self.path_tree.column("segs", width=70, anchor=tk.CENTER, stretch=False)
        self.path_tree.heading("state", text="状态")
        self.path_tree.column("state", width=70, anchor=tk.CENTER, stretch=False)
        tree_scroll = ttk.Scrollbar(tree_wrap, orient=tk.VERTICAL, command=self.path_tree.yview)
        self.path_tree.configure(yscrollcommand=tree_scroll.set)
        self.path_tree.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.path_tree.bind("<<TreeviewSelect>>", self._on_path_selected)

        # 输出模式
        mode_frame = ttk.LabelFrame(left, text="输出模式", padding=6)
        mode_frame.pack(fill=tk.X, padx=6, pady=4)
        ttk.Radiobutton(
            mode_frame, text="分段表达式（精确）", variable=self.current_mode,
            value="segments", command=self._update_control_states,
        ).pack(anchor=tk.W)
        ttk.Radiobutton(
            mode_frame, text="傅里叶级数拟合", variable=self.current_mode,
            value="fourier", command=self._update_control_states,
        ).pack(anchor=tk.W)

        # 傅里叶参数
        self.fourier_frame = ttk.LabelFrame(left, text="傅里叶参数", padding=6)
        self.fourier_frame.pack(fill=tk.X, padx=6, pady=4)

        ttk.Label(self.fourier_frame, text="谐波次数:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.harmonics_spin = ttk.Spinbox(
            self.fourier_frame, from_=1, to=50, textvariable=self.fourier_harmonics, width=7
        )
        self.harmonics_spin.grid(row=0, column=1, sticky=tk.E, padx=6, pady=2)

        ttk.Label(self.fourier_frame, text="采样点数:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.samples_spin = ttk.Spinbox(
            self.fourier_frame, from_=100, to=20000, textvariable=self.fourier_samples,
            width=7, increment=100,
        )
        self.samples_spin.grid(row=1, column=1, sticky=tk.E, padx=6, pady=2)

        ttk.Label(self.fourier_frame, text="目标路径索引:").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.path_spinbox = ttk.Spinbox(
            self.fourier_frame, from_=0, to=0, textvariable=self.selected_path_idx, width=7
        )
        self.path_spinbox.grid(row=2, column=1, sticky=tk.E, padx=6, pady=2)
        self.path_spinbox.bind("<KeyRelease>", lambda e: self._sync_tree_from_spinbox())
        self.path_spinbox.configure(command=self._sync_tree_from_spinbox)

        self.split_check = ttk.Checkbutton(
            self.fourier_frame, text="分割不连续点", variable=self.split_discont
        )
        self.split_check.grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=2)
        self.fit_all_check = ttk.Checkbutton(
            self.fourier_frame, text="拟合所有路径", variable=self.fit_all_paths,
            command=self._update_control_states,
        )
        self.fit_all_check.grid(row=4, column=0, columnspan=2, sticky=tk.W, pady=2)
        self.fourier_frame.columnconfigure(1, weight=1)

        # 通用参数
        common_frame = ttk.LabelFrame(left, text="通用参数", padding=6)
        common_frame.pack(fill=tk.X, padx=6, pady=4)
        ttk.Label(common_frame, text="小数精度:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.precision_spin = ttk.Spinbox(
            common_frame, from_=0, to=10, textvariable=self.precision, width=7
        )
        self.precision_spin.grid(row=0, column=1, sticky=tk.E, padx=6, pady=2)
        self.flip_check = ttk.Checkbutton(
            common_frame, text="翻转 Y 坐标（适配 Desmos）", variable=self.flip_y,
            command=self._schedule_redraw,
        )
        self.flip_check.grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=2)
        common_frame.columnconfigure(1, weight=1)

        # 生成按钮
        self.generate_btn = ttk.Button(left, text="生成表达式  (F5)", command=self.generate)
        self.generate_btn.pack(fill=tk.X, padx=6, pady=(8, 6))

        # ---- 右侧：预览 + 输出 ----
        right = ttk.PanedWindow(paned, orient=tk.VERTICAL)
        paned.add(right, weight=1)

        preview_frame = ttk.LabelFrame(right, text="预览（蓝色为当前选中路径）", padding=4)
        right.add(preview_frame, weight=1)
        self.preview_canvas = tk.Canvas(preview_frame, background="#ffffff", highlightthickness=0)
        self.preview_canvas.pack(fill=tk.BOTH, expand=True)
        self.preview_canvas.bind("<Configure>", self._on_canvas_configure)

        output_frame = ttk.LabelFrame(right, text="输出结果", padding=4)
        right.add(output_frame, weight=3)
        self.output_text = scrolledtext.ScrolledText(
            output_frame, wrap=tk.WORD, font=("Consolas", 10)
        )
        self.output_text.pack(fill=tk.BOTH, expand=True)

        btn_row = ttk.Frame(output_frame)
        btn_row.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(btn_row, text="复制到剪贴板", command=self.copy_output).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(btn_row, text="保存到文件", command=self.save_output).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(btn_row, text="清空输出", command=self.clear_output).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Checkbutton(
            btn_row, text="自动换行", variable=self.auto_wrap, command=self._toggle_wrap
        ).pack(side=tk.RIGHT)
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
            messagebox.showerror("缺少依赖", "未安装 svgpathtools：\npip install svgpathtools")
            return
        filepath = filedialog.askopenfilename(
            title="选择 SVG 文件",
            filetypes=[("SVG 文件", "*.svg"), ("所有文件", "*.*")],
        )
        if not filepath:
            return
        try:
            paths, _ = svg2paths(filepath)
        except Exception as exc:
            messagebox.showerror("错误", f"加载 SVG 失败：{exc}")
            self.status_var.set("加载失败")
            return
        if not paths:
            messagebox.showwarning("警告", "SVG 文件中没有找到路径。")
            return

        self.paths = paths
        self.filepath = filepath
        self.file_label.config(text=os.path.basename(filepath))
        self.root.title(f"SVG 路径转数学表达式 — {os.path.basename(filepath)}")
        self._refresh_path_list()
        self._schedule_redraw()
        self.status_var.set(f"已加载 {len(paths)} 条路径")

    def _refresh_path_list(self):
        """重建左侧路径列表。"""
        self._updating_ui = True
        try:
            self.path_tree.delete(*self.path_tree.get_children())
            for i, path in enumerate(self.paths):
                closed = core.is_path_closed(path) if core is not None else False
                state = "闭合" if closed else "开放"
                self.path_tree.insert(
                    "", tk.END, iid=str(i), text=str(i), values=(len(path), state)
                )
            self.path_spinbox.configure(to=max(0, len(self.paths) - 1))
            self.selected_path_idx.set(0)
            if self.paths:
                self.path_tree.selection_set("0")
                self.path_tree.see("0")
        finally:
            self._updating_ui = False

    def _current_path_index(self) -> int:
        """当前选中的路径索引（越界时归一化）。"""
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
        if not selection:
            return
        self._updating_ui = True
        try:
            self.selected_path_idx.set(int(selection[0]))
        finally:
            self._updating_ui = False
        self._schedule_redraw()

    def _sync_tree_from_spinbox(self, *_args):
        if self._updating_ui or not self.paths:
            return
        idx = self._current_path_index()
        self._updating_ui = True
        try:
            self.selected_path_idx.set(idx)
            if self.path_tree.exists(str(idx)):
                self.path_tree.selection_set(str(idx))
                self.path_tree.see(str(idx))
        finally:
            self._updating_ui = False
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
        """在画布上绘制所有路径，高亮当前选中路径。"""
        self._redraw_job = None
        canvas = self.preview_canvas
        canvas.delete("all")
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width < 30 or height < 30:
            return
        if core is None or not self.paths:
            canvas.create_text(
                width / 2, height / 2, text="打开 SVG 文件后在此预览",
                fill="#999999", font=("Microsoft YaHei UI", 11),
            )
            return

        flip = self.flip_y.get()
        selected = self._current_path_index()

        # 采样所有路径，同时计算包围盒
        polylines = []  # (is_selected, [(x, y), ...])
        min_x = min_y = float("inf")
        max_x = max_y = float("-inf")
        for i, path in enumerate(self.paths):
            transformed = core.transform_path(path, flip) if flip else path
            for sub in core.split_path_at_discontinuities(transformed):
                if not sub:
                    continue
                n = max(40, min(self.PREVIEW_SAMPLES, 6 * len(sub)))
                pts = [sub.point(k / (n - 1)) for k in range(n)]
                polylines.append((i == selected, pts))
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

        # 先画未选中，再画选中（保证高亮在最上层）
        for is_selected in (False, True):
            for sel, pts in polylines:
                if sel != is_selected:
                    continue
                flat = []
                for p in pts:
                    cx, cy = to_canvas(p.real, p.imag)
                    flat.extend((cx, cy))
                if len(flat) >= 4:
                    canvas.create_line(
                        *flat,
                        fill="#2563eb" if sel else "#aab4c3",
                        width=2 if sel else 1,
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
        """生成表达式：在后台线程执行，完成后通过队列回传。"""
        if self._busy:
            return
        if core is None:
            messagebox.showerror("导入错误", "\n".join(_LOAD_ERRORS) or "核心模块不可用")
            return
        if not self.paths:
            messagebox.showwarning("警告", "请先加载 SVG 文件。")
            return

        mode = self.current_mode.get()
        params = {
            "mode": mode,
            "precision": self._read_int(self.precision, 4, 0, 10),
            "flip": self.flip_y.get(),
            "raw_paths": list(self.paths),
        }
        if mode == "fourier":
            params.update(
                harmonics=self._read_int(self.fourier_harmonics, 5, 1, 50),
                samples=self._read_int(self.fourier_samples, 1000, 2, 20000),
                split=bool(self.split_discont.get()),
                fit_all=bool(self.fit_all_paths.get()),
                path_index=self._current_path_index(),
            )
            if np is None:
                messagebox.showerror("缺少依赖", "傅里叶模式需要 numpy：\npip install numpy")
                return

        self._set_busy(True)
        self.status_var.set("正在计算，请稍候…")
        threading.Thread(target=self._worker, args=(params,), daemon=True).start()

    @staticmethod
    def _build_text(params: dict) -> str:
        """工作线程：构造报告文本（不触碰任何 Tk 对象）。"""
        paths = params["raw_paths"]
        if params["flip"]:
            paths = [core.transform_path(p, flip_y=True) for p in paths]

        if params["mode"] == "segments":
            return core.build_segment_report(paths, precision=params["precision"])

        return core.build_fourier_report(
            paths,
            n_harmonics=params["harmonics"],
            samples=params["samples"],
            precision=params["precision"],
            fit_all=params["fit_all"],
            path_index=params["path_index"],
            split=params["split"],
        )

    def _worker(self, params: dict):
        """后台线程入口：执行耗时计算并把结果放入队列。"""
        started = time.monotonic()
        try:
            text = self._build_text(params)
            self._results.put(("ok", text, time.monotonic() - started))
        except Exception as exc:
            self._results.put(("error", f"{type(exc).__name__}: {exc}", time.monotonic() - started))

    def _poll_results(self):
        """主线程轮询结果队列，安全地更新 UI。"""
        try:
            while True:
                kind, payload, elapsed = self._results.get_nowait()
                self._set_busy(False)
                if kind == "ok":
                    self._set_output(payload)
                    self.status_var.set(
                        f"生成完成，用时 {elapsed:.2f} 秒，共 {len(payload)} 个字符"
                    )
                else:
                    messagebox.showerror("生成失败", payload)
                    self.status_var.set("生成失败")
        except queue.Empty:
            pass
        self.root.after(self.POLL_MS, self._poll_results)

    def _set_output(self, text: str):
        self.output_text.configure(state=tk.NORMAL)
        self.output_text.delete("1.0", tk.END)
        self.output_text.insert(tk.END, text)
        self.stats_label.configure(text=f"{len(text)} 字符")

    # ---------------- 输出操作 ----------------

    def _output_content(self) -> str:
        return self.output_text.get("1.0", tk.END).rstrip("\n")

    def copy_output(self):
        content = self._output_content()
        if not content:
            messagebox.showinfo("提示", "没有内容可复制。")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        self.status_var.set("已复制到剪贴板")

    def save_output(self):
        content = self._output_content()
        if not content:
            messagebox.showinfo("提示", "没有内容可保存。")
            return
        initial = ""
        if self.filepath:
            initial = os.path.splitext(os.path.basename(self.filepath))[0] + ".txt"
        filepath = filedialog.asksaveasfilename(
            title="保存输出",
            initialfile=initial,
            defaultextension=".txt",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
        )
        if not filepath:
            return
        try:
            with open(filepath, "w", encoding="utf-8") as fh:
                fh.write(content)
            self.status_var.set(f"已保存到 {filepath}")
        except Exception as exc:
            messagebox.showerror("错误", f"保存失败：{exc}")

    def clear_output(self):
        self._set_output("")
        self.stats_label.configure(text="")
        self.status_var.set("输出已清空")

    def _toggle_wrap(self):
        wrap = tk.WORD if self.auto_wrap.get() else tk.NONE
        self.output_text.configure(wrap=wrap)

    def _show_about(self):
        messagebox.showinfo(
            "关于",
            "SVG 路径转数学表达式生成器\n\n"
            "将 SVG 路径转换为可直接粘贴到 Desmos 的参数方程，\n"
            "支持分段精确表达式与傅里叶级数拟合。\n\n"
            "快捷键：Ctrl+O 打开 · F5 生成 · Ctrl+S 保存 · Ctrl+Shift+C 复制",
        )


def main() -> int:
    if _LOAD_ERRORS:
        # 依赖缺失：先弹错误对话框（Tk 会自行创建默认窗口），再退出
        messagebox.showerror("缺少依赖", "\n\n".join(_LOAD_ERRORS))
        return 1
    root = tk.Tk()
    SVGMathGUI(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
