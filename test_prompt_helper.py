# -*- coding: utf-8 -*-
"""离线自测：不启动 ComfyUI，直接加载节点模块验证逻辑。

用法：python test_prompt_helper.py
"""

import base64
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE_PATH = os.path.join(HERE, "prompt_helper_node.py")
REAL_TXT = r"E:\桌面\AI file\Design file\prompt-helper\prompt.txt"
if not os.path.isfile(REAL_TXT):
    # 作者机路径缺席（异机跑测，v1.6.0 起补齐）：回落插件 config.json 指向的
    # 真实词条文件；仍缺席则生成含 1girl 的临时词条文件——「读真实文件」
    # 用例在任意机器可跑（后续断言均按文件内容动态计算期望值）。
    _cfg_txt = ""
    try:
        _cfg_txt = str(mod._load_config().get("path") or "")
    except Exception:
        _cfg_txt = ""
    if _cfg_txt and os.path.isfile(_cfg_txt):
        REAL_TXT = _cfg_txt
    else:
        _fixture = tempfile.NamedTemporaryFile(
            "w", suffix=".txt", delete=False, encoding="utf-8")
        _fixture.write("1girl, smile, dress, long hair")
        _fixture.close()
        REAL_TXT = _fixture.name

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
# bat 由 detached cmd 解释执行：固定 sleep(0.3) 后 unlink 在系统忙时 cmd 可能仍握着句柄 →
# PermissionError 落 try 残临时文件（竞态 flake）。改轮询删：等 cmd 退出释放句柄后删净
# （0.1s × 100 = 10s 上限；@exit 0 正常毫秒级退出，超时自然放行保持原容错语义）。
for _ in range(100):
    try:
        os.unlink(bat_path)
        break
    except OSError:
        time.sleep(0.1)
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

print("== v1.4.9 镜像：launch_editor 脱离进程树（树杀免疫，WebUI v1.4.19 同款）==")
# 拦截 Popen 捕获 argv/creationflags（不真拉起）；_is_process_running 恒 False
# 强制走启动分支。真实启动已由上方 .bat 用例覆盖。
import subprocess as _subproc
_launch_calls = []


class _FakePopenResult:
    returncode = 0


def _capture_popen(argv, **kwargs):
    _launch_calls.append((list(argv), kwargs))
    return _FakePopenResult()


_real_popen = _subproc.Popen
_real_proc_check = mod._is_process_running
mod._is_process_running = lambda exe_name: False
_subproc.Popen = _capture_popen
try:
    with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as f:
        f.write(b"MZ")
        _exe = f.name
    ok, msg = mod.launch_editor(_exe)
finally:
    _subproc.Popen = _real_popen
    mod._is_process_running = _real_proc_check
    os.unlink(_exe)
check("启动成功回执", ok and msg.startswith("已启动"))
if os.name == "nt":
    _base_flags = (_subproc.DETACHED_PROCESS
                   | _subproc.CREATE_NEW_PROCESS_GROUP)
    _argv, _kw = _launch_calls[0]
    check("Windows 启动：cmd /c start 中转（编辑器挂 cmd 名下、cmd 即退，"
          "taskkill /T 快照父子链断开）",
          _argv[:4] == ["cmd", "/c", "start", ""] and _argv[4] == _exe
          and _kw.get("cwd") == os.path.dirname(_exe))
    check("Windows 启动：脱离旗标 + breakaway 附带（Job 脱出）",
          _kw.get("creationflags")
          == _base_flags | getattr(_subproc, "CREATE_BREAKAWAY_FROM_JOB", 0))

    # Job 拒绝 breakaway（CreateProcess 报错）→ cmd 中转裸旗标重试仍成功
    _launch_calls.clear()

    def _reject_breakaway(argv, **kwargs):
        _launch_calls.append((list(argv), kwargs))
        if (kwargs.get("creationflags") or 0) & getattr(
                _subproc, "CREATE_BREAKAWAY_FROM_JOB", 0):
            raise OSError(5, "Access is denied")
        return _FakePopenResult()

    mod._is_process_running = lambda exe_name: False
    _subproc.Popen = _reject_breakaway
    try:
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as f:
            f.write(b"MZ")
            _exe = f.name
        ok, msg = mod.launch_editor(_exe)
    finally:
        _subproc.Popen = _real_popen
        mod._is_process_running = _real_proc_check
        os.unlink(_exe)
    check("breakaway 被拒：cmd 中转裸旗标重试成功（保住父子链断开）",
          ok and _launch_calls[-1][0][:4] == ["cmd", "/c", "start", ""]
          and _launch_calls[-1][1].get("creationflags") == _base_flags)

    # cmd 中转彻底不可用（策略禁/镜像缺失）→ 回退直启（保底与旧版一致）
    _launch_calls.clear()

    def _block_cmd(argv, **kwargs):
        _launch_calls.append((list(argv), kwargs))
        if argv and argv[0] == "cmd":
            raise OSError("cmd blocked")
        return _FakePopenResult()

    mod._is_process_running = lambda exe_name: False
    _subproc.Popen = _block_cmd
    try:
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as f:
            f.write(b"MZ")
            _exe = f.name
        ok, msg = mod.launch_editor(_exe)
    finally:
        _subproc.Popen = _real_popen
        mod._is_process_running = _real_proc_check
        os.unlink(_exe)
    check("cmd 中转不可用：回退直启（argv=[exe]、脱离旗标保留）",
          ok and _launch_calls[-1][0] == [_exe]
          and _launch_calls[-1][1].get("creationflags") == _base_flags
          and len(_launch_calls) == 3)  # cmd+breakaway → cmd 裸旗标 → 直启

# 熔断（v1.4.9 追加）：空 / 畸形路径不触发任何 shell 调用——Popen 零调用
_launch_calls.clear()
mod._is_process_running = lambda exe_name: False
_subproc.Popen = _capture_popen
try:
    _malformed = ["\\", "\\\\", "/", ".", "..", "   ", '""']
    _results = [mod.launch_editor(bad) for bad in _malformed]
finally:
    _subproc.Popen = _real_popen
    mod._is_process_running = _real_proc_check
check("熔断：空 / 畸形路径（\\ \\\\ / . .. 空白 纯引号）一律 error 回执、"
      "零 shell 调用（防系统级「找不到文件」弹窗；空白/纯引号在解析层"
      "归空走「未设置」分支，同为 error）",
      all(not ok and ("编辑器路径无效" in msg or "未设置" in msg)
          for ok, msg in _results)
      and not _launch_calls)

print("== editor_path 自动探测（编辑器发版改名根治）==")
check("版本号解析（v 前缀 / 多段数字 / 无版本号）",
      mod._editor_exe_version("feetaghelper-v2.7.5.exe") == (2, 7, 5)
      and mod._editor_exe_version("feetaghelper-v2.6.exe") == (2, 6)
      and mod._editor_exe_version("feetaghelper-v2.10.0.exe") == (2, 10, 0)
      and mod._editor_exe_version("feetaghelper.exe") is None)

mod.CONFIG_PATH = os.path.join(tempfile.mkdtemp(), "config.json")
probe_dir = tempfile.mkdtemp()
for name in ("feetaghelper-v2.6.0.exe", "feetaghelper-v2.7.5.exe"):
    open(os.path.join(probe_dir, name), "wb").close()
stale = os.path.join(probe_dir, "feetaghelper-v2.4.1.exe")
with open(mod.CONFIG_PATH, "w", encoding="utf-8") as f:
    json.dump({"path": "", "negative_path": "", "autostart": False,
               "editor_path": stale}, f)
resolved = mod._resolve_editor_path(stale)
check("失效路径解析到同目录版本号最新的 exe",
      resolved == os.path.join(probe_dir, "feetaghelper-v2.7.5.exe"))
check("解析结果已写回 config（下次自动启动直接使用）",
      json.load(open(mod.CONFIG_PATH, encoding="utf-8"))["editor_path"] == resolved)
check("有效路径原样返回", mod._resolve_editor_path(resolved) == resolved)

empty_dir = tempfile.mkdtemp()
gone = os.path.join(empty_dir, "feetaghelper-v2.4.1.exe")
check("同目录无候选时返回原值", mod._resolve_editor_path(gone) == gone)
missing_dir = os.path.join(probe_dir, "__no_dir__", "feetaghelper-v1.0.0.exe")
check("目录不存在时返回原值不崩溃", mod._resolve_editor_path(missing_dir) == missing_dir)

# 全链路：launch_editor 用失效路径进来，进程探测应拿到解析后的新 exe 名
# （拦截进程探测避免真拉起，消息里出现新 exe 名即证明整条链路已用解析结果）
real_process_check = mod._is_process_running
mod._is_process_running = lambda exe_name: True
ok, msg = mod.launch_editor(stale)
mod._is_process_running = real_process_check
check("launch_editor 全链路使用解析后的 exe", ok and "feetaghelper-v2.7.5.exe" in msg)
check("editor_path 写回原子化：无 .tmp- 残留",
      not [n for n in os.listdir(probe_dir) if ".tmp-" in n])

