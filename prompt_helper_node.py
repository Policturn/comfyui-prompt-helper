# -*- coding: utf-8 -*-
"""comfyui-prompt-helper v2.0.0（架构 v2·2026-11-04）：路径直读双节点 + 编辑器联动拉起。

v2.0.0 起六个契约节点（Inject / LoraStack / Params / Checkpoint / ImageInput /
ImageOutput / Filter）整体退役——FeeTagHelper v2.24.0（架构 v2）起改用**图构建器**：
编辑器按快照直接构建完整 API 格式工作流 JSON（原生节点直搭）提交 /prompt，动态参数
（seed/steps/文本/lora/底模/输入图）全部以字面量直进图，params.json / lora_list.json /
image.json / checkpoint.json / prompt.txt 双件 / filter_output.txt / workflow_
snapshot.json / image_manifest.json 等契约文件机制连根退役。本插件只剩两件职能：

① **路径直读双节点**（本插件存在的硬需求——2026-11-04 实测原生 LoraLoader 的
   lora_name 是 COMBO 校验，喂绝对路径 400 value_not_in_list 拒收）：
   - PathLoraLoader：{path, strength_model, strength_clip, model, clip} → 直读
     任意目录的 LoRA 文件（跨目录模型库直载——fth 是模型位置的唯一事实源，
     不依赖 extra_model_paths.yaml 桥 / folder_paths 注册名解析）；
   - PathCheckpointLoader：{path} → 直读任意目录的底模文件。
   编辑器图构建期三层解析（注册名>路径>搜索）已上收到 fth 侧：库内可解析路径 →
   本双节点；未解析到路径（ComfyUI 自带模型/手写名）→ 原生 LoraLoader /
   CheckpointLoaderSimple 注册名自解析。手搭工作流亦可直接用本双节点。

② **编辑器自动拉起**（autostart_editor，v1.x 全量保留）：ComfyUI 启动加载本节点包
   时按 config 自动拉起外部词条编辑器（防重复启动 / 版本自愈 / editor.hint 自荐
   路径契约），编辑器作为独立进程运行，关闭 ComfyUI 不会连带关闭它。

workflows/ 目录下旧模板 JSON 保留（标注 deprecated）供网页画布手动参考——执行链
不再读它。离线自测见 test_prompt_helper.py。
"""

import json
import os
import re
import subprocess
import threading
import time

PLUGIN_VERSION = "2.0.0"

NODE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(NODE_DIR, "config.json")
EDITOR_HINT_PATH = os.path.join(NODE_DIR, "editor.hint")

# config 族文件写锁（v1.4.8 镜像，与 _atomic_write_text 配套）：串行化
# _resolve_editor_path 写回的并发（WebUI 侧还串行 _save_config / settings.pin，
# 本仓保持共享块逐字一致故一并定义）
_config_lock = threading.Lock()

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


# ---------------------------------------------------------------------------
# v2.0.0 路径直读双节点
# ---------------------------------------------------------------------------

# 模型文件扩展名白名单（与 fth 图构建器/模型库口径一致：LoRA 四类 / 底模四类）
LORA_EXTS = (".safetensors", ".ckpt", ".pt", ".bin")
CKPT_EXTS = (".safetensors", ".ckpt", ".pt", ".bin")


def _resolve_model_path(raw, exts, label):
    """路径直读节点共通路径解析（纯函数，离线可测）：剥引号空白 + expandvars/
    expanduser；相对路径以插件目录为基准解析（手搭工作流便利位——fth 主链恒送
    绝对路径）。空 / 白名单外扩展名 / 文件不存在 → 明确 ValueError（节点执行期
    可见报错，不静默跳过——静默不生效是 v1.x LoraStack 的历史病灶，本版根除）。"""
    path = _normalize_path(raw)
    if not path:
        raise ValueError(f"{label}路径为空——请填模型文件完整路径（fth 生成轮由模型库自动解析）")
    if not os.path.isabs(path):
        path = os.path.join(NODE_DIR, path)
    path = os.path.normpath(path)
    if not path.lower().endswith(exts):
        raise ValueError(f"{label}文件类型不支持（{'/'.join(e.lstrip('.') for e in exts)}）：{path}")
    if not os.path.isfile(path):
        raise ValueError(f"{label}文件不存在：{path}")
    return path


