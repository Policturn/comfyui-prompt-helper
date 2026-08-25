# -*- coding: utf-8 -*-
"""外部提示词注入（prompt-helper 的 ComfyUI 节点版）

提供一个"外部提示词注入"节点：每次队列执行时现场重新读取词条 txt 文件
（由 prompt-helper / FeeTagHelper 等外部词条编辑器实时输出），并把词条
拼接到基础提示词，输出给 CLIP Text Encode 等节点。

通过 IS_CHANGED 返回 NaN 强制每次执行都重新读盘，绕过 ComfyUI 的节点
结果缓存——这是"文件内容实时变化"场景的关键。
"""

import json
import os
import time

NODE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(NODE_DIR, "config.json")

POSITIONS = ("追加到末尾", "插入到最前")


def _log(message):
    print(f"[prompt-helper] {message}")


def _default_path():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
            value = json.load(f).get("path", "")
        return str(value)
    except (OSError, ValueError, AttributeError):
        return ""


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
