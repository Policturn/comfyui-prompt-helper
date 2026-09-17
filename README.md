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

## 姊妹项目

同一核心逻辑的 SD WebUI 扩展版：
[sd-webui-prompt-helper](https://github.com/Policturn/sd-webui-prompt-helper)
（文生图 / 图生图页面常驻操作栏，每次生成时注入）。两个版本同步维护。

## 许可证

[MIT](LICENSE)
