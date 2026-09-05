# -*- coding: utf-8 -*-
"""离线自测：不启动 ComfyUI，直接加载节点模块验证逻辑。

用法：python test_prompt_helper.py
"""

import base64
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
check("输入包含 path / negative_path / base_prompt / negative_base_prompt / position / merge_lines",
      {"path", "negative_path", "base_prompt", "negative_base_prompt",
       "position", "merge_lines"} <= set(inputs))
check("path 默认值来自 config.json", inputs["path"][1].get("default", "").endswith("prompt.txt"))
check("返回 4 个输出", node_cls.RETURN_TYPES == ("STRING", "STRING", "STRING", "STRING"))

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

print("== 注入（v1.4.1 起恒定前置，position 取值被忽略）==")
# 与插件注入管线一致地算期望值（prompt.txt 将来携带元数据 tag 时断言依然成立）
base = mod.strip_meta_tags(mod.read_tag_file(REAL_TXT)[0])[0]
with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
    f.write("blurry, bad hands")
    NEG_TXT = f.name
neg_base = mod.read_tag_file(NEG_TXT)[0]

prompt, negative, tags, status = node.inject(REAL_TXT, NEG_TXT, "masterpiece, best quality", "lowres", "追加到末尾", True)
check("注入恒在最前（正反向各自生效）", prompt == base + ", masterpiece, best quality"
      and negative == "blurry, bad hands, lowres" and "文件正常" in status)

prompt, negative, tags, status = node.inject(REAL_TXT, NEG_TXT, "masterpiece", "lowres", "插入到最前", True)
check("position 取任意值结果一致（仅兼容旧工作流）", prompt == base + ", masterpiece"
      and negative == neg_base + ", lowres")

prompt, negative, tags, status = node.inject(REAL_TXT, "", "", "lowres", "追加到末尾", True)
check("反向留空不注入", prompt == base and negative == "lowres")

prompt, negative, tags, status = node.inject(REAL_TXT, r"C:\__no_neg__.txt", "", "lowres", "追加到末尾", True)
check("反向文件缺失时跳过反向", negative == "lowres" and "不存在" in status)

prompt, negative, tags, status = node.inject(REAL_TXT, NEG_TXT, "", "", "追加到末尾", True)
check("基础为空时仅输出词条", prompt == base and negative == neg_base and tags == base)

prompt, negative, tags, status = node.inject(r"C:\__no_such_file__.txt", NEG_TXT, "masterpiece", "lowres", "追加到末尾", True)
check("正向缺失时跳过且反向仍注入", prompt == "masterpiece"
      and negative == "blurry, bad hands, lowres")

os.unlink(NEG_TXT)

with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
    f.write("tag_a")
    tmp_txt = f.name
p1 = node.inject(tmp_txt, "", "base", "", "追加到末尾", True)[0]
with open(tmp_txt, "w", encoding="utf-8") as f:
    f.write("tag_b")
p2 = node.inject(tmp_txt, "", "base", "", "追加到末尾", True)[0]
os.unlink(tmp_txt)
check("文件变化后下一次执行读到新内容", p1 == "tag_a, base" and p2 == "tag_b, base")

print("== 元数据剥离（不展开 BREAK）==")


def _b64url(s):
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode("ascii").rstrip("=")


def _linked(tags_text, meta):
    """模拟 FeeTagHelper 构建区的 txt 链路输出：平铺 tag 流 + 末尾元数据 tag。"""
    return tags_text + ", <fth:meta:" + _b64url(json.dumps(meta, separators=(",", ":"))) + ">"


META = {"v": 1, "breaks": [2], "pick": [{"path": "seg-1", "key": "smile"}]}

clean, metas = mod.strip_meta_tags(_linked("1girl, smile, dress", META))
check("元数据 tag 整体剥离", clean == "1girl, smile, dress")
check("元数据解码为 dict", metas == [META])

clean, metas = mod.strip_meta_tags("a, b, c")
check("无元数据时原样返回", clean == "a, b, c" and metas == [])

clean, metas = mod.strip_meta_tags("a, <fth:meta:zzzz>, b")
check("解码失败静默丢弃整 tag", clean == "a, b" and metas == [])

if os.path.exists(mod.META_LOG_PATH):
    os.unlink(mod.META_LOG_PATH)
with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
    f.write(_linked("1girl, smile, dress", META))
    META_TXT = f.name
prompt, negative, tags, status = node.inject(META_TXT, "", "base", "", "追加到末尾", True)
check("注入前剥离元数据（BREAK 位置不展开）",
      prompt == "1girl, smile, dress, base" and "\n" not in prompt
      and tags == "1girl, smile, dress" and "<fth:meta:" not in prompt + tags)
sidecar = json.load(open(mod.META_LOG_PATH, encoding="utf-8"))
check("元数据 + 注入统计写入 sidecar JSON（附插件版本 + 时间戳）",
      os.path.isfile(mod.META_LOG_PATH)
      and sidecar.get("positive", {}).get("breaks") == [2]
      and sidecar.get("positive", {}).get("pick") == META["pick"]
      and sidecar.get("plugin") == mod.PLUGIN_VERSION
      and sidecar.get("updated")
      and "negative" not in sidecar)
check("injected_tags / full_text 字段（无元数据亦记录）",
      sidecar.get("positive", {}).get("injected_tags") == 3
      and sidecar.get("positive", {}).get("full_text") == "1girl, smile, dress, base")
with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
    f.write("tag_x, tag_y")
    PLAIN_TXT = f.name
node.inject(PLAIN_TXT, "", "base", "", "追加到末尾", True)
sidecar = json.load(open(mod.META_LOG_PATH, encoding="utf-8"))
check("txt 不带元数据时 sidecar 仍记录统计",
      sidecar.get("positive", {}).get("injected_tags") == 2
      and sidecar.get("positive", {}).get("full_text") == "tag_x, tag_y, base"
      and "breaks" not in sidecar.get("positive", {}))
os.unlink(META_TXT)
os.unlink(PLAIN_TXT)
os.unlink(mod.META_LOG_PATH)

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
check("配置含 path/negative_path/autostart/editor_path",
      {"path", "negative_path", "autostart", "editor_path"} <= set(cfg))
mod.CONFIG_PATH = os.path.join(tempfile.mkdtemp(), "config.json")
mod.autostart_editor()
check("autostart 关闭时无动作", True)
with open(mod.CONFIG_PATH, "w", encoding="utf-8") as f:
    json.dump({"autostart": True, "editor_path": r"C:\__no__.exe"}, f)
mod.autostart_editor()
check("autostart 开启但路径无效时不崩溃", True)

print("\n全部测试通过 ✔")
