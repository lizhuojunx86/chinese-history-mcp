"""只读数据库层 + 出处格式化 (纯 stdlib).

关键约束: **绝不写库**。不复用 ``storyextractor.db.connect`` —— 那个会跑 SCHEMA +
版本化迁移 (写操作)。这里直接以 ``mode=ro`` URI 打开, 再上 ``PRAGMA query_only``
双保险; 任何写语句都会抛错。
"""
from __future__ import annotations

import os
import sqlite3

DEFAULT_DB = os.environ.get(
    "STORYEXTRACTOR_DB",
    os.path.join(os.getcwd(), "data", "corpus.db"),
)


def ro_connect(path: str) -> sqlite3.Connection:
    """只读打开 SQLite。mode=ro 禁写; query_only 兜底 (即便 URI 被绕过也拒写)。

    只读边界: 保证【绝不修改库内容】(mode=ro 拒 INSERT/UPDATE/DELETE/DDL, query_only 双保险)。
    注意: 读一个仍处 WAL 模式的库时, SQLite 可能创建/更新 -shm(共享内存 wal-index)边车文件
    以正确合并 -wal 帧 —— 这是读 WAL 的必要代价, 非改动语料。若把 corpus.db 作【只读介质/
    只读目录】分发, 请先出一个无 WAL 依赖的干净副本 (见 README §8.2:
    sqlite3 corpus.db "VACUUM INTO 'corpus_release.db'"), 令发布产物零边车、任何只读环境可读。
    不设 immutable=1: immutable 会忽略未 checkpoint 的 -wal 帧、可能读到旧数据。
    """
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"找不到语料库: {path} —— 用 STORYEXTRACTOR_DB 指定 corpus.db 路径, "
            f"或在项目根目录运行。")
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10.0)
    conn.execute("PRAGMA query_only = ON")
    conn.row_factory = sqlite3.Row
    return conn


# ---------- 出处 (每条返回的核心卖点: 书 → 篇 → 段) ----------
def chapter_name(conn: sqlite3.Connection, book_slug: str, chapter_seq: int) -> str:
    """(book_slug, chapter_seq) → 篇名 (raw_texts.chapter)。查不到回退 '#seq'。

    chapter_seq 可空 (schema 允许 event_sources/entity_mentions 的旁证行 NULL): 直接回
    '#?', 不透出 '#None' 假出处。"""
    if chapter_seq is None:
        return "#?"
    row = conn.execute(
        "SELECT chapter FROM raw_texts r JOIN books b ON b.id=r.book_id "
        "WHERE b.slug=? AND r.chapter_seq=? LIMIT 1",
        (book_slug, chapter_seq)).fetchone()
    return row[0] if row else f"#{chapter_seq}"


def citation(book_title: str, chapter: str, para_start=None, para_end=None) -> dict:
    """结构化 + 人类可读出处。段范围可空 (人物画像/整篇引用时)。

    text 形如 '史记·项羽本纪第七 段 1–2'; 篇名本身多已含 '第N' 序次。
    """
    ref = f"{book_title}·{chapter}"
    if para_start is not None:
        if para_end is not None and para_end != para_start:
            ref += f" 段 {para_start}–{para_end}"
        else:
            ref += f" 段 {para_start}"
    out = {"book": book_title, "chapter": chapter, "text": ref}
    if para_start is not None:
        out["para_start"] = para_start
        out["para_end"] = para_end if para_end is not None else para_start
    return out
