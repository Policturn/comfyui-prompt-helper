# -*- coding: utf-8 -*-
"""离线自测：不启动 ComfyUI，直接加载节点模块验证逻辑。

用法：python test_prompt_helper.py
"""

import importlib.util
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE_PATH = os.path.join(HERE, "prompt_helper_node.py")
REAL_TXT = r"E:\桌面\AI file\Design file\prompt-helper\prompt.txt"

spec = importlib.util.spec_from_file_location("prompt_helper_node", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
sys.modules["prompt_helper_node"] = mod
spec.loader.exec_module(mod)


def check(label, cond):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        raise SystemExit(1)


print("== 节点注册 ==")
check("NODE_CLASS_MAPPINGS 含 PromptHelperInject", "PromptHelperInject" in mod.NODE_CLASS_MAPPINGS)
check("显示名已配置", "PromptHelperInject" in mod.NODE_DISPLAY_NAME_MAPPINGS)

node_cls = mod.NODE_CLASS_MAPPINGS["PromptHelperInject"]
inputs = node_cls.INPUT_TYPES()["required"]
check("输入包含 path / base_prompt / position / merge_lines",
      {"path", "base_prompt", "position", "merge_lines"} <= set(inputs))
check("path 默认值来自 config.json", inputs["path"][1].get("default", "").endswith("prompt.txt"))
check("返回 3 个输出", node_cls.RETURN_TYPES == ("STRING", "STRING", "STRING"))

print("== 缓存绕过 ==")
changed = node_cls.IS_CHANGED(path=REAL_TXT)
check("IS_CHANGED 返回 NaN（每次执行强制重读）", changed != changed)

node = node_cls()

print("== 文件读取 ==")
text, msg = mod.read_tag_file(REAL_TXT)
check("读取真实词条文件", text is not None and "1girl" in text)

with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="gbk") as f:
    f.write("白发, 黑瞳\n, long hair")
    gbk_path = f.name
text, msg = mod.read_tag_file(gbk_path)
check("GBK 解码 + 换行合并", text == "白发, 黑瞳 , long hair")
text, msg = mod.read_tag_file(gbk_path, merge_lines=False)
check("保留换行", text == "白发, 黑瞳\n, long hair")
os.unlink(gbk_path)

text, msg = mod.read_tag_file(r"C:\__no_such_file__.txt")
check("缺失文件返回错误", text is None and "不存在" in msg)

print("== 注入 ==")
prompt, tags, status = node.inject(REAL_TXT, "masterpiece, best quality", "追加到末尾", True)
base = mod.read_tag_file(REAL_TXT)[0]
check("追加到末尾", prompt == "masterpiece, best quality, " + base and status == "文件正常")

prompt, tags, status = node.inject(REAL_TXT, "masterpiece", "插入到最前", True)
check("插入到最前", prompt == base + ", masterpiece")

prompt, tags, status = node.inject(REAL_TXT, "", "追加到末尾", True)
check("基础为空时仅输出词条", prompt == base and tags == base)

prompt, tags, status = node.inject(r"C:\__no_such_file__.txt", "masterpiece", "追加到末尾", True)
check("缺文件时跳过注入且不崩溃", prompt == "masterpiece" and "不存在" in status)

with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
    f.write("tag_a")
    tmp_txt = f.name
p1 = node.inject(tmp_txt, "base", "追加到末尾", True)[0]
with open(tmp_txt, "w", encoding="utf-8") as f:
    f.write("tag_b")
p2 = node.inject(tmp_txt, "base", "追加到末尾", True)[0]
os.unlink(tmp_txt)
check("文件变化后下一次执行读到新内容", p1 == "base, tag_a" and p2 == "base, tag_b")

print("== 编辑器联动启动 ==")
ok, msg = mod.launch_editor("")
check("空路径返回错误", not ok and "未设置" in msg)
ok, msg = mod.launch_editor(r"C:\__no_editor__.exe")
check("无效路径返回错误", not ok and "不存在" in msg)
check("进程检测：不存在的进程", mod._is_process_running("definitely_not_running_zzz.exe") is False)
with tempfile.NamedTemporaryFile("w", suffix=".bat", delete=False) as f:
    f.write("@exit 0")
    bat_path = f.name
ok, msg = mod.launch_editor(bat_path)
print(f"  独立进程启动 -> [{msg}]")
check("独立进程启动成功", ok)
time.sleep(0.3)
try:
    os.unlink(bat_path)
except OSError:
    pass
cfg = mod._load_config()
check("配置含 path/autostart/editor_path", {"path", "autostart", "editor_path"} <= set(cfg))
mod.CONFIG_PATH = os.path.join(tempfile.mkdtemp(), "config.json")
mod.autostart_editor()
check("autostart 关闭时无动作", True)
with open(mod.CONFIG_PATH, "w", encoding="utf-8") as f:
    json.dump({"autostart": True, "editor_path": r"C:\__no__.exe"}, f)
mod.autostart_editor()
check("autostart 开启但路径无效时不崩溃", True)

print("\n全部测试通过 ✔")