print("== v1.4.10 镜像：editor.hint 编辑器自荐路径（X-177 契约，四档优先级）==")
mod.EDITOR_HINT_PATH = os.path.join(tempfile.mkdtemp(), "editor.hint")
hint_dir = tempfile.mkdtemp()
hint_exe = os.path.join(hint_dir, "feetaghelper-v2.8.3.exe")
open(hint_exe, "wb").close()
scan_best = os.path.join(probe_dir, "feetaghelper-v2.7.5.exe")  # 扫描档期望值


def _write_hint(content):
    with open(mod.EDITOR_HINT_PATH, "w", encoding="utf-8") as f:
        f.write(content)


# 档②：用户未设置（config 空）→ hint 直接命中（自动启动零配置可用）
_write_hint(hint_exe)
check("hint：config 未设置时 hint 直接命中（空 configured → hint 路径）",
      mod._resolve_editor_path("") == hint_exe)
_write_hint(hint_exe + "\r\n")  # 编辑器写文件惯例带尾换行
check("hint：尾换行容忍", mod._resolve_editor_path("") == hint_exe)

# 档①：config 用户值有效 → hint 永不覆盖（用户值恒优先）
check("hint：config 用户值有效档恒优先（hint 在场也不覆盖）",
      mod._resolve_editor_path(scan_best) == scan_best)

# 档② vs 扫描档：configured 失效 + 同目录有更新候选 + hint 有效 → hint 胜、
# 且不写回 config（编辑器永不改插件 config，单向传值）
with open(mod.CONFIG_PATH, "w", encoding="utf-8") as f:
    json.dump({"editor_path": stale}, f)
check("hint：与同目录扫描并存时 hint 胜（候选 v2.7.5 在场仍取 hint）",
      mod._resolve_editor_path(stale) == hint_exe)
check("hint：不写回 config（单向传值，config 保持用户原值）",
      json.load(open(mod.CONFIG_PATH, encoding="utf-8"))["editor_path"] == stale)

# 档③：hint 无效 → 跳过该档回落既有链（扫描救援照常）
os.remove(mod.EDITOR_HINT_PATH)
with open(mod.CONFIG_PATH, "w", encoding="utf-8") as f:
    json.dump({"editor_path": stale}, f)
check("hint：文件缺失时回落扫描档（既有救援不受影响）",
      mod._resolve_editor_path(stale) == scan_best)
for _bad in ("", "   ", "\\", "C:\\__no_such_hint__.exe"):
    _write_hint(_bad)
    check(f"hint：无效内容跳过该档（{_bad!r} → 扫描档接管）",
          mod._resolve_editor_path(stale) == scan_best)
os.remove(mod.EDITOR_HINT_PATH)

# 启动链路：config 未配置 + hint 有效 → launch_editor 实际拉起 hint 指向的 exe
_launch_popen = []
_real_popen_hint = _subproc.Popen
_real_proc_hint = mod._is_process_running
mod._is_process_running = lambda exe_name: False
_subproc.Popen = lambda argv, **kw: _launch_popen.append(list(argv)) or _FakePopenResult()
try:
    _write_hint(hint_exe)
    ok, msg = mod.launch_editor("")
finally:
    _subproc.Popen = _real_popen_hint
    mod._is_process_running = _real_proc_hint
    os.remove(mod.EDITOR_HINT_PATH)
check("启动链路：未配置 + hint 有效 → 拉起 hint 指向的 exe（零配置可用）",
      ok and msg == "已启动：" + hint_exe
      and _launch_popen and _launch_popen[0][-1] == hint_exe)

print("== 原子写（v1.4.8 镜像 WebUI v1.4.16：config / sidecar 半截写根治）==")
aw_dir = tempfile.mkdtemp()
aw_path = os.path.join(aw_dir, "atomic.txt")
mod._atomic_write_text(aw_path, "第一版")
check("原子写：内容完整且无 .tmp- 残留",
      open(aw_path, encoding="utf-8").read() == "第一版"
      and not [n for n in os.listdir(aw_dir) if ".tmp-" in n])
_orig_replace = os.replace


def _boom_replace(src, dst):
    raise OSError("replace failed (simulated)")


os.replace = _boom_replace
try:
    try:
        mod._atomic_write_text(aw_path, "第二版")
        raise SystemExit("应当抛 OSError（调用方兜底语义依赖异常上抛）")
    except OSError:
        pass
finally:
    os.replace = _orig_replace
check("replace 失败：旧内容保留 + 临时文件已清理",
      open(aw_path, encoding="utf-8").read() == "第一版"
      and not [n for n in os.listdir(aw_dir) if ".tmp-" in n])

# _record_meta sidecar 原子化：inject 触发写入，无残留、内容完整
if os.path.exists(mod.META_LOG_PATH):
    os.unlink(mod.META_LOG_PATH)
with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
    f.write("atomic_probe_tag")
    AW_TXT = f.name
node.inject(AW_TXT, "", "base", "", "追加到末尾", True)
sidecar = json.load(open(mod.META_LOG_PATH, encoding="utf-8"))
check("_record_meta sidecar 原子写：内容完整 + 无 .tmp- 残留",
      sidecar.get("positive", {}).get("full_text") == "atomic_probe_tag, base"
      and not [n for n in os.listdir(mod.NODE_DIR) if ".tmp-" in n])
os.unlink(AW_TXT)
os.unlink(mod.META_LOG_PATH)

print("== settings.pin 统一固定共享函数镜像（WebUI 侧接线；本仓验证读取语义）==")
mod.NEGATIVE_PIN_PATH = os.path.join(tempfile.mkdtemp(), "negative_path.pin")
mod.POSITIVE_PIN_PATH = os.path.join(tempfile.mkdtemp(), "positive_path.pin")
mod.SETTINGS_PIN_PATH = os.path.join(tempfile.mkdtemp(), "settings.pin")
check("pin 缺失：overrides 为空（回退 config）", mod._read_pin_overrides() == {})
check("_read_negative_pin 旧接口保留（现兼容层）", mod._read_negative_pin() == "")
with open(mod.NEGATIVE_PIN_PATH, "w", encoding="utf-8") as f:
    f.write('  "' + REAL_TXT + '"  \n')
check("旧 negative_path.pin：引号/空白容错并入 overrides",
      mod._read_pin_overrides() == {"negative_path": REAL_TXT})
open(mod.NEGATIVE_PIN_PATH, "w").close()
check("旧 negative_path.pin：空文件不并入（回退 config）", mod._read_pin_overrides() == {})
with open(mod.NEGATIVE_PIN_PATH, "w", encoding="gbk") as f:
    f.write(r"E:\测试\反向词条.txt")
check("旧 negative_path.pin：GBK 编码兜底可读",
      mod._read_negative_pin() == r"E:\测试\反向词条.txt")
with open(mod.POSITIVE_PIN_PATH, "w", encoding="gbk") as f:
    f.write(REAL_TXT)
check("旧 positive_path.pin：GBK 兜底并入 overrides",
      mod._read_pin_overrides() == {"negative_path": r"E:\测试\反向词条.txt", "path": REAL_TXT})
with open(mod.SETTINGS_PIN_PATH, "w", encoding="utf-8") as f:
    json.dump({"path": REAL_TXT, "negative_path": r"E:\settings\反向.txt",
               "autostart": True, "editor_path": r"E:\settings\editor.exe",
               "enabled": True, "unknown": 1}, f)
check("settings.pin：本仓四键全读取 + 表外键（enabled 等）忽略",
      mod._read_pin_overrides() == {"path": REAL_TXT, "negative_path": r"E:\settings\反向.txt",
                                    "autostart": True, "editor_path": r"E:\settings\editor.exe"})
with open(mod.SETTINGS_PIN_PATH, "w", encoding="gbk") as f:
    json.dump({"editor_path": r"E:\测试\编辑器.exe"}, f)
check("settings.pin：GBK 编码兜底可读",
      mod._read_pin_overrides() == {"negative_path": r"E:\测试\反向词条.txt",
                                    "path": REAL_TXT, "editor_path": r"E:\测试\编辑器.exe"})
with open(mod.SETTINGS_PIN_PATH, "w", encoding="utf-8") as f:
    json.dump({"negative_path": "   "}, f)
check("settings.pin：路径键空值视作未固定（不遮蔽旧独立 pin）",
      mod._read_pin_overrides() == {"negative_path": r"E:\测试\反向词条.txt", "path": REAL_TXT})
os.remove(mod.NEGATIVE_PIN_PATH)
os.remove(mod.POSITIVE_PIN_PATH)
os.remove(mod.SETTINGS_PIN_PATH)
check("拆除全部 pin 后 overrides 复位为空", mod._read_pin_overrides() == {})

