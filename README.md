# comfyui-prompt-helper（路径直读双节点 + 编辑器联动）

ComfyUI 自定义节点包（**v2.0.0 架构 v2·2026-11-04**）。v2.0.0 起职责缩编为两件：

1. **PathLoraLoader / PathCheckpointLoader**——按完整路径直读 LoRA / 底模的加载节点
   （跨目录模型库直载，不经 ComfyUI 注册名解析）；
2. **编辑器自动拉起**——ComfyUI 启动时按 config 联动拉起外部词条编辑器（v1.x 全量保留）。

> **v1.x 的六个契约节点（Inject / LoraStack / Params / Checkpoint / ImageInput /
> ImageOutput / Filter）与契约文件机制（params.json / lora_list.json / image.json /
> checkpoint.json / prompt.txt 双件 / filter_output.txt / workflow_snapshot.json /
> image_manifest.json）随 v2.0.0 整体退役**：FeeTagHelper v2.24.0（架构 v2）起改用
> **图构建器**——编辑器按快照直接构建完整 API 格式工作流 JSON（原生节点直搭）提交
> /prompt，动态参数全部以字面量直进图，不再有任何契约文件读写。旧工作流模板
> （workflows/ 目录）保留并标注 deprecated，仅供网页画布手动参考（其中 PromptHelper*
> 六节点已删除，画布打开会缺节点——请改用原生节点 + 本包双节点）。历史版本的节点
> 行为文档见 git 历史的 README。

## 为什么要路径直读节点

原生 `LoraLoader` / `CheckpointLoaderSimple` 的 `lora_name` / `ckpt_name` 是
**COMBO 校验**（只收 ComfyUI 注册名清单内的相对名——2026-11-04 实测喂绝对路径
400 `value_not_in_list` 拒收）。而 FeeTagHelper 的模型库扫描的是 SD WebUI 侧
目录（fth 是模型位置的唯一事实源），对 ComfyUI 是跨目录路径。图构建器在构建期
做三层解析（注册名 > 路径 > 搜索）：

- **库内命中（带绝对路径）→ 本包双节点直读**（零 `extra_model_paths.yaml` 桥依赖）；
- **未命中（ComfyUI 自带模型 / 手写名）→ 原生 LoraLoader / CheckpointLoaderSimple**
  注册名自解析。

手搭工作流同样可用：把任意目录的模型文件完整路径填进 `path` 即可。

## 安装

### 方式一：Git 克隆（推荐）

在 ComfyUI 的 `custom_nodes` 目录打开命令行：

```bat
git clone https://github.com/Policturn/comfyui-prompt-helper comfyui-prompt-helper
```

### 方式二：手动复制

下载并解压本仓库，把整个 `comfyui-prompt-helper` 文件夹放进
`ComfyUI/custom_nodes/`（秋叶 ComfyUI 整合包在 `ComfyUI-aki-vx.x\ComfyUI\custom_nodes\`）。

重启 ComfyUI。**无需安装任何依赖**（纯 Python 标准库 + ComfyUI 自带 comfy 包）。

## 使用（路径直读双节点）

- **PathLoraLoader**：`model` / `clip` 接上游（底模加载器或上一个 LoRA 节点——
  可串链），`path` 填 LoRA 文件完整路径，`strength_model` / `strength_clip` 与原生
  LoraLoader 同义（0/0 时短路直通）。加载链与原生 LoraLoader 逐字同构
  （`comfy.utils.load_torch_file` + `comfy.sd.load_lora_for_models`，含单实例缓存）。
- **PathCheckpointLoader**：`path` 填底模文件完整路径，输出 `MODEL` / `CLIP` / `VAE`，
  加载链与原生 CheckpointLoaderSimple 逐字同构（embeddings 目录仍取注册目录）。

```
┌──────────────────────────┐        ┌────────────────────────────┐
│ 底模路径直读               │ MODEL→│ LoRA 路径直读 ①            │ MODEL→ （可再串 ②③…）
│ path: D:\模型\底模.safetensors      │ CLIP →│ path: D:\Lora库\角色\A.safetensors │ CLIP →
└──────────────────────────┘        └────────────────────────────┘
```

容错口径：路径为空 / 扩展名不在白名单（safetensors/ckpt/pt/bin）/ 文件不存在 →
**执行期可见报错**（红节点错误），不静默跳过——静默不生效是 v1.x LoraStack 的历史
病灶，本版根除。相对路径以本节点包目录为基准解析（手搭便利位；fth 生成轮恒送绝对
路径）。离线自测：`python test_prompt_helper.py`（版本/注册面 + 路径解析纯函数面 +
comfy 桩注入的加载链 + 编辑器族回归 + 模板退役标注）。

## 自动启动词条编辑器（可选）

在节点目录的 `config.json` 里配置（参照 `config.example.json`，改完重启 ComfyUI 生效）：

```json
{
  "path": "E:\\...\\prompt.txt",
  "negative_path": "E:\\...\\negative-prompt.txt",
  "autostart": true,
  "editor_path": "E:\\...\\你的词条编辑器.exe"
}
```

- `autostart` 为 `true` 时，ComfyUI 启动加载本节点包的同时会自动拉起 `editor_path`
  指定的外部词条编辑器；
- 编辑器以**独立进程**运行，关闭 ComfyUI 不会连带关闭它；
- 检测到同名进程已在运行时不会重复拉起（和 SD WebUI 版同时开启也只启动一份）；
- **editor_path 失效自动探测（v1.4.2）**：编辑器发版 exe 改名（如
  `feetaghelper-v2.7.5.exe` → `v2.8.0`）后，旧配置路径失效时启动编辑器会自动
  在同目录扫描 `feetaghelper-v*.exe`、取版本号最新的一个并写回 config——
  发版不再需要手动更新配置（仅同目录生效；新 exe 换了目录仍需手改）。
- **editor.hint 编辑器自荐路径（v1.4.10）**：FeeTagHelper 编辑器在连接
  「检测」时会把自身 exe 绝对路径写进节点目录根的 `editor.hint` 文件
  （单行文本；编辑器**永不修改插件 config**，单向传值）。启动时的解析链：
  **config 用户值有效 > editor.hint 有效 > 同目录扫描最新版 > 原值兜底**
  ——你在 config 里配置的路径永远不会被 hint 覆盖；没配置时 hint 直接
  生效（自动启动零配置可用）。hint 档不写回 config；内容缺失 / 空 /
  畸形 / 指向不存在的文件时自动跳过该档。

## 版本

- **v2.0.0（2026-11-04，架构 v2）**：六契约节点与契约文件机制整体退役；新增
  PathLoraLoader / PathCheckpointLoader；编辑器自动拉起保留；workflows/ 模板标注
  deprecated。配套 FeeTagHelper v2.24.0（快照→图构建器执行底座）。
- v1.x 版本历史（v1.4.x 编辑器联动 → v1.5.0 节点群 → v1.6.0~v1.9.0 改图链与
  契约演进）见 git 历史 README。

## 姊妹项目

同一核心逻辑的 SD WebUI 扩展版：
[sd-webui-prompt-helper](https://github.com/Policturn/sd-webui-prompt-helper)
（文生图 / 图生图页面常驻操作栏，每次生成时注入）。两个版本同步维护。

## 许可证

[MIT](LICENSE)
