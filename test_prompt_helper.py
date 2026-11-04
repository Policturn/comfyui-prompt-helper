# -*- coding: utf-8 -*-
"""离线自测（v2.0.0 大瘦身版）：不启动 ComfyUI，直接加载节点模块验证逻辑。

覆盖面：路径直读双节点（_resolve_model_path 纯函数面 + comfy 桩注入的加载链）
与编辑器联动拉起族（v1.x 保留面）。v1.x 的六契约节点/契约文件读侧/模板结构
断言随节点删除整体退役（历史用例见 git）。

用法：python test_prompt_helper.py
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
check("PLUGIN_VERSION = 2.0.0", mod.PLUGIN_VERSION == "2.0.0")
check("注册面恰为路径双节点（六节点缩编完成）",
      set(mod.NODE_CLASS_MAPPINGS) == {"PathLoraLoader", "PathCheckpointLoader"})
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
