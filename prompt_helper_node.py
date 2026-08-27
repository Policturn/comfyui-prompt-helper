# -*- coding: utf-8 -*-
"""外部提示词注入（prompt-helper 的 ComfyUI 节点版）

提供一个"外部提示词注入"节点：每次队列执行时现场重新读取词条 txt 文件
（由 prompt-helper / FeeTagHelper 等外部词条编辑器实时输出），并把词条
拼接到基础提示词，输出给 CLIP Text Encode 等节点。

通过 IS_CHANGED 返回 NaN 强制每次执行都重新读盘，绕过 ComfyUI 的节点
结果缓存——这是"文件内容实时变化"场景的关键。

另提供可选联动：ComfyUI 启动加载本节点包时自动拉起外部词条编辑器
（由 __init__.py 调用 autostart_editor；防重复启动；编辑器作为独立进程
运行，关闭 ComfyUI 不会连带关闭它）。
"""

import json
import os
import subprocess
import time

NODE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(NODE_DIR, "config.json")

POSITIONS = ("追加到末尾", "插入到最前")

DEFAULT_CONFIG = {
    "path": "",
    "autostart": False,
    "editor_path": "",
}


def _log(message):
    print(f"[prompt-helper] {message}")


def _load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            cfg.update(loaded)
    except (OSError, ValueError):
        pass
    cfg["autostart"] = bool(cfg["autostart"])
    cfg["path"] = str(cfg["path"] or "")
    cfg["editor_path"] = str(cfg["editor_path"] or "")
    return cfg


def _default_path():
    return _load_config()["path"]


def _normalize_path(path):
    path = (path or "").strip().strip('"').strip("'")
    return os.path.expandvars(os.path.expanduser(path))


def read_tag_file(path, merge_lines=True):
    """读取词条文件。返回 (内容或 None, 状态消息)。"""
    path = _normalize_path(path)
    if not path:
        return None, "未设置文件路径"
    if not os.path.isfile(path):
        return None, "文件不存在：" + path

    last_error = "编码无法识别（UTF-8 / GBK 均解码失败）"
    for _ in range(3):  # 编辑器写入瞬间可能短暂占用文件，重试几次
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                with open(path, "r", encoding=encoding) as f:
                    text = f.read()
            except UnicodeDecodeError:
                continue
            except OSError as e:
                last_error = "读取失败：" + str(e)
                break
            text = text.strip()
            if not text:
                return None, "文件为空"
            if merge_lines:
                text = " ".join(text.split())
            return text, "文件正常"
        time.sleep(0.15)
    return None, last_error


def _inject(base, tags, prepend, sep=", "):
    if not base:
        return tags
    return f"{tags}{sep}{base}" if prepend else f"{base}{sep}{tags}"


def _is_process_running(exe_name):
    """查询同名进程是否已在运行（用于防止编辑器被重复拉起）。"""
    exe_name = exe_name.lower()
    try:
        if os.name == "nt":
            # tasklist 在中文系统输出 GBK，errors="replace" 防止解码崩溃（exe 名匹配不受影响）
            result = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {exe_name}"],
                capture_output=True, text=True, errors="replace", timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return exe_name in (result.stdout or "").lower()
        output = subprocess.run(
            ["pgrep", "-f", exe_name], capture_output=True, text=True, timeout=10,
        ).stdout
        return bool((output or "").strip())
    except (OSError, subprocess.SubprocessError):
        return False  # 检测失败时宁可重复启动，也不要让编辑器永远起不来


def launch_editor(editor_path):
    """启动外部词条编辑器（独立进程，关闭 ComfyUI 不会连带关闭它）。返回 (是否成功, 消息)。"""
    editor_path = _normalize_path(editor_path)
    if not editor_path:
        return False, "未设置编辑器路径"
    if not os.path.isfile(editor_path):
        return False, "文件不存在：" + editor_path

    exe_name = os.path.basename(editor_path)
    if _is_process_running(exe_name):
        return True, f"{exe_name} 已在运行，跳过启动"

    flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
             if os.name == "nt" else 0)
    try:
        subprocess.Popen([editor_path], cwd=os.path.dirname(editor_path),
                         creationflags=flags, close_fds=True)
    except OSError as e:
        return False, f"启动失败：{e}"
    return True, "已启动：" + editor_path


def autostart_editor():
    """ComfyUI 启动加载本节点包时由 __init__.py 调用。"""
    cfg = _load_config()
    if not cfg["autostart"]:
        return
    ok, message = launch_editor(cfg["editor_path"])
    _log(f"自动启动编辑器：{message}")


class PromptHelperInject:
    """读取外部词条文件并注入提示词。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "path": ("STRING", {
                    "default": _default_path(),
                    "multiline": False,
                    "tooltip": "词条 txt 文件的绝对路径；每次执行时重新读取最新内容",
                }),
                "base_prompt": ("STRING", {
                    "default": "",
                    "multiline": True,
                    "tooltip": "现有提示词；可右键转换为输入端口，接其他文本节点",
                }),
                "position": (list(POSITIONS), {"tooltip": "词条拼接到基础提示词的末尾还是最前"}),
                "merge_lines": ("BOOLEAN", {"default": True, "tooltip": "把文件内换行合并为一行"}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("prompt", "tags", "status")
    FUNCTION = "inject"
    CATEGORY = "prompt-helper"
    DESCRIPTION = "每次执行时重新读取外部 txt 词条文件并注入提示词（prompt-helper 桥接节点）"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        # NaN 与任何值（包括自身）都不相等 → 每次队列执行都强制重新读盘
        return float("NaN")

    def inject(self, path, base_prompt, position, merge_lines):
        tags, message = read_tag_file(path, merge_lines)
        if tags is None:
            _log(f"跳过注入：{message}")
            return (base_prompt or "", "", message)

        combined = _inject(base_prompt or "", tags, position == POSITIONS[1])
        shown = tags[:120] + ("…" if len(tags) > 120 else "")
        _log(f"已注入 {len(tags)} 个字符（{message}）：{shown}")
        return (combined, tags, message)


NODE_CLASS_MAPPINGS = {
    "PromptHelperInject": PromptHelperInject,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "PromptHelperInject": "外部提示词注入 (prompt-helper)",
}