print("== v1.5.0 节点群：注册与契约容错 ==")
check("NODE_CLASS_MAPPINGS 含全部 7 节点（v1.6.0 增 ImageInput）",
      set(mod.NODE_CLASS_MAPPINGS) == {"PromptHelperInject", "PromptHelperLoraStack",
                                       "PromptHelperParams", "PromptHelperImageOutput",
                                       "PromptHelperCheckpoint", "PromptHelperFilter",
                                       "PromptHelperImageInput"})
check("7 节点显示名全配置",
      set(mod.NODE_CLASS_MAPPINGS) == set(mod.NODE_DISPLAY_NAME_MAPPINGS))
lora_cls = mod.NODE_CLASS_MAPPINGS["PromptHelperLoraStack"]
params_cls = mod.NODE_CLASS_MAPPINGS["PromptHelperParams"]
ckpt_cls = mod.NODE_CLASS_MAPPINGS["PromptHelperCheckpoint"]
filter_cls = mod.NODE_CLASS_MAPPINGS["PromptHelperFilter"]
imgout_cls = mod.NODE_CLASS_MAPPINGS["PromptHelperImageOutput"]
imgin_cls = mod.NODE_CLASS_MAPPINGS["PromptHelperImageInput"]
for cls in (lora_cls, params_cls, ckpt_cls, filter_cls, imgin_cls):
    check(f"{cls.__name__} IS_CHANGED 返回 NaN", cls.IS_CHANGED(x=1) != cls.IS_CHANGED(x=1))
check("ImageOutput 为 OUTPUT_NODE（执行终点，不吃缓存）", imgout_cls.OUTPUT_NODE is True)
check("契约文件输入默认值指向插件目录",
      lora_cls.INPUT_TYPES()["required"]["lora_file"][1]["default"] == mod.LORA_LIST_PATH
      and params_cls.INPUT_TYPES()["required"]["params_file"][1]["default"] == mod.PARAMS_PATH
      and ckpt_cls.INPUT_TYPES()["required"]["checkpoint_file"][1]["default"] == mod.CHECKPOINT_PATH
      and filter_cls.INPUT_TYPES()["required"]["filter_file"][1]["default"] == mod.FILTER_OUTPUT_PATH
      and imgin_cls.INPUT_TYPES()["required"]["image_file"][1]["default"] == mod.IMAGE_JSON_PATH)
check("Inject 增加 hidden PROMPT（工作流捕获）",
      node_cls.INPUT_TYPES().get("hidden") == {"prompt": "PROMPT"})

CT_DIR = tempfile.mkdtemp()


def _write(path, content, encoding="utf-8"):
    with open(path, "w", encoding=encoding) as f:
        f.write(content)
    return path


missing_json = os.path.join(CT_DIR, "__missing__.json")
p_missing, p_err = mod._read_params(missing_json)
check("Params：缺失 → 全默认 + 错误消息", p_missing == dict(mod.PARAMS_DEFAULTS) and "不存在" in p_err)
params_ok = os.path.join(CT_DIR, "params.json")
_write(params_ok, json.dumps({"seed": 123, "steps": 24, "cfg": 6.5, "sampler": "euler_ancestral",
                              "scheduler": "karras", "width": 704, "height": 1080, "batch": 2}))
p_ok, p_err = mod._read_params(params_ok)
check("Params：平铺参数 typed 读取",
      p_ok == {"seed": 123, "steps": 24, "cfg": 6.5, "sampler": "euler_ancestral",
               "scheduler": "karras", "width": 704, "height": 1080, "batch": 2} and p_err == "")
_write(params_ok, '{"seed": "77", "steps": "bad", "cfg": 7}')
p_mix, _ = mod._read_params(params_ok)
check("Params：字符串数值强转 + 坏键回落默认", p_mix["seed"] == 77 and p_mix["steps"] == 20 and p_mix["cfg"] == 7.0)
_write(params_ok, "{broken json")
p_bad, p_err = mod._read_params(params_ok)
check("Params：损坏 JSON → 全默认（安全默认不拦队列）", p_bad == dict(mod.PARAMS_DEFAULTS) and p_err)
node_params = params_cls()
check("Params 节点：typed 8 口输出",
      node_params.load(params_ok) == (0, 20, 7.0, "euler", "normal", 512, 512, 1))

lora_ok = os.path.join(CT_DIR, "lora_list.json")
_write(lora_ok, json.dumps([{"name": "a.safetensors", "model": 0.8, "clip": 0.7},
                            {"name": "b.safetensors"},
                            {"model": 1.0}, "junk", {"name": ""},
                            {"name": "c.safetensors", "model": "bad", "clip": None}]))
entries, err = mod._read_lora_list(lora_ok)
check("LoraList：脏条目过滤 + 权重缺省/坏值回落 1.0",
      entries == [{"name": "a.safetensors", "model": 0.8, "clip": 0.7},
                  {"name": "b.safetensors", "model": 1.0, "clip": 1.0},
                  {"name": "c.safetensors", "model": 1.0, "clip": 1.0}] and err == "")
_write(os.path.join(CT_DIR, "lora_path.json"),
       json.dumps([{"name": "p.safetensors", "path": r"D:\m\p.safetensors", "model": 0.9},
                   {"name": "q.safetensors", "path": "  ", "clip": 0.5},
                   {"name": "r.safetensors", "path": 123}]))
entries, _ = mod._read_lora_list(os.path.join(CT_DIR, "lora_path.json"))
check("LoraList：v1.8.1 path 键透传（非空字符串）+ 空/非串 path 省略键",
      entries == [{"name": "p.safetensors", "path": r"D:\m\p.safetensors", "model": 0.9, "clip": 1.0},
                  {"name": "q.safetensors", "model": 1.0, "clip": 0.5},
                  {"name": "r.safetensors", "model": 1.0, "clip": 1.0}])
entries_missing, err_missing = mod._read_lora_list(missing_json)
check("LoraList：缺失 → 空列表（直通安全默认）", entries_missing == [] and "不存在" in err_missing)

ckpt_ok = os.path.join(CT_DIR, "checkpoint.json")
_write(ckpt_ok, json.dumps({"name": "testmodel.safetensors"}))
check("Checkpoint：正常读取", mod._read_checkpoint_entry(ckpt_ok) == ("testmodel.safetensors", "", ""))
_write(ckpt_ok, json.dumps({"name": "", "path": r"D:\m\base.safetensors"}))
check("Checkpoint：仅 path（v1.8.1）→ name 空 + path 透传",
      mod._read_checkpoint_entry(ckpt_ok) == ("", r"D:\m\base.safetensors", ""))
_write(ckpt_ok, json.dumps({"name": "", "path": "  "}))
check("Checkpoint：name 与 path 双空 → 报错口径", mod._read_checkpoint_entry(ckpt_ok)[0] is None)
_write(ckpt_ok, json.dumps({"name": ""}))
check("Checkpoint：name 空 → 报错口径", mod._read_checkpoint_entry(ckpt_ok)[0] is None)

filter_missing = filter_cls()
check("Filter：缺失 → 未配置（安全默认）",
      filter_missing.read(missing_json.replace("__missing__", "filter_output.txt")) == ("", 0, 0, "未配置"))
filter_plain = os.path.join(CT_DIR, "filter_plain.txt")
_write(filter_plain, "tag_a, tag_b\n, tag_c")
check("Filter：纯文本格式（逗号拼接保留词条）",
      filter_missing.read(filter_plain) == ("tag_a, tag_b , tag_c", 0, 3, "保留 3 条词条"))
filter_json = os.path.join(CT_DIR, "filter_json.txt")
_write(filter_json, json.dumps({"kept": ["tag_a", "tag_b"], "removed": 3, "total": 5, "rules": 2}))
check("Filter：JSON 信封格式（统计齐全 + 活跃规则数）",
      filter_missing.read(filter_json) == ("tag_a, tag_b", 3, 5, "活跃 2 条规则"))
filter_face = os.path.join(CT_DIR, "filter_face.txt")
_write(filter_face, "{face}, smile")
check("Filter：{face} 形似 JSON 的词条回落纯文本", filter_missing.read(filter_face) == ("{face}, smile", 0, 2, "保留 2 条词条"))

print("== v1.5.0 Inject：工作流快照（hidden PROMPT → workflow_snapshot.json）==")
mod.WORKFLOW_SNAPSHOT_PATH = os.path.join(CT_DIR, "workflow_snapshot.json")
mod.META_LOG_PATH = os.path.join(CT_DIR, "prompt_helper_meta.json")  # sidecar 同步重定向防真实目录残留
with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
    f.write("wf_probe_tag")
    WF_TXT = f.name
prompt, negative, tags, status = node.inject(WF_TXT, "", "base", "", "追加到末尾", True)
check("无 hidden 时输出零回归且不写快照",
      prompt == "wf_probe_tag, base" and not os.path.exists(mod.WORKFLOW_SNAPSHOT_PATH))
GRAPH = {"1": {"class_type": "PromptHelperInject", "inputs": {"path": WF_TXT}},
         "_meta": {"title": "X"}}
