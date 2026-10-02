# -*- coding: utf-8 -*-
"""
SVG2Desmos 界面中英双语字符串表与语言偏好管理。

设计要点:
    * 所有界面文案集中在 ``STRINGS`` 字典中, key -> {"zh": ..., "en": ...}
    * ``t(lang, key, **kw)`` 取词并做 str.format 插值; 缺 key 时回退 key 本身,
      单个语言缺翻译时回退到中文, 因此新增文案不会导致界面崩溃
    * 语言探测顺序: 环境变量 SVG2DESMOS_LANG -> 系统 locale -> 首选编码
    * 语言偏好写入用户目录下的 JSON (可用 SVG2DESMOS_CONFIG 改路径),
      任何读写失败都静默忽略, 不影响程序运行
"""

from __future__ import annotations

import json
import locale
import os
import sys

LANGS = ("zh", "en")
LANG_NATIVE = {"zh": "中文", "en": "English"}
DEFAULT_LANG = "zh"

# 代码语言 / 中文 Windows 常见编码 -> 中文界面
_ZH_LOCALE_PREFIXES = ("zh", "chinese", "chi_")
_ZH_ENCODINGS = {
    "cp936", "gbk", "gb2312", "gb18030", "big5", "big5hkscs", "cp950",
    "euc_cn", "euc_tw",
}

# SVG2DESMOS_LANG 环境变量可覆盖探测结果 (测试与无头环境使用)
ENV_LANG = "SVG2DESMOS_LANG"
ENV_CONFIG = "SVG2DESMOS_CONFIG"

