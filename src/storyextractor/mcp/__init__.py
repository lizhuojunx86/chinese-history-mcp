"""可溯源中国历史故事 MCP server (只读, 纯 stdlib).

把 corpus.db 的四轴数据 (事件 / 人物 / 地点 / 品质) 经 4 个 MCP tool 暴露给
支持 Model Context Protocol 的客户端 (Claude Desktop / Cline / …)。每条返回都带
【书 → 篇 → 段】结构化出处; review_status 如实透出 (不宣称逐条人审); 机器生成的
标点/白话统一标注。

设计约束: 运行时零第三方依赖 (项目宪法 P-10, 见 docs/CONSTITUTION.md ADR-008) ——
stdio JSON-RPC 2.0 手写实现, 不引入官方 MCP SDK。只读打开 corpus.db, 绝不写库。
"""
from __future__ import annotations

SERVER_NAME = "gushi-story"
SERVER_VERSION = "0.1.1"