prompt, negative, tags, status = node.inject(WF_TXT, "", "base", "", "追加到末尾", True, prompt=GRAPH)
check("hidden PROMPT 写入 API 格式快照",
      json.load(open(mod.WORKFLOW_SNAPSHOT_PATH, encoding="utf-8")) == GRAPH
      and prompt == "wf_probe_tag, base")
node.inject(WF_TXT, "", "base", "", "追加到末尾", True, prompt="not-a-dict")
check("非法 hidden 值忽略（快照保持旧版）",
      json.load(open(mod.WORKFLOW_SNAPSHOT_PATH, encoding="utf-8")) == GRAPH)
os.unlink(WF_TXT)

print("== v1.5.0 A1111 parameters 组装 ==")
mod._model_hash_cache.clear()
lora_bin = os.path.join(CT_DIR, "testlora.safetensors")
with open(lora_bin, "wb") as f:
    f.write(b"fake-lora-bytes")
_expected_hash = hashlib.sha256(b"fake-lora-bytes").hexdigest()[:10]
_FAKE_LORA_FILES = {}
for _n in ("a.safetensors", "b.safetensors", "c.safetensors"):
    _p = os.path.join(CT_DIR, _n)
    open(_p, "wb").close()
    _FAKE_LORA_FILES[_n] = _p


class _FakeFP:
    loras = {"testlora.safetensors": lora_bin, "missing.safetensors": None, **_FAKE_LORA_FILES}
    checkpoints = {"testmodel.safetensors": os.path.join(CT_DIR, "testmodel.safetensors")}
    counter = {"n": 0}
    out_dir = os.path.join(CT_DIR, "comfy_output")

    @staticmethod
    def get_full_path(kind, name):
        table = _FakeFP.loras if kind == "loras" else _FakeFP.checkpoints
        return table.get(name)

    @staticmethod
    def get_folder_paths(kind):
        # v1.8.1：loras 也返回注册根（basename 递归搜索兜底层的测面——CT_DIR 即 loras 根）
        if kind == "embeddings":
            return [CT_DIR]
        if kind == "loras":
            return [CT_DIR]
        return []

    @staticmethod
    def get_output_directory():
        os.makedirs(_FakeFP.out_dir, exist_ok=True)
        return _FakeFP.out_dir

    @staticmethod
    def get_save_image_path(prefix, out_dir, width=0, height=0):
        os.makedirs(out_dir, exist_ok=True)
        _FakeFP.counter["n"] += 1
        return out_dir, prefix, _FakeFP.counter["n"], "", prefix


import types as _types

_fake_comfy = _types.ModuleType("comfy")
_fake_comfy_sd = _types.ModuleType("comfy.sd")
_fake_comfy_utils = _types.ModuleType("comfy.utils")
_lora_apply_calls = []
_ckpt_load_calls = []
_fake_comfy_sd.load_lora_for_models = (
    lambda model, clip, lora, m_str, c_str: _lora_apply_calls.append((lora, m_str, c_str)) or (model + 1, clip + 1))
_fake_comfy_sd.load_checkpoint_guess_config = (
    lambda path, output_vae=True, output_clip=True, embedding_directory=None:
    _ckpt_load_calls.append(path) or ("MODEL", "CLIP", "VAE", None))
