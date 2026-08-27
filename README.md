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
2. `path` 填词条 txt 文件的绝对路径（资源管理器 Shift + 右键文件 → "复制文件地址"）；
3. `base_prompt` 填你的基础提示词——也可以右键把它**转换为输入端口**，接任何文本节点；
4. 把输出 `prompt` 接到 **CLIP Text Encode** 的 `text`；
5. 反向提示词需要注入时，再加一个同样的节点，输出接负向条件的 CLIP Text Encode。

```
┌─────────────────────────────┐          ┌──────────────────────┐
│ 外部提示词注入 (prompt-helper) │  prompt →│ CLIP Text Encode      │→ 正向条件
│  path: ...\prompt.txt        │          └──────────────────────┘
│  base_prompt: masterpiece,  │
│  position: 追加到末尾         │          ┌──────────────────────┐
│              tags →          │──────────│ CLIP Text Encode      │→ 反向条件
└─────────────────────────────┘ (第二个实例)└──────────────────────┘
```

`tags` 输出为文件原始内容，`status` 输出为状态消息（文件正常 / 不存在等），
可接文本显示类节点做检查。

## 自动启动词条编辑器（可选）

在节点目录的 `config.json` 里配置（参照 `config.example.json`，改完重启 ComfyUI 生效）：

```json
{
  "path": "E:\\...\\prompt.txt",
  "autostart": true,
  "editor_path": "E:\\...\\你的词条编辑器.exe"
}
```

- `autostart` 为 `true` 时，ComfyUI 启动加载本节点包的同时会自动拉起 `editor_path`
  指定的外部词条编辑器；
- 编辑器以**独立进程**运行，关闭 ComfyUI 不会连带关闭它；
- 检测到同名进程已在运行时不会重复拉起（和 SD WebUI 版同时开启也只启动一份）。

## 行为细节

- **每次执行重新读盘**：节点通过 `IS_CHANGED` 返回 NaN 绕过 ComfyUI 的结果缓存，
  队列里每跑一次、每次批量迭代都会重新读取文件最新内容。
- **编码兼容**：自动识别 UTF-8（含 BOM）与 GBK。
- **容错**：文件被编辑器占用的瞬间自动重试 3 次；读不到就跳过注入
  （`prompt` 输出 = 原样 `base_prompt`），不中断队列，控制台打印 `[prompt-helper]` 日志。
- **默认路径**：`path` 的初始默认值读取节点目录下的 `config.json`（参照
  `config.example.json`）；工作流里改过的值保存在工作流自身，优先级更高。

## 姊妹项目

同一核心逻辑的 SD WebUI 扩展版：
[sd-webui-prompt-helper](https://github.com/Policturn/sd-webui-prompt-helper)
（文生图 / 图生图页面常驻操作栏，每次生成时注入）。两个版本同步维护。

## 许可证

[MIT](LICENSE)
