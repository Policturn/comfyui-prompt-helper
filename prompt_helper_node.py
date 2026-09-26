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

v1.4.1 起注入位置确定化：词条恒定拼接在提示词最前，输出的 prompt 结构恒为
[注入词条][base_prompt]（position 输入仅为兼容旧工作流保留，取值被忽略）。
sidecar 各侧记录同时新增 injected_tags（注入区 tag 数，按逗号拆分计数，与
编辑器自然条 offset 同基准）与 full_text（注入后的完整提示词）两个字段，
供编辑器端校准实际送入 CLIP 的文本。

v1.4.2 根治 editor_path 随编辑器发版 exe 改名失效的问题（config 硬编码完整
路径，编辑器每次发版必断链、自动启动静默失效）：launch_editor 起始处经
_resolve_editor_path 解析——configured 指向的 exe 存在则原样使用；已失效则
在同目录扫描 feetaghelper-v*.exe，按文件名版本号元组（(2, 7, 5) 式比较，
兼容 v 前缀与任意多段数字）取最新者，并把解析结果写回 config（下次自动
启动直接使用新路径）；同目录无候选时返回原值，保持"文件不存在"的原有报错
行为。进程防重探测（_is_process_running）与最终 subprocess 均使用解析后的
路径。

v1.4.3 镜像共享函数 _read_negative_pin（读节点目录根 negative_path.pin，
纯文本一行=反向词条 txt 完整路径；utf-8 / GBK 双编码兜底，每次读取现读、
修改即刻生效）：WebUI 版用它根治 config 反向路径被旧页面内存值回写冲空
（pin 存在且非空时无视 config / UI 值，UI 提交值同步写回 pin）。ComfyUI
的反向路径来自工作流节点输入、不存在该回写问题，故 inject() 语义不变，
本仓仅镜像共享函数保持双仓共享面一致（未来如需 pin 固定反向路径可直接接线）。

v1.4.5 镜像统一固定机制 _read_pin_overrides（WebUI v1.4.12 的 settings.pin
同款读取语义：JSON 任意 config 键子集，逐键优先级 settings.pin > 旧独立
pin（negative_path.pin / positive_path.pin 迁移兼容并入）> config；路径键
空值视作未固定、utf-8 / GBK 双编码兜底、每次现读即刻生效）。ComfyUI 的
路径来自工作流节点输入、不存在回写冲空问题，inject() 语义不变，本仓仅
镜像共享函数面保持双仓一致（未来如需 pin 固定路径可直接接线）。

v1.4.6 版本镜像 +1：WebUI v1.4.13 指令延迟压缩（总线轮询 500ms→200ms +
apply 完成信号 applied_ts 取代盲等 700ms + 信号等待双节拍驱动）为
生成页总线专属机制，ComfyUI 无 JS 轮询 / 无浏览器触发链，零代码改动仅
版本对齐（分叉规则见 同步维护说明.md）。

v1.4.8 镜像共享函数 _atomic_write_text（WebUI v1.4.16 配置 / 状态原子写
同款：同目录临时文件 + os.replace 原子落盘，读者不再可能读到半截 JSON），
_config_lock 镜像定义保持共享块逐字一致；本仓应用于 _resolve_editor_path
的 config 写回与 _record_meta 的 sidecar 写入。WebUI v1.4.16 其余三项
（接线分组分线 / Reload UI 接线复位 / 总线看门狗）为生成页总线专属机制，
ComfyUI 无对应机制零改动（分叉规则见 同步维护说明.md）。

v1.4.9 镜像 launch_editor 进程树脱离（WebUI v1.4.19 同款，X-174 环境坑ⓐ：
外部 stop 按快照父子链递归强杀宿主进程树（如 webui.py stop 的
taskkill /F /T），编辑器作为宿主直接子进程被连带杀——DETACHED_PROCESS /
CREATE_NEW_PROCESS_GROUP 只隔离控制台信号，挡不住显式树杀）：Windows
分支改经 cmd /c start 中转（编辑器挂到 cmd 名下、cmd 随即退出，快照
父子链断开，树杀不再沿链命中；启动后约 0.1s 起免疫）并附
CREATE_BREAKAWAY_FROM_JOB（宿主被启动器放进 kill-on-close 的 Job 对象时
子进程脱出；Job 拒绝 breakaway 则 CreateProcess 报错，裸旗标重试一次，
cmd 中转彻底不可用再回退直启，保底行为与旧版一致）。同批追加畸形路径熔断：
cmd 中转送 shell 前，剥引号/空白后为空或仅由分隔符与点号组成（\、\\、/、
.、.. 等）的路径一律不送 cmd / start，直接回「编辑器路径无效」error
（shell 类调用收到空目标会触发系统级「找不到文件」弹窗）。WebUI v1.4.19 的
直发路径同图双落盘 + pass 双计根除为生成页总线专属机制，ComfyUI 无
直发链路零改动（分叉规则见 同步维护说明.md）。