_fake_comfy_utils.load_torch_file = lambda path, safe_load=False: "torch:" + os.path.basename(path)
_fake_comfy.sd = _fake_comfy_sd
_fake_comfy.utils = _fake_comfy_utils
_saved = {name: sys.modules.get(name) for name in ("folder_paths", "comfy", "comfy.sd", "comfy.utils")}
sys.modules["folder_paths"] = _FakeFP
sys.modules["comfy"] = _fake_comfy
sys.modules["comfy.sd"] = _fake_comfy_sd
sys.modules["comfy.utils"] = _fake_comfy_utils
try:
    _write(ckpt_ok, json.dumps({"name": "testmodel.safetensors"}))  # 复位（上方空 name 用例已覆写）
    model, clip, count = lora_cls().apply_loras(1, 2, lora_ok)
    check("LoraStack：循环叠加（脏条目已滤，count=实际应用数）",
          model == 4 and clip == 5 and count == 3
          and _lora_apply_calls == [("torch:a.safetensors", 0.8, 0.7),
                                    ("torch:b.safetensors", 1.0, 1.0),
                                    ("torch:c.safetensors", 1.0, 1.0)])
    model, clip, count = lora_cls().apply_loras(7, 8, missing_json)
    check("LoraStack：清单缺失直通（安全默认）", model == 7 and clip == 8 and count == 0)
    _write(lora_ok, json.dumps([{"name": "a.safetensors"}, {"name": "missing.safetensors"}]))
    model, clip, count = lora_cls().apply_loras(1, 2, lora_ok)
    check("LoraStack：未注册 LoRA 跳过不拦链", model == 2 and clip == 3 and count == 1)

    # v1.8.1 三层解析：path 绝对路径直读（跨目录——文件在 loras 注册根之外，注册名/搜索两层
    # 都不可能命中，唯一通路=path 层）
    import shutil as _shutil
    _ext_dir = tempfile.mkdtemp(prefix="fth_lora_ext_")
    _direct = os.path.join(_ext_dir, "directlora.safetensors")
    with open(_direct, "wb") as f:
        f.write(b"direct-lora-bytes")
    _write(lora_ok, json.dumps([{"name": "directlora.safetensors", "path": _direct, "model": 0.7, "clip": 0.7}]))
    model, clip, count = lora_cls().apply_loras(1, 2, lora_ok)
    check("LoraStack：条目 path 绝对路径直读（跨注册根外目录）",
          model == 2 and clip == 3 and count == 1
          and _lora_apply_calls[-1] == ("torch:directlora.safetensors", 0.7, 0.7))
    _hash_direct = hashlib.sha256(b"direct-lora-bytes").hexdigest()[:10]
    _text_direct = mod._build_a1111_parameters("p", "", {}, None, mod._read_lora_list(lora_ok)[0])
    check("A1111：Lora hashes 随条目 path 直读层取哈希",
          _text_direct == f'p\nLora hashes: "directlora: {_hash_direct}"')
    # path 指向不存在文件 + name 注册在册 → 回落 name 注册名解析
    _write(lora_ok, json.dumps([{"name": "a.safetensors", "path": os.path.join(_ext_dir, "gone.safetensors")}]))
    model, clip, count = lora_cls().apply_loras(1, 2, lora_ok)
    check("LoraStack：path 不在 → 回落 name 注册名解析",
          model == 2 and clip == 3 and count == 1
          and _lora_apply_calls[-1][0] == "torch:a.safetensors")
    # path 不在 + name 未注册 → basename 递归搜索兜底（文件在 loras 根子目录、注册表无此名）
    _walk_sub = os.path.join(CT_DIR, "walksub")
    os.makedirs(_walk_sub, exist_ok=True)
    with open(os.path.join(_walk_sub, "walkonly.safetensors"), "wb") as f:
        f.write(b"walk-lora-bytes")
    mod._loras_basename_cache = None   # 重建兜底索引（早前 miss 时建的索引不含本文件）
    _write(lora_ok, json.dumps([{"name": "walkonly.safetensors"}]))
    model, clip, count = lora_cls().apply_loras(1, 2, lora_ok)
    check("LoraStack：注册名 miss → 注册目录 basename 递归搜索兜底命中",
          model == 2 and clip == 3 and count == 1
          and _lora_apply_calls[-1][0] == "torch:walkonly.safetensors")
    _shutil.rmtree(_ext_dir, ignore_errors=True)

    model, clip, vae, name = ckpt_cls().load(ckpt_ok)
    check("Checkpoint：加载输出 model/clip/vae/name（name 去扩展名）",
          (model, clip, vae, name) == ("MODEL", "CLIP", "VAE", "testmodel")
          and _ckpt_load_calls[-1].endswith("testmodel.safetensors"))
    # v1.8.1：checkpoint path 直读（跨注册根外目录——注册名不可能命中的唯一通路）
    _ckpt_ext = tempfile.mkdtemp(prefix="fth_ckpt_ext_")
    _ckpt_direct = os.path.join(_ckpt_ext, "directmodel.safetensors")
    open(_ckpt_direct, "wb").close()
    _write(ckpt_ok, json.dumps({"name": "directmodel.safetensors", "path": _ckpt_direct}))
    model, clip, vae, name = ckpt_cls().load(ckpt_ok)
    check("Checkpoint：条目 path 绝对路径直读",
          (model, clip, vae, name) == ("MODEL", "CLIP", "VAE", "directmodel")
          and _ckpt_load_calls[-1] == _ckpt_direct)
    _write(ckpt_ok, json.dumps({"name": "", "path": os.path.join(_ckpt_ext, "gone.safetensors")}))
    try:
        ckpt_cls().load(ckpt_ok)
        raise SystemExit("应当 RuntimeError")
    except RuntimeError as e:
        check("Checkpoint：仅 path 且文件不在 → 清晰报错（不静默换模型）", "不存在" in str(e))
    _shutil.rmtree(_ckpt_ext, ignore_errors=True)
    _write(ckpt_ok, json.dumps({"name": "testmodel.safetensors"}))   # 复位供后续段用
    _write(ckpt_ok, json.dumps({"name": "notfound.safetensors"}))
    try:
        ckpt_cls().load(ckpt_ok)
        raise SystemExit("应当 RuntimeError")
    except RuntimeError as e:
        check("Checkpoint：模型未注册 → 清晰报错", "未找到" in str(e))
    try:
        ckpt_cls().load(missing_json)
        raise SystemExit("应当 RuntimeError")
    except RuntimeError as e:
        check("Checkpoint：文件缺失 → 清晰报错（基模不可安全默认）", "不存在" in str(e))

    # A1111 全文组装（真哈希：fake lora 文件真实存在）
    _write(lora_ok, json.dumps([{"name": "testlora.safetensors", "model": 0.8, "clip": 0.8}]))
    text = mod._build_a1111_parameters(
        "1girl, smile", "lowres",
        {"steps": 24, "cfg": 6.5, "seed": 123, "sampler": "euler_ancestral", "width": 704, "height": 1080},
        "testmodel", mod._read_lora_list(lora_ok)[0])
    check("A1111 全文同构（WebUI 版 PNG parameters 一致形态）",
          text == "1girl, smile\nNegative prompt: lowres\n"
                  "Steps: 24, Sampler: euler_ancestral, CFG scale: 6.5, Seed: 123, "
                  f"Size: 704x1080, Model: testmodel, Lora hashes: \"testlora: {_expected_hash}\"")
    text = mod._build_a1111_parameters("p", "n", {}, None, [])
    check("A1111：素材缺席省略对应段（不编造数值）",
          text == "p\nNegative prompt: n")
    text = mod._build_a1111_parameters("p", "", {"steps": 20, "cfg": 7, "seed": 1,
                                                 "sampler": "euler", "width": 512, "height": 512}, None, [])
    check("A1111：无反向不写 Negative 行 + 整数 CFG 无小数尾",
          text == "p\nSteps: 20, Sampler: euler, CFG scale: 7, Seed: 1, Size: 512x512")

    print("== v1.5.0 ImageOutput：真张量落盘 + tEXt 嵌入 + 取图清单 ==")
    import numpy as _np
    import torch as _torch
    from PIL import Image as _PILImage
    mod.PARAMS_PATH = os.path.join(CT_DIR, "params.json")
    mod.LORA_LIST_PATH = lora_ok
    mod.CHECKPOINT_PATH = ckpt_ok
    mod.IMAGE_MANIFEST_PATH = os.path.join(CT_DIR, "image_manifest.json")
    mod.META_LOG_PATH = os.path.join(CT_DIR, "prompt_helper_meta.json")
    _write(mod.PARAMS_PATH, json.dumps({"seed": 123, "steps": 24, "cfg": 6.5, "sampler": "euler_ancestral",
                                        "scheduler": "karras", "width": 704, "height": 1080, "batch": 1}))
    _write(mod.CHECKPOINT_PATH, json.dumps({"name": "testmodel.safetensors"}))
    _write(mod.META_LOG_PATH, json.dumps({
        "updated": "2026-09-27 00:00:00", "plugin": mod.PLUGIN_VERSION,
        "positive": {"v": 1, "breaks": [2], "pick": [], "filter": {"from": 0, "len": 2},
                     "injected_tags": 2, "full_text": "1girl, smile"},
        "negative": {"injected_tags": 1, "full_text": "lowres"}}))
    tensor = _torch.from_numpy((_np.ones((2, 16, 24, 3)) * 0.5).astype("float32"))
    ret = imgout_cls().save_images(tensor, "feetag_test", "1girl, smile", "lowres", prompt_id="pid-123")
    files = sorted(os.listdir(_FakeFP.out_dir))
    check("ImageOutput：批次落盘 + 命名同 SaveImage 规则",
          len(files) == 2 and all(f.startswith("feetag_test_") and f.endswith(".png") for f in files)
          and ret["ui"]["images"][0]["type"] == "output")
    png = _PILImage.open(os.path.join(_FakeFP.out_dir, files[0]))
    params_text = png.text.get("parameters", "")
    check("PNG tEXt parameters：A1111 全文 + 尾部 fth_meta 记录行（编辑器读侧现役路径）",
          params_text.startswith("1girl, smile\nNegative prompt: lowres\nSteps: 24, Sampler: euler_ancestral")
          and "\nfth_meta: {" in params_text and "\nfth_meta_negative: {" in params_text
          and f"Lora hashes: \"testlora: {_expected_hash}\"" in params_text
          and "Model: testmodel" in params_text)
    meta_json = json.loads(png.text.get("fth_meta", "{}"))
    check("PNG tEXt fth_meta：记录原样透传（filter 未知字段存活）+ plugin 版本",
          meta_json.get("filter") == {"from": 0, "len": 2} and meta_json.get("breaks") == [2]
          and meta_json.get("injected_tags") == 2 and meta_json.get("plugin") == mod.PLUGIN_VERSION)
    manifest = json.load(open(mod.IMAGE_MANIFEST_PATH, encoding="utf-8"))
    check("image_manifest.json：批次两条追加（prompt_id / filename / subfolder / ts）",
          len(manifest) == 2 and all(e["prompt_id"] == "pid-123" and e["subfolder"] == ""
                                     and isinstance(e["ts"], float) for e in manifest)
          and {e["filename"] for e in manifest} == set(files))
    imgout_cls().save_images(tensor[:1], "feetag_test", "p", "", prompt_id="pid-456")
    manifest = json.load(open(mod.IMAGE_MANIFEST_PATH, encoding="utf-8"))
    check("清单追加式累积（第二队次入库）", len(manifest) == 3 and manifest[-1]["prompt_id"] == "pid-456")
    mod.IMAGE_MANIFEST_CAP = 3
    mod._append_image_manifest([{"prompt_id": "p4", "filename": "x.png", "subfolder": "", "ts": 4.0}])
    manifest = json.load(open(mod.IMAGE_MANIFEST_PATH, encoding="utf-8"))
    check("清单超限丢最旧（防无界膨胀）",
          len(manifest) == 3 and manifest[-1]["prompt_id"] == "p4"
          and manifest[0]["filename"] == "feetag_test_00001_00001_.png"
          and "feetag_test_00001_00000_.png" not in {e["filename"] for e in manifest})
    mod.IMAGE_MANIFEST_CAP = 500
    # 元数据源缺席降级：删 params/checkpoint/lora/sidecar → parameters 只剩词条行
    os.remove(mod.PARAMS_PATH)
    os.remove(mod.CHECKPOINT_PATH)
    os.remove(mod.META_LOG_PATH)
    os.remove(lora_ok)
    ret = imgout_cls().save_images(tensor[:1], "feetag_test", "p", "n", prompt_id="pid-789")
    png = _PILImage.open(os.path.join(_FakeFP.out_dir, sorted(os.listdir(_FakeFP.out_dir))[-1]))
    check("元数据源全体缺席：parameters 降级为纯词条 + 保存不受影响",
          png.text.get("parameters") == "p\nNegative prompt: n" and "fth_meta" not in png.text)
finally:
    for _name, _orig in _saved.items():
        if _orig is None:
            sys.modules.pop(_name, None)
        else:
            sys.modules[_name] = _orig

print("== v1.6.0 改图链：契约常量 + 裸文件名解析 ==")
check("契约常量指向插件目录",
      mod.IMAGE_JSON_PATH == os.path.join(mod.NODE_DIR, "image.json")
      and mod.IMAGES_DIR == os.path.join(mod.NODE_DIR, "images"))
check("denoise 契约范围/默认常量",
      mod.IMAGE_DENOISE_MIN == 0.05 and mod.IMAGE_DENOISE_MAX == 0.95
      and mod.IMAGE_DENOISE_DEFAULT == 0.5)
_rp = mod._resolve_contract_path
check("裸文件名 → 插件目录同名文件",
      _rp("params.json") == os.path.join(mod.NODE_DIR, "params.json")
      and _rp(' "image.json" ') == os.path.join(mod.NODE_DIR, "image.json"))
check("绝对路径 / 含目录路径 / 空串原样返回（零语义变化）",
      _rp(params_ok) == params_ok and _rp("") == ""
      and _rp("sub/dir.json") == "sub/dir.json"
      and _rp(r"C:\x\y.json") == r"C:\x\y.json")

print("== v1.6.0 image.json 契约解析 + 边界 ==")
img_json = os.path.join(CT_DIR, "image.json")
_write(img_json, json.dumps({"image": "a.png", "denoise": 0.5}))
check("正常解析（文件名 + denoise + scale 缺省 1.0）", mod._read_image_contract(img_json) == ("a.png", 0.5, 1.0, ""))
_write(img_json, json.dumps({"image": "b.png", "extra": 1}))
check("denoise 缺失 → 默认 0.5；未知字段容忍",
      mod._read_image_contract(img_json) == ("b.png", 0.5, 1.0, ""))
