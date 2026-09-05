# -*- coding: utf-8 -*-
"""外部提示词注入（prompt-helper 的 ComfyUI 节点版）

提供一个"外部提示词注入"节点：每次队列执行时现场重新读取正向 / 反向两个
词条 txt 文件（由 prompt-helper / FeeTagHelper 等外部词条编辑器实时输出），
并把词条分别拼接到正向、反向基础提示词，输出给 CLIP Text Encode 等节点
（反向路径留空则只处理正向）。

通过 IS_CHANGED 返回 NaN 强制每次执行都重新读盘，绕过 ComfyUI 的节点
结果缓存——这是"文件内容实时变化"场景的关键。

另提供可选联动：ComfyUI 启动加载本节点包时自动拉起外部词条编辑器
（由 __init__.py 调用 autostart_editor；防重复启动；编辑器作为独立进程
运行，关闭 ComfyUI 不会连带关闭它）。

FeeTagHelper 构建区可能在 txt 末尾追加元数据 tag（<fth:meta:…>，携带 BREAK
位置 / 选一记录）。注入前会剥离该 tag（解码失败静默丢弃），解码后的元数据
写入节点目录的 prompt_helper_meta.json（附插件版本 + 时间戳）——ComfyUI 的
PNG 工作流元数据由节点输入值构成，运行期读取的文件内容无法注入，故用文件
兜底记录。ComfyUI 分块机制与 WebUI 不同，breaks 位置信息不展开、直接丢弃。
"""

import base64
import binascii
import json
import os
import re
import subprocess
import time

NODE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(NODE_DIR, "config.json")
META_LOG_PATH = os.path.join(NODE_DIR, "prompt_helper_meta.json")

PLUGIN_VERSION = "1.4.0"

POSITIONS = ("追加到末尾", "插入到最前")

# FeeTagHelper 构建区元数据 tag：<fth:meta:BASE64URL>（base64url 无填充，字符集不含逗号，
# 不破坏 tag 流；载荷为紧凑 JSON：v / breaks / pick）。追加在 txt 末尾，注入前剥离。
META_TAG_RE = re.compile(r"<fth:meta:([A-Za-z0-9_-]+)>")

