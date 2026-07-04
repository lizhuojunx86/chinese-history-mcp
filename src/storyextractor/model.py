"""Core data contracts (mirrors huadian's ingest contract, adapted & lightweight).

Book → Chapter (篇) → Paragraph (段). A Paragraph carries 原文 + optional 白话译文.
Stories (P2) are spans of paragraphs and are modeled in the DB, not here.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Paragraph:
    paragraph_no: int          # 1-based, within its 篇
    original: str              # 文言原文
    vernacular: str | None = None   # 白话译文 (None if not present / unaligned)
    # aligned = 取自对照本并按段对齐; machine = LLM 生成; unaligned = 原文有但译文缺位; none = 无译文
    translation_source: str = "none"


@dataclass
class Chapter:
    category: str              # 大类/卷: 本纪/世家/列传 或 卷一 孟春纪第一
    title: str                 # 篇名: 五帝本纪第一 / 本生
    paragraphs: list[Paragraph] = field(default_factory=list)


@dataclass
class Book:
    slug: str
    title: str
    author: str = ""
    dynasty: str = ""
    genre: str = ""            # official_history | philosophy | classic ...
    # public_domain = 原文公有领域; copyright_translation = 含受版权译文(自用)
    license: str = "public_domain"
    chapters: list[Chapter] = field(default_factory=list)