v1.5.0（2026-09-27 拍板，ComfyUI 插件优化计划 v3 步骤 1，本仓专属——WebUI
版无对应机制零改动，分叉规则见 同步维护说明.md）新增节点群 + 文件契约：
插件节点 = 智能适配器（读编辑器写的文件），编辑器 = 文件写方 + 远程触发器。
五个新节点：LoraStack（lora_list.json 循环叠加 LoRA）/ Params（params.json
平铺参数 → typed 输出）/ ImageOutput（OUTPUT_NODE：IMAGE→PIL 落盘 ComfyUI
output 目录，PNG tEXt 嵌入 A1111 格式 parameters + fth_meta 元数据，追加
image_manifest.json 取图清单）/ Checkpoint（checkpoint.json 选基模）/
Filter（filter_output.txt 过滤结果）。Inject 节点增强：hidden PROMPT 携带
本次执行的完整 API 格式工作流图，每次执行原子写 workflow_snapshot.json
（编辑器「生成前检查 + 原样重提交」的数据源），原有注入功能零变化。
容错口径：LoraStack/Params/Filter 契约文件缺失损坏 → 安全默认直通（日志
定位）；Checkpoint → 清晰报错（基模不可静默默认）；ImageOutput 各元数据源
缺席 → 对应 parameters 段省略，保存本体不受影响。
"""

import base64
import binascii
import hashlib
import json
import os
import re
import subprocess
import threading
import time

NODE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(NODE_DIR, "config.json")
META_LOG_PATH = os.path.join(NODE_DIR, "prompt_helper_meta.json")
# 反向词条路径固定文件（v1.4.3，共享函数镜像）：存在且非空时反向路径以 pin 为准。
# ComfyUI 侧暂未接线（反向路径来自工作流，无回写冲空问题），仅保持双仓共享面一致。
NEGATIVE_PIN_PATH = os.path.join(NODE_DIR, "negative_path.pin")
# 正向词条路径固定文件 + 统一设置固定文件（v1.4.5，共享函数镜像）：WebUI v1.4.12
# 的 settings.pin（JSON 任意 config 键子集）同款读取语义；ComfyUI 侧暂未接线，
# 仅保持双仓共享面一致。
POSITIVE_PIN_PATH = os.path.join(NODE_DIR, "positive_path.pin")
SETTINGS_PIN_PATH = os.path.join(NODE_DIR, "settings.pin")
# 编辑器自荐路径（v1.4.10，X-177 契约）：插件根 editor.hint，单行文本 =
# 编辑器 exe 绝对路径（编辑器侧在连接「检测」时幂等写入；编辑器永不改
# 插件 config，单向传值）。解析链位次：config 用户值有效 > editor.hint
# 有效 > 同目录扫描最新版（救援保持）> 原有兜底——用户值恒优先，hint
# 只在用户未设置 / 已失效时兜住（零配置可用）。已被 .gitignore 排除。
EDITOR_HINT_PATH = os.path.join(NODE_DIR, "editor.hint")

# ---- v1.5.0（2026-09-27 拍板，ComfyUI v3 计划步骤 1）文件契约路径 ----
# 契约文件全部落在插件目录（与 config.json 同目录）。编辑器（写方）→ 插件（读方）：
LORA_LIST_PATH = os.path.join(NODE_DIR, "lora_list.json")      # [{name, model, clip}, ...]
PARAMS_PATH = os.path.join(NODE_DIR, "params.json")            # {seed, steps, cfg, ...} 平铺
CHECKPOINT_PATH = os.path.join(NODE_DIR, "checkpoint.json")    # {"name": "xxx.safetensors"}
FILTER_OUTPUT_PATH = os.path.join(NODE_DIR, "filter_output.txt")  # 过滤页实时输出（保留词条）
# 插件（写方）→ 编辑器（读方）：
WORKFLOW_SNAPSHOT_PATH = os.path.join(NODE_DIR, "workflow_snapshot.json")  # API 格式工作流图
IMAGE_MANIFEST_PATH = os.path.join(NODE_DIR, "image_manifest.json")        # 取图清单（追加式）
IMAGE_MANIFEST_CAP = 500  # 清单条数上限（超出丢最旧，防长期累积无界膨胀；编辑器消费后旧条目无留存价值）

# v1.4.5：镜像统一固定机制 _read_pin_overrides（WebUI v1.4.12 settings.pin 同款
# 读取语义 + 旧独立 pin 兼容并入），与 v1.4.3 的 negative 镜像同款处理——
# 仅共享函数面，inject 语义不变。
# v1.5.0（2026-09-27）：ComfyUI v3 节点群（本仓专属，见模块 docstring）。
PLUGIN_VERSION = "1.5.0"

POSITIONS = ("追加到末尾", "插入到最前")

# config 族文件写锁（v1.4.8 镜像，与 _atomic_write_text 配套）：串行化
# _resolve_editor_path 写回与 _record_meta sidecar 写入的并发（WebUI 侧还
# 串行 _save_config / settings.pin，本仓保持共享块逐字一致故一并定义）
_config_lock = threading.Lock()

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


def _atomic_write_text(path, text):
    """原子写文本文件（v1.4.16，共享函数）：先写同目录临时文件再 os.replace
    覆盖目标（同卷原子操作，Windows / Linux 均原子）。读者（编辑器面板轮询 /
    下次 _load_config）只会看到完整的旧版或新版内容，不会读到半截；进程在
    写入中途崩溃也只会留下临时文件、目标保持旧版。

    Windows 细节：目标正被并发读者持有句柄时 replace 可能短暂 PermissionError
    （CPython 的读取端不带 FILE_SHARE_DELETE，而 Rust / JS 读端带、不受影响）
    ——读取窗口只有微秒级，短暂重试后仍失败才抛 OSError（调用方兜底、目标
    保持旧版），临时文件尽力清理。"""
    tmp = f"{path}.tmp-{os.getpid()}-{threading.get_ident()}"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.02)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _load_config():
    cfg = dict(DEFAULT_CONFIG)
    loaded = None
    for attempt in range(3):  # v1.4.8 镜像：原子替换窗口内 open 可能被短暂拒绝，重读即愈
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                loaded = json.load(f)
            break
        except FileNotFoundError:
            break  # 配置缺失（新装机常态）：默认值即可，不重试不拖延
        except (OSError, ValueError):
            if attempt < 2:
                time.sleep(0.05)
    if isinstance(loaded, dict):
        cfg.update(loaded)
    cfg["autostart"] = bool(cfg["autostart"])
    for key in ("path", "negative_path", "editor_path"):
        cfg[key] = str(cfg[key] or "")
    return cfg


def _normalize_path(path):
    path = (path or "").strip().strip('"').strip("'")
    return os.path.expandvars(os.path.expanduser(path))


def _read_pin_file(path):
    """读单行路径 pin 文件（旧独立 pin 机制，v1.4.3 镜像）。存在且非空 → 返回
    路径（去引号/首尾空白 + expandvars/expanduser 容错，utf-8 / GBK 双编码
    兜底）；不存在 / 空文件 / 读取失败 → 返回 ""。"""
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            with open(path, "r", encoding=encoding) as f:
                return _normalize_path(f.read())
        except (OSError, ValueError):
            continue
    return ""


def _read_negative_pin():
    """读 negative_path.pin（v1.4.3 旧机制读取接口原样保留，现为
    _read_pin_overrides 的兼容层）。"""
    return _read_pin_file(NEGATIVE_PIN_PATH)


def _read_pin_json(path):
    """读 JSON pin 文件（settings.pin，v1.4.5 镜像）。缺失 / 损坏 / 非对象 →
    返回 {}，不抛错；utf-8 / GBK 双编码兜底（与单行 pin 同款容错）。"""
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            with open(path, "r", encoding=encoding) as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        return data if isinstance(data, dict) else {}
    return {}


def _read_pin_overrides():
    """读统一固定文件 settings.pin（JSON，任意 config 键子集）并合并旧独立 pin
    （v1.4.5 镜像 WebUI v1.4.12 同款读取语义，本仓暂未接线、仅共享面一致）。

    读取优先级（逐键）：settings.pin > 旧独立 pin（negative_path.pin /
    positive_path.pin，迁移兼容）> config（调用方回退，零迁移）。每次现读，
    放置/修改/删除即刻生效，无需重启 ComfyUI。返回仅含本仓 config 键
    （path / negative_path / autostart / editor_path）；路径键空值视作未固定
    （不遮蔽旧独立 pin / config），布尔键原样透传（调用方按键语义强转）。
    """
    overrides = {}
    for pin_path, key in ((NEGATIVE_PIN_PATH, "negative_path"),
                          (POSITIVE_PIN_PATH, "path")):
        value = _read_pin_file(pin_path)
        if value:
            overrides[key] = value
    for key, value in _read_pin_json(SETTINGS_PIN_PATH).items():
        if key not in DEFAULT_CONFIG:
            continue
        if key in ("path", "negative_path", "editor_path"):
            value = _normalize_path(str(value or ""))
            if not value:
                continue  # 空值 = 该键未固定，勿遮蔽旧独立 pin / config
        overrides[key] = value
    return overrides


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


def _inject(base, tags, sep=", "):
    # v1.4.1 起注入位置确定化：词条恒定拼接在最前，结构恒为 [注入词条][base]
    if not base:
        return tags
    return f"{tags}{sep}{base}"


def _count_tags(text):
    """按逗号拆分统计非空 tag 数（与编辑器自然条 offset 同基准，BREAK 展开前计数）。"""
    return len([t for t in (x.strip() for x in text.split(",")) if t])


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


def _record_meta(records):
    """把各侧注入记录写入节点目录 sidecar JSON（附插件版本 + 时间戳，每次执行覆盖）。

    records 形如 {"positive": 记录 或 None, "negative": 记录 或 None}；每条记录为
    元数据（可能没有）+ injected_tags（注入区 tag 数）+ full_text（注入后完整
    提示词）的合并 dict，None 表示该侧本次未注入、键缺席。写入失败不影响注入。
    """
    entry = {"updated": time.strftime("%Y-%m-%d %H:%M:%S"), "plugin": PLUGIN_VERSION}
    for side, record in records.items():
        if record is not None:
            entry[side] = record
    try:
        with _config_lock:
            _atomic_write_text(META_LOG_PATH, json.dumps(entry, ensure_ascii=False, indent=2))
    except OSError:
        pass  # 记录失败不影响注入


# ---------------------------------------------------------------------------
# v1.5.0（2026-09-27 拍板）：编辑器 ↔ 插件文件契约的读写助手
# ---------------------------------------------------------------------------

def _read_json_file(path, expect):
    """容错读 JSON 契约文件（编辑器写方）。返回 (数据或 None, 错误消息)。

    expect 为期望顶层类型（dict / list），不符视作损坏。utf-8-sig / GBK 双编码
    兜底 + 编辑器写入瞬间短暂占用的重读（与 read_tag_file / _read_pin_json
    同款语义）。调用方按节点语义决定安全默认或清晰报错。"""
    if not _normalize_path(path):
        return None, "未设置文件路径"
    if not os.path.isfile(path):
        return None, "文件不存在：" + path
    last_error = "编码无法识别（UTF-8 / GBK 均解码失败）"
    for _ in range(3):
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                with open(path, "r", encoding=encoding) as f:
                    data = json.load(f)
            except (OSError, ValueError) as e:
                last_error = f"读取失败：{e}"
                continue
            if not isinstance(data, expect):
                return None, "格式不符：顶层应为" + ("对象" if expect is dict else "数组")
            return data, ""
        time.sleep(0.05)
    return None, last_error


def _to_int(value, default):
    """宽松 int 强转（"20" / 20 → 20；坏值 / None → default）。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value, default):
    """宽松 float 强转（"7" / 7 / 7.0 → float；坏值 / None → default）。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# Params 节点安全默认（params.json 缺失 / 损坏时工作流仍可跑；采样器名用
# ComfyUI 原生拼写，A1111 别名（Euler a 等）的映射归编辑器写方负责）
PARAMS_DEFAULTS = {
    "seed": 0, "steps": 20, "cfg": 7.0, "sampler": "euler",
    "scheduler": "normal", "width": 512, "height": 512, "batch": 1,
}


def _read_params(params_file):
    """读 params.json（编辑器写方，平铺参数）→ typed dict。返回 (params, 错误消息)。

    缺失 / 损坏 → 全默认值 + 非空错误消息（调用方日志定位）；文件在但个别键
    坏值 → 仅该键回落默认。字符串数值（"20"）宽松强转。"""
    data, err = _read_json_file(params_file, dict)
    params = dict(PARAMS_DEFAULTS)
    if data:
        params["seed"] = _to_int(data.get("seed"), params["seed"])
        params["steps"] = _to_int(data.get("steps"), params["steps"])
        params["cfg"] = _to_float(data.get("cfg"), params["cfg"])
        params["width"] = _to_int(data.get("width"), params["width"])
        params["height"] = _to_int(data.get("height"), params["height"])
        params["batch"] = _to_int(data.get("batch"), params["batch"])
        params["sampler"] = str(data.get("sampler") or "").strip() or params["sampler"]
        params["scheduler"] = str(data.get("scheduler") or "").strip() or params["scheduler"]
    return params, err


def _read_lora_list(lora_file):
    """读 lora_list.json → 规范化条目列表。返回 (entries, 错误消息)。

    [{name, model, clip}, ...]：name 必填（strip 后非空），权重缺省 1.0、
    坏值回落 1.0；非对象条目 / 无名条目静默跳过（写方脏数据不拦整链）。
    缺失 / 损坏 → 空列表（LoraStack 直通安全默认）。"""
    data, err = _read_json_file(lora_file, list)
    entries = []
    for item in (data or []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        entries.append({
            "name": name,
            "model": _to_float(item.get("model", 1.0), 1.0),
            "clip": _to_float(item.get("clip", 1.0), 1.0),
        })
    return entries, err


def _read_checkpoint_name(checkpoint_file):
    """读 checkpoint.json（{"name": "xxx.safetensors"}）。返回 (名字或 None, 错误消息)。"""
    data, err = _read_json_file(checkpoint_file, dict)
    if data is None:
        return None, err
    name = str(data.get("name") or "").strip()
    if not name:
        return None, "未指定模型名（checkpoint.json 的 name 为空）"
    return name, ""


def _parse_filter_output(text):
    """解析 filter_output.txt（编辑器过滤页实时输出）→ (kept_tags, removed, total, rules)。

    双格式容忍（读方适配，写方格式由编辑器侧定，v3 步骤 4 落地）：
    ① 纯文本 = 契约表基线格式（逗号拼接保留词条）：kept 原文、total 按逗号
       拆分计数，removed 无从得知记 0、rules 记 None（未知）；
    ② JSON 对象信封（可选增强）：kept / kept_tags（字符串或数组）、
       removed / removed_count、total、rules / active_rules——统计字段齐全，
       节点 status 可报「活跃 N 条规则」。形似 {face} 的词条 JSON 解析失败
       自然回落纯文本路径。"""
    text = (text or "").strip()
    if not text:
        return "", 0, 0, None
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, dict):
            kept = data.get("kept_tags", data.get("kept", ""))
            if isinstance(kept, list):
                kept = ", ".join(str(t).strip() for t in kept if str(t).strip())
            kept = str(kept or "").strip()
            removed = _to_int(data.get("removed_count", data.get("removed")), 0)
            total = _to_int(data.get("total"), _count_tags(kept))
            rules = _to_int(data.get("active_rules", data.get("rules")), None)
            return kept, removed, total, rules
    return text, 0, _count_tags(text), None


def _write_workflow_snapshot(workflow):
    """把本次执行的完整工作流图（hidden PROMPT，ComfyUI API 格式）原子写入插件
    目录 workflow_snapshot.json——编辑器读它做「生成前 mtime 检查 + 原样重提交
    POST /prompt」。非 dict（前端未传 / 旧版本缺席）直接跳过；写失败不影响注入。"""
    if not isinstance(workflow, dict) or not workflow:
        return False
    try:
        with _config_lock:
            _atomic_write_text(
                WORKFLOW_SNAPSHOT_PATH,
                json.dumps(workflow, ensure_ascii=False, indent=2))
        return True
    except (OSError, TypeError, ValueError):
        _log("workflow_snapshot.json 写入失败（不影响注入）")
        return False


def _append_image_manifest(entries):
    """把本队次落盘图条目追加进 image_manifest.json（编辑器取图清单，
    [{prompt_id, filename, subfolder, ts}, ...]）。原子读改写（_config_lock
    串行）；清单损坏则重建（旧内容放弃）；超出 IMAGE_MANIFEST_CAP 丢最旧；
    写失败只打日志，不影响保存图像。"""
    try:
        with _config_lock:
            existing = []
            try:
                with open(IMAGE_MANIFEST_PATH, "r", encoding="utf-8-sig") as f:
                    loaded = json.load(f)
                if isinstance(loaded, list):
                    existing = [e for e in loaded if isinstance(e, dict)]
            except (OSError, ValueError):
                existing = []
            existing.extend(entries)
            if len(existing) > IMAGE_MANIFEST_CAP:
                del existing[:len(existing) - IMAGE_MANIFEST_CAP]
            _atomic_write_text(
                IMAGE_MANIFEST_PATH,
                json.dumps(existing, ensure_ascii=False, indent=2))
        return True
    except OSError:
        _log("image_manifest.json 追加失败（不影响保存图像）")
        return False


_model_hash_cache = {}


def _file_hash_prefix(path):
    """计算文件 sha256 前 10 位十六进制（A1111 infotext 短哈希同款口径）。
    进程内按绝对路径缓存（LoRA 文件动辄上百 MB，重复出图不重算）。失败返回 ""。"""
    path = os.path.abspath(path)
    cached = _model_hash_cache.get(path)
    if cached:
        return cached
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    value = digest.hexdigest()[:10]
    _model_hash_cache[path] = value
    return value


def _resolve_lora_path(name):
    """把 LoRA 名字解析为 models/loras 注册表下的完整路径。绝对路径且文件在 →
    原样接受；无 ComfyUI 环境（离线干跑）/ 未注册 / 不在 → None。"""
    if os.path.isabs(name) and os.path.isfile(name):
        return name
    try:
        from folder_paths import get_full_path
    except ImportError:
        return None
    try:
        return get_full_path("loras", name) or None
    except Exception:
        return None


def _lora_hashes_field(lora_entries):
    """LoRA 短哈希 infotext 段：`"name1: hash1, name2: hash2"`（A1111
    Lora hashes 键同款，名字去扩展名）。文件解析不到 / 哈希失败的条目跳过；
    全空返回 ""（调用方省略该段）。"""
    parts = []
    for entry in lora_entries:
        path = _resolve_lora_path(entry["name"])
        prefix = _file_hash_prefix(path) if path else ""
        if not prefix:
            continue
        parts.append(f"{os.path.splitext(entry['name'])[0]}: {prefix}")
    return ", ".join(parts)


def _fth_meta_records():
    """从 sidecar prompt_helper_meta.json 取注入记录，组装 PNG 元数据 JSON 字符串。

    Inject 节点每次执行先于采样 / 输出节点执行，此处读到的即本队次最新记录。
    返回 (正向记录 JSON 或 None, 反向记录 JSON 或 None)；记录 = sidecar 各侧
    记录**原样透传 + plugin 版本号**（编辑器元数据任意未知字段随载荷存活——
    v1.5.0 的 filter:{from,len} 字段即走此路透传，插件不做白名单），与 WebUI
    版 fth_meta 同构。sidecar 缺失 / 该侧未注入 → None（不嵌该 chunk）。"""
    try:
        with open(META_LOG_PATH, "r", encoding="utf-8-sig") as f:
            entry = json.load(f)
    except (OSError, ValueError):
        return None, None

    def _pack(side):
        record = entry.get(side) if isinstance(entry, dict) else None
        if not isinstance(record, dict) or not record:
            return None
        packed = dict(record)
        packed["plugin"] = PLUGIN_VERSION
        try:
            return json.dumps(packed, ensure_ascii=False)
        except (TypeError, ValueError):
            return None

    return _pack("positive"), _pack("negative")


def _build_a1111_parameters(prompt, negative, params, model_name, lora_entries):
    """组装 A1111 格式 parameters 全文（与 WebUI 版生成的 PNG 同构）。

    结构：正向词条行 / Negative prompt 行 / 末行逗号键值对（Steps, Sampler,
    CFG scale, Seed, Size, Model, Lora hashes）+ fth_meta 记录行（由调用方
    追加——编辑器读侧对 parameters 原文做括号配平扫描提取记录，独立行是它
    的现役消费路径；tEXt chunk 另存一份双保险）。params 为 {}（params.json
    缺失 / 损坏）时省略 Steps..Size 段（不编造数值）；model_name 为 None
    省略 Model 段；无可用 LoRA 哈希省略 Lora hashes 段。"""
    lines = [prompt or ""]
    if negative:
        lines.append("Negative prompt: " + negative)
    fields = []
    if params:
        fields.append(f"Steps: {params['steps']}")
        fields.append(f"Sampler: {params['sampler']}")
        fields.append(f"CFG scale: {params['cfg']:g}")
        fields.append(f"Seed: {params['seed']}")
        fields.append(f"Size: {params['width']}x{params['height']}")
    if model_name:
        fields.append(f"Model: {model_name}")
    hashes = _lora_hashes_field(lora_entries)
    if hashes:
        fields.append('Lora hashes: "' + hashes + '"')  # A1111 形态：整体双引号包裹
    if fields:
        lines.append(", ".join(fields))
    return "\n".join(lines)


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


# 编辑器 exe 自动探测：编辑器发版 exe 改名（如 feetaghelper-v2.7.5.exe
# → v2.8.0）会让 config 硬编码的完整路径失效，故按文件名版本号在同目录自动接管。
# 版本号解析为元组比较（v2.7.5 → (2, 7, 5)，兼容 v 前缀与任意多段数字）。
EDITOR_EXE_RE = re.compile(r"^feetaghelper-v(\d+(?:\.\d+)*)\.exe$", re.IGNORECASE)


def _editor_exe_version(filename):
    """从编辑器 exe 文件名解析版本号元组（feetaghelper-v2.7.5.exe → (2, 7, 5)）。
    不符合 feetaghelper-v<数字串>.exe 命名（含无版本号、非 .exe）返回 None。"""
    match = EDITOR_EXE_RE.match(filename)
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def _read_editor_hint():
    """读插件根 editor.hint（v1.4.10 编辑器自荐路径契约）：单行 exe 绝对
    路径，取首行、剥引号与空白。任何读取异常都不外抛；文件缺失 / 空 /
    畸形（剥引号空白后为空或仅由分隔符点号组成——熔断守卫同款语义）/
    指向不存在的文件，一律返回 ""（= 该档无效，解析链跳过继续走扫描
    兜底）。编码兜底 utf-8-sig → gb18030。"""
    try:
        with open(EDITOR_HINT_PATH, "rb") as f:
            raw = f.read(4096)
    except OSError:
        return ""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("gb18030")
        except UnicodeDecodeError:
            return ""
    lines = text.splitlines()
    line = lines[0].strip().strip("\"'").strip() if lines else ""
    if not line or all(ch in "\\/. " for ch in line) or not os.path.isfile(line):
        return ""
    return line


def _resolve_editor_path(configured):
    """解析编辑器 exe 路径（v1.4.10 起五档链）：① configured 存在 → 原样
    返回（config 用户值有效，恒优先）；② editor.hint 有效 → 直接用
    （编辑器自荐路径，不写回 config——编辑器永不改插件 config，单向传值）；
    ③ configured 已失效（编辑器发版 exe 改名）→ 在 configured 所在目录扫描
    feetaghelper-v*.exe，按版本号元组取最新者返回，并把解析结果写回 config
    （下次 UI / 自动启动直接显示新路径）；④ 同目录无任何候选 → 返回原值，
    保持"文件不存在"的原有报错行为。pin 档在调用方 launch_editor 先于本
    函数应用（位次最高）。"""
    path = _normalize_path(configured)
    if path and os.path.isfile(path):
        return path
    # v1.4.10 editor.hint 档：用户未设置 / 已失效时编辑器自荐路径兜住
    # （立即启动 / 自动启动零配置可用）；不写回 config，每次解析照走全链。
    hint = _read_editor_hint()
    if hint:
        return hint
    if not path:
        return path
    try:
        names = os.listdir(os.path.dirname(path))
    except OSError:
        return path
    best_version, best_name = None, ""
    for name in names:
        version = _editor_exe_version(name)
        if version is not None and (best_version is None or version >= best_version):
            best_version, best_name = version, name
    if best_version is None:
        return path
    resolved = os.path.join(os.path.dirname(path), best_name)
    try:  # 解析结果写回 config（读原文件 → 只改 editor_path → 原样写回；
          # v1.4.16 起持 _config_lock + 原子落盘，与其他 config 写入并发亦无半截）
        with _config_lock:
            with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("editor_path") != resolved:
                data["editor_path"] = resolved
                _atomic_write_text(CONFIG_PATH, json.dumps(data, ensure_ascii=False, indent=2))
                _log(f"editor_path 已失效，自动探测到最新版本并写回：{resolved}")
    except (OSError, ValueError):
        pass  # 写回失败不影响本次启动
    return resolved


def launch_editor(editor_path):
    """启动外部词条编辑器（独立进程，关闭 ComfyUI 不会连带关闭它）。返回 (是否成功, 消息)。"""
    editor_path = _resolve_editor_path(editor_path)
    if not editor_path:
        return False, "未设置编辑器路径"
    # v1.4.19 熔断（用户桌面弹「找不到 '\\' 文件」后追加）：空 / 畸形路径
    # （剥引号与空白后为空，或仅由分隔符 / 点号组成，如 \、\\、/、.、..）
    # 一律不送 cmd / start——shell 类调用收到空目标会触发系统级「找不到
    # 文件」弹窗。放在 isfile 判定之前：畸形路径零 shell 交互、零歧义回执。
    stripped = editor_path.strip().strip("\"'").strip()
    if not stripped or all(ch in "\\/. " for ch in stripped):
        return False, "编辑器路径无效：" + editor_path
    if not os.path.isfile(editor_path):
        return False, "文件不存在：" + editor_path

    exe_name = os.path.basename(editor_path)
    if _is_process_running(exe_name):
        return True, f"{exe_name} 已在运行，跳过启动"

    flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
             if os.name == "nt" else 0)
    # v1.4.19（X-174 环境坑ⓐ）：DETACHED_PROCESS / CREATE_NEW_PROCESS_GROUP 只
    # 隔离控制台信号，挡不住显式树杀（外部 stop 按快照父子链递归强杀，如
    # webui.py stop 的 taskkill /F /T——编辑器作为宿主直接子进程被连带杀）。
    # Windows 下两道补强：① 经 cmd /c start 中转——编辑器挂到 cmd 名下、cmd
    # 随即退出，快照父子链断开，树杀不再沿链命中（启动后约 0.1s 起免疫）；
    # ② 附 CREATE_BREAKAWAY_FROM_JOB——宿主被启动器放进 kill-on-close 的
    # Job 对象时子进程脱出 Job（Job 拒绝 breakaway 则 CreateProcess 报错，
    # 裸旗标重试一次）；cmd 中转彻底不可用再回退直启，保底与旧版一致。
    if os.name == "nt":
        breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
        for extra in (breakaway, 0):  # 先带 breakaway；Job 拒绝则裸旗标重试
            try:
                subprocess.Popen(
                    ["cmd", "/c", "start", "", editor_path],
                    cwd=os.path.dirname(editor_path),
                    creationflags=flags | extra, close_fds=True)
                return True, "已启动：" + editor_path
            except OSError:
                continue
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
                "position": (list(POSITIONS), {
                    "tooltip": "v1.4.1 起注入恒定在最前（位置确定化）；此选项仅为兼容旧工作流保留，取值被忽略",
                }),
                "merge_lines": ("BOOLEAN", {"default": True, "tooltip": "把文件内换行合并为一行"}),
            },
            # v1.5.0：hidden PROMPT = 本次执行的完整 API 格式工作流图 →
            # workflow_snapshot.json（编辑器生成前检查 + 原样重提交的数据源）
            "hidden": {"prompt": "PROMPT"},
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

    def inject(self, path, negative_path, base_prompt, negative_base_prompt, position, merge_lines, **hidden):
        # v1.4.1 起注入位置确定化：恒定拼接在最前，position 仅为兼容旧工作流保留、取值被忽略
        # v1.5.0 工作流上报：hidden PROMPT 落盘快照，失败 / 缺席不影响注入（零回归）
        if _write_workflow_snapshot(hidden.get("prompt")):
            _log(f"工作流快照已写入：{WORKFLOW_SNAPSHOT_PATH}")
        prompt_out, tags_out, pos_status = base_prompt or "", "", ""
        pos_record = None
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
                prompt_out = _inject(base_prompt or "", tags)
                tags_out = tags
                pos_status = message
                pos_record = dict(pos_meta) if pos_meta else {}
                pos_record["injected_tags"] = _count_tags(tags)
                pos_record["full_text"] = prompt_out
                if pos_meta is not None:
                    _log(f"正向元数据已剥离并记录（breaks={pos_meta.get('breaks')}）")
                shown = tags[:120] + ("…" if len(tags) > 120 else "")
                _log(f"正向已注入 {pos_record['injected_tags']} 个 tag / {len(tags)} 个字符（{message}）：{shown}")

        neg_out, neg_status = negative_base_prompt or "", "未设置"
        neg_record = None
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
                    neg_out = _inject(negative_base_prompt or "", neg_tags)
                    neg_status = neg_message
                    neg_record = dict(neg_meta) if neg_meta else {}
                    neg_record["injected_tags"] = _count_tags(neg_tags)
                    neg_record["full_text"] = neg_out
                    if neg_meta is not None:
                        _log(f"反向元数据已剥离并记录（breaks={neg_meta.get('breaks')}）")
                    _log(f"反向已注入 {neg_record['injected_tags']} 个 tag / {len(neg_tags)} 个字符（{neg_message}）")

        _record_meta({"positive": pos_record, "negative": neg_record})

        return (prompt_out, neg_out, tags_out, f"正向：{pos_status}；反向：{neg_status}")


class PromptHelperLoraStack:
    """按编辑器 lora_list.json 依次叠加 LoRA（智能适配器：LoRA 编排全在编辑器
    工作台做，节点每次执行现读文件、改清单即生效，工作流零改动）。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("MODEL",),
                "clip": ("CLIP",),
                "lora_file": ("STRING", {
                    "default": LORA_LIST_PATH,
                    "multiline": False,
                    "tooltip": "LoRA 清单 JSON（编辑器工作台写方）：[{\"name\":\"x.safetensors\",\"model\":0.8,\"clip\":0.8},...]；缺失/损坏时直通不叠加",
                }),
            },
        }

    RETURN_TYPES = ("MODEL", "CLIP", "INT")
    RETURN_NAMES = ("model", "clip", "count")
    FUNCTION = "apply_loras"
    CATEGORY = "prompt-helper"
    DESCRIPTION = "读外部 LoRA 清单 JSON 依次叠加（prompt-helper 桥接节点）"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("NaN")  # 文件内容实时变化，绕过缓存每次重读

    def apply_loras(self, model, clip, lora_file):
        entries, err = _read_lora_list(lora_file)
        if err:
            # 安全默认：清单缺失 / 损坏 → 直通 model/clip、count=0（队列不中断）
            _log(f"LoRA 清单读取：{err}（直通 model/clip，count=0）")
        applied = 0
        if entries:
            import comfy.sd
            import comfy.utils
            for entry in entries:
                path = _resolve_lora_path(entry["name"])
                if not path:
                    _log(f"LoRA 未找到，跳过：{entry['name']}")
                    continue
                try:
                    lora = comfy.utils.load_torch_file(path, safe_load=True)
                    model, clip = comfy.sd.load_lora_for_models(
                        model, clip, lora, entry["model"], entry["clip"])
                except Exception as e:  # 单个 LoRA 坏文件不拦整链
                    _log(f"LoRA 加载失败，跳过 {entry['name']}：{e}")
                    continue
                applied += 1
                _log(f"LoRA 已叠加：{entry['name']}（model={entry['model']:g}, clip={entry['clip']:g}）")
        return (model, clip, applied)