DEFAULT_CONFIG = {
    "path": "",
    "negative_path": "",
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
    for key in ("path", "negative_path", "editor_path"):
        cfg[key] = str(cfg[key] or "")
    return cfg


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


def _decode_meta_payload(payload):
    """base64url 解码元数据 tag 载荷。失败返回 None（调用方静默丢弃，不报错不中断）。"""
    try:
        padded = payload + "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, binascii.Error):
        return None
    return data if isinstance(data, dict) else None


def strip_meta_tags(text):
    """剥离词条文本中的 <fth:meta:…> 元数据 tag。返回 (剥离后文本, 元数据列表)。

    元数据 tag 由 FeeTagHelper 构建区追加在 txt 末尾（base64url 编码的紧凑
    JSON，字段 v / breaks / pick）。无论解码是否成功，整个 tag 都被移除、
    不进入生成用提示词；解码失败的 tag 静默丢弃，不报错不中断。
    文本不含元数据 tag 时原样返回（分隔符不动）。
    """
    if not text or "<fth:meta:" not in text:
        return text, []

    metas = []

    def _take(match):
        meta = _decode_meta_payload(match.group(1))
        if meta is not None:
            metas.append(meta)
        return ""  # 解码失败的 tag 同样整个移除

    kept = []
    for chunk in text.split(","):
        chunk = META_TAG_RE.sub(_take, chunk).strip()
        if chunk:
            kept.append(chunk)
    return ", ".join(kept), metas


def _record_meta(meta_by_side):
    """把剥离出的元数据写入节点目录 sidecar JSON（附插件版本 + 时间戳，每次注入覆盖）。"""
    entry = {"updated": time.strftime("%Y-%m-%d %H:%M:%S"), "plugin": PLUGIN_VERSION}
    for side, meta in meta_by_side.items():
        if meta is not None:
            entry[side] = meta
    try:
        with open(META_LOG_PATH, "w", encoding="utf-8") as f:
            json.dump(entry, f, ensure_ascii=False, indent=2)
    except OSError:
        pass  # 记录失败不影响注入


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
        cfg = _load_config()
        return {
            "required": {
                "path": ("STRING", {
                    "default": cfg["path"],
                    "multiline": False,
                    "tooltip": "正向词条 txt 文件的绝对路径；每次执行时重新读取最新内容",
                }),
                "negative_path": ("STRING", {
                    "default": cfg["negative_path"],
                    "multiline": False,
                    "tooltip": "反向词条 txt 文件的绝对路径；留空则不注入反向提示词",
                }),
                "base_prompt": ("STRING", {
                    "default": "",
                    "multiline": True,
                    "tooltip": "正向基础提示词；可右键转换为输入端口，接其他文本节点",
                }),
                "negative_base_prompt": ("STRING", {
                    "default": "",
                    "multiline": True,
                    "tooltip": "反向基础提示词；反向词条（若设置）会拼接到它后面/前面",
                }),
                "position": (list(POSITIONS), {"tooltip": "词条拼接到基础提示词的末尾还是最前"}),
                "merge_lines": ("BOOLEAN", {"default": True, "tooltip": "把文件内换行合并为一行"}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("prompt", "negative_prompt", "tags", "status")
    FUNCTION = "inject"
    CATEGORY = "prompt-helper"
    DESCRIPTION = "每次执行时重新读取外部 txt 词条文件并注入提示词（prompt-helper 桥接节点）"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        # NaN 与任何值（包括自身）都不相等 → 每次队列执行都强制重新读盘
        return float("NaN")

    def inject(self, path, negative_path, base_prompt, negative_base_prompt, position, merge_lines):
        prepend = position == POSITIONS[1]

        prompt_out, tags_out, pos_status, pos_meta = base_prompt or "", "", "", None
        tags, message = read_tag_file(path, merge_lines)
        if tags is None:
            _log(f"正向跳过注入：{message}")
            pos_status = message
        else:
            tags, metas = strip_meta_tags(tags)
            pos_meta = metas[-1] if metas else None
            if not tags:
                _log("正向跳过注入：剥离元数据 tag 后内容为空")
                pos_status = "剥离元数据 tag 后内容为空"
            else:
                prompt_out = _inject(base_prompt or "", tags, prepend)
                tags_out = tags
                pos_status = message
                if pos_meta is not None:
                    _log(f"正向元数据已剥离并记录（breaks={pos_meta.get('breaks')}）")
                shown = tags[:120] + ("…" if len(tags) > 120 else "")
                _log(f"正向已注入 {len(tags)} 个字符（{message}）：{shown}")

        neg_out, neg_status, neg_meta = negative_base_prompt or "", "未设置", None
        if _normalize_path(negative_path):
            neg_tags, neg_message = read_tag_file(negative_path, merge_lines)
            if neg_tags is None:
                _log(f"反向跳过注入：{neg_message}")
                neg_status = neg_message
            else:
                neg_tags, neg_metas = strip_meta_tags(neg_tags)
                neg_meta = neg_metas[-1] if neg_metas else None
                if not neg_tags:
                    _log("反向跳过注入：剥离元数据 tag 后内容为空")
                    neg_status = "剥离元数据 tag 后内容为空"
                else:
                    neg_out = _inject(negative_base_prompt or "", neg_tags, prepend)
                    neg_status = neg_message
                    if neg_meta is not None:
                        _log(f"反向元数据已剥离并记录（breaks={neg_meta.get('breaks')}）")
                    _log(f"反向已注入 {len(neg_tags)} 个字符（{neg_message}）")

        if pos_meta is not None or neg_meta is not None:
            _record_meta({"positive": pos_meta, "negative": neg_meta})

        return (prompt_out, neg_out, tags_out, f"正向：{pos_status}；反向：{neg_status}")


NODE_CLASS_MAPPINGS = {
    "PromptHelperInject": PromptHelperInject,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "PromptHelperInject": "外部提示词注入 (prompt-helper)",
}