STRINGS = {
    # ---- 应用 ----
    "app.title": {
        "zh": "SVG 路径转数学表达式",
        "en": "SVG Path to Math Expressions",
    },
    # ---- 菜单 ----
    "menu.file": {"zh": "文件", "en": "File"},
    "menu.file.open": {"zh": "打开 SVG…", "en": "Open SVG…"},
    "menu.file.save": {"zh": "保存输出…", "en": "Save Output…"},
    "menu.file.exit": {"zh": "退出", "en": "Exit"},
    "menu.edit": {"zh": "编辑", "en": "Edit"},
    "menu.edit.copy_all": {"zh": "复制全部输出", "en": "Copy All Output"},
    "menu.edit.clear": {"zh": "清空输出", "en": "Clear Output"},
    "menu.edit.wrap": {"zh": "自动换行", "en": "Wrap Lines"},
    "menu.view": {"zh": "视图", "en": "View"},
    "menu.view.language": {"zh": "语言", "en": "Language"},
    "menu.help": {"zh": "帮助", "en": "Help"},
    "menu.help.about": {"zh": "关于", "en": "About"},
    # ---- 文件区 ----
    "frame.file": {"zh": "文件", "en": "File"},
    "btn.open_svg": {"zh": "打开 SVG 文件", "en": "Open SVG File"},
    "file.none": {"zh": "未加载文件", "en": "No file loaded"},
    # ---- 路径列表 ----
    "frame.paths": {"zh": "路径（点击选中，Ctrl/Shift 可多选）",
                    "en": "Paths (click to select, Ctrl/Shift for several)"},
    "tree.segs": {"zh": "线段", "en": "Segments"},
    "tree.state": {"zh": "状态", "en": "State"},
    "state.closed": {"zh": "闭合", "en": "Closed"},
    "state.open": {"zh": "开放", "en": "Open"},
    "btn.select_all": {"zh": "全选", "en": "Select all"},
    "btn.clear_sel": {"zh": "清除选择", "en": "Clear"},
    "paths.selected": {"zh": "已选 {n}/{total} 条", "en": "{n}/{total} selected"},
    # ---- 输出模式 ----
    "frame.mode": {"zh": "输出模式", "en": "Output mode"},
    "mode.segments": {"zh": "分段表达式（精确）", "en": "Piecewise expressions (exact)"},
    "mode.fourier": {"zh": "傅里叶级数拟合", "en": "Fourier series fit"},
    # ---- 傅里叶参数 ----
    "frame.fourier": {"zh": "傅里叶参数", "en": "Fourier parameters"},
    "fourier.harmonics": {"zh": "谐波次数:", "en": "Harmonics:"},
    "fourier.samples": {"zh": "采样点数:", "en": "Sample points:"},
    "fourier.path_index": {"zh": "目标路径索引:", "en": "Target path index:"},
    "fourier.split": {"zh": "分割不连续点", "en": "Split discontinuities"},
    "fourier.fit_all": {"zh": "拟合所有路径", "en": "Fit all paths"},
    # ---- 通用参数 ----
    "frame.common": {"zh": "通用参数", "en": "Common settings"},
    "common.precision": {"zh": "小数精度:", "en": "Decimal precision:"},
    "common.flip": {"zh": "翻转 Y 坐标（适配 Desmos）",
                    "en": "Flip Y coordinate (for Desmos)"},
    # ---- 生成 ----
    "btn.generate": {"zh": "生成表达式  (F5)", "en": "Generate expressions  (F5)"},
    # ---- 预览 ----
    "frame.preview": {"zh": "预览（蓝色为已选路径）",
                      "en": "Preview (blue = selected paths)"},
    "preview.empty": {"zh": "打开 SVG 文件后在此预览",
                      "en": "Open an SVG file to preview it here"},
    # ---- 输出 ----
    "frame.output": {"zh": "输出结果", "en": "Output"},
    "btn.copy": {"zh": "复制到剪贴板", "en": "Copy to clipboard"},
    "btn.save": {"zh": "保存到文件", "en": "Save to file"},
    "btn.clear": {"zh": "清空输出", "en": "Clear output"},
    "opt.wrap": {"zh": "自动换行", "en": "Wrap lines"},
    "stats.chars": {"zh": "{n} 字符", "en": "{n} chars"},
    # ---- 状态栏 ----
    "status.ready": {"zh": "就绪", "en": "Ready"},
    "status.load_failed": {"zh": "加载失败", "en": "Load failed"},
    "status.loaded": {"zh": "已加载 {n} 条路径", "en": "Loaded {n} paths"},
    "status.loading": {"zh": "正在计算，请稍候…", "en": "Computing, please wait…"},
    "status.done": {"zh": "生成完成，用时 {elapsed} 秒，共 {n} 个字符",
                    "en": "Done in {elapsed} s — {n} characters"},
    "status.failed": {"zh": "生成失败", "en": "Generation failed"},
    "status.copied": {"zh": "已复制到剪贴板", "en": "Copied to clipboard"},
    "status.saved": {"zh": "已保存到 {path}", "en": "Saved to {path}"},
    "status.cleared": {"zh": "输出已清空", "en": "Output cleared"},
    "status.selected": {"zh": "已选中 {n} 条路径", "en": "{n} paths selected"},
    # ---- 对话框标题 ----
    "dlg.error": {"zh": "错误", "en": "Error"},
    "dlg.warn": {"zh": "警告", "en": "Warning"},
    "dlg.info": {"zh": "提示", "en": "Notice"},
    "dlg.dependency": {"zh": "缺少依赖", "en": "Missing dependency"},
    "dlg.about": {"zh": "关于", "en": "About"},
    "dlg.gen_failed": {"zh": "生成失败", "en": "Generation failed"},
    "dlg.import_error": {"zh": "导入错误", "en": "Import error"},
    # ---- 对话框正文 ----
    "err.no_svg": {"zh": "请先加载 SVG 文件。", "en": "Please load an SVG file first."},
    "err.no_selection": {
        "zh": "请先在路径列表中选择至少一条路径。",
        "en": "Please select at least one path in the path list first.",
    },
    "err.open_fail": {"zh": "加载 SVG 失败：{exc}", "en": "Failed to load SVG: {exc}"},
    "err.no_paths": {"zh": "SVG 文件中没有找到路径。",
                     "en": "No paths were found in this SVG file."},
    "err.numpy": {
        "zh": "傅里叶模式需要 numpy：\n\tpip install numpy",
        "en": "Fourier mode requires numpy:\n\tpip install numpy",
    },
    "err.save_fail": {"zh": "保存失败：{exc}", "en": "Failed to save: {exc}"},
    "err.no_copy": {"zh": "没有内容可复制。", "en": "There is nothing to copy."},
    "err.no_save": {"zh": "没有内容可保存。", "en": "There is nothing to save."},
    "err.core_missing": {"zh": "核心模块不可用", "en": "Core module unavailable"},
    "err.dep_pip": {"zh": "{name}：{exc}\n请运行 {cmd}",
                    "en": "{name}: {exc}\nRun: {cmd}"},
    "err.dep_dir": {
        "zh": "{name}：{exc}\n请确认 {name}.py 与本脚本在同一目录",
        "en": "{name}: {exc}\nMake sure {name}.py is in the same folder as this script",
    },
    # ---- 文件对话框 ----
    "dlg.open_svg": {"zh": "选择 SVG 文件", "en": "Choose an SVG file"},
    "dlg.save_output": {"zh": "保存输出", "en": "Save output"},
    "type.svg": {"zh": "SVG 文件", "en": "SVG files"},
    "type.txt": {"zh": "文本文件", "en": "Text files"},
    "type.all": {"zh": "所有文件", "en": "All files"},
    # ---- 关于 ----
    "about.body": {
        "zh": (
            "SVG 路径转数学表达式生成器\n\n"
            "将 SVG 路径转换为可直接粘贴到 Desmos 的参数方程，\n"
            "支持分段精确表达式与傅里叶级数拟合。\n\n"
            "快捷键：Ctrl+O 打开 · F5 生成 · Ctrl+S 保存 · Ctrl+Shift+C 复制\n"
            "在“视图 → 语言”中可切换 中文 / English"
        ),
        "en": (
            "SVG Path to Math Expression Generator\n\n"
            "Converts SVG paths into parametric equations that paste directly\n"
            "into Desmos; supports exact piecewise expressions and Fourier\n"
            "series fitting.\n\n"
            "Shortcuts: Ctrl+O open · F5 generate · Ctrl+S save · Ctrl+Shift+C copy\n"
            "Switch between English / 中文 in View → Language"
        ),
    },
}


