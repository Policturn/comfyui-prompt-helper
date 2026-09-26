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
check("NODE_CLASS_MAPPINGS 含全部 6 节点",
      set(mod.NODE_CLASS_MAPPINGS) == {"PromptHelperInject", "PromptHelperLoraStack",
                                       "PromptHelperParams", "PromptHelperImageOutput",
                                       "PromptHelperCheckpoint", "PromptHelperFilter"})
check("6 节点显示名全配置",
      set(mod.NODE_CLASS_MAPPINGS) == set(mod.NODE_DISPLAY_NAME_MAPPINGS))
lora_cls = mod.NODE_CLASS_MAPPINGS["PromptHelperLoraStack"]
params_cls = mod.NODE_CLASS_MAPPINGS["PromptHelperParams"]
ckpt_cls = mod.NODE_CLASS_MAPPINGS["PromptHelperCheckpoint"]
filter_cls = mod.NODE_CLASS_MAPPINGS["PromptHelperFilter"]
imgout_cls = mod.NODE_CLASS_MAPPINGS["PromptHelperImageOutput"]
for cls in (lora_cls, params_cls, ckpt_cls, filter_cls):
    check(f"{cls.__name__} IS_CHANGED 返回 NaN", cls.IS_CHANGED(x=1) != cls.IS_CHANGED(x=1))
check("ImageOutput 为 OUTPUT_NODE（执行终点，不吃缓存）", imgout_cls.OUTPUT_NODE is True)
check("契约文件输入默认值指向插件目录",
      lora_cls.INPUT_TYPES()["required"]["lora_file"][1]["default"] == mod.LORA_LIST_PATH
      and params_cls.INPUT_TYPES()["required"]["params_file"][1]["default"] == mod.PARAMS_PATH
      and ckpt_cls.INPUT_TYPES()["required"]["checkpoint_file"][1]["default"] == mod.CHECKPOINT_PATH
      and filter_cls.INPUT_TYPES()["required"]["filter_file"][1]["default"] == mod.FILTER_OUTPUT_PATH)
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
entries_missing, err_missing = mod._read_lora_list(missing_json)
check("LoraList：缺失 → 空列表（直通安全默认）", entries_missing == [] and "不存在" in err_missing)

ckpt_ok = os.path.join(CT_DIR, "checkpoint.json")
_write(ckpt_ok, json.dumps({"name": "testmodel.safetensors"}))
check("Checkpoint：正常读取", mod._read_checkpoint_name(ckpt_ok) == ("testmodel.safetensors", ""))
_write(ckpt_ok, json.dumps({"name": ""}))
check("Checkpoint：name 空 → 报错口径", mod._read_checkpoint_name(ckpt_ok)[0] is None)

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
        return [CT_DIR] if kind == "embeddings" else []

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

    model, clip, vae, name = ckpt_cls().load(ckpt_ok)
    check("Checkpoint：加载输出 model/clip/vae/name（name 去扩展名）",
          (model, clip, vae, name) == ("MODEL", "CLIP", "VAE", "testmodel")
          and _ckpt_load_calls[-1].endswith("testmodel.safetensors"))
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

print("\n全部测试通过 ✔")