class PromptHelperParams:
    """读编辑器 params.json（平铺参数）→ typed 输出（接 KSampler /
    EmptyLatentImage 连线；生成面板改参数 = 改文件，工作流零改动）。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "params_file": ("STRING", {
                    "default": PARAMS_PATH,
                    "multiline": False,
                    "tooltip": "平铺参数 JSON（编辑器生成面板写方）：{seed, steps, cfg, sampler, scheduler, width, height, batch}；缺失/损坏时用默认值",
                }),
            },
        }

    RETURN_TYPES = ("INT", "INT", "FLOAT", "STRING", "STRING", "INT", "INT", "INT")
    RETURN_NAMES = ("seed", "steps", "cfg", "sampler", "scheduler", "width", "height", "batch")
    FUNCTION = "load"
    CATEGORY = "prompt-helper"
    DESCRIPTION = "读外部平铺参数 JSON 输出 typed 生成参数（prompt-helper 桥接节点）"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("NaN")

    def load(self, params_file):
        params, err = _read_params(params_file)
        if err:
            # 安全默认：缺失 / 损坏 → 全默认值（工作流仍可跑）
            _log(f"参数文件读取：{err}（用默认值 {json.dumps(PARAMS_DEFAULTS, ensure_ascii=False)}）")
        else:
            _log(f"参数已加载：seed={params['seed']} steps={params['steps']} cfg={params['cfg']:g} "
                 f"sampler={params['sampler']} scheduler={params['scheduler']} "
                 f"size={params['width']}x{params['height']} batch={params['batch']}")
        return (params["seed"], params["steps"], params["cfg"], params["sampler"],
                params["scheduler"], params["width"], params["height"], params["batch"])


class PromptHelperImageOutput:
    """专用取图 + 元数据嵌入节点（OUTPUT_NODE，执行终点）。

    IMAGE → PIL 落盘 ComfyUI output 目录（前缀 [feetag]_）；PNG tEXt 嵌入
    "parameters"（A1111 格式全文，与 WebUI 版生成的 PNG 同构）与 "fth_meta" /
    "fth_meta_negative"（注入记录 JSON，编辑器图库详情 / 读图还原 / 过滤组识别
    直接解析）；parameters 原文尾部同时按 WebUI 形态附 fth_meta 记录行（编辑器
    读侧的现役提取路径）。元数据素材自动取自 params.json / lora_list.json /
    checkpoint.json / sidecar（均插件目录，各源缺席省略对应段、不影响保存）。
    落盘后追加 image_manifest.json 取图清单（编辑器 GET /view 取图依据）。"""

    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "filename_prefix": ("STRING", {
                    "default": "feetag",
                    "multiline": False,
                    "tooltip": "输出文件名前缀（支持 a/b 子目录语法，同 SaveImage）",
                }),
                "prompt": ("STRING", {
                    "default": "",
                    "multiline": True,
                    "tooltip": "正向提示词全文；接 Inject 节点的 prompt 输出（已含注入词条）",
                }),
                "negative_prompt": ("STRING", {
                    "default": "",
                    "multiline": True,
                    "tooltip": "反向提示词全文；接 Inject 节点的 negative_prompt 输出",
                }),
            },
            "hidden": {"prompt_id": "PROMPT_ID"},  # 写进取图清单，编辑器按队次对账
        }

    RETURN_TYPES = ()
    RETURN_NAMES = ()
    FUNCTION = "save_images"
    CATEGORY = "prompt-helper"
    DESCRIPTION = "保存图像并嵌入 A1111 parameters + fth_meta 元数据（prompt-helper 桥接节点）"

    def save_images(self, image, filename_prefix, prompt, negative_prompt, **hidden):
        from PIL import Image, PngImagePlugin
        # 输出目录 / 命名沿用 ComfyUI SaveImage 同款（含子目录前缀与全局计数器）
        try:
            from folder_paths import get_output_directory, get_save_image_path
        except ImportError:  # 无 ComfyUI 环境（离线干跑兜底，正式环境不会走到）
            output_dir = os.path.join(NODE_DIR, "output")
            os.makedirs(output_dir, exist_ok=True)

            def get_output_directory():
                return output_dir

            def get_save_image_path(prefix, out_dir, width=0, height=0):
                return out_dir, os.path.splitext(prefix.replace("/", "_"))[0], 0, "", prefix

        # 元数据素材（各源缺席省略对应段，日志定位，不影响保存本体）
        params, params_err = _read_params(PARAMS_PATH)
        if params_err:
            _log(f"params.json 读取：{params_err}（parameters 省略 Steps 等参数段）")
        lora_entries, lora_err = _read_lora_list(LORA_LIST_PATH)
        if lora_err:
            _log(f"lora_list.json 读取：{lora_err}（parameters 省略 Lora hashes 段）")
        model_name, ckpt_err = _read_checkpoint_name(CHECKPOINT_PATH)
        if ckpt_err:
            _log(f"checkpoint.json 读取：{ckpt_err}（parameters 省略 Model 段）")
        meta_pos, meta_neg = _fth_meta_records()
        params_text = _build_a1111_parameters(
            prompt, negative_prompt, {} if params_err else params, model_name, lora_entries)
        if meta_pos:
            params_text += "\nfth_meta: " + meta_pos
        if meta_neg:
            params_text += "\nfth_meta_negative: " + meta_neg

        prompt_id = str(hidden.get("prompt_id") or "")
        output_dir = get_output_directory()
        full_folder, filename, counter, subfolder, _prefix = get_save_image_path(
            filename_prefix, output_dir, image.shape[1] if len(image.shape) > 2 else 0,
            image.shape[2] if len(image.shape) > 2 else 0)
        results, manifest_entries = [], []
        for i in range(image.shape[0]):
            array = (255.0 * image[i].cpu().numpy()).clip(0, 255).astype("uint8")
            img = Image.fromarray(array)
            file = f"{filename}_{counter:05}_{i:05}_.png"
            metadata = PngImagePlugin.PngInfo()
            metadata.add_text("parameters", params_text)
            if meta_pos:
                metadata.add_text("fth_meta", meta_pos)
            if meta_neg:
                metadata.add_text("fth_meta_negative", meta_neg)
            img.save(os.path.join(full_folder, file), pnginfo=metadata, compress_level=4)
            results.append({"filename": file, "subfolder": subfolder, "type": "output"})
            manifest_entries.append({"prompt_id": prompt_id, "filename": file,
                                     "subfolder": subfolder, "ts": time.time()})
            _log(f"图像已保存（A1111 parameters + fth_meta 已嵌入）："
                 f"{os.path.join(subfolder, file) if subfolder else file}")
        if manifest_entries:
            _append_image_manifest(manifest_entries)
        return {"ui": {"images": results}, "result": ()}


class PromptHelperCheckpoint:
    """读编辑器 checkpoint.json（{"name": "xxx.safetensors"}）加载基模——
    编辑器以后支持切换基础模型 = 改文件，工作流零改动。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "checkpoint_file": ("STRING", {
                    "default": CHECKPOINT_PATH,
                    "multiline": False,
                    "tooltip": "基模选择 JSON（编辑器模型页写方）：{\"name\":\"xxx.safetensors\"}；缺失/未注册时报错",
                }),
            },
        }

    RETURN_TYPES = ("MODEL", "CLIP", "VAE", "STRING")
    RETURN_NAMES = ("model", "clip", "vae", "name")
    FUNCTION = "load"
    CATEGORY = "prompt-helper"
    DESCRIPTION = "读外部基模选择 JSON 加载 checkpoint（prompt-helper 桥接节点）"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("NaN")

    def load(self, checkpoint_file):
        name, err = _read_checkpoint_name(checkpoint_file)
        if name is None:
            # 不可安全默认：基模缺席静默换模型出图比报错更糟——节点红字清晰报错
            raise RuntimeError(
                f"prompt-helper 基模加载失败：{err}（请在 FeeTagHelper 模型页选择基模生成 checkpoint.json）")
        try:
            from folder_paths import get_full_path, get_folder_paths
        except ImportError as e:
            raise RuntimeError(f"prompt-helper：无 ComfyUI 模型环境（{e}）")
        path = get_full_path("checkpoints", name)
        if not path:
            raise RuntimeError(f"prompt-helper：模型未找到：{name}（models/checkpoints 无此文件）")
        import comfy.sd
        embed_dirs = get_folder_paths("embeddings")
        out = comfy.sd.load_checkpoint_guess_config(
            path, output_vae=True, output_clip=True,
            embedding_directory=embed_dirs[0] if embed_dirs else None)
        _log(f"基模已加载：{name}")
        # name 输出 = 去 extension 的模型名（与 parameters 的 Model 字段同口径）
        return (out[0], out[1], out[2], os.path.splitext(os.path.basename(name))[0])