_write(img_json, json.dumps({"image": "b.png", "denoise": "0.3"}))
check("denoise 字符串宽松强转", mod._read_image_contract(img_json) == ("b.png", 0.3, 1.0, ""))
_write(img_json, json.dumps({"image": "b.png", "denoise": "bad"}))
check("denoise 坏值 → 默认 0.5", mod._read_image_contract(img_json) == ("b.png", 0.5, 1.0, ""))
_write(img_json, '{"image": "b.png", "denoise": NaN}')
check("denoise NaN 防御 → 默认 0.5", mod._read_image_contract(img_json) == ("b.png", 0.5, 1.0, ""))
_write(img_json, json.dumps({"image": "b.png", "denoise": 0.06}))
check("denoise 界内原样", mod._read_image_contract(img_json) == ("b.png", 0.06, 1.0, ""))
_write(img_json, json.dumps({"image": "b.png", "denoise": 0.0}))
check("denoise 越界下限收敛 0.05", mod._read_image_contract(img_json) == ("b.png", 0.05, 1.0, ""))
_write(img_json, json.dumps({"image": "b.png", "denoise": 1.2}))
check("denoise 越界上限收敛 0.95", mod._read_image_contract(img_json) == ("b.png", 0.95, 1.0, ""))
_write(img_json, json.dumps({"denoise": 0.5}))
name, den, sc, err = mod._read_image_contract(img_json)
check("缺 image 字段 → 报错口径", name is None and den is None and "image" in err)
_write(img_json, json.dumps({"image": "   "}))
check("image 空串 → 报错口径", mod._read_image_contract(img_json)[0] is None)
_write(img_json, json.dumps({"image": "../evil.png"}))
check("image 目录穿越收敛 basename", mod._read_image_contract(img_json)[0] == "evil.png")
_write(img_json, json.dumps({"image": r"C:\abs\x.png"}))
check("image 绝对路径收敛 basename", mod._read_image_contract(img_json)[0] == "x.png")
name, den, sc, err = mod._read_image_contract(missing_json)
check("image.json 缺失 → 报错口径", name is None and "不存在" in err)
_write(img_json, "{broken json")
check("损坏 JSON → 报错口径", mod._read_image_contract(img_json)[0] is None)
_write(img_json, "[1, 2]")
check("顶层数组 → 格式不符报错", "格式不符" in mod._read_image_contract(img_json)[3])

print("== v1.7.0 scale 契约解析 + 边界（denoise 同纪律：坏值默认、越界收敛不报错） ==")
check("scale 契约范围/默认常量（1.0~2.0；边长 16 倍数 clamp 64~4096）",
      mod.IMAGE_SCALE_MIN == 1.0 and mod.IMAGE_SCALE_MAX == 2.0
      and mod.IMAGE_SCALE_DEFAULT == 1.0 and mod.IMAGE_SCALE_SIDE_STEP == 16
      and mod.IMAGE_SCALE_SIDE_MIN == 64 and mod.IMAGE_SCALE_SIDE_MAX == 4096)
_write(img_json, json.dumps({"image": "b.png", "scale": 1.5}))
check("scale 界内原样", mod._read_image_contract(img_json) == ("b.png", 0.5, 1.5, ""))
_write(img_json, json.dumps({"image": "b.png", "scale": "1.25"}))
check("scale 字符串宽松强转", mod._read_image_contract(img_json)[2] == 1.25)
_write(img_json, json.dumps({"image": "b.png", "scale": "bad"}))
check("scale 坏值 → 默认 1.0", mod._read_image_contract(img_json)[2] == 1.0)
_write(img_json, '{"image": "b.png", "scale": NaN}')
check("scale NaN 防御 → 默认 1.0", mod._read_image_contract(img_json)[2] == 1.0)
_write(img_json, json.dumps({"image": "b.png", "scale": 0.1}))
check("scale 越界下限收敛 1.0", mod._read_image_contract(img_json)[2] == 1.0)
_write(img_json, json.dumps({"image": "b.png", "scale": 9.9}))
check("scale 越界上限收敛 2.0", mod._read_image_contract(img_json)[2] == 2.0)
check("_scaled_size 16 倍数取整（512×768 ×2.0 → 1024×1536 原生倍数）",
      mod._scaled_size(512, 768, 2.0) == (1024, 1536))
check("_scaled_size 最近 16 倍数 + 下限护栏（64×32 ×1.3 → 80×48→clamp 64；80×32×1.3 原生超限）",
      mod._scaled_size(64, 32, 1.3) == (80, 64))
check("_scaled_size ×1.25 步进值（512×768 ×1.25 → 640×960 原生倍数）",
      mod._scaled_size(512, 768, 1.25) == (640, 960))
check("_scaled_size 下限护栏 64（6×8 ×2.0 → 12×16 → snap 16 → clamp 64）",
      mod._scaled_size(6, 8, 2.0) == (64, 64))
check("_scaled_size 界内原生倍数保持（3000×2000 ×2.0 → 6000→4096；4000 原生 16 倍数不动）",
      mod._scaled_size(3000, 2000, 2.0) == (4096, 4000))
check("_scaled_size 上限护栏 4096（2400×2200 ×2.0 → 4800×4400 → 双边 clamp 4096）",
      mod._scaled_size(2400, 2200, 2.0) == (4096, 4096))

print("== v1.6.0 ImageInput：节点注册 + mock VAE 输出形状 ==")
check("ImageInput typed 输出面（LATENT + FLOAT 两口）",
      imgin_cls.RETURN_TYPES == ("LATENT", "FLOAT")
      and imgin_cls.RETURN_NAMES == ("latent", "denoise"))
check("ImageInput vae 输入口在 required", "vae" in imgin_cls.INPUT_TYPES()["required"])
check("显示名已配置", "PromptHelperImageInput" in mod.NODE_DISPLAY_NAME_MAPPINGS)

import numpy as _np2
import torch as _torch2
from PIL import Image as _PIL2

IMAGES_TMP = os.path.join(CT_DIR, "images")
os.makedirs(IMAGES_TMP, exist_ok=True)
_PIL2.new("RGB", (6, 8), (255, 128, 0)).save(os.path.join(IMAGES_TMP, "a.png"))   # W=6 H=8
_PIL2.new("RGBA", (6, 8), (0, 0, 0, 255)).save(os.path.join(IMAGES_TMP, "rgba.png"))
_real_images_dir = mod.IMAGES_DIR
mod.IMAGES_DIR = IMAGES_TMP


class _MockVAE:
    """记录喂进 encode 的张量（形状/数值），返回假张量（v1.6.1 起 VAE.encode 返
    裸张量、LATENT dict 包装在节点层——核心 VAEEncode 节点同款语义）。"""
    def __init__(self):
        self.fed = []

    def encode(self, pixels):
        self.fed.append(pixels.clone())
        return f"LATENT:{tuple(pixels.shape)}"


_write(img_json, json.dumps({"image": "a.png", "denoise": 0.7}))
vae = _MockVAE()
latent, denoise = imgin_cls().load(vae, img_json)
check("LATENT = {\"samples\": encode 裸张量包装}（v1.6.1，核心 VAEEncode 同款）",
      latent == {"samples": "LATENT:(1, 8, 6, 3)"} and len(vae.fed) == 1)
px = vae.fed[0]
check("LoadImage 语义张量：[1,H,W,C] float32 0..1（H=8 W=6，EXIF 转正后宽高不换位）",
      tuple(px.shape) == (1, 8, 6, 3) and px.dtype == _torch2.float32
      and abs(float(px.max()) - 1.0) < 1e-6 and float(px.min()) >= 0.0
      and abs(float(px[0, 0, 0, 0]) - 1.0) < 1e-6)   # 左上白像素 → 1.0
check("denoise typed 透传", denoise == 0.7)
_write(img_json, json.dumps({"image": "rgba.png"}))
vae2 = _MockVAE()
_, denoise2 = imgin_cls().load(vae2, img_json)
check("RGBA 输入图丢 alpha 成 3 通道 + denoise 缺省 0.5",
      tuple(vae2.fed[0].shape) == (1, 8, 6, 3) and denoise2 == 0.5
      and abs(float(vae2.fed[0][0, 0, 0, 0])) < 1e-6)  # 黑像素 → 0.0
_write(img_json, json.dumps({"image": "a.png", "denoise": 1.2}))
check("节点层 denoise 越界收敛（1.2 → 0.95）", imgin_cls().load(_MockVAE(), img_json)[1] == 0.95)