class PathLoraLoader:
    """按绝对路径直读 LoRA（v2.0.0 硬需求节点）：与原生 LoraLoader 同输入输出面
    （model/clip 串链、strength 双权重），仅把 lora_name 换成 path（STRING——无
    COMBO 校验，任意目录可载）。加载链与原生 LoraLoader.load_lora 逐字同构
    （comfy.utils.load_torch_file + comfy.sd.load_lora_for_models，含单实例
    lora 缓存与 0/0 短路），fth 图构建器对库内命中的每个 lora 生成一个本节点串链。"""

    def __init__(self):
        self.loaded_lora = None

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("MODEL", {"tooltip": "要叠加 LoRA 的扩散模型（链式输入：接上一个 LoRA 节点或底模加载器）"}),
                "clip": ("CLIP", {"tooltip": "要叠加 LoRA 的 CLIP 模型"}),
                "path": ("STRING", {"default": "", "tooltip": "LoRA 文件完整路径（任意目录可载——不经 ComfyUI 注册名解析）"}),
                "strength_model": ("FLOAT", {"default": 1.0, "min": -100.0, "max": 100.0, "step": 0.01, "tooltip": "扩散模型侧强度（可为负）"}),
                "strength_clip": ("FLOAT", {"default": 1.0, "min": -100.0, "max": 100.0, "step": 0.01, "tooltip": "CLIP 侧强度（可为负）"}),
            }
        }

    RETURN_TYPES = ("MODEL", "CLIP")
    OUTPUT_TOOLTIPS = ("叠加后的扩散模型。", "叠加后的 CLIP 模型。")
    FUNCTION = "load_lora"
    CATEGORY = "model/loaders"
    DESCRIPTION = "按完整路径直读 LoRA 文件（跨目录模型库直载——FeeTagHelper 图构建器 v2 的配套节点；原生 LoraLoader 只收注册名）"
    SEARCH_ALIASES = ["lora path", "路径 lora", "fth lora"]

    def load_lora(self, model, clip, path, strength_model, strength_clip):
        if strength_model == 0 and strength_clip == 0:
            return (model, clip)
        lora_path = _resolve_model_path(path, LORA_EXTS, "LoRA")
        lora = None
        lora_metadata = None
        if self.loaded_lora is not None:
            if self.loaded_lora[0] == lora_path:
                lora = self.loaded_lora[1]
                lora_metadata = self.loaded_lora[2] if len(self.loaded_lora) > 2 else None
            else:
                self.loaded_lora = None
        if lora is None:
            import comfy.utils
            lora, lora_metadata = comfy.utils.load_torch_file(lora_path, safe_load=True, return_metadata=True)
            self.loaded_lora = (lora_path, lora, lora_metadata)
        import comfy.sd
        model_lora, clip_lora = comfy.sd.load_lora_for_models(
            model, clip, lora, strength_model, strength_clip, lora_metadata=lora_metadata)
        return (model_lora, clip_lora)


class PathCheckpointLoader:
    """按绝对路径直读底模（v2.0.0 可选配套节点）：与原生 CheckpointLoaderSimple
    同输出面（MODEL/CLIP/VAE），ckpt_name 换 path。加载链与原生逐字同构
    （comfy.sd.load_checkpoint_guess_config，embedding 目录取注册目录）。fth
    图构建器对基模下拉选中的库条目（跨目录绝对路径）用本节点，缺省兜底走原生
    CheckpointLoaderSimple 注册名。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "path": ("STRING", {"default": "", "tooltip": "底模文件完整路径（任意目录可载——不经 ComfyUI 注册名解析）"}),
            }
        }

    RETURN_TYPES = ("MODEL", "CLIP", "VAE")
    OUTPUT_TOOLTIPS = ("去噪用的扩散模型。", "编码提示词的 CLIP 模型。", "图与 latent 互转的 VAE 模型。")
    FUNCTION = "load_checkpoint"
    CATEGORY = "model/loaders"
    DESCRIPTION = "按完整路径直读底模文件（跨目录模型库直载——FeeTagHelper 图构建器 v2 的配套节点；原生 CheckpointLoaderSimple 只收注册名）"
    SEARCH_ALIASES = ["checkpoint path", "路径底模", "fth checkpoint"]

    def load_checkpoint(self, path):
        ckpt_path = _resolve_model_path(path, CKPT_EXTS, "底模")
        import comfy.sd
        from folder_paths import get_folder_paths
        out = comfy.sd.load_checkpoint_guess_config(
            ckpt_path, output_vae=True, output_clip=True,
            embedding_directory=get_folder_paths("embeddings"))
        return out[:3]


# ---------------------------------------------------------------------------
# 编辑器联动拉起（v1.x 全量保留——v2.0.0 唯一不变职能）
# ---------------------------------------------------------------------------

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
        return False  # 检测失败宁可重复启动，也不要让编辑器永远起不来


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


NODE_CLASS_MAPPINGS = {
    "PathLoraLoader": PathLoraLoader,
    "PathCheckpointLoader": PathCheckpointLoader,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "PathLoraLoader": "LoRA 路径直读 (prompt-helper)",
    "PathCheckpointLoader": "底模路径直读 (prompt-helper)",
}