class PromptHelperFilter:
    """读编辑器过滤页实时输出 filter_output.txt → 保留词条 + 统计。
    过滤设置全在编辑器过滤页做，节点只消费结果（参数面板只读展示）。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "filter_file": ("STRING", {
                    "default": FILTER_OUTPUT_PATH,
                    "multiline": False,
                    "tooltip": "过滤输出 txt（编辑器过滤页写方）：逗号拼接保留词条，或含统计的 JSON 信封；缺失时 status=未配置",
                }),
            },
        }

    RETURN_TYPES = ("STRING", "INT", "INT", "STRING")
    RETURN_NAMES = ("kept_tags", "removed_count", "total", "status")
    FUNCTION = "read"
    CATEGORY = "prompt-helper"
    DESCRIPTION = "读外部过滤结果输出保留词条（prompt-helper 桥接节点）"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("NaN")

    def read(self, filter_file):
        path = _normalize_path(filter_file)
        text, message = read_tag_file(path) if path else (None, "未设置文件路径")
        if text is None:
            # 安全默认：未配置 = 空词条 + status 明示（不报错不拦队列）
            _log(f"过滤结果读取：{message}（status=未配置）")
            return ("", 0, 0, "未配置")
        kept, removed, total, rules = _parse_filter_output(text)
        status = f"活跃 {rules} 条规则" if rules is not None else f"保留 {total} 条词条"
        _log(f"过滤结果：{status}（removed={removed}）")
        return (kept, removed, total, status)


NODE_CLASS_MAPPINGS = {
    "PromptHelperInject": PromptHelperInject,
    "PromptHelperLoraStack": PromptHelperLoraStack,
    "PromptHelperParams": PromptHelperParams,
    "PromptHelperImageOutput": PromptHelperImageOutput,
    "PromptHelperCheckpoint": PromptHelperCheckpoint,
    "PromptHelperFilter": PromptHelperFilter,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "PromptHelperInject": "外部提示词注入 (prompt-helper)",
    "PromptHelperLoraStack": "LoRA 清单叠加 (prompt-helper)",
    "PromptHelperParams": "生成参数 (prompt-helper)",
    "PromptHelperImageOutput": "保存图像·元数据嵌入 (prompt-helper)",
    "PromptHelperCheckpoint": "基模加载 (prompt-helper)",
    "PromptHelperFilter": "过滤结果 (prompt-helper)",
}
