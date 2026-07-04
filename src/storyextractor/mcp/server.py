"""纯 stdlib 手写的 MCP stdio server (JSON-RPC 2.0, 换行分隔)。

为什么不用官方 MCP SDK: 项目宪法 P-10 要求运行时零第三方依赖 (见 CONSTITUTION.md
ADR-008)。MCP 的 stdio 传输对一个【只读工具服务器】极简 —— 换行分隔的 JSON-RPC 2.0
over stdin/stdout, 只需实现 initialize / tools/list / tools/call (+ ping、
notifications/*)。手写 ~一个文件, 零依赖、可一眼审计, 契合项目"可溯源"定位。

协议要点:
  - 每行一个 JSON 消息 (消息体内不含裸换行); 请求带 id → 回响应, 通知 (无 id) → 不回。
  - initialize 回 protocolVersion (回显客户端所请, 不支持则回本服务器最新) + capabilities。
  - tools/call 的工具级错误走 result{isError:true} (让 LLM 能读到并纠正), 而非 JSON-RPC error。

运行:  python -m storyextractor.mcp.server [--db data/corpus.db]
       ./se mcp
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys

from . import SERVER_NAME, SERVER_VERSION
from . import queries as Q
from .db import DEFAULT_DB, ro_connect

# 本服务器认识的协议版本 (新→旧); 回显客户端所请之一, 否则回 [0]
_SUPPORTED_PROTO = ("2025-06-18", "2025-03-26", "2024-11-05")

_INSTRUCTIONS = (
    "可溯源中国历史故事库 (先秦-汉魏 9 部正史/子书: 史记/汉书/后汉书/三国志/左传/论语/"
    "孟子/吕氏春秋/资治通鉴)。四个工具分别按【事件/人物/今地名/品质】查询, 每条返回都带"
    "【书→篇→段】结构化出处。诚实约束: 事件 approved 多为机审批量过审的可信推定 (非逐条"
    "人审)、人物画像与部分品质映射为 draft、原文标点与白话译文为机器生成 —— 返回中的 "
    "review_status / *_note 字段如实标注, 请勿据此宣称'逐条人工核校'。只读, 不改库。"
)


# ---------- 工具注册 (name → 描述 / inputSchema / handler) ----------
_BOOK_SLUGS = "shiji, hanshu, houhanshu, sanguozhi, zuozhuan, lunyu, mengzi, lushi-chunqiu, zizhitongjian"

TOOLS = [
    {
        "name": "search_events",
        "description": (
            "查跨书融合历史事件 + 逐源出处。同一史事在史记/汉书/资治通鉴等多书的记载被合并为"
            "一条, 每个来源带【书·篇·段】与角色 (主叙/详述/简述/评论/旁证)。返回 review_status "
            "(approved=机审批量过审的可信推定, 非逐条人审); canonical_summary 为 LLM 融合的机器叙述。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string",
                            "description": "事件关键词, 匹配标题或融合叙述, 如 '鸿门宴'、'赤壁'、'七国之乱'"},
                "book": {"type": "string",
                         "description": f"限定来源书 slug (可选): {_BOOK_SLUGS}"},
                "person": {"type": "string",
                           "description": "限定涉及人物 (标题/叙述/摘录含此名), 如 '韩信'"},
                "limit": {"type": "integer", "description": "返回条数 1-50, 默认 10", "default": 10},
            },
        },
    },
    {
        "name": "get_person",
        "description": (
            "查人物画像 + 他者评价 + 参与事件 (逐条带出处)。画像 (profile) 为 LLM 综合生成、"
            "review_status=draft (未逐条人核); 他者评价 excerpt 是公版原文照抄可溯源; 附史料"
            "评为的品质。库内为先秦-汉魏人物。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "人物本名或别名, 如 '曹操'、'项羽'、'淮阴侯'"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "query_by_place",
        "description": (
            "用今天的地名反查发生在这块土地上的古籍故事 + 出处。今地名可为省/地级市/区县 "
            "(如 '西安'、'陕西西安'、'洛阳')。古今映射多为多 LLM 机审共识 (auto_approved); 同名"
            "异地/古名多解时返回候选列表让你消歧, 绝不替你单选; 方向性泛称 (江东/关中) 不收录。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "place": {"type": "string", "description": "今天的地名, 如 '西安'、'洛阳'、'开封'"},
                "limit": {"type": "integer", "description": "返回故事数 1-50, 默认 15", "default": 15},
            },
            "required": ["place"],
        },
    },
    {
        "name": "query_by_quality",
        "description": (
            "按品质 (德性/才能/性情/为政…) 查代表性最强的事件与人物 + 原文证据。品质取自 55 词"
            "受控词表 (如 忠/谋略/勇/仁/残暴/骄), 可用中文名或英文 slug。映射是【判断】非事实: "
            "auto_approved=机审高置信、draft=待人审; evidence_quote 是原文真子串; 默认只出机审通过。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "quality": {"type": "string",
                            "description": "品质名或 slug, 如 '忠'、'谋略'、'yong'"},
                "limit": {"type": "integer", "description": "事件/人物各返回条数 1-30, 默认 10", "default": 10},
                "include_draft": {"type": "boolean",
                                  "description": "是否含待人审(draft)映射, 默认 false 只返回机审通过", "default": False},
            },
            "required": ["quality"],
        },
    },
]

_TOOL_NAMES = {t["name"] for t in TOOLS}


def _dispatch_tool(conn: sqlite3.Connection, name: str, args: dict) -> dict:
    """按工具名调用对应查询函数。args 来自客户端, 做防御式取值。"""
    args = args or {}
    if name == "search_events":
        return Q.search_events(conn, keyword=args.get("keyword"), book=args.get("book"),
                               person=args.get("person"), limit=args.get("limit", 10))
    if name == "get_person":
        return Q.get_person(conn, name=args.get("name", ""))
    if name == "query_by_place":
        return Q.query_by_place(conn, place=args.get("place", ""), limit=args.get("limit", 15))
    if name == "query_by_quality":
        return Q.query_by_quality(conn, quality=args.get("quality", ""),
                                  limit=args.get("limit", 10),
                                  include_draft=bool(args.get("include_draft", False)))
    raise KeyError(name)


# ---------- JSON-RPC 2.0 ----------
def _result(rid, result) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _error(rid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def handle_message(conn: sqlite3.Connection, msg: dict):
    """处理一条 JSON-RPC 消息。返回响应 dict; 通知 (无 id) 返回 None。"""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return _error(msg.get("id") if isinstance(msg, dict) else None,
                      -32600, "Invalid Request: 需 JSON-RPC 2.0 对象")
    # 通知 (无 id): JSON-RPC 2.0 规定 MUST NOT 回应 —— 含 notifications/initialized 及任何
    # 无 id 消息。本 server 无副作用, 一律静默忽略 (在方法分派之前, 避免回 id:null 响应)。
    if "id" not in msg:
        return None
    rid = msg.get("id")
    method = msg.get("method")
    if not isinstance(method, str):              # Request 必须含字符串 method, 否则非法请求
        return _error(rid, -32600, "Invalid Request: 缺 method")
    params = msg.get("params") or {}

    if method == "initialize":
        client_proto = params.get("protocolVersion")
        proto = client_proto if client_proto in _SUPPORTED_PROTO else _SUPPORTED_PROTO[0]
        return _result(rid, {
            "protocolVersion": proto,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": _INSTRUCTIONS,
        })
    if method == "ping":
        return _result(rid, {})
    if method == "tools/list":
        return _result(rid, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name not in _TOOL_NAMES:
            return _result(rid, {
                "content": [{"type": "text", "text": f"未知工具: {name}。可用: {sorted(_TOOL_NAMES)}"}],
                "isError": True})
        try:
            data = _dispatch_tool(conn, name, args)
        except Exception as e:                    # noqa: BLE001 工具级错误回给 LLM, 不崩服务
            return _result(rid, {
                "content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}],
                "isError": True})
        text = json.dumps(data, ensure_ascii=False, indent=2)
        return _result(rid, {"content": [{"type": "text", "text": text}]})

    return _error(rid, -32601, f"Method not found: {method}")


def serve_stdio(conn: sqlite3.Connection, stdin=None, stdout=None) -> None:
    """读 stdin 逐行 JSON-RPC, 写 stdout 逐行响应。EOF 退出。"""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _write(stdout, _error(None, -32700, "Parse error"))
            continue
        if isinstance(msg, list):
            if not msg:                          # 空批量数组: JSON-RPC 2.0 要求回单条 -32600
                _write(stdout, _error(None, -32600, "Invalid Request: 空批量"))
                continue
            responses = [r for r in (handle_message(conn, m) for m in msg) if r is not None]
            if responses:                        # 批: 有响应才回 (全通知则不回)
                _write(stdout, responses)
        else:
            r = handle_message(conn, msg)
            if r is not None:
                _write(stdout, r)


def _write(stdout, payload) -> None:
    stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    stdout.flush()


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="storyextractor.mcp.server",
                                 description="可溯源中国历史故事 MCP server (只读, stdio)")
    ap.add_argument("--db", default=DEFAULT_DB, help=f"corpus.db 路径 (默认 {DEFAULT_DB})")
    args = ap.parse_args(argv)
    # 强制 stdio 用 UTF-8: 客户端可能在非 UTF-8 locale (LANG/PYTHONIOENCODING) 下 spawn 本
    # server, 而全部载荷是中文 —— 默认编码会在首条消息 UnicodeEncode/DecodeError 静默崩溃。
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    conn = ro_connect(args.db)
    print(f"[gushi-mcp] 只读加载 {args.db}; stdio JSON-RPC 就绪 (4 工具)。", file=sys.stderr)
    try:
        serve_stdio(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