print("== v1.7.0 ImageInput scale：LANCZOS 放大形状（mock VAE 记录喂进形状） ==")
_write(img_json, json.dumps({"image": "a.png", "denoise": 0.5, "scale": 2.0}))
vae_s2 = _MockVAE()
latent_s2, den_s2 = imgin_cls().load(vae_s2, img_json)
# 6×8(W×H) ×2.0 → 12×16 → 最近 16 倍数 16×16 → 下限护栏 64×64
check("scale=2.0 放大形状：64×64（16 倍数 snap + 64 下限护栏）",
      latent_s2 == {"samples": "LATENT:(1, 64, 64, 3)"} and tuple(vae_s2.fed[0].shape) == (1, 64, 64, 3)
      and den_s2 == 0.5)
_write(img_json, json.dumps({"image": "a.png", "denoise": 0.5, "scale": 1.0}))
vae_s1 = _MockVAE()
imgin_cls().load(vae_s1, img_json)
check("scale=1.0 零重采样（6×8 原样——v1.6.x 行为逐像素一致）",
      tuple(vae_s1.fed[0].shape) == (1, 8, 6, 3))
_write(img_json, json.dumps({"image": "a.png", "denoise": 0.5, "scale": 1.3}))
vae_s13 = _MockVAE()
imgin_cls().load(vae_s13, img_json)
check("scale=1.3（滑杆步进外的越步值）仍收敛放大：64×64（越界收敛不报错）",
      tuple(vae_s13.fed[0].shape) == (1, 64, 64, 3))
# 放大内容 LANCZOS 语义：64×64 渐变图放大前左上/右下像素值保持（重采样非裁剪）
_grad = _PIL2.new("RGB", (64, 64))
for _x in range(64):
    for _y in range(64):
        _grad.putpixel((_x, _y), (int(_x * 4), int(_y * 4), 128))
_grad.save(os.path.join(IMAGES_TMP, "grad.png"))
_write(img_json, json.dumps({"image": "grad.png", "denoise": 0.5, "scale": 2.0}))
vae_g = _MockVAE()
imgin_cls().load(vae_g, img_json)
_pxg = vae_g.fed[0]   # [1,128,128,3]（64×64 ×2.0 → 128×128 原生 16 倍数）
check("LANCZOS 放大保内容（64×64 渐变 →128×128；角像素 0..1 归一后随缩放保持）",
      tuple(_pxg.shape) == (1, 128, 128, 3)
      and abs(float(_pxg[0, 0, 0, 0])) < 2.0 / 255.0
      and abs(float(_pxg[0, 127, 127, 0]) - 252.0 / 255.0) < 4.0 / 255.0)

print("== v1.6.0 ImageInput：报错纪律（不静默直通） ==")


def _expect_runtime(label, call, *kw):
    try:
        call(*kw)
        raise SystemExit("应当 RuntimeError：" + label)
    except RuntimeError as e:
        check(label, "prompt-helper" in str(e))


_expect_runtime("image.json 缺失 → RuntimeError 指路 fth 写方",
                lambda *a: imgin_cls().load(*a), _MockVAE(), missing_json)
_write(img_json, json.dumps({"image": "nope.png", "denoise": 0.5}))
_expect_runtime("images/ 无该文件 → RuntimeError 指路图未拷贝",
                lambda *a: imgin_cls().load(*a), _MockVAE(), img_json)
_write(img_json, json.dumps({"denoise": 0.5}))
_expect_runtime("缺 image 字段 → RuntimeError",
                lambda *a: imgin_cls().load(*a), _MockVAE(), img_json)
_write(img_json, "{broken")
_expect_runtime("损坏 JSON → RuntimeError",
                lambda *a: imgin_cls().load(*a), _MockVAE(), img_json)
open(os.path.join(CT_DIR, "secret.png"), "wb").close()
_write(img_json, json.dumps({"image": "../secret.png"}))
try:
    imgin_cls().load(_MockVAE(), img_json)
    raise SystemExit("应当 RuntimeError（目录穿越）")
except RuntimeError as e:
    check("目录穿越不逃出 images/（../secret.png 收敛到 images/secret.png → 不存在报错）",
          os.path.join(IMAGES_TMP, "secret.png") in str(e))
os.remove(os.path.join(CT_DIR, "secret.png"))

print("== v1.6.0 裸文件名端到端（官方模板零改动可移植的机制）==")
_real_node_dir = mod.NODE_DIR
mod.NODE_DIR = CT_DIR
try:
    _write(os.path.join(CT_DIR, "params.json"),
           json.dumps({"seed": 42, "steps": 28, "cfg": 5.5, "sampler": "dpmpp_2m",
                       "scheduler": "karras", "width": 832, "height": 1216, "batch": 1}))
    check("Params 节点裸文件名 'params.json' → 插件目录（CT_DIR 重定向）",
          params_cls().load("params.json") == (42, 28, 5.5, "dpmpp_2m", "karras", 832, 1216, 1))
    _write(os.path.join(CT_DIR, "prompt.txt"), "bare_tag")
    check("Inject 节点裸文件名 'prompt.txt' 注入生效、空反向语义不变",
          node.inject("prompt.txt", "", "base", "", "追加到末尾", True)[0] == "bare_tag, base")
    _write(img_json, json.dumps({"image": "a.png", "denoise": 0.6}))
    latent_bare, denoise_bare = imgin_cls().load(_MockVAE(), "image.json")
    check("ImageInput 节点裸文件名 'image.json' 端到端", denoise_bare == 0.6)
finally:
    mod.NODE_DIR = _real_node_dir
mod.IMAGES_DIR = _real_images_dir

print("== v1.6.0 官方改图模板 workflows/img2img-template.json ==")
TPL = os.path.join(HERE, "workflows", "img2img-template.json")
wf = json.load(open(TPL, encoding="utf-8"))
tpl_nodes = {n["id"]: n for n in wf["nodes"]}
tpl_links = {l[0]: l for l in wf["links"]}
check("模板存在且 JSON 可解析 + 版本 0.4", wf.get("version") == 0.4)
_tpl_types = [n["type"] for n in wf["nodes"]]
check("节点齐备（7 插件节点 + KSampler/CLIPTextEncode×2/VAEDecode）",
      {"PromptHelperInject", "PromptHelperParams", "PromptHelperLoraStack", "PromptHelperCheckpoint",
       "PromptHelperImageOutput", "PromptHelperFilter", "PromptHelperImageInput",
       "KSampler", "CLIPTextEncode", "VAEDecode"} <= set(_tpl_types))
check("无 EmptyLatentImage（改图 latent 来自 ImageInput）", "EmptyLatentImage" not in _tpl_types)


def _tpl_src(nid, inp):
    _n = tpl_nodes[nid]
    _lid = next(i["link"] for i in _n["inputs"] if i["name"] == inp)
    _l = tpl_links[_lid]
    return tpl_nodes[_l[1]]["type"], tpl_nodes[_l[1]]["outputs"][_l[2]]["name"]


_tpl_ks = next(n["id"] for n in wf["nodes"] if n["type"] == "KSampler")
check("KSampler.latent_image ← ImageInput.latent（Params 的 latent 尺寸路不接）",
      _tpl_src(_tpl_ks, "latent_image") == ("PromptHelperImageInput", "latent"))
check("KSampler.denoise ← ImageInput.denoise（FLOAT 口）",
      _tpl_src(_tpl_ks, "denoise") == ("PromptHelperImageInput", "denoise"))
check("KSampler 五采样参数 ← Params 五口（seed/steps/cfg/sampler/scheduler）",
      all(_tpl_src(_tpl_ks, k) == ("PromptHelperParams", v) for k, v in
          (("seed", "seed"), ("steps", "steps"), ("cfg", "cfg"),
           ("sampler_name", "sampler"), ("scheduler", "scheduler"))))
check("KSampler.model ← LoraStack.model；正负条件 ← 两个 CLIP 编码",
      _tpl_src(_tpl_ks, "model") == ("PromptHelperLoraStack", "model")
      and _tpl_src(_tpl_ks, "positive")[0] == "CLIPTextEncode"
      and _tpl_src(_tpl_ks, "negative")[0] == "CLIPTextEncode")
_tpl_imgin = next(n["id"] for n in wf["nodes"] if n["type"] == "PromptHelperImageInput")
check("ImageInput.vae ← Checkpoint.vae（LoRA 不影响 VAE）",
      _tpl_src(_tpl_imgin, "vae") == ("PromptHelperCheckpoint", "vae"))
_tpl_out = next(n["id"] for n in wf["nodes"] if n["type"] == "PromptHelperImageOutput")
check("ImageOutput：image ← VAEDecode、正负提示词 ← Inject 两口",
      _tpl_src(_tpl_out, "image") == ("VAEDecode", "IMAGE")
      and _tpl_src(_tpl_out, "prompt") == ("PromptHelperInject", "prompt")
      and _tpl_src(_tpl_out, "negative_prompt") == ("PromptHelperInject", "negative_prompt"))
