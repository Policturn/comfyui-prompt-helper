# comfyui-prompt-helper（外部提示词注入节点）

ComfyUI 自定义节点：把一个由外部词条编辑器（[prompt-helper](https://github.com/Policturn) / FeeTagHelper）
实时输出的 txt 文件，在**每次执行队列时**重新读取，并把词条拼接到提示词中。

配合外部词条编辑器使用：编辑器里改词条 → 保存 → ComfyUI 里直接再跑一次队列即可，
**无需刷新页面、无需重新加载工作流**。

## 安装

### 方式一：Git 克隆（推荐）

在 ComfyUI 的 `custom_nodes` 目录打开命令行：

```bat
git clone https://github.com/Policturn/comfyui-prompt-helper comfyui-prompt-helper
```

### 方式二：手动复制

下载并解压本仓库，把整个 `comfyui-prompt-helper` 文件夹放进
`ComfyUI/custom_nodes/`（秋叶 ComfyUI 整合包在 `ComfyUI-aki-vx.x\ComfyUI\custom_nodes\`）。

重启 ComfyUI。**无需安装任何依赖**（纯 Python 标准库）。

## 使用

1. 双击画布（或右键 → Add Node）搜索 **"外部提示词注入"** 或 **PromptHelperInject** 添加节点；
2. `path` 填正向词条 txt 的绝对路径（资源管理器 Shift + 右键文件 → "复制文件地址"）；
   需要注入反向提示词时，`negative_path` 再填一个独立的反向词条文件（**留空则不注入反向**）；
3. `base_prompt` / `negative_base_prompt` 填正向、反向的基础提示词——
   也可以右键把 `base_prompt` **转换为输入端口**，接任何文本节点；
4. 输出 `prompt` 接正向 **CLIP Text Encode** 的 `text`，
   输出 `negative_prompt` 接负向 **CLIP Text Encode** 的 `text`。

```
┌──────────────────────────────────┐   prompt →┌────────────────────┐→ 正向条件
│ 外部提示词注入 (prompt-helper)      │           │ CLIP Text Encode   │
│  path:          ...\prompt.txt    │          └────────────────────┘
│  negative_path: ...\negative.txt  │ negative →┌────────────────────┐
│  base_prompt:   masterpiece, ...  │  _prompt →│ CLIP Text Encode   │→ 反向条件
│  negative_base_prompt: lowres, .. │           └────────────────────┘
│  position: （恒定最前，已忽略）      │   tags = 正向文件原始内容
└──────────────────────────────────┘   status = 正反向状态汇总
```

`tags` 输出为正向文件内容（若含元数据 tag 已剥离），`status` 输出为正反向状态汇总
（文件正常 / 不存在 / 未设置等），可接文本显示类节点做检查。

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

## 行为细节

- **注入位置固定在最前**（v1.4.1 起位置确定化）：词条恒定拼接在基础提示词
  最前面，输出的 `prompt` 结构恒为 `[注入词条][base_prompt]`。节点上的
  `position` 输入**仅为兼容旧工作流保留、取值被忽略**（避免删掉输入导致
  旧工作流组件错位）——新建工作流无需理会它。
- **每次执行重新读盘**：节点通过 `IS_CHANGED` 返回 NaN 绕过 ComfyUI 的结果缓存，
  队列里每跑一次、每次批量迭代都会重新读取文件最新内容。
- **编码兼容**：自动识别 UTF-8（含 BOM）与 GBK。
- **容错**：文件被编辑器占用的瞬间自动重试 3 次；读不到就跳过注入
  （`prompt` 输出 = 原样 `base_prompt`），不中断队列，控制台打印 `[prompt-helper]` 日志。
- **默认路径**：`path` 的初始默认值读取节点目录下的 `config.json`（参照
  `config.example.json`）；工作流里改过的值保存在工作流自身，优先级更高。

## FeeTagHelper 元数据 tag（v1.4.0+）

FeeTagHelper 构建区开启"携带元数据"时，txt 末尾会追加一个
`<fth:meta:BASE64URL>` tag（携带 BREAK 分组位置 / 选一记录）。插件注入时会：

1. **剥离**该 tag——无论解码是否成功，它都不会进入生成用提示词
   （解码失败静默丢弃，不报错不中断）；
2. **记录**——解码后的元数据写入节点目录的 `prompt_helper_meta.json`
   （附插件版本号 + 时间戳，每次执行覆盖为最新快照）。ComfyUI 的 PNG
   工作流元数据由节点输入值构成，运行期读取的文件内容无法写入，故用
   sidecar 文件兜底；
3. **不展开 BREAK**——ComfyUI 的提示词分块机制与 WebUI 不同，breaks
   位置信息直接丢弃，词条以纯平铺注入。

不需要剥离记录时，在 FeeTagHelper 设置里关闭"携带元数据"即可（txt 恢复纯平铺）。

sidecar 里各侧（positive / negative）记录的字段（v1.4.1 起，只要该侧注入
成功就记录，txt 未携带元数据 tag 时也记录；该侧未注入则键缺席）：

```json
{
  "updated": "2026-09-05 12:00:00",
  "plugin": "1.4.1",
  "positive": {
    "v": 1, "breaks": [5], "pick": [...],   // 编辑器元数据（有才带）
    "injected_tags": 22,                     // 注入区 tag 数（按逗号拆分计数，
                                             //   与编辑器自然条 offset 同基准）
    "full_text": "注入词条, base_prompt"      // 注入后的完整提示词
  }
}
```

供编辑器（如 FeeTagHelper 的 75 token 自然条）校准实际送入 CLIP 的文本；
分块本身由编辑器端用自带 tokenizer 依据 `full_text` 自行计算。

## 节点群 v1.5.0（2026-09-27，ComfyUI v3 计划步骤 1）

v1.5.0 起插件从单节点扩展为**节点群**：配合 FeeTagHelper 编辑器（文件写方 +
远程触发器）实现"设置全在编辑器、ComfyUI 只跑图"。插件节点 = 智能适配器，
每次执行现读插件目录下的契约文件（与 `config.json` 同目录）：

| 节点 | 读 / 写 | 契约文件 | 格式 |
|---|---|---|---|
| 外部提示词注入（现有） | 写 | `workflow_snapshot.json` | ComfyUI API 格式工作流图（每次执行更新，编辑器生成前检查 + 原样重提交） |
| LoRA 清单叠加 | 读 | `lora_list.json` | `[{"name":"x.safetensors","model":0.8,"clip":0.8},...]` → 循环 `load_lora_for_models`，输出 model/clip/count |
| 生成参数 | 读 | `params.json` | `{seed, steps, cfg, sampler, scheduler, width, height, batch}` 平铺 → typed 输出接 KSampler / EmptyLatentImage |
| 保存图像·元数据嵌入 | 读+写 | `params.json` / `lora_list.json` / `checkpoint.json` + sidecar；写 `image_manifest.json` | 见下 |
| 基模加载 | 读 | `checkpoint.json` | `{"name":"xxx.safetensors"}` → 加载输出 model/clip/vae/name |
| 过滤结果 | 读 | `filter_output.txt` | 逗号拼接保留词条（或含 `kept/removed/total/rules` 统计的 JSON 信封）→ kept_tags / removed_count / total / status |
| 改图输入·图生图（v1.6.0） | 读 | `image.json` + `images/` | `{"image":"x.png","denoise":0.5,"scale":1.0}` → LoadImage 语义加载（scale>1 时 LANCZOS 放大 16 倍数边长）+ VAEEncode，输出 LATENT + denoise 接 KSampler |

### 保存图像·元数据嵌入（PromptHelperImageOutput）

把任何生图节点的 IMAGE 输出接到此节点（OUTPUT_NODE，执行终点）：

1. IMAGE 张量 → PIL，落盘 ComfyUI output 目录（前缀默认 `feetag`，
   文件名 `feetag_00001_00000_.png` 形态，同 SaveImage 命名规则）；
2. 自动读 params.json / lora_list.json / checkpoint.json + 注入 sidecar，
   组装 **A1111 格式 parameters 全文**（正向 / Negative prompt / Steps /
   Sampler / CFG scale / Seed / Size / Model / Lora hashes——与 WebUI 版
   生成的 PNG 完全同构，编辑器图库详情 / 读图还原零改动直接解析）；
3. PNG tEXt 双写：`parameters`（A1111 全文，尾部附 `fth_meta: {json}` 记录
   行，WebUI extra_generation_params 同形态）+ `fth_meta` / `fth_meta_negative`
   独立 chunk（注入记录 JSON：breaks / pick / injected_tags / full_text /
   filter 等字段**原样透传**，不做白名单）；
4. 追加 `image_manifest.json` 取图清单：`[{prompt_id, filename, subfolder,
   ts}, ...]`（`ts` = epoch 秒；上限 500 条，超出丢最旧）——编辑器据此
   GET /view 取图。

### 容错口径（v1.5.0）

- **LoRA 清单 / 生成参数 / 过滤结果**：契约文件缺失 / 损坏 → 安全默认
  （直通 model/clip、count=0 / 默认参数 / status=未配置），控制台
  `[prompt-helper]` 日志定位，队列不中断；
- **基模加载**：缺失 / 未注册 → 节点红字报错（基模不可静默默认）；
- **保存图像**：各元数据源缺席省略对应 parameters 段（不编造数值），
  保存本体与取图清单不受影响；
- **注入节点**：工作流快照写失败不影响注入（原有功能零回归）；
- 采样器 / 调度器名**原样透传**（ComfyUI 原生拼写如 `euler` / `normal`，
  A1111 别名如 `Euler a` 的映射由编辑器写方负责）。

## 改图链 v1.6.0（图生图 img2img，2026-11-03；v1.7.0 增尺寸倍率）

v1.6.0 新增图生图输入段：**改图输入·图生图（PromptHelperImageInput）**节点 +
`image.json` 独立契约 + 官方工作流模板。设计纪律：**不动 `params.json` 八字段
面、不动 Params 节点 8 输出口**（typed 输出面加键 = 旧工作流连线要重接），
改图专属参数全部走独立契约文件。

### image.json 契约（编辑器改图面板 = 写方）

```json
{
  "image": "输入图文件名.png",
  "denoise": 0.5,
  "scale": 1.0
}
```

- 契约文件落在插件目录（与 `config.json` 同目录）；**输入图实体放插件目录
  `images/` 子目录**——编辑器（fth 侧）负责把用户选的图拷贝进来，节点侧
  只按 `image` 里的文件名加载（basename 收敛，不取 `images/` 外路径）；
- `denoise` 契约范围 **0.05 ~ 0.95**（编辑器滑杆范围）：缺失 / 坏值 → 默认
  `0.5`；越界收敛到最近端（如 `1.2` → `0.95`），不报错不拦队列；
- `scale` 尺寸倍率契约范围 **1.0 ~ 2.0**（编辑器滑杆步进 0.25，v1.7.0）：
  缺失 / 坏值 → 默认 `1.0`（= v1.6.x 行为，旧契约零迁移）；越界收敛到最近端，
  不报错不拦队列；
- **容错口径（与基模加载同款纪律）**：`image.json` 缺失 / 损坏 / 缺 `image`
  字段 / `images/` 里没有该文件 → 节点红字清晰报错，文案指路「fth 侧未写
  image.json 或图未拷贝」——改图没有源图时静默直通（跑成意外噪声图）比
  报错更糟；
- 表外未知字段容忍忽略。

### 改图输入·图生图（PromptHelperImageInput）节点行为

读 `image.json` → **LoadImage 语义**加载 `images/<image>`（EXIF 方向转正 +
RGB + 0..1 张量，与原生 LoadImage 节点输出同构）→（`scale > 1.0` 时按倍率
**LANCZOS 放大**：逐边 round(边长×scale) 取最近 **16 倍数**，再钳制到
**64 ~ 4096**——超分=尺寸放大的图生图，放大段在 VAEEncode 之前，输出
latent 即放大后尺寸）→ **VAEEncode**（`vae`
输入口接基模加载节点的 `vae` 输出——LoRA 不影响 VAE，无需经过 LoRA 清单
叠加节点）→ 输出两口：

| 输出 | 类型 | 接线 |
|---|---|---|
| `latent` | LATENT | KSampler 的 `latent_image`（**改图链不接 EmptyLatentImage**，Params 的 width/height/batch 三口悬空不消费） |
| `denoise` | FLOAT | KSampler 的 `denoise` |

`IS_CHANGED` 返回 NaN 绕缓存（改契约 / 换图后无需刷新，下次执行现读）。
改图链输出张数 = 1（输入图 batch 维固定 1）。

### 官方改图工作流模板（workflows/img2img-template.json）

仓内 [`workflows/img2img-template.json`](workflows/img2img-template.json) 是
完整的可导入工作流：v1.5.0 六节点（注入 / 生成参数 / LoRA 清单 / 基模 /
保存图像 / 过滤）+ 改图输入节点 + 原生采样段（CLIP 编码 ×2 / KSampler /
VAEDecode）的完整接线。使用：

1. ComfyUI 网页 UI 里 **导入**（工作流菜单 → 打开/导入该文件，或直接拖进画布）；
2. 点一次生成（Queue）——注入节点会上报 `workflow_snapshot.json`，此后
   编辑器侧「生成前检查 + 原样重提交」链路即接管；
3. 模板同时是 **fth 侧 `workflow_snapshot.json` 的官方种子**——FeeTagHelper
   改图面板（阶段 2 起）以本模板为基准快照原样提交，用户也可以在导入后
   自由改动画布（加 ControlNet 等），点一次生成后新拓扑即成为自己的快照。

### 契约路径裸文件名解析（v1.6.0）

各契约节点的文件路径输入（`params_file` / `lora_file` / `checkpoint_file` /
`filter_file` / `image_file` / 注入节点的 `path` / `negative_path`）新增
**裸文件名**写法：只填文件名（如 `params.json`）时自动解析为插件目录下
同名文件——官方模板因此在**任意安装路径**导入即用，无需手工改路径；
绝对路径照旧原样使用，旧工作流零迁移。

## 文生图官方模板 v1.8.0（workflows/txt2img-template.json，2026-11-04）

v1.8.0 新增**文生图**官方工作流模板。生成逻辑与改图模板（v1.6.0）完全
同构，只差输入段：

- **去掉** `PromptHelperImageInput`（及其 VAE 接线）——文生图没有源图；
- `KSampler.latent_image` 改接原生 **EmptyLatentImage**；
- EmptyLatentImage 的 `width` / `height` / `batch_size` 三口分别接
  **Params 节点的 `width` / `height` / `batch` 三输出口**（改图模板里这三口
  悬空不消费，文生图正好启用——尺寸与总张数仍由编辑器 `params.json` 契约
  驱动，`batch` 语义 = 批次 × 单批合并）；
- `KSampler.denoise` 悬空由 widget 承载（= 1，文生图全重绘）。

节点与契约面**零改动**（七件套不变、无新契约文件）。FeeTagHelper
v2.22.0 起生成面板的 ComfyUI 轮直读本模板（UI 格式确定性转 API 格式后
提交）——**不再依赖「先在 ComfyUI 手搭六节点点一次生成上报
workflow_snapshot.json」的启动序**；快照上报机制本身保留（Inject 节点
行为不变，手搭工作流照常可用）。

## 姊妹项目

同一核心逻辑的 SD WebUI 扩展版：
[sd-webui-prompt-helper](https://github.com/Policturn/sd-webui-prompt-helper)
（文生图 / 图生图页面常驻操作栏，每次生成时注入）。两个版本同步维护。

## 许可证

[MIT](LICENSE)
