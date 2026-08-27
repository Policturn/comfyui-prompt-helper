# -*- coding: utf-8 -*-
"""ComfyUI 自定义节点：外部提示词注入（prompt-helper 桥接）。"""

from .prompt_helper_node import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS, autostart_editor

# 按配置在 ComfyUI 启动时联动拉起外部词条编辑器
autostart_editor()

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
