"""MCP server 守卫: 只读强制 + JSON-RPC 协议 + 四工具形状 + 诚实标注 + 出处。

新增测试 (不改既有)。构建临时文件 fixture (db.connect 建表 + 填四轴最小数据),
再以 ro_connect 只读打开, 端到端跑 handle_message。数据库 gitignore, 故不依赖
data/corpus.db。
"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from storyextractor import db as sdb                       # noqa: E402
from storyextractor.mcp import queries as Q                # noqa: E402
from storyextractor.mcp import server as S                 # noqa: E402
from storyextractor.mcp.db import chapter_name, ro_connect  # noqa: E402

SRC = os.path.join(os.path.dirname(__file__), "..", "src", "storyextractor", "mcp")


def _build_fixture(path: str) -> None:
    """建含四轴最小数据的库 (写用 db.connect, 之后只读打开)。"""
    conn = sdb.connect(path)
    c = conn.cursor()
    c.execute("INSERT INTO books(id,slug,title,license) VALUES(1,'shiji','史记','public_domain')")
    for pno, orig in ((1, "沛公军霸上"), (2, "项羽大怒"), (3, "范增说项羽")):
        c.execute("INSERT INTO raw_texts(book_id,chapter_seq,chapter,paragraph_no,original,"
                  "vernacular,translation_source) VALUES(1,7,'项羽本纪第七',?,?,?,'machine')",
                  (pno, orig, orig + "(白话)"))
    # 事件 + 逐源
    c.execute("INSERT INTO events(id,slug,title,kind,time_label,canonical_summary,book_id,status) "
              "VALUES(1,'evt-test01','鸿门宴','事件','汉元年','刘邦项羽会于鸿门',NULL,'approved')")
    c.execute("INSERT INTO event_sources(event_id,book_id,chapter_seq,para_start,para_end,role,excerpt)"
              " VALUES(1,1,7,1,2,'主叙','沛公军霸上')")
    # 人物 + 他者评价
    c.execute("INSERT INTO entities(id,slug,name,kind,profile,status,era,aliases,home_book_id,home_seq)"
              " VALUES(1,'per-test01','项羽','person','西楚霸王','draft','秦末','项王、西楚霸王',1,7)")
    c.execute("INSERT INTO entity_mentions(id,entity_id,book_id,chapter_seq,para_start,para_end,"
              "aspect,excerpt,note) VALUES(1,1,1,7,2,2,'评价','项羽勇而无谋','勇武')")
    # 同名异指消歧用例: 两人共用别名 token『文侯』(整词匹配须返回候选, 不静默选一)
    c.execute("INSERT INTO entities(id,slug,name,kind,status,era,aliases) "
              "VALUES(2,'per-test02','魏斯','person','draft','战国','魏文侯、文侯')")
    c.execute("INSERT INTO entities(id,slug,name,kind,status,era,aliases) "
              "VALUES(3,'per-test03','韩虔','person','draft','战国','韩文侯、文侯')")
    # 地点 + 映射 + 故事 + 反查
    c.execute("INSERT INTO places(id,slug,modern_name,admin_level,province,keywords) "
              "VALUES(1,'xian','陕西西安','ancient_capital','陕西','西安 长安')")
    c.execute("INSERT INTO place_aliases(ancient_name,place_id,confidence,is_vague,review_status,"
              "approved_by) VALUES('霸上',1,0.9,0,'auto_approved','consensus')")
    c.execute("INSERT INTO stories(id,slug,book_id,chapter_seq,title,gist,story_type,reality_status,"
              "source_citation,status) VALUES(1,'shiji-7-1',1,7,'鸿门宴前夜','刘邦驻军霸上',"
              "'narrative','historical','史记·项羽本纪第七 段 1–2','approved')")
    c.execute("INSERT INTO story_places(story_id,ancient_name,raw_text_id,is_vague,role,mention_ctx)"
              " VALUES(1,'霸上',1,0,'setting','沛公军霸上')")
    # 品质 + 事件品质 + 评价品质
    c.execute("INSERT INTO qualities(id,slug,name,polarity,category,gloss,aliases,status) "
              "VALUES(1,'yong','勇','positive','德性','临难不避','勇武、勇敢','active')")
    c.execute("INSERT INTO qualities(id,slug,name,polarity,category,gloss,aliases,status) "
              "VALUES(2,'gangzhi','刚直','positive','德性','骨鲠敢言','刚正','active')")
    c.execute("INSERT INTO event_qualities(event_id,quality_id,rationale,evidence_quote,confidence,"
              "review_status) VALUES(1,1,'项羽临阵不惧','项羽大怒',0.9,'auto_approved')")
    c.execute("INSERT INTO mention_qualities(mention_id,quality_id,rationale,evidence_quote,confidence,"
              "review_status) VALUES(1,1,'评其勇','项羽勇而无谋',0.8,'auto_approved')")
    # 一条 draft (未人审) 品质边: get_person 须逐字透出 review_status='draft', 不静默当人审通过
    c.execute("INSERT INTO mention_qualities(mention_id,quality_id,rationale,evidence_quote,confidence,"
              "review_status) VALUES(1,2,'评其刚','项羽勇而无谋',0.95,'draft')")
    conn.commit()
    conn.close()


class _FixtureCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fd, cls.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        _build_fixture(cls.path)
        cls.conn = ro_connect(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(cls.path + suffix)
            except OSError:
                pass


class TestReadOnly(_FixtureCase):
    def test_writes_rejected(self):
        for stmt in ("INSERT INTO books(slug,title) VALUES('x','y')",
                     "UPDATE events SET status='draft'",
                     "DELETE FROM events"):
            with self.assertRaises(sqlite3.OperationalError, msg=stmt):
                self.conn.execute(stmt)

    def test_missing_db_raises(self):
        with self.assertRaises(FileNotFoundError):
            ro_connect("/nonexistent/nope.db")

    def test_source_has_no_write_sql(self):
        """queries/server 不含写 SQL, 不 import 会跑迁移的 db.connect (静态兜底)。"""
        for fn in ("queries.py", "server.py"):
            with open(os.path.join(SRC, fn), encoding="utf-8") as fh:
                src = fh.read()
            self.assertNotRegex(src, r"\b(INSERT|UPDATE|DELETE|CREATE|DROP|ALTER)\b",
                                f"{fn} 含写 SQL")


class TestProtocol(_FixtureCase):
    def _call(self, msg):
        return S.handle_message(self.conn, msg)

    def test_initialize_echoes_supported_proto(self):
        r = self._call({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2025-06-18"}})
        self.assertEqual(r["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(r["result"]["serverInfo"]["name"], S.SERVER_NAME)
        self.assertIn("tools", r["result"]["capabilities"])

    def test_initialize_unknown_proto_falls_back(self):
        r = self._call({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "1999-01-01"}})
        self.assertEqual(r["result"]["protocolVersion"], S._SUPPORTED_PROTO[0])

    def test_notification_no_response(self):
        self.assertIsNone(self._call({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_ping(self):
        r = self._call({"jsonrpc": "2.0", "id": 9, "method": "ping"})
        self.assertEqual(r["result"], {})

    def test_tools_list_has_four(self):
        r = self._call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {t["name"] for t in r["result"]["tools"]}
        self.assertEqual(names, {"search_events", "get_person", "query_by_place", "query_by_quality"})
        for t in r["result"]["tools"]:               # 每工具须有 inputSchema
            self.assertEqual(t["inputSchema"]["type"], "object")

    def test_method_not_found(self):
        r = self._call({"jsonrpc": "2.0", "id": 3, "method": "no/such"})
        self.assertEqual(r["error"]["code"], -32601)

    def test_invalid_request(self):
        r = self._call({"id": 3, "method": "ping"})   # 缺 jsonrpc
        self.assertEqual(r["error"]["code"], -32600)

    def test_unknown_tool_is_error_result(self):
        r = self._call({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                        "params": {"name": "nope", "arguments": {}}})
        self.assertTrue(r["result"]["isError"])       # 工具级错误走 result, 非 JSON-RPC error

    def _tool(self, name, args):
        r = self._call({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                        "params": {"name": name, "arguments": args}})
        self.assertNotIn("error", r)
        self.assertFalse(r["result"].get("isError"))
        return json.loads(r["result"]["content"][0]["text"])

    def test_search_events(self):
        d = self._tool("search_events", {"keyword": "鸿门"})
        self.assertEqual(d["events"][0]["title"], "鸿门宴")
        self.assertEqual(d["events"][0]["review_status"], "approved")
        self.assertTrue(d["events"][0]["cross_book"])
        self.assertEqual(d["events"][0]["sources"][0]["citation"]["text"], "史记·项羽本纪第七 段 1–2")

    def test_get_person(self):
        d = self._tool("get_person", {"name": "项羽"})
        self.assertEqual(d["person"]["review_status"], "draft")
        self.assertIn("项王", d["person"]["aliases"])
        self.assertEqual(d["person"]["appraisals_by_others"][0]["citation"]["book"], "史记")
        self.assertTrue(any(q["name"] == "勇" for q in d["person"]["qualities"]))

    def test_get_person_alias_resolves(self):
        d = self._tool("get_person", {"name": "西楚霸王"})
        self.assertEqual(d["person"]["name"], "项羽")

    def test_query_by_place(self):
        d = self._tool("query_by_place", {"place": "西安"})
        self.assertEqual(d["resolved"]["modern_name"], "陕西西安")
        s = d["stories"][0]
        self.assertEqual(s["place_mapping"]["ancient_name"], "霸上")
        self.assertEqual(s["place_mapping"]["review_status"], "auto_approved")
        self.assertEqual(s["citation"]["book"], "史记")

    def test_query_by_place_missing(self):
        d = self._tool("query_by_place", {"place": "东京"})
        self.assertEqual(d["stories"], [])
        self.assertIn("message", d)

    def test_query_by_quality_by_name_and_slug(self):
        for key in ("勇", "yong", "勇武"):            # 名 / slug / 别名 都能解析
            d = self._tool("query_by_quality", {"quality": key})
            self.assertEqual(d["resolved"]["slug"], "yong", key)
        self.assertTrue(d["events"][0]["evidence_quote"])
        self.assertEqual(d["events"][0]["sources"][0]["citation"]["book"], "史记")
        self.assertEqual(d["persons"][0]["name"], "项羽")

    def test_quality_default_excludes_draft(self):
        # fixture 无 draft 边; 断言 include_draft 参数被接受且 auto_approved 默认可见
        d = self._tool("query_by_quality", {"quality": "勇", "include_draft": False})
        self.assertFalse(d["include_draft"])
        self.assertEqual(d["events"][0]["review_status"], "auto_approved")


class TestHonesty(_FixtureCase):
    """每工具返回都带诚实标注; 关键字段如实透出 review_status / machine-generated。"""
    def test_events_honesty(self):
        d = Q.search_events(self.conn, keyword="鸿门")
        self.assertIn("机审", d["honesty"])
        self.assertIn("machine-generated", d["events"][0]["summary_note"])

    def test_person_honesty(self):
        d = Q.get_person(self.conn, "项羽")
        self.assertIn("draft", d["person"]["profile_note"])
        self.assertIn("machine-generated", d["person"]["profile_note"])

    def test_place_and_text_honesty(self):
        d = Q.query_by_place(self.conn, "西安")
        self.assertIn("machine-generated", d["honesty"])   # 含 HONESTY['text']

    def test_quality_honesty(self):
        d = Q.query_by_quality(self.conn, "勇")
        self.assertIn("判断", d["honesty"])
        self.assertIn("machine-generated", d["events"][0]["rationale_note"])


class TestReviewFixes(_FixtureCase):
    """审查工作流确认问题的回归守卫 (只新增)。"""

    def _tool(self, name, args):
        r = S.handle_message(self.conn, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                         "params": {"name": name, "arguments": args}})
        return json.loads(r["result"]["content"][0]["text"])

    # --- 别名整词匹配 + 消歧 (子串误配) ---
    def test_alias_token_exact_resolves(self):
        self.assertEqual(self._tool("get_person", {"name": "西楚霸王"})["person"]["name"], "项羽")

    def test_alias_substring_no_longer_matches(self):
        # '霸' 仅是别名『西楚霸王』的子串 → 整词匹配后不再误命中 项羽
        d = self._tool("get_person", {"name": "霸"})
        self.assertIsNone(d["person"])
        self.assertNotIn("disambiguation", d)   # 无候选, 是 not-found

    def test_person_shared_alias_disambiguates(self):
        # 魏斯 与 韩虔 共用别名 token『文侯』→ 返回候选, 绝不静默选一
        d = self._tool("get_person", {"name": "文侯"})
        self.assertIsNone(d["person"])
        names = {c["name"] for c in d["disambiguation"]}
        self.assertEqual(names, {"魏斯", "韩虔"})

    def test_quality_alias_token_exact(self):
        self.assertEqual(self._tool("query_by_quality", {"quality": "勇武"})["resolved"]["slug"], "yong")
        # '武' 仅是 '勇武' 的子串, 整词匹配后解析不到 → not found
        self.assertIsNone(self._tool("query_by_quality", {"quality": "武"})["resolved"])

    # --- 品质逐字 review_status (不折成布尔) ---
    def test_person_qualities_carry_review_status(self):
        quals = self._tool("get_person", {"name": "项羽"})["person"]["qualities"]
        self.assertTrue(all("review_status" in q for q in quals))
        self.assertNotIn("consumable", quals[0])
        gz = next(q for q in quals if q["slug"] == "gangzhi")
        self.assertEqual(gz["review_status"], "draft")   # 未人审边如实标 draft

    # --- LIKE 通配符转义 ---
    def test_like_wildcard_escaped(self):
        self.assertEqual(self._tool("query_by_place", {"place": "%"})["stories"], [])
        self.assertEqual(self._tool("search_events", {"keyword": "%"})["total_matches"], 0)

    # --- 协议错误码 + 通知 + 空批量 ---
    def test_notification_ping_no_response(self):
        self.assertIsNone(S.handle_message(self.conn, {"jsonrpc": "2.0", "method": "ping"}))

    def test_missing_method_is_invalid_request(self):
        r = S.handle_message(self.conn, {"jsonrpc": "2.0", "id": 7})
        self.assertEqual(r["error"]["code"], -32600)

    def test_empty_batch_returns_invalid_request(self):
        out = io.StringIO()
        S.serve_stdio(self.conn, stdin=io.StringIO("[]\n"), stdout=out)
        self.assertEqual(json.loads(out.getvalue())["error"]["code"], -32600)

    def test_serve_stdio_roundtrip(self):
        out = io.StringIO()
        S.serve_stdio(self.conn, stdin=io.StringIO(
            '{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n'), stdout=out)
        r = json.loads(out.getvalue())
        self.assertEqual(len(r["result"]["tools"]), 4)

    # --- chapter_name NULL 守卫 (不透出 '#None') ---
    def test_chapter_name_null_guard(self):
        self.assertEqual(chapter_name(self.conn, "shiji", None), "#?")


if __name__ == "__main__":
    unittest.main(verbosity=2)
