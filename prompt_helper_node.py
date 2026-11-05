# -*- coding: utf-8 -*-
"""comfyui-prompt-helper v2.1.0（线性灰度噪声蒙版·2026-11-05）：路径直读双节点 +
线性噪声蒙版 + 编辑器联动拉起。

v2.1.0（2026-11-05 用户令「我要的就是可以直接按照灰度线性对应噪声强度」）新增
PromptHelperLinearNoiseMask：时间门控每像素线性有效 denoise——原生 SetLatentNoiseMask
灰度响应非线性（软阈值拐点~0.75，0.5 灰实测仅轻微效果），本节点把灰度 m 映射为
「加入点=步数进度 (1-m^CAL) 处」的时间门控（CAL=效果线性定标指数，E2E 实测）：
黑=全程冻结零变化 / 白=全重绘 / 0.5 灰≈50% 重绘效果。机理与选型理由（含 σ 线性/
步数线性/效果定标三档 E2E 数据）见 PromptHelperLinearNoiseMask 类注。

v2.0.0 起六个契约节点（Inject / LoraStack / Params / Checkpoint / ImageInput /
ImageOutput / Filter）整体退役——FeeTagHelper v2.24.0（架构 v2）起改用**图构建器**：
编辑器按快照直接构建完整 API 格式工作流 JSON（原生节点直搭）提交 /prompt，动态参数
（seed/steps/文本/lora/底模/输入图）全部以字面量直进图，params.json / lora_list.json /
image.json / checkpoint.json / prompt.txt 双件 / filter_output.txt / workflow_
snapshot.json / image_manifest.json 等契约文件机制连根退役。本插件职能：

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

PLUGIN_VERSION = "2.1.0"

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
# v2.1.0 线性灰度噪声蒙版（σ 门控每像素线性有效 denoise）
# ---------------------------------------------------------------------------

# 线性门控标记键（LATENT dict 附带键）：本节点写入，nodes.common_ksampler 桥接补丁
# 消费后剥离——不随输出 latent 下游泄漏（链式 KSampler 不会被误触发二次门控）
PH_LINEAR_LATENT_KEY = "ph_linear_noise_gate"


# 效果线性定标指数（件四 E2E 实测，2026-11-05，tmp/linear-proof/）：SD 系模型
# denoise→像素变化响应为凸函数——纯步数刻度（有效 denoise=灰度 g）实测归一效果
# 0.25/0.5/0.75 档=0.16/0.32/0.65（0.5 档欠 0.5±0.15 带宽）。有效 denoise 取
# g^CAL（CAL=0.7，四锚点拟合：预测 0.25/0.5/0.75 档效果≈0.24/0.50/0.73）把响应
# 等化回灰度线性。端点恒定：g=0 恒冻结 / g=1 首步即加入（不受指数影响）。
# 定标链：waiIllustriousSDXL_v160 + euler/normal 28 步 + CFG 6.5 + denoise 1.0。
_LINEAR_CAL = 0.7


def _linear_noise_gate(sigma, denoise_mask, extra_options=None):
    """denoise_mask_function 钩子本体（v2.1.0 核心纯函数，逐调用无状态）：

    把灰度蒙版 m（值=重绘强度 0~1）按采样进度门控为 {0,1} 参与掩码——
      有效 denoise = m^_LINEAR_CAL（效果线性定标，见常量注）；加入点=步数进度
      (1-有效 denoise) 处（σ_m = sigmas[round((1-m^CAL)·N)]，N=本轮模型调用数）：
      当前进度 i/N ≥ 1-m^CAL（即 m ≥ (1-i/N)^(1/CAL)）→ gate=1 正常参与去噪；
      此前 gate=0 全冻结。黑 m=0 恒冻结（零变化）/白 m=1 首步即加入（全重绘）/
      0.5 灰≈50% 重绘效果（效果空间线性——件四 E2E 实测落 0.5±0.15 带宽）。

    刻度选型（件四 E2E 实测定标，2026-11-05）：①σ 空间线性（σ_m=m·σ_max，任务书
    初稿机理）实测 0.5 档效果≈0.96——SD 系采样表在高 σ 段极陡，σ 降到 0.5·σ_max
    时步数进度已 ~86%，效果近饱和；②纯步数线性（有效 denoise=灰度）实测 0.5 档
    效果=0.32——模型 denoise→变化响应凸性；③本实现=②加定标指数（_LINEAR_CAL），
    效果空间线性成立。

    当前进度 i 由 sigmas（extra_options["sigmas"]——KSamplerX0Inpaint 透传的完整
    采样表，见 comfy/samplers.py 的 denoise_mask_function 调用点）计数得出；sigmas
    缺席时退化为 σ 比阈（thr=1，只有纯白参与——防御形态，正常链路不会走到）。任何
    取值异常按原掩码直通（不破坏采样）。"""
    try:
        cur = float(sigma.max())
    except (TypeError, ValueError, AttributeError, RuntimeError):
        return denoise_mask
    try:
        m = denoise_mask.clamp(0.0, 1.0)
    except (AttributeError, RuntimeError):
        return denoise_mask
    sigmas = extra_options.get("sigmas") if isinstance(extra_options, dict) else None
    try:
        if sigmas is not None and len(sigmas) >= 2:
            # 降序采样表：i=严格大于当前 σ 的条目数=当前调用序号（0 基）；
            # N=模型调用数（表长-1，末位是终态 0）。容差比较吸收 s_churn 的 sigma_hat 微差。
            import torch as _torch
            i = int((sigmas > _torch.tensor(cur, dtype=sigmas.dtype, device=sigmas.device) * (1 + 1e-6)).sum().item())
            n = len(sigmas) - 1
            thr = max(1.0 - float(i) / float(n), 0.0)
            thr = thr ** (1.0 / _LINEAR_CAL)          # m >= thr ⟺ 进度 i/n >= 1-m^CAL
        else:
            sigma_max = float(sigmas.max()) if sigmas is not None else cur
            thr = cur / sigma_max if sigma_max > 0 else 1.0
    except (TypeError, ValueError, AttributeError, RuntimeError):
        return denoise_mask
    try:
        return (m >= thr).to(denoise_mask.dtype)
    except (AttributeError, RuntimeError):
        return denoise_mask


def _install_linear_gate_bridge():
    """装载 nodes.common_ksampler 桥接补丁（模块装载时调用，幂等）。

    为什么需要补丁：sampler_post_cfg_function / denoise_mask_function 都登记在
    ModelPatcher.model_options（MODEL 链）上，而本节点输入面只有 LATENT+MASK
    （与原生 SetLatentNoiseMask 同面——fth 图构建器零额外接线即可替换）。唯一从
    LATENT dict 通到采样器的公共通道是 latent["noise_mask"]，其每步门控钩子
    （comfy/samplers.py KSamplerX0Inpaint.__call__）从 model_options 读
    denoise_mask_function——故在本模块装载时补 common_ksampler（KSampler /
    KSamplerAdvanced 两节点的公共采样入口，节点方法按模块全局名解析、补丁即生效）：
    见 PH_LINEAR_LATENT_KEY 标记的 latent → 把 _linear_noise_gate 登记进
    model.model_options["denoise_mask_function"]（try/finally 原值恢复——不动共享
    MODEL 的持久状态），标记键剥离防下游泄漏。无标记的 latent 原样直通（零开销）。

    SamplerCustom 路径不经 common_ksampler：标记不消费，蒙版按原生
    SetLatentNoiseMask 语义生效（优雅降级，fth 图构建器走 KSampler 不受影响）。"""
    try:
        import nodes as _nodes
    except ImportError:
        return False  # 离线测试环境（无 ComfyUI 壳）——节点逻辑仍可单测
    orig = getattr(_nodes, "common_ksampler", None)
    if orig is None or getattr(orig, "_ph_linear_gate_bridge", False):
        return True  # seam 缺席（ComfyUI 版本变动）/ 已装（模块重载幂等）

    def common_ksampler(model, seed, steps, cfg, sampler_name, scheduler, positive, negative, latent,
                        denoise=1.0, disable_noise=False, start_step=None, last_step=None,
                        force_full_denoise=False, _ph_orig=orig):
        if isinstance(latent, dict) and latent.get(PH_LINEAR_LATENT_KEY) and "noise_mask" in latent:
            clean = {k: v for k, v in latent.items() if k != PH_LINEAR_LATENT_KEY}
            mo = getattr(model, "model_options", None)
            if isinstance(mo, dict):
                prev = mo.get("denoise_mask_function")
                mo["denoise_mask_function"] = _linear_noise_gate
                try:
                    return _ph_orig(model, seed, steps, cfg, sampler_name, scheduler, positive, negative, clean,
                                    denoise=denoise, disable_noise=disable_noise, start_step=start_step,
                                    last_step=last_step, force_full_denoise=force_full_denoise)
                finally:
                    if prev is None:
                        mo.pop("denoise_mask_function", None)
                    else:
                        mo["denoise_mask_function"] = prev
        return _ph_orig(model, seed, steps, cfg, sampler_name, scheduler, positive, negative, latent,
                        denoise=denoise, disable_noise=disable_noise, start_step=start_step,
                        last_step=last_step, force_full_denoise=force_full_denoise)

    common_ksampler._ph_linear_gate_bridge = True
    _nodes.common_ksampler = common_ksampler
    return True


class PromptHelperLinearNoiseMask:
    """线性灰度噪声蒙版（v2.1.0）：samples(LATENT)+mask(MASK) → LATENT。

    需求（2026-11-05 用户令）：灰度线性对应噪声强度——0.5 灰=恰 50% 重绘效果。
    原生 SetLatentNoiseMask 灰度响应非线性（软阈值拐点~0.75，0.5 灰实测仅轻微
    效果）：其每步按 m 连续混合 x 与再噪化 latent，采样动力学把幅值混合整流成
    阈值响应。本节点改为**时间门控**：灰度 m 的有效 denoise=m^_LINEAR_CAL（效果
    线性定标指数，件四 E2E 实测——SD 系 denoise→像素变化响应为凸函数，恒等刻度
    0.5 档仅 0.32 归一效果，定标后 0.5 档≈0.5）——加入点=步数进度 (1-m^CAL) 处
    （σ_m=sigmas[round((1-m^CAL)·N)]），此前该像素完全冻结在原 latent（输入侧
    x=按当前 σ 再噪化的原 latent、输出侧 x0=原 latent——与 mask=0 的原生冻结路径
    逐位一致），到点后整像素参与去噪。加入时刻像素状态恰为「从 σ_m 起步的
    img2img」。黑/白端点恒定：m=0 全程冻结零变化 / m=1 首步加入全重绘。

    钩子选型（任务书勘明 sampler_post_cfg_function，实现改挂 denoise_mask_function
    的理由）：①冻结必须同时作用于**输入 x 与输出 x0**两侧——KSamplerX0Inpaint 的
    原生冻结链（comfy/samplers.py 634-643：x*scale_latent_inpaint 回拉 + 输出钉
    latent_image）两侧齐备且按模型类型（EPS/V/EDM/flow）各自正确的 noise_scaling
    再噪化；post-CFG 只能改 denoised 估计，冻结像素的模型输入 x 在 SDE/多步采样器
    下会累积未跟踪的新噪声（fresh noise 无人去除→终点漂移）；②门控即把 denoise_mask
    按步二值化（denoise_mask_function 正是官方给的时间相关掩码钩子），加入像素
    gate=1 时行为与无蒙版全重绘逐位一致，零幅值混合=零软阈值伪影；③与原生
    SetLatentNoiseMask 叠加语义：上游区域 noise_mask 在场时先收（逐像素相乘，
    区域外 m=0 恒冻结），线性强度后调。注册路径说明见 _install_linear_gate_bridge。

    局限（如实）：批量 batch>1 且两掩码 batch 维均 >1 且不等时不广播（报错可见）；
    SamplerCustom 链按原生语义降级（见 _install_linear_gate_bridge 注）。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "samples": ("LATENT", {"tooltip": "输入 latent（VAEEncode 产物；上游带 SetLatentNoiseMask 区域蒙版时先收区域、线性强度后调）"}),
                "mask": ("MASK", {"tooltip": "灰度蒙版：值=线性重绘强度=有效 denoise（白 1=全重绘 / 黑 0=零变化 / 0.5=后半程加入=50% 重绘效果）——替代 SetLatentNoiseMask 的非线性灰度响应"}),
            }
        }

    RETURN_TYPES = ("LATENT",)
    OUTPUT_TOOLTIPS = ("带线性灰度噪声蒙版的 latent（接 KSampler——黑位逐位零变化）。",)
    FUNCTION = "apply_linear"
    CATEGORY = "model/latent"
    DESCRIPTION = ("线性灰度噪声蒙版（替代 SetLatentNoiseMask 的线性版）：时间门控每像素线性有效 denoise——"
                   "灰度 m 的加入点=步数进度 (1-m) 处（与 img2img denoise 同刻度），到点前该像素完全冻结原 latent、之后正常去噪。"
                   "黑=零变化/白=全重绘/0.5 灰≈50% 重绘效果。需接 KSampler/KSamplerAdvanced（SamplerCustom 链按原生语义降级）。")
    SEARCH_ALIASES = ["linear noise mask", "线性 重绘 强度", "fth linear mask"]

    def apply_linear(self, samples, mask):
        s = samples.copy()
        m = mask.reshape((-1, 1, mask.shape[-2], mask.shape[-1])).float()
        region = s.get("noise_mask")
        if region is not None:
            # 区域 mask 先收（上游 SetLatentNoiseMask/VAEEncode(inpaint) 在场）：
            # 逐像素相乘——区域 0 处 m=0 恒冻结；空间尺寸不一致时区域蒙版双线性
            # 缩到本蒙版尺寸（batch 维靠广播）
            if tuple(region.shape[-2:]) != tuple(m.shape[-2:]):
                import torch.nn.functional as F
                region = F.interpolate(region, size=m.shape[-2:], mode="bilinear", align_corners=False)
            m = m * region
        s["noise_mask"] = m                      # 触发 KSamplerX0Inpaint 每步冻结链（值=m）
        s[PH_LINEAR_LATENT_KEY] = True           # common_ksampler 桥接标记（消费即剥离）
        return (s,)


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
    "PromptHelperLinearNoiseMask": PromptHelperLinearNoiseMask,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "PathLoraLoader": "LoRA 路径直读 (prompt-helper)",
    "PathCheckpointLoader": "底模路径直读 (prompt-helper)",
    "PromptHelperLinearNoiseMask": "线性灰度噪声蒙版 (prompt-helper)",
}

# v2.1.0 线性噪声蒙版桥接补丁（幂等——见 _install_linear_gate_bridge 注）
_install_linear_gate_bridge()