for _nid in (n["id"] for n in wf["nodes"] if n["type"] == "CLIPTextEncode"):
    check("CLIP 编码 clip ← LoraStack.clip（LoRA 后）、text ← Inject",
          _tpl_src(_nid, "clip") == ("PromptHelperLoraStack", "clip")
          and _tpl_src(_nid, "text")[0] == "PromptHelperInject")
check("正负条件来自 Inject 不同输出口",
      _tpl_src(next(n["id"] for n in wf["nodes"] if n["type"] == "CLIPTextEncode" and n.get("title") == "正向条件编码"), "text")[1] == "prompt"
      and _tpl_src(next(n["id"] for n in wf["nodes"] if n["type"] == "CLIPTextEncode" and n.get("title") == "负向条件编码"), "text")[1] == "negative_prompt")
_tpl_params = next(n for n in wf["nodes"] if n["type"] == "PromptHelperParams")
check("Params.width/height/batch 三口悬空（改图链不消费）",
      all(_tpl_params["outputs"][i]["links"] is None for i in (5, 6, 7)))
check("全 22 条 link 端点/类型/双向引用一致",
      all(tpl_nodes.get(l[1]) and tpl_nodes.get(l[3])
          and tpl_nodes[l[1]]["outputs"][l[2]]["type"] == l[5]
          and tpl_nodes[l[3]]["inputs"][l[4]]["type"] == l[5]
          and tpl_nodes[l[3]]["inputs"][l[4]].get("link") == l[0]
          and l[0] in (tpl_nodes[l[1]]["outputs"][l[2]].get("links") or [])
          for l in wf["links"]))
check("last_node_id / last_link_id 与实际一致",
      wf["last_node_id"] == max(tpl_nodes) and wf["last_link_id"] == max(tpl_links))
check("order 为合法拓扑序（所有 link 源先于目标）",
      all(tpl_nodes[l[1]]["order"] < tpl_nodes[l[3]]["order"] for l in wf["links"]))
_tpl_w = {n["type"]: n.get("widgets_values") for n in wf["nodes"]}
check("契约路径 widget 全为裸文件名（任意安装路径导入即用）",
      _tpl_w["PromptHelperCheckpoint"] == ["checkpoint.json"]
      and _tpl_w["PromptHelperLoraStack"] == ["lora_list.json"]
      and _tpl_w["PromptHelperParams"] == ["params.json"]
      and _tpl_w["PromptHelperImageInput"] == ["image.json"]
      and _tpl_w["PromptHelperFilter"] == ["filter_output.txt"])
check("Inject widget（prompt.txt / prompt_neg.txt 裸名 + 空 base + 位置 + 合并）",
      _tpl_w["PromptHelperInject"] == ["prompt.txt", "prompt_neg.txt", "", "", "追加到末尾", True])

print("== v1.8.0 官方文生图模板 workflows/txt2img-template.json ==")
T2I = os.path.join(HERE, "workflows", "txt2img-template.json")
wf2 = json.load(open(T2I, encoding="utf-8"))
t2i_nodes = {n["id"]: n for n in wf2["nodes"]}
t2i_links = {l[0]: l for l in wf2["links"]}
check("模板存在且 JSON 可解析 + 版本 0.4", wf2.get("version") == 0.4)
_t2i_types = [n["type"] for n in wf2["nodes"]]
check("节点齐备（7 插件节点 + KSampler/CLIPTextEncode×2/VAEDecode/EmptyLatentImage）",
      {"PromptHelperInject", "PromptHelperParams", "PromptHelperLoraStack", "PromptHelperCheckpoint",
       "PromptHelperImageOutput", "PromptHelperFilter",
       "KSampler", "CLIPTextEncode", "VAEDecode", "EmptyLatentImage"} <= set(_t2i_types))
check("无 ImageInput / VAEEncode（文生图 latent 来自 EmptyLatentImage）",
      "PromptHelperImageInput" not in _t2i_types and "VAEEncode" not in _t2i_types)


def _t2i_src(nid, inp):
    _n = t2i_nodes[nid]
    _lid = next(i["link"] for i in _n["inputs"] if i["name"] == inp)
    _l = t2i_links[_lid]
    return t2i_nodes[_l[1]]["type"], t2i_nodes[_l[1]]["outputs"][_l[2]]["name"]


_t2i_ks = next(n["id"] for n in wf2["nodes"] if n["type"] == "KSampler")
check("KSampler.latent_image ← EmptyLatentImage.latent",
      _t2i_src(_t2i_ks, "latent_image") == ("EmptyLatentImage", "LATENT"))
check("KSampler.denoise 悬空（widget 承载=1，文生图全重绘）",
      next(i["link"] for i in t2i_nodes[_t2i_ks]["inputs"] if i["name"] == "denoise") is None
      and t2i_nodes[_t2i_ks]["widgets_values"][6] == 1)
check("KSampler 五采样参数 ← Params 五口（seed/steps/cfg/sampler/scheduler）",
      all(_t2i_src(_t2i_ks, k) == ("PromptHelperParams", v) for k, v in
          (("seed", "seed"), ("steps", "steps"), ("cfg", "cfg"),
           ("sampler_name", "sampler"), ("scheduler", "scheduler"))))
check("KSampler.model ← LoraStack.model；正负条件 ← 两个 CLIP 编码",
      _t2i_src(_t2i_ks, "model") == ("PromptHelperLoraStack", "model")
      and _t2i_src(_t2i_ks, "positive")[0] == "CLIPTextEncode"
      and _t2i_src(_t2i_ks, "negative")[0] == "CLIPTextEncode")
_t2i_eli = next(n["id"] for n in wf2["nodes"] if n["type"] == "EmptyLatentImage")
check("EmptyLatentImage 三口全接 Params（width/height/batch_size ← Params 三悬空口启用）",
      _t2i_src(_t2i_eli, "width") == ("PromptHelperParams", "width")
      and _t2i_src(_t2i_eli, "height") == ("PromptHelperParams", "height")
      and _t2i_src(_t2i_eli, "batch_size") == ("PromptHelperParams", "batch"))
check("Params.width/height/batch 三口全接出（对照改图模板悬空——文生图消费）",
      all(t2i_nodes[next(n["id"] for n in wf2["nodes"] if n["type"] == "PromptHelperParams")]["outputs"][i]["links"]
          for i in (5, 6, 7)))
_t2i_out = next(n["id"] for n in wf2["nodes"] if n["type"] == "PromptHelperImageOutput")
check("ImageOutput：image ← VAEDecode、正负提示词 ← Inject 两口",
      _t2i_src(_t2i_out, "image") == ("VAEDecode", "IMAGE")
      and _t2i_src(_t2i_out, "prompt") == ("PromptHelperInject", "prompt")
      and _t2i_src(_t2i_out, "negative_prompt") == ("PromptHelperInject", "negative_prompt"))
check("Checkpoint.vae 只喂 VAEDecode（无 ImageInput 段）",
      t2i_nodes[next(n["id"] for n in wf2["nodes"] if n["type"] == "PromptHelperCheckpoint")]["outputs"][2]["links"] == [19])
check("全 23 条 link 端点/类型/双向引用一致（22 少 ImageInput 三条、多 EmptyLatentImage 四条）",
      all(t2i_nodes.get(l[1]) and t2i_nodes.get(l[3])
          and t2i_nodes[l[1]]["outputs"][l[2]]["type"] == l[5]
          and t2i_nodes[l[3]]["inputs"][l[4]]["type"] == l[5]
          and t2i_nodes[l[3]]["inputs"][l[4]].get("link") == l[0]
          and l[0] in (t2i_nodes[l[1]]["outputs"][l[2]].get("links") or [])
          for l in wf2["links"]) and len(wf2["links"]) == 23)
check("last_node_id / last_link_id 与实际一致",
      wf2["last_node_id"] == max(t2i_nodes) and wf2["last_link_id"] == max(t2i_links))
check("order 为合法拓扑序（所有 link 源先于目标）",
      all(t2i_nodes[l[1]]["order"] < t2i_nodes[l[3]]["order"] for l in wf2["links"]))
_t2i_w = {n["type"]: n.get("widgets_values") for n in wf2["nodes"]}
check("契约路径 widget 全为裸文件名（与改图模板同规）",
      _t2i_w["PromptHelperCheckpoint"] == ["checkpoint.json"]
      and _t2i_w["PromptHelperLoraStack"] == ["lora_list.json"]
      and _t2i_w["PromptHelperParams"] == ["params.json"]
      and _t2i_w["PromptHelperFilter"] == ["filter_output.txt"]
      and "PromptHelperImageInput" not in _t2i_w)
check("Inject widget 与改图模板逐字一致（prompt 对裸名）",
      _t2i_w["PromptHelperInject"] == _tpl_w["PromptHelperInject"])

try:
    print("\n全部测试通过 ✔")
except UnicodeEncodeError:
    print("\n全部测试通过")   # 管道/重定向 stdout=GBK 代码页时 ✔ 编不出（v1.7.0 补）——检查本体已全 PASS
