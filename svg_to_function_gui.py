#!/usr/bin/env python3
"""
SVG 路径数学表达式生成器 - 图形界面

依赖: svgpathtools, numpy, tkinter (内置)
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import sys
import threading
from typing import List, Tuple, Optional

# 尝试导入核心依赖
try:
    from svgpathtools import svg2paths, Path
except ImportError:
    messagebox.showerror("缺少依赖", "请先安装 svgpathtools：\npip install svgpathtools")
    sys.exit(1)

try:
    import numpy as np
except ImportError:
    np = None

# 导入原脚本中的处理函数（假设 svg_to_function.py 在同一目录）
try:
    from svg_to_function import (
        process_paths,
        fourier_fit_path,
        split_path_at_discontinuities,
        collect_continuous_segments,
        PRECISION as default_precision,
    )
except ImportError:
    messagebox.showerror("导入错误", "找不到 svg_to_function.py，请确保该文件在脚本同一目录下。")
    sys.exit(1)


class SVGMathGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("SVG 路径转数学表达式")
        self.root.geometry("900x700")
        self.root.minsize(800, 600)

        # 存储数据
        self.paths = []          # 原始路径列表
        self.current_mode = tk.StringVar(value="segments")
        self.fourier_harmonics = tk.IntVar(value=5)
        self.fourier_samples = tk.IntVar(value=1000)
        self.precision = tk.IntVar(value=4)
        self.fit_all_paths = tk.BooleanVar(value=False)
        self.split_discont = tk.BooleanVar(value=False)
        self.selected_path_idx = tk.IntVar(value=0)

        self.build_ui()

    def build_ui(self):
        """构建界面布局"""
        # 主框架
        main_frame = ttk.Frame(self.root, padding="5")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 左：控制面板
        left_frame = ttk.LabelFrame(main_frame, text="控制面板", padding="5")
        left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)

        # 文件选择
        ttk.Button(left_frame, text="打开 SVG 文件", command=self.load_svg).pack(fill=tk.X, pady=5)
        self.file_label = ttk.Label(left_frame, text="未加载文件", wraplength=200)
        self.file_label.pack(fill=tk.X, pady=2)

        ttk.Separator(left_frame, orient='horizontal').pack(fill=tk.X, pady=5)

        # 路径信息
        ttk.Label(left_frame, text="路径信息").pack(anchor=tk.W)
        self.path_info_text = tk.Text(left_frame, height=10, width=30, state=tk.DISABLED, wrap=tk.WORD)
        self.path_info_text.pack(fill=tk.BOTH, expand=True, pady=5)

        # 模式选择
        ttk.Label(left_frame, text="输出模式").pack(anchor=tk.W, pady=(10, 0))
        ttk.Radiobutton(left_frame, text="分段表达式", variable=self.current_mode, value="segments").pack(anchor=tk.W)
        ttk.Radiobutton(left_frame, text="傅里叶级数拟合", variable=self.current_mode, value="fourier").pack(anchor=tk.W)

        # 傅里叶参数（初始启用）
        fourier_frame = ttk.LabelFrame(left_frame, text="傅里叶参数", padding="5")
        fourier_frame.pack(fill=tk.X, pady=5)

        # 谐波次数
        ttk.Label(fourier_frame, text="谐波次数:").grid(row=0, column=0, sticky=tk.W, padx=2, pady=2)
        ttk.Spinbox(fourier_frame, from_=1, to=50, textvariable=self.fourier_harmonics, width=6,
                    increment=1).grid(row=0, column=1, padx=5)

        # 采样点数
        ttk.Label(fourier_frame, text="采样点数:").grid(row=1, column=0, sticky=tk.W, padx=2, pady=2)
        ttk.Spinbox(fourier_frame, from_=100, to=5000, textvariable=self.fourier_samples, width=6,
                    increment=100).grid(row=1, column=1, padx=5)

        # 选项
        ttk.Checkbutton(fourier_frame, text="分割不连续点", variable=self.split_discont).grid(
            row=2, column=0, columnspan=2, sticky=tk.W, pady=2)
        ttk.Checkbutton(fourier_frame, text="拟合所有路径", variable=self.fit_all_paths).grid(
            row=3, column=0, columnspan=2, sticky=tk.W, pady=2)

        # 路径选择（仅单路径模式）
        path_select_frame = ttk.Frame(left_frame)
        path_select_frame.pack(fill=tk.X, pady=5)
        ttk.Label(path_select_frame, text="目标路径索引:").pack(side=tk.LEFT)
        self.path_spinbox = ttk.Spinbox(path_select_frame, from_=0, to=0, textvariable=self.selected_path_idx,
                                        width=5, increment=1)
        self.path_spinbox.pack(side=tk.LEFT, padx=5)

        # 公共参数
        ttk.Label(left_frame, text="小数精度:").pack(anchor=tk.W, pady=(10, 0))
        ttk.Spinbox(left_frame, from_=0, to=10, textvariable=self.precision, width=5,
                    increment=1).pack(anchor=tk.W)

        # 生成按钮
        ttk.Button(left_frame, text="生成表达式", command=self.generate_expr).pack(fill=tk.X, pady=10)

        # 右：输出区域
        right_frame = ttk.LabelFrame(main_frame, text="输出结果", padding="5")
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.output_text = scrolledtext.ScrolledText(right_frame, wrap=tk.WORD, font=("Consolas", 10))
        self.output_text.pack(fill=tk.BOTH, expand=True)

        # 底部按钮
        btn_frame = ttk.Frame(right_frame)
        btn_frame.pack(fill=tk.X, pady=5)
        ttk.Button(btn_frame, text="复制到剪贴板", command=self.copy_output).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="保存到文件", command=self.save_output).pack(side=tk.LEFT, padx=5)

        # 状态栏
        self.status_var = tk.StringVar(value="就绪")
        status_bar = ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)

    def load_svg(self):
        """加载 SVG 文件"""
        filepath = filedialog.askopenfilename(filetypes=[("SVG文件", "*.svg"), ("所有文件", "*.*")])
        if not filepath:
            return
        try:
            self.paths, _ = svg2paths(filepath)
            if not self.paths:
                messagebox.showwarning("警告", "SVG 文件中没有找到路径。")
                return
            self.file_label.config(text=filepath.split('/')[-1])
            self.update_path_info()
            self.path_spinbox.config(to=max(0, len(self.paths)-1))
            self.status_var.set(f"已加载 {len(self.paths)} 条路径")
        except Exception as e:
            messagebox.showerror("错误", f"加载 SVG 失败：{e}")
            self.status_var.set("加载失败")

    def update_path_info(self):
        """更新左侧路径信息显示"""
        info = []
        for i, path in enumerate(self.paths):
            seg_count = len(path)
            info.append(f"路径 {i}: {seg_count} 个线段")
        self.path_info_text.config(state=tk.NORMAL)
        self.path_info_text.delete(1.0, tk.END)
        self.path_info_text.insert(tk.END, "\n".join(info))
        self.path_info_text.config(state=tk.DISABLED)

    def generate_expr(self):
        """根据当前设置生成表达式（可能耗时，在后台线程中运行）"""
        if not self.paths:
            messagebox.showwarning("警告", "请先加载 SVG 文件。")
            return

        mode = self.current_mode.get()
        precision = self.precision.get()
        # 设置全局精度（原脚本使用全局变量）
        try:
            import svg_to_function
            svg_to_function.PRECISION = precision
        except:
            pass

        if mode == "segments":
            self.generate_segments()
        else:
            self.generate_fourier()

    def generate_segments(self):
        """分段模式，直接使用原脚本的 process_paths"""
        try:
            output_lines, coord_exprs = process_paths(self.paths)
            if coord_exprs:
                output_lines.extend(["", "各线段坐标表达式 (每行一条):"])
                output_lines.extend(coord_exprs)
            output_text = "\n".join(output_lines)
            self.output_text.delete(1.0, tk.END)
            self.output_text.insert(tk.END, output_text)
            self.status_var.set("分段表达式生成完成")
        except Exception as e:
            messagebox.showerror("错误", f"生成失败：{e}")
            self.status_var.set("生成失败")

    def generate_fourier(self):
        """傅里叶模式，在后台线程中执行以避免界面冻结"""
        if np is None:
            messagebox.showerror("缺少依赖", "傅里叶模式需要 numpy，请运行：pip install numpy")
            return

        # 获取参数
        harmonics = self.fourier_harmonics.get()
        samples = self.fourier_samples.get()
        split = self.split_discont.get()
        fit_all = self.fit_all_paths.get()
        path_idx = self.selected_path_idx.get()

        if not fit_all and (path_idx < 0 or path_idx >= len(self.paths)):
            messagebox.showerror("错误", f"路径索引 {path_idx} 无效，共有 {len(self.paths)} 条路径。")
            return

        # 禁用按钮，显示进度
        self.status_var.set("正在计算傅里叶级数，请稍候...")
        # 启动线程
        thread = threading.Thread(target=self._fourier_worker,
                                  args=(harmonics, samples, split, fit_all, path_idx),
                                  daemon=True)
        thread.start()

    def _fourier_worker(self, harmonics, samples, split, fit_all, path_idx):
        """后台执行傅里叶拟合，完成后更新 UI"""
        try:
            from svg_to_function import fourier_fit_path, collect_continuous_segments
            output_parts = []

            if fit_all:
                # 收集所有连续段
                segments = collect_continuous_segments(self.paths, split)
                results = []
                for label, path in segments:
                    try:
                        x_expr, y_expr, coord_expr = fourier_fit_path(path, harmonics, samples, self.precision.get())
                        results.append((label, x_expr, y_expr, coord_expr))
                    except Exception as e:
                        results.append((label, f"拟合失败: {e}", "", ""))
                # 构建输出文本
                for label, x_expr, y_expr, coord_expr in results:
                    if x_expr.startswith("拟合失败"):
                        output_parts.append(f"{label} 拟合失败: {x_expr}")
                    else:
                        output_parts.append(f"{label} 傅里叶级数拟合 (谐波数 N={harmonics}):")
                        output_parts.append(x_expr)
                        output_parts.append(y_expr)
                        output_parts.append("t ∈ [0, 2π]")
                        output_parts.append("---------------------")
                if results:
                    output_parts.append("各连续段坐标表达式 (每行一条):")
                    for _, _, _, coord_expr in results:
                        if coord_expr:
                            output_parts.append(coord_expr)
            else:
                # 单路径模式，需要处理分割
                path = self.paths[path_idx]
                if split:
                    subpaths = split_path_at_discontinuities(path)
                    if len(subpaths) == 1:
                        x_expr, y_expr, coord_expr = fourier_fit_path(path, harmonics, samples, self.precision.get())
                        output_parts.append(f"路径 {path_idx} 傅里叶级数拟合 (谐波数 N={harmonics}):")
                        output_parts.append(x_expr)
                        output_parts.append(y_expr)
                        output_parts.append("t ∈ [0, 2π]")
                        output_parts.append("坐标表达式:")
                        output_parts.append(coord_expr)
                    else:
                        output_parts.append(f"路径 {path_idx} 被分割为 {len(subpaths)} 个连续段:")
                        for sub_idx, subpath in enumerate(subpaths):
                            x_expr, y_expr, coord_expr = fourier_fit_path(subpath, harmonics, samples, self.precision.get())
                            output_parts.append(f"段 {sub_idx}:")
                            output_parts.append(x_expr)
                            output_parts.append(y_expr)
                            output_parts.append("坐标表达式: " + coord_expr)
                            output_parts.append("---")
                else:
                    x_expr, y_expr, coord_expr = fourier_fit_path(path, harmonics, samples, self.precision.get())
                    output_parts.append(f"路径 {path_idx} 傅里叶级数拟合 (谐波数 N={harmonics}):")
                    output_parts.append(x_expr)
                    output_parts.append(y_expr)
                    output_parts.append("t ∈ [0, 2π]")
                    output_parts.append("坐标表达式:")
                    output_parts.append(coord_expr)

            output_text = "\n".join(output_parts)
            # 更新 UI（需在主线程中）
            self.root.after(0, self._update_output, output_text)
            self.root.after(0, lambda: self.status_var.set("傅里叶拟合完成"))
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("错误", f"傅里叶拟合失败：{e}"))
            self.root.after(0, lambda: self.status_var.set("生成失败"))

    def _update_output(self, text):
        """更新输出文本框"""
        self.output_text.delete(1.0, tk.END)
        self.output_text.insert(tk.END, text)

    def copy_output(self):
        """复制输出内容到剪贴板"""
        content = self.output_text.get(1.0, tk.END).strip()
        if not content:
            messagebox.showinfo("提示", "没有内容可复制。")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        self.status_var.set("已复制到剪贴板")

    def save_output(self):
        """保存输出内容到文件"""
        content = self.output_text.get(1.0, tk.END).strip()
        if not content:
            messagebox.showinfo("提示", "没有内容可保存。")
            return
        filepath = filedialog.asksaveasfilename(defaultextension=".txt",
                                                 filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
        if not filepath:
            return
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            self.status_var.set(f"已保存到 {filepath}")
        except Exception as e:
            messagebox.showerror("错误", f"保存失败：{e}")


if __name__ == "__main__":
    root = tk.Tk()
    app = SVGMathGUI(root)
    root.mainloop()