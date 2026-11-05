# -*- coding: utf-8 -*-
"""离线自测（v2.1.0）：不启动 ComfyUI，直接加载节点模块验证逻辑。

覆盖面：路径直读双节点（_resolve_model_path 纯函数面 + comfy 桩注入的加载链）、
线性灰度噪声蒙版（v2.1.0：_linear_noise_gate σ 门控纯函数 + 节点输入输出面 +
common_ksampler 桥接补丁）与编辑器联动拉起族（v1.x 保留面）。v1.x 的六契约节点/
契约文件读侧/模板结构断言随节点删除整体退役（历史用例见 git）。

用法：python test_prompt_helper.py（真 torch 用 ComfyUI 环境 python 跑——门控用例
依赖张量语义；无 torch 环境跑则门控/节点段按缺库跳过计 PASS-SKIP）
"""

import importlib.util
import json
import os
import sys
import tempfile
import threading
import types

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE_PATH = os.path.join(HERE, "prompt_helper_node.py")

spec = importlib.util.spec_from_file_location("prompt_helper_node", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
sys.modules["prompt_helper_node"] = mod
spec.loader.exec_module(mod)

FAILED = []


def check(label, cond):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        FAILED.append(label)


def expect_raises(label, fn, exc=ValueError):
    try:
        fn()
    except exc:
        check(label, True)
    except Exception as e:  # noqa: BLE001
        check(f"{label}（抛错类型不符：{type(e).__name__}）", False)
    else:
        check(f"{label}（未抛错）", False)


TMP_DIR = tempfile.mkdtemp(prefix="fth_plugin_v2_test_")


def make_model_file(name, ext=".safetensors"):
    """临时模型文件（白名单内扩展名）——用后由收尾统一清目录。"""
    fd, path = tempfile.mkstemp(suffix=ext, prefix=name + "-", dir=TMP_DIR)
    with os.fdopen(fd, "wb") as f:
        f.write(b"\x00" * 16)
    return path


def fake_comfy_modules(torch_calls, lora_calls, ckpt_calls):
    """注入桩 comfy.* / folder_paths 模块（离线无 ComfyUI 环境）——记录调用与
    固定回值。回值用哨兵对象验证透传。"""
    sentinel_model_out = object()
    sentinel_clip_out = object()
    sentinel_ckpt_tuple = (object(), object(), object(), object())

    comfy_pkg = types.ModuleType("comfy")
    utils_pkg = types.ModuleType("comfy.utils")
    sd_pkg = types.ModuleType("comfy.sd")

    def _load_torch_file(path, safe_load=True, return_metadata=False):
        torch_calls.append(path)
        return ({"stub-lora": True}, {"stub-meta": 1}) if return_metadata else {"stub-lora": True}

    def _load_lora_for_models(model, clip, lora, sm, sc, lora_metadata=None):
        lora_calls.append({"lora": lora, "sm": sm, "sc": sc, "meta": lora_metadata})
        return sentinel_model_out, sentinel_clip_out

    def _load_checkpoint_guess_config(path, output_vae=True, output_clip=True, embedding_directory=None):
        ckpt_calls.append({"path": path, "vae": output_vae, "clip": output_clip, "emb": embedding_directory})
        return sentinel_ckpt_tuple

    utils_pkg.load_torch_file = _load_torch_file
    sd_pkg.load_lora_for_models = _load_lora_for_models
    sd_pkg.load_checkpoint_guess_config = _load_checkpoint_guess_config
    comfy_pkg.utils = utils_pkg
    comfy_pkg.sd = sd_pkg

    folder_paths_pkg = types.ModuleType("folder_paths")
    folder_paths_pkg.get_folder_paths = lambda kind: ([r"C:\emb"] if kind == "embeddings" else [])

    sys.modules["comfy"] = comfy_pkg
    sys.modules["comfy.utils"] = utils_pkg
    sys.modules["comfy.sd"] = sd_pkg
    sys.modules["folder_paths"] = folder_paths_pkg
    return sentinel_model_out, sentinel_clip_out, sentinel_ckpt_tuple


# == 版本与注册 ==

print("== 版本与注册 ==")
check("PLUGIN_VERSION = 2.1.0", mod.PLUGIN_VERSION == "2.1.0")
check("注册面恰为三节点（路径双节点+线性噪声蒙版）",
      set(mod.NODE_CLASS_MAPPINGS) == {"PathLoraLoader", "PathCheckpointLoader", "PromptHelperLinearNoiseMask"})
check("显示名已配置", set(mod.NODE_DISPLAY_NAME_MAPPINGS) == set(mod.NODE_CLASS_MAPPINGS))

# == _resolve_model_path（纯函数面） ==

print("== 路径解析（_resolve_model_path） ==")
lora_file = make_model_file("testlora")
check("绝对路径原样（normpath 后）",
      mod._resolve_model_path(lora_file, mod.LORA_EXTS, "LoRA") == os.path.normpath(lora_file))
check("引号包裹剥除",
      mod._resolve_model_path('"' + lora_file + '"', mod.LORA_EXTS, "LoRA") == os.path.normpath(lora_file))
expect_raises("空路径 ValueError",
              lambda: mod._resolve_model_path("", mod.LORA_EXTS, "LoRA"))
expect_raises("纯空白路径 ValueError",
              lambda: mod._resolve_model_path("   ", mod.LORA_EXTS, "LoRA"))
expect_raises("白名单外扩展名 ValueError（.gif）",
              lambda: mod._resolve_model_path(lora_file + ".gif", mod.LORA_EXTS, "LoRA"))
expect_raises("无扩展名 ValueError",
              lambda: mod._resolve_model_path(os.path.join(TMP_DIR, "noext"), mod.LORA_EXTS, "LoRA"))
expect_raises("文件不存在 ValueError（可见报错不静默跳过）",
              lambda: mod._resolve_model_path(os.path.join(TMP_DIR, "ghost.safetensors"), mod.LORA_EXTS, "LoRA"))
old_cwd = os.getcwd()
os.chdir(tempfile.gettempdir())  # 刻意把 cwd 挪到别处——相对路径必须锚在 NODE_DIR 而非 cwd
try:
    try:
        mod._resolve_model_path("ghost-anchor.safetensors", mod.LORA_EXTS, "LoRA")
        check("相对路径锚定（未抛错？）", False)
    except ValueError as e:
        check("相对路径锚定插件目录（报错路径含 NODE_DIR 前缀，不受 cwd 影响）",
              os.path.join(HERE, "ghost-anchor.safetensors") in str(e))
finally:
    os.chdir(old_cwd)

# == PathLoraLoader（comfy 桩注入） ==

print("== PathLoraLoader ==")
inputs = mod.PathLoraLoader.INPUT_TYPES()["required"]
check("输入面 = model/clip/path/strength_model/strength_clip（与原生 LoraLoader 同构+path）",
      set(inputs) == {"model", "clip", "path", "strength_model", "strength_clip"})
check("path 为 STRING（无 COMBO 校验——绝对路径可载）", inputs["path"][0] == "STRING")
check("输出 = (MODEL, CLIP)", mod.PathLoraLoader.RETURN_TYPES == ("MODEL", "CLIP"))

torch_calls, lora_calls, ckpt_calls = [], [], []
m_out, c_out, ck_out = fake_comfy_modules(torch_calls, lora_calls, ckpt_calls)
node = mod.PathLoraLoader()
m_in, c_in = object(), object()
r = node.load_lora(m_in, c_in, lora_file, 0.8, 0.7)
check("加载链回值透传（model/clip 哨兵）", r == (m_out, c_out))
check("load_torch_file 收解析后路径", torch_calls == [os.path.normpath(lora_file)])
check("强度与元数据透传", lora_calls and lora_calls[0]["sm"] == 0.8 and lora_calls[0]["sc"] == 0.7
      and lora_calls[0]["meta"] == {"stub-meta": 1})
node.load_lora(m_in, c_in, lora_file, 0.9, 0.9)
check("同路径单实例缓存（torch 只读一次）", len(torch_calls) == 1 and lora_calls[1]["sm"] == 0.9)
r2 = node.load_lora(m_in, c_in, lora_file, 0, 0)
check("0/0 强度短路（原模型直通、不触发加载）", r2 == (m_in, c_in) and len(torch_calls) == 1)
expect_raises("坏路径执行期可见报错（ValueError 语义）",
              lambda: node.load_lora(m_in, c_in, os.path.join(TMP_DIR, "ghost.safetensors"), 1, 1))

# == PathCheckpointLoader（comfy 桩注入） ==

print("== PathCheckpointLoader ==")
inputs = mod.PathCheckpointLoader.INPUT_TYPES()["required"]
check("输入面 = path 单键", set(inputs) == {"path"} and inputs["path"][0] == "STRING")
check("输出 = (MODEL, CLIP, VAE)", mod.PathCheckpointLoader.RETURN_TYPES == ("MODEL", "CLIP", "VAE"))

ckpt_file = make_model_file("testckpt", ".ckpt")
node2 = mod.PathCheckpointLoader()
r3 = node2.load_checkpoint(ckpt_file)
check("回值=加载元组前三（out[:3] 语义——哨兵元组）",
      r3 == tuple(ck_out[:3]) and len(r3) == 3)
check("load_checkpoint_guess_config 收完整路径 + vae/clip 全开 + embeddings 注册目录",
      ckpt_calls and ckpt_calls[0]["path"] == os.path.normpath(ckpt_file)
      and ckpt_calls[0]["vae"] and ckpt_calls[0]["clip"] and ckpt_calls[0]["emb"] == [r"C:\emb"])
expect_raises("底模坏路径可见报错",
              lambda: node2.load_checkpoint(os.path.join(TMP_DIR, "ghost.safetensors")))

# == 线性灰度噪声蒙版（v2.1.0） ==

print("== 线性灰度噪声蒙版（v2.1.0） ==")
try:
    import torch  # noqa: F401
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False
    print("  PASS-SKIP  本环境无 torch——门控/节点执行面用例跳过（ComfyUI 环境跑全量）")

if HAS_TORCH:
    import torch

    # —— _linear_noise_gate（σ 门控纯函数）——
    sigmas = torch.tensor([14.0, 10.0, 7.0, 4.0, 2.0, 0.9, 0.0])   # 降序采样表：σ_max=14
    m = torch.tensor([[[[0.0, 0.25], [0.5, 1.0]]]])                 # [1,1,2,2] 四档灰度
    # 首步 σ=σ_max：thr=1 → 只有纯白 m>=1 加入
    g = mod._linear_noise_gate(torch.tensor([14.0]), m.clone(), {"sigmas": sigmas})
    check("首步 σ=σ_max：仅纯白加入（黑/0.25/0.5 全冻结）",
          g.tolist() == [[[[0.0, 0.0], [0.0, 1.0]]]] and g.dtype == m.dtype)
    # σ=7=0.5·σ_max：thr=0.5 → m>=0.5 加入（0.5 恰在加入点——边界含等）
    g = mod._linear_noise_gate(torch.tensor([7.0]), m.clone(), {"sigmas": sigmas})
    check("σ=0.5·σ_max：m>=0.5 加入（0.5 恰达加入点，含等）",
          g.tolist() == [[[[0.0, 0.0], [1.0, 1.0]]]])
    # σ=4：thr≈0.2857 → 0.25 仍冻结 / 0.5 已加入
    g = mod._linear_noise_gate(torch.tensor([4.0]), m.clone(), {"sigmas": sigmas})
    check("σ=4：thr≈0.286——0.25 冻结 / 0.5+ 加入", g.tolist() == [[[[0.0, 0.0], [1.0, 1.0]]]])
    # σ=2：thr≈0.143 → 0.25 加入
    g = mod._linear_noise_gate(torch.tensor([2.0]), m.clone(), {"sigmas": sigmas})
    check("σ=2：0.25 也加入（仅黑恒冻结）", g.tolist() == [[[[0.0, 1.0], [1.0, 1.0]]]])
    # 钳制：越界值收 [0,1] 再门控
    m2 = torch.tensor([[[[1.7, -0.3]]]])
    g = mod._linear_noise_gate(torch.tensor([14.0]), m2, {"sigmas": sigmas})
    check("越界灰度钳制（1.7→加入 / -0.3→冻结）", g.tolist() == [[[[1.0, 0.0]]]])
    # sigmas 缺席防御：退化为 thr=1（只有纯白通过），不抛错
    g = mod._linear_noise_gate(torch.tensor([3.3]), torch.tensor([[[[1.0, 0.9]]]]), None)
    check("extra_options 缺席防御：thr=1 纯白直通不抛", g.tolist() == [[[[1.0, 0.0]]]])
    # 非 dict extra_options / σ=0 退化直通
    check("σ_max=0（空表）按原掩码直通",
          mod._linear_noise_gate(torch.tensor([0.0]), m.clone(), {"sigmas": torch.tensor([0.0])}).tolist() == m.tolist())

    # —— PromptHelperLinearNoiseMask（节点面）——
    inputs = mod.PromptHelperLinearNoiseMask.INPUT_TYPES()["required"]
    check("输入面 = samples+mask（与原生 SetLatentNoiseMask 同面）",
          set(inputs) == {"samples", "mask"})
    check("输出 = (LATENT,)", mod.PromptHelperLinearNoiseMask.RETURN_TYPES == ("LATENT",))
    node3 = mod.PromptHelperLinearNoiseMask()
    lat = {"samples": torch.zeros(1, 4, 8, 8)}
    mask_in = torch.tensor([[[[0.0, 0.5], [1.0, 0.5]]]])
    out = node3.apply_linear(lat, mask_in)[0]
    check("noise_mask=灰度值直存（reshape [-1,1,h,w] float）",
          torch.equal(out["noise_mask"], torch.tensor([[[[0.0, 0.5], [1.0, 0.5]]]]))
          and out["noise_mask"].dtype == torch.float32)
    check("线性门控标记键写入（消费方=common_ksampler 桥）", out.get(mod.PH_LINEAR_LATENT_KEY) is True)
    check("samples 原 dict 不被改写（拷贝语义）", "noise_mask" not in lat and mod.PH_LINEAR_LATENT_KEY not in lat)
    check("samples 张量原样透传", torch.equal(out["samples"], lat["samples"]))
    # 叠加语义：上游区域 noise_mask 先收（逐像素相乘）
    lat_region = {"samples": torch.zeros(1, 4, 8, 8), "noise_mask": torch.tensor([[[[1.0, 0.0], [1.0, 1.0]]]])}
    out2 = node3.apply_linear(lat_region, mask_in)[0]
    check("区域 mask 先收：区域 0 处 m=0（恒冻结），区域内 m=线性强度",
          torch.equal(out2["noise_mask"], torch.tensor([[[[0.0, 0.0], [1.0, 0.5]]]])))
    # 区域蒙版尺寸不一致：双线性缩到本蒙版尺寸
    lat_big = {"samples": torch.zeros(1, 4, 8, 8), "noise_mask": torch.ones(1, 1, 4, 4)}
    out3 = node3.apply_linear(lat_big, mask_in)[0]
    check("区域蒙版尺寸不一致自动对齐（4×4→2×2）", tuple(out3["noise_mask"].shape) == (1, 1, 2, 2))

# —— common_ksampler 桥接补丁（伪造 nodes 模块验证登记/恢复/剥离/直通）——
calls = []
mo_seen = []


class _FakeModel:
    def __init__(self):
        self.model_options = {}


def _fake_orig(model, seed, steps, cfg, sampler_name, scheduler, positive, negative, latent, **kw):
    calls.append({"latent_keys": sorted(latent.keys()), "denoise": kw.get("denoise"), "start_step": kw.get("start_step")})
    mo_seen.append(dict(model.model_options))
    return {"samples": "stub-out"}


fake_nodes = types.ModuleType("nodes")
fake_nodes.common_ksampler = _fake_orig
sys.modules["nodes"] = fake_nodes
try:
    ok = mod._install_linear_gate_bridge()
    check("桥接补丁装载成功", ok is True and getattr(fake_nodes.common_ksampler, "_ph_linear_gate_bridge", False))
    wrapper_first = fake_nodes.common_ksampler
    check("包装器内层即原函数（__defaults__ 缺省参绑定）", _fake_orig in (wrapper_first.__defaults__ or ()))
    mod._install_linear_gate_bridge()
    check("幂等重装（同一包装器对象，不再包一层）", fake_nodes.common_ksampler is wrapper_first)
    # 标记 latent：登记-剥离-恢复全链
    model = _FakeModel()
    model.model_options["denoise_mask_function"] = "prev-fn"      # 预置他人钩子——finally 必须原值恢复
    lat_marked = {"samples": "s", "noise_mask": "m", mod.PH_LINEAR_LATENT_KEY: True}
    r = fake_nodes.common_ksampler(model, 7, 20, 5.0, "euler", "karras", "pos", "neg", lat_marked, denoise=0.7)
    check("标记轮：原函数被调且标记键剥离不外漏",
          r == {"samples": "stub-out"} and calls[-1]["latent_keys"] == ["noise_mask", "samples"] and calls[-1]["denoise"] == 0.7)
    check("标记轮：门控函数登记进 model_options（原函数视角）", mo_seen[-1]["denoise_mask_function"] is mod._linear_noise_gate)
    check("标记轮：finally 原值恢复（他人钩子不被吞）", model.model_options["denoise_mask_function"] == "prev-fn")
    # 无标记 latent：原样直通（同对象、model_options 零动作）
    lat_plain = {"samples": "s2"}
    r2 = fake_nodes.common_ksampler(model, 1, 20, 5.0, "euler", "karras", "p", "n", lat_plain)
    check("无标记轮：原样直通零登记",
          r2 == {"samples": "stub-out"} and calls[-1]["latent_keys"] == ["samples"]
          and mo_seen[-1].get("denoise_mask_function") == "prev-fn")
    # 标记但缺 noise_mask（防御形态）：直通不登记
    lat_nomask = {"samples": "s3", mod.PH_LINEAR_LATENT_KEY: True}
    fake_nodes.common_ksampler(model, 1, 20, 5.0, "euler", "karras", "p", "n", lat_nomask)
    check("标记但无 noise_mask：直通（门控无从生效不误挂）", calls[-1]["latent_keys"] == [mod.PH_LINEAR_LATENT_KEY, "samples"])
finally:
    sys.modules.pop("nodes", None)

# 清桩（避免影响后续编辑器族用例对 sys.modules 的假设）
for name in ("comfy", "comfy.utils", "comfy.sd", "folder_paths"):
    sys.modules.pop(name, None)

# == config / 原子写 / 编辑器族（v1.x 保留面回归） ==

print("== config 与编辑器族（保留面） ==")
# 隔离纪律：编辑器族用例把 CONFIG_PATH/EDITOR_HINT_PATH 指到临时目录——真实 config.json
# （本机在役 autostart 配置）/editor.hint 不被测试触碰（首轮跑测曾把自愈写回打进真 config）。
REAL_CONFIG_PATH = mod.CONFIG_PATH
REAL_EDITOR_HINT_PATH = mod.EDITOR_HINT_PATH
mod.CONFIG_PATH = os.path.join(TMP_DIR, "config.json")
mod.EDITOR_HINT_PATH = os.path.join(TMP_DIR, "editor.hint")
with open(mod.CONFIG_PATH, "w", encoding="utf-8") as f:
    json.dump({"autostart": False, "editor_path": ""}, f)

check("_load_config 缺省形态（autostart 布尔化/三键字符串化）",
      (lambda cfg: cfg["autostart"] in (True, False)
       and all(isinstance(cfg[k], str) for k in ("path", "negative_path", "editor_path")))(mod._load_config()))
check("_load_config 读隔离 config（autostart=false）", mod._load_config()["autostart"] is False)

atomic_path = os.path.join(TMP_DIR, "atomic.json")
mod._atomic_write_text(atomic_path, '{"a": 1}')
check("_atomic_write_text 落盘可读", json.load(open(atomic_path, encoding="utf-8")) == {"a": 1})
mod._atomic_write_text(atomic_path, '{"a": 2}')
check("_atomic_write_text 覆盖写", json.load(open(atomic_path, encoding="utf-8")) == {"a": 2})
leftovers = [f for f in os.listdir(TMP_DIR) if f.startswith("atomic.json.tmp-")]
check("临时文件无残留", not leftovers)

check("编辑器版本号解析（v2.7.5 → (2,7,5)）",
      mod._editor_exe_version("feetaghelper-v2.7.5.exe") == (2, 7, 5))
check("非编辑器命名返回 None",
      mod._editor_exe_version("other.exe") is None and mod._editor_exe_version("feetaghelper.exe") is None)

ok, msg = mod.launch_editor("")
check("空编辑器路径：拒绝且零 shell 交互", ok is False and "未设置" in msg)
ok2, msg2 = mod.launch_editor("\\")
check("畸形路径熔断：拒绝不弹系统窗", ok2 is False and "无效" in msg2)
ghost = os.path.join(TMP_DIR, "ghost-editor.exe")
ok3, msg3 = mod.launch_editor(ghost)
check("不存在文件：明确报不存在", ok3 is False and "不存在" in msg3)

# autostart_editor：隔离 config 的 autostart 关=零动作零异常；开+空路径=明确失败消息不抛
try:
    mod.autostart_editor()
    check("autostart_editor 关态零动作通过", True)
except Exception as e:  # noqa: BLE001
    check(f"autostart_editor 异常：{e}", False)
with open(mod.CONFIG_PATH, "w", encoding="utf-8") as f:
    json.dump({"autostart": True, "editor_path": ""}, f)
try:
    mod.autostart_editor()   # 空路径 → launch_editor 回 (False, "未设置编辑器路径")，不抛
    check("autostart_editor 开态+空路径零异常（消息吞在 _log）", True)
except Exception as e:  # noqa: BLE001
    check(f"autostart_editor 开态异常：{e}", False)

# == 模板退役标注 ==

print("== workflows 模板退役标注 ==")
for tpl in ("workflows/txt2img-template.json", "workflows/img2img-template.json"):
    data = json.load(open(os.path.join(HERE, tpl), encoding="utf-8"))
    check(f"{os.path.basename(tpl)} 带 _deprecated_note 且内容提及 v2.0.0",
          isinstance(data.get("_deprecated_note"), str) and "v2.0.0" in data["_deprecated_note"])

# == 收尾 ==

mod.CONFIG_PATH = REAL_CONFIG_PATH
mod.EDITOR_HINT_PATH = REAL_EDITOR_HINT_PATH
import shutil
shutil.rmtree(TMP_DIR, ignore_errors=True)

if FAILED:
    print(f"\n{len(FAILED)} 项失败：" + "；".join(FAILED))
    raise SystemExit(1)
try:
    print("\n全部测试通过 ✔")
except UnicodeEncodeError:
    print("\n全部测试通过")   # 管道/重定向 stdout=GBK 代码页时 ✔ 编不出（v1.7.0 补）