# ---------------- 取词 ----------------


def normalize(lang) -> str:
    """把任意语言标识归一化为 'zh' / 'en'。"""
    if isinstance(lang, str):
        code = lang.strip().lower().replace("_", "-")
        if code.startswith("zh") or code.startswith("chinese"):
            return "zh"
        if code.startswith("en") or code.startswith("english"):
            return "en"
    return DEFAULT_LANG


def t(lang: str, key: str, **kwargs) -> str:
    """
    取 key 对应文案并插值。

    缺 key -> 返回 key；该语言缺翻译 -> 回退中文；格式化失败 -> 返回原始模板，
    任何情况下都不抛异常。
    """
    entry = STRINGS.get(key)
    if entry is None:
        return key
    text = entry.get(lang) or entry.get(DEFAULT_LANG) or key
    if not kwargs:
        return text
    try:
        return text.format(**kwargs)
    except Exception:
        return text


# ---------------- 语言探测与持久化 ----------------


def _default_config_path() -> str:
    override = os.environ.get(ENV_CONFIG)
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".svg2desmos_gui.json")


def _looks_chinese(value: str) -> bool:
    lowered = (value or "").strip().lower()
    if lowered.startswith(_ZH_LOCALE_PREFIXES):
        return True
    # 编码名: cp936 / gbk / gb18030 / big5 …
    return lowered in _ZH_ENCODINGS or any(
        part in lowered for part in ("gb18030", "gbk", "big5")
    )


def _looks_english(value: str) -> bool:
    lowered = (value or "").strip().lower()
    if not lowered or lowered in ("c", "posix", "unknown"):
        return False
    return lowered.startswith(("en", "english", "american"))


def detect_language() -> str:
    """
    探测界面语言。

    顺序: SVG2DESMOS_LANG 环境变量 -> LANG/LC_ALL -> locale.getlocale()
    -> 首选编码; 全部无法判定时返回 DEFAULT_LANG。
    """
    env = os.environ.get(ENV_LANG)
    if env:
        return normalize(env)

    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var)
        if not value:
            continue
        if _looks_chinese(value):
            return "zh"
        if _looks_english(value):
            return "en"

    try:
        name = locale.getlocale()[0]
    except Exception:
        name = None
    if name:
        if _looks_chinese(name):
            return "zh"
        if _looks_english(name):
            return "en"

    try:
        encoding = locale.getpreferredencoding(False) or ""
    except Exception:
        encoding = ""
    if _looks_chinese(encoding):
        return "zh"
    if _looks_english(encoding):
        return "en"

    return DEFAULT_LANG


def load_language() -> str:
    """读取上次保存的语言，失败或缺失时按系统环境探测。"""
    env = os.environ.get(ENV_LANG)
    if env:
        return normalize(env)
    try:
        with open(_default_config_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        stored = data.get("language")
        if stored:
            return normalize(stored)
    except Exception:
        pass
    return detect_language()


def save_language(lang: str) -> bool:
    """保存语言偏好；任何失败都返回 False 而不抛异常。"""
    path = _default_config_path()
    try:
        data = {}
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh) or {}
        except Exception:
            data = {}
        data["language"] = normalize(lang)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


if __name__ == "__main__":  # pragma: no cover - 便捷入口
    print(f"detected: {detect_language()}  stored: {load_language()}")
    print(t("zh", "app.title"), "|", t("en", "app.title"))
    sys.exit(0)
