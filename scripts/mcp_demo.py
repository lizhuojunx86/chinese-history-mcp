"""可溯源中国历史故事 MCP server 的 demo / 录屏脚本 (也是一份最小 MCP 客户端参考实现)。

以子进程拉起 `python -m storyextractor.mcp.server`, 走真实 stdio JSON-RPC 握手,
逐场景调用四工具, 打印干净的中文 transcript (适合截图/录屏)。纯 stdlib、只读。

    PYTHONPATH=src python3 scripts/mcp_demo.py [--db data/corpus.db]

场景: ①今地名反查(西安→消歧→洛阳) ②品质代表故事(忠) ③人物画像(曹操) ④跨书事件(官渡之战)。
每条都带【书→篇→段】出处; review_status 如实透出。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class MCPClient:
    """最小 stdio MCP 客户端: 换行分隔 JSON-RPC, 同步请求/响应。"""

    def __init__(self, db_path: str):
        env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"))
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "storyextractor.mcp.server", "--db", db_path],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=env, text=True, bufsize=1, cwd=ROOT)
        self._id = 0

    def _rpc(self, method: str, params: dict | None = None, notify: bool = False):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self._id += 1
            msg["id"] = self._id
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        if notify:
            return None
        return json.loads(self.proc.stdout.readline())

    def initialize(self):
        r = self._rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                     "clientInfo": {"name": "mcp_demo", "version": "0"}})
        self._rpc("notifications/initialized", notify=True)
        return r["result"]

    def call(self, name: str, arguments: dict) -> dict:
        r = self._rpc("tools/call", {"name": name, "arguments": arguments})
        return json.loads(r["result"]["content"][0]["text"])

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            self.proc.kill()


# ---------- 打印 ----------
def hr(title: str) -> None:
    print("\n" + "─" * 68)
    print("  " + title)
    print("─" * 68)


def cite(c) -> str:
    return c["text"] if isinstance(c, dict) and c.get("text") else "—"


def demo(db_path: str) -> None:
    cli = MCPClient(db_path)
    try:
        info = cli.initialize()
        print(f"● 已连接 MCP server: {info['serverInfo']['name']} v{info['serverInfo']['version']}"
              f"  (协议 {info['protocolVersion']})")

        # ① 今地名反查 —— 先展示"同名异地绝不替选"的消歧
        hr("① query_by_place『西安』——同名异地, 绝不替你单选")
        d = cli.call("query_by_place", {"place": "西安"})
        for c in d.get("disambiguation", []):
            print(f"   • {c['modern_name']}（{c['province']}·{c['admin_level']}）"
                  f" 有 {c['story_count']} 个故事")
        print(f"   → {d.get('message', '')}")

        hr("① query_by_place『南阳』——发生在这块土地上的古籍故事 + 出处")
        d = cli.call("query_by_place", {"place": "南阳", "limit": 3})
        if d.get("resolved"):
            print(f"   今地: {d['resolved']['modern_name']}  共 {d.get('total_stories')} 个故事")
            for s in d.get("stories", []):
                m = s["place_mapping"]
                print(f"   • {s['title']}  〔{cite(s['citation'])}〕")
                print(f"       {s.get('gist') or ''}")
                print(f"       原文作『{m['ancient_name']}』· 映射 {m['review_status']}"
                      f"/{m['confidence_tier']}")
        print(f"   ⚠ {d.get('honesty', '')}")

        # ② 品质 → 代表故事/人物 + 原文证据
        hr("② query_by_quality『忠』——代表事件/人物 + 原文证据")
        d = cli.call("query_by_quality", {"quality": "忠", "limit": 2})
        r = d["resolved"]
        print(f"   品质: {r['name']}（{r['polarity']}·{r['category']}）释: {r['gloss']}")
        for e in d.get("events", []):
            print(f"   [事件] {e['title']}  · 强度 {e['strength']} · {e['review_status']}")
            print(f"          理由: {e['rationale']}")
            if e.get("evidence_quote"):
                print(f"          证据: 「{e['evidence_quote'][:40]}」")
            if e.get("sources"):
                print(f"          出处: {cite(e['sources'][0]['citation'])}")
        for p in d.get("persons", []):
            print(f"   [人物] {p['name']}  · {p['review_status']}  〔{cite(p.get('citation'))}〕")
            if p.get("evidence_quote"):
                print(f"          史料: 「{p['evidence_quote'][:40]}」")

        # ③ 人物画像 + 他者评价 (逐条带出处)
        hr("③ get_person『曹操』——画像 + 他者评价（逐条带出处）")
        d = cli.call("get_person", {"name": "曹操"})
        p = d["person"]
        print(f"   {p['name']}（{p['era']}）别名: {'、'.join(p['aliases'][:4])}  · {p['review_status']}")
        print(f"   画像: {(p['profile'] or '')[:60]}…  [{p['profile_note']}]")
        print(f"   史料评为的品质: {'、'.join(q['name'] for q in p['qualities'][:8])}")
        for a in [x for x in p["appraisals_by_others"] if x.get("excerpt")][:2]:
            print(f"   • 他者评价〔{cite(a['citation'])}〕「{a['excerpt'][:44]}」")

        # ④ 跨书融合事件 + 逐源出处
        hr("④ search_events『官渡之战』——跨书融合 + 逐源出处")
        d = cli.call("search_events", {"keyword": "官渡", "limit": 1})
        for e in d.get("events", []):
            tag = "跨书合并" if e["cross_book"] else "单书"
            print(f"   {e['title']}（{tag}·{e['review_status']}）")
            print(f"   融合叙述: {(e['canonical_summary'] or '')[:70]}…  [{e['summary_note']}]")
            print(f"   {len(e['sources'])} 个来源:")
            for s in e["sources"][:6]:
                print(f"     - 〔{s['role']}〕{cite(s['citation'])}  摘: {(s['excerpt'] or '')[:24]}")
        print(f"   ⚠ {d.get('honesty', '')}")

        print("\n" + "═" * 68)
        print("  每条返回都可追到【书→篇→段】; review_status 如实标注 (approved 多为机审"
              "批量过审的可信推定, 画像为 draft)。对照『裸 LLM vs 本 server』见 docs/MCP_DEMO.md。")
        print("═" * 68)
    finally:
        cli.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="MCP server demo / 录屏脚本")
    ap.add_argument("--db", default=os.path.join(ROOT, "data", "corpus.db"))
    demo(ap.parse_args().db)
