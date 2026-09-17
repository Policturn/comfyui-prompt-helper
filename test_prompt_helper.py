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

print("\n全部测试通过 ✔")
