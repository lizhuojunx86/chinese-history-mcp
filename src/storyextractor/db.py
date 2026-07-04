"""SQLite store. Schema mirrors huadian's design (books / raw_texts / stories /
story_segments), trimmed to a single-file local DB. P1 populates books+raw_texts;
stories/story_segments are created now and filled in P2.
"""
from __future__ import annotations

import sqlite3

from .model import Book

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id       INTEGER PRIMARY KEY,
    slug     TEXT UNIQUE NOT NULL,
    title    TEXT NOT NULL,
    author   TEXT,
    dynasty  TEXT,
    genre    TEXT,
    license  TEXT
);

CREATE TABLE IF NOT EXISTS raw_texts (
    id                 INTEGER PRIMARY KEY,
    book_id            INTEGER NOT NULL REFERENCES books(id),
    category           TEXT,                 -- 大类/卷
    chapter_seq        INTEGER NOT NULL,     -- 1-based 篇序 (篇名可能重复, 故用序号)
    chapter            TEXT NOT NULL,        -- 篇名
    paragraph_no       INTEGER NOT NULL,     -- 1-based within 篇
    original           TEXT NOT NULL,        -- 文言原文
    vernacular         TEXT,                 -- 白话译文 (nullable)
    translation_source TEXT DEFAULT 'none',  -- aligned|machine|unaligned|none
    UNIQUE(book_id, chapter_seq, paragraph_no)
);
CREATE INDEX IF NOT EXISTS idx_rawtexts_book_chapter ON raw_texts(book_id, chapter_seq);

-- P2 (created now, populated later)
CREATE TABLE IF NOT EXISTS stories (
    id                     INTEGER PRIMARY KEY,
    slug                   TEXT UNIQUE,
    book_id                INTEGER REFERENCES books(id),
    chapter_seq            INTEGER,          -- 故事所属篇序 (对应 raw_texts.chapter_seq)
    title                  TEXT,
    gist                   TEXT,             -- 一句话梗概 (LLM 分割产出)
    story_type             TEXT,             -- narrative|fable|debate|anecdote ...
    reality_status         TEXT,             -- historical|legendary|fictional
    vernacular_translation TEXT,
    source_citation        TEXT,             -- 人类可读出处, e.g. 史记·项羽本纪 (#7) 段 12–20
    status                 TEXT DEFAULT 'draft'  -- draft|reviewing|approved
);
CREATE INDEX IF NOT EXISTS idx_stories_book_chapter ON stories(book_id, chapter_seq);

CREATE TABLE IF NOT EXISTS story_segments (
    id            INTEGER PRIMARY KEY,
    story_id      INTEGER REFERENCES stories(id),
    raw_text_id   INTEGER REFERENCES raw_texts(id),
    segment_order INTEGER
);

-- 聚合层 (P5): 把分散在多篇(乃至多书)的同一事件融合成一条, 每源溯源存 event_sources.
-- slug = evt-<hash8> 内容寻址(成员来源段集合的稳定 hash, ADR-001/P-2): 不用 LLM 事件名,
--   故 LLM 命名浮动不产生重复事件、重跑稳定收敛(P-9); title 才存人类可读名(可编辑/可重名).
-- book_id: 单书事件 = 该书 id; NULL = 多书合并事件(P1 启用); 逐源归属仍在 event_sources.book_id.
CREATE TABLE IF NOT EXISTS events (
    id                 INTEGER PRIMARY KEY,
    slug               TEXT UNIQUE,      -- evt-<hash8>; 内容寻址稳定键 (ADR-001)
    title              TEXT,             -- 人类可读事件名 (LLM 生成, 可编辑, 可重名)
    kind               TEXT,             -- 事件 | 场景
    time_label         TEXT,             -- 如 "秦王政二十年"
    canonical_summary  TEXT,             -- LLM 融合多源生成的完整叙述 (要点标来源)
    book_id            INTEGER REFERENCES books(id),  -- 单书=该书; NULL=多书合并 (P1)
    status             TEXT DEFAULT 'draft'
);

CREATE TABLE IF NOT EXISTS event_sources (
    id           INTEGER PRIMARY KEY,
    event_id     INTEGER NOT NULL REFERENCES events(id),
    book_id      INTEGER REFERENCES books(id),
    chapter_seq  INTEGER,                -- 来源篇 (按 book+chapter_seq+段范围 精确溯源)
    para_start   INTEGER,
    para_end     INTEGER,
    role         TEXT,                   -- 主叙 | 详述 | 简述 | 评论 | 旁证
    excerpt      TEXT,                   -- 原文关键摘录
    note         TEXT
);
CREATE INDEX IF NOT EXISTS idx_event_sources_event ON event_sources(event_id);

-- 地点发现层 (功能B): 今地标识 + 古今映射(人审) + 故事↔古地名反查.
-- 命门(§4): 人审只门控 place_aliases.review_status; story_places 是语料确定性函数, 无人审.
CREATE TABLE IF NOT EXISTS places (
    id            INTEGER PRIMARY KEY,
    slug          TEXT UNIQUE NOT NULL,      -- 稳定键, e.g. suzhou/shaoxing/kaifeng
    modern_name   TEXT NOT NULL,             -- 规范今地名: 江苏苏州/浙江绍兴/河南开封
    admin_level   TEXT NOT NULL,             -- commandery(郡)|city(地级市,粒度封顶)|ancient_capital(古都)|district(区县,仅参考)
    province      TEXT,                      -- 省, 同名异地消歧
    authority_url TEXT,                      -- §2留缝: 外链权威源(谭其骧/CHGIS/聚典); 本系统永不生成沿革文本
    note          TEXT,                      -- 人工消歧备注 (禁写政区沿革)
    keywords      TEXT,                      -- 搜索词(空格分隔): 省/地级市/区县/今区名/别名, 支持多粒度查
    UNIQUE(modern_name, province)
);
CREATE INDEX IF NOT EXISTS idx_places_modern ON places(modern_name);

-- 古名原文→今地 的人审映射边 (命门表). 同一 ancient_name 可多行 = 多解(同名异地/治所迁移).
CREATE TABLE IF NOT EXISTS place_aliases (
    id               INTEGER PRIMARY KEY,
    ancient_name     TEXT NOT NULL,          -- 古地名原文照抄: 大梁/会稽
    place_id         INTEGER REFERENCES places(id),  -- 解析到的今地; 泛称/未定时可空
    confidence       REAL NOT NULL DEFAULT 0.0,      -- 0..1; UI 折成 高/中/存疑 三档 (不显百分比)
    is_vague         INTEGER NOT NULL DEFAULT 0,     -- 1=方向/区域泛称(江东/关中), 不可定位
    review_status    TEXT NOT NULL DEFAULT 'draft',  -- draft|auto_approved|needs_human|approved
                                                     -- 进对外查询 = approved|auto_approved; draft/needs_human/泛称 不进
    approved_by      TEXT,                    -- 谁批的: human(人审) | consensus(多LLM机审); 区分以便事后抽查
    uncertainty_note TEXT,                   -- 当前判断的存疑短语 (禁写时间线沿革)
    evidence         TEXT,                   -- 依据出处 (人填, 非 LLM 生成)
    UNIQUE(ancient_name, place_id)
);
CREATE INDEX IF NOT EXISTS idx_aliases_ancient ON place_aliases(ancient_name);
CREATE INDEX IF NOT EXISTS idx_aliases_place   ON place_aliases(place_id);

-- 故事提及古地名 的事实层 (②自动产 draft, 无人审). 桥接键 = ancient_name 文本(非 place_id),
-- 故一个故事的某古名天然展开到该名【全部】approved 今地候选(多解不被单条 link 吞).
-- story_id ON DELETE CASCADE: segment.write_stories 重切篇 DELETE stories 时随之清旧反查行.
CREATE TABLE IF NOT EXISTS story_places (
    id           INTEGER PRIMARY KEY,
    story_id     INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    ancient_name TEXT NOT NULL,             -- 故事原文中的古地名原文 (照抄)
    raw_text_id  INTEGER REFERENCES raw_texts(id),  -- 该古名出现的段 (段级溯源/同字异指消歧)
    is_vague     INTEGER NOT NULL DEFAULT 0,-- 泛称二道防线
    role         TEXT,                      -- origin|setting|destination|mention
    mention_ctx  TEXT,                      -- 古名所在原文上下文 (代码切片, 保证真子串)
    extractor    TEXT DEFAULT 'llm',        -- 产出标记: llm-<tier>
    UNIQUE(story_id, ancient_name, raw_text_id)
);
CREATE INDEX IF NOT EXISTS idx_storyplaces_story   ON story_places(story_id);
CREATE INDEX IF NOT EXISTS idx_storyplaces_ancient ON story_places(ancient_name);

-- 多LLM 共识审定的投票溯源 (功能B 机审). 每家判官每次审一行, 供人复核「为什么自动通过」:
-- gemini说苏州·claude说苏州·gpt说绍兴·deepseek弃权 这种全貌。聚合结论落 place_aliases,
-- 此表只存原始票, 不门控查询 (审计用). run_id 把一次 panel 的各家票串成一组。
CREATE TABLE IF NOT EXISTS alias_reviews (
    id              INTEGER PRIMARY KEY,
    run_id          TEXT,                    -- 一次 panel 审定的批次键 (串起同名各家票)
    ancient_name    TEXT NOT NULL,           -- 被审的古地名原文 (桥接键, 对齐全系统)
    mode            TEXT,                    -- propose(啃worklist) | check(校验既有映射)
    alias_id        INTEGER REFERENCES place_aliases(id),  -- check 模式: 被校验的映射边; propose 为空
    provider        TEXT,                    -- 判官 provider (gemini/openai/deepseek-flash/claude)
    model           TEXT,
    verdict         TEXT,                    -- check: correct|wrong|change|multi; propose 留空
    candidates_json TEXT,                    -- 该判官给的今地候选数组 (JSON 原样)
    confidence      REAL,                    -- 该判官首选候选的置信 (abstain/vague 时可空)
    is_vague        INTEGER DEFAULT 0,       -- 该判官判方向/区域泛称
    is_ambiguous    INTEGER DEFAULT 0,       -- 该判官判同名异地/治所迁移 → 应多解
    abstain         INTEGER DEFAULT 0,       -- 该判官弃权 (没把握, 宁可升级人审)
    era_note        TEXT,                    -- 判断所依据的朝代/语境
    reason          TEXT,                    -- 一句话依据 (禁写沿革时间线)
    latency_s       REAL,
    cost_usd        REAL,                    -- 该次调用估算成本 (订阅计费时为空)
    created_at      TEXT                     -- ISO 时间戳
);
CREATE INDEX IF NOT EXISTS idx_aliasreviews_ancient ON alias_reviews(ancient_name);
CREATE INDEX IF NOT EXISTS idx_aliasreviews_run     ON alias_reviews(run_id);

-- 实体层 (P5c): 人物画像. 地点用 places/* 专用系统, 这里只做 person.
-- 身份卡 (home_seq/aliases/era/anchors/disambig_note): 区分【同名异指】(淮阴侯韩信 vs
-- 韩王信). 本传定位=篇名含名/别名的篇(多命中=歧义); anchors=共现锚点(人/地/事);
-- disambig_note=易混人物负锚点. 抽取按身份而非字符串匹配 (见 extract/entities.py).
CREATE TABLE IF NOT EXISTS entities (
    id            INTEGER PRIMARY KEY,
    slug          TEXT UNIQUE,            -- per-<hash8>; 身份派生稳定键 (name+era+本传, ADR-002/P-2)
    name          TEXT NOT NULL,
    kind          TEXT DEFAULT 'person',  -- person (地点见 places)
    profile       TEXT,                   -- LLM 综合人物画像 (主传事迹 + 他者评价, 标来源)
    home_seq      INTEGER,                -- 本传篇序 (raw_texts.chapter_seq); 歧义时为本人那篇
    home_book_id  INTEGER REFERENCES books(id),  -- 本传所属书 (多书消歧维度, ADR-002); 与 home_seq 合成本传定位
    aliases       TEXT,                   -- 别名/称谓 (顿号分隔), e.g. 淮阴侯、淮阴
    era           TEXT,                   -- 时代, e.g. 西汉
    anchors       TEXT,                   -- 共现锚点 (顿号分隔人/地/事), 消歧正信号
    disambig_note TEXT,                   -- 易混同名人物及其负锚点 (LLM judge 用)
    status        TEXT DEFAULT 'draft'
);

-- 引用层 (P1 实体消解地基, ADR-006): 每个叙事故事抽出的【具体专名】(人/地/封号/独特事件名),
-- 是语料确定性函数(随分割增量, 无人审, 同 story_places). 供【共享实体守卫】(剔同纪元误并:
-- 候选事件簇须共享≥1 具体实体, 只共享通用词的拆开) 与将来【事件 group-by】(共享实体+纪元成组,
-- 拆 embedding O(n²) 墙) 用. story_id ON DELETE CASCADE: 重切篇随之清.
CREATE TABLE IF NOT EXISTS story_mentions (
    id           INTEGER PRIMARY KEY,
    story_id     INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    surface      TEXT NOT NULL,            -- 专名原文照抄 (子串校验, 防 LLM 脑补)
    kind         TEXT,                     -- person|place|appellation|event|other
    raw_text_id  INTEGER REFERENCES raw_texts(id),  -- 该专名出现的段 (溯源)
    mention_ctx  TEXT,                     -- 上下文切片 (代码切, 真子串)
    extractor    TEXT DEFAULT 'llm',       -- 产出标记: llm:<provider>
    UNIQUE(story_id, surface)
);
CREATE INDEX IF NOT EXISTS idx_story_mentions_story   ON story_mentions(story_id);
CREATE INDEX IF NOT EXISTS idx_story_mentions_surface ON story_mentions(surface);

-- P1.5 人物自动发现 (仿 story_places 工作清单): 候选由 story_mentions(kind=person) 跨篇频次
-- + 本传篇名定位 (genre 注册表判本传类) 聚出; 人审勾选 pending→approved/rejected, 批量
-- build_person 后置 built. 机器刷新 (discover_persons) 只更新计数/本传列, 不覆盖人审 status
-- (P-3/P-9). 把"手敲人名"变"审候选", 根治人物层覆盖率随书量稀释.
CREATE TABLE IF NOT EXISTS person_candidates (
    id           INTEGER PRIMARY KEY,
    name         TEXT UNIQUE,              -- 候选人名 (story_mentions.surface 原文)
    n_books      INTEGER DEFAULT 0,        -- 出现书数
    n_chapters   INTEGER DEFAULT 0,        -- 出现篇数 (跨篇度 = 聚合价值)
    n_stories    INTEGER DEFAULT 0,        -- 出现故事数
    home_book_id INTEGER REFERENCES books(id),  -- 本传所在书 (篇名确定性匹配, 强信号)
    home_chapter TEXT,                     -- 本传篇名
    status       TEXT DEFAULT 'pending',   -- pending|approved|rejected|built
    note         TEXT,                     -- 机器提示 (同名异指/高频泛称), 人审参考
    reviewed_by  TEXT                      -- 评审痕迹: human | rules:<规则> | llm:<provider>
);
CREATE INDEX IF NOT EXISTS idx_person_candidates_status ON person_candidates(status);

-- 聚类确认缓存 (B 项尾巴): cluster_stories 的逐批 LLM 确认是 fuse 增量化后剩余的 O(全库)/轮
-- 成本, 且非确定 (同输入不同轮可得不同簇 → 事件 slug 漂移 → 无谓重 fuse + prune churn).
-- 按【批输入内容】寻址: batch_key = sha1(确认 system prompt + 批文本). 同批内容 → 复用上轮
-- 判定 (零调用且判定稳定); 故事重切/标题变/prompt 改 → 键变 → 自然失效重判. 解析失败不入缓存.
CREATE TABLE IF NOT EXISTS cluster_confirm_cache (
    id         INTEGER PRIMARY KEY,
    batch_key  TEXT UNIQUE,            -- sha1(确认 prompt + 批输入文本), 内容寻址
    clusters   TEXT,                   -- 判定结果 JSON (clusters 数组; 空数组=合法负结果)
    provider   TEXT,                   -- 产出档 (溯源, 不参与键: 换档不重判, 刷新用 --no-confirm-cache)
    created_at TEXT DEFAULT (datetime('now'))
);

-- 人物身份种子 (ADR-002 尾巴, 根治 P-9 漂移): slug 列是身份本体——build_person 首建后自动
-- pin (INSERT OR IGNORE), 此后无论 LLM 锚点浮动/home 解析翻转/era 漂移, 同名重建恒得同 slug.
-- home 两列 pin identity_card 的本传解析 (确定性, 跳过跨书锚点评分); 人工纠错只改 home
-- 不动 slug → 解析可改善、身份不漂移. 已有种子神圣不覆盖 (人审/既有身份优先).
CREATE TABLE IF NOT EXISTS disambiguation_seeds (
    id             INTEGER PRIMARY KEY,
    name           TEXT UNIQUE,           -- 人物名 (与 build_person 输入一致)
    slug           TEXT,                  -- pin 的身份键 (per-<hash8>, 一经写入不再漂移)
    home_book_slug TEXT,                  -- pin 的本传所在书 (slug, 跨 DB 稳定文本键)
    home_chapter   TEXT,                  -- pin 的本传篇名
    note           TEXT,                  -- 人审备注 (为何如此 pin / 纠错原因)
    created_by     TEXT DEFAULT 'machine' -- machine|human
);

-- 人物提及/评价的多源溯源边. aspect=评价 即他者评论(如淮阴侯列传韩信评项羽"妇人之仁").
CREATE TABLE IF NOT EXISTS entity_mentions (
    id          INTEGER PRIMARY KEY,
    entity_id   INTEGER NOT NULL REFERENCES entities(id),
    book_id     INTEGER REFERENCES books(id),
    chapter_seq INTEGER,
    para_start  INTEGER,
    para_end    INTEGER,
    aspect      TEXT,                  -- 主传 | 评价 | 事迹 | 外貌 | ...
    excerpt     TEXT,
    note        TEXT
);
CREATE INDEX IF NOT EXISTS idx_entity_mentions_entity ON entity_mentions(entity_id);
"""

# 品质层 (功能C, 北极星消费层): 受控词表 (闭集白名单, 仿 story_mentions._GENERIC 反向) +
# 故事/事件/人物评价 → 品质 的人审映射边. 映射是【判断】非【事实】, 故对应 places 的
# place_aliases(人审门控)那一半, 非 story_places(无人审事实)那一半: 默认 draft, 只 approved 进查询.
# polarity 在词表(品质内禀, 不每条重判); 证据双轨 = rationale(允许情节归纳) + evidence_quote
# (代码从真实出处段切片, 弱子串校验防脑补). 详见 docs/QUALITY_LAYER.md.
_QUALITY_DDL = """
CREATE TABLE IF NOT EXISTS qualities (
    id           INTEGER PRIMARY KEY,
    slug         TEXT UNIQUE NOT NULL,      -- 稳定英文键: ren/yong/gangbi
    name         TEXT NOT NULL,             -- 规范中文名: 仁/勇/刚愎自用
    polarity     TEXT NOT NULL,             -- positive|negative|neutral (品质内禀, 非每条映射重判)
    category     TEXT NOT NULL,             -- 德性|才能|性情|为政|气度|处世 (D1: 才能轴与德性轴并列)
    axis         TEXT,                      -- 价值轴分组 (可选): 勇气/廉俭/谦傲
    antonym_slug TEXT,                      -- 反义品质 slug (可选, 正反对照查)
    aliases      TEXT,                      -- 别名/触发词 (顿号分隔), 供输入归一 + 抽取
    gloss        TEXT,                      -- 古义释义, 锚定 LLM 判断标准 (防同名歧义 勇≠鲁莽)
    corpus_tier  TEXT,                      -- strong|mid|weak (他者评价语料支撑档; weak 证据门槛单设)
    status       TEXT NOT NULL DEFAULT 'active'  -- active|deprecated (弃用不删, 防孤儿映射)
);
CREATE INDEX IF NOT EXISTS idx_qualities_category ON qualities(category);
CREATE INDEX IF NOT EXISTS idx_qualities_polarity ON qualities(polarity);

-- 事件 → 品质 (主表, 高质量成品载体). UNIQUE(event_id, quality_id) = 幂等身份键 (一事件多品质=多行).
CREATE TABLE IF NOT EXISTS event_qualities (
    id             INTEGER PRIMARY KEY,
    event_id       INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    quality_id     INTEGER NOT NULL REFERENCES qualities(id),
    rationale      TEXT NOT NULL,           -- LLM 归纳理由"为何该事件体现该品质"(落库可审, 防脑补核心)
    evidence_src   INTEGER REFERENCES event_sources(id),  -- 证据指向的来源行 (复用既有溯源边)
    evidence_quote TEXT,                     -- 原文摘录 (代码从来源段范围切, 保证真子串)
    confidence     REAL NOT NULL DEFAULT 0.0,-- 0..1; UI 折 高/中/存疑 三档
    review_status  TEXT NOT NULL DEFAULT 'draft',  -- draft|reviewing|approved|rejected; 进查询=approved
    reviewed_by    TEXT,                     -- human|llm:<provider>|consensus (机器永不覆盖 human)
    note           TEXT,
    extractor      TEXT DEFAULT 'llm',
    UNIQUE(event_id, quality_id)
);
CREATE INDEX IF NOT EXISTS idx_eq_event   ON event_qualities(event_id);
CREATE INDEX IF NOT EXISTS idx_eq_quality ON event_qualities(quality_id);
CREATE INDEX IF NOT EXISTS idx_eq_status  ON event_qualities(review_status);

-- 人物评价 → 品质 (冷启动金矿: 4465 条现成他者评价, aspect=评价, 古人原判直接标品质).
-- 证据切自 entity_mentions.excerpt (已是文言原评价句). 注: 挂 mention_id, 画像重建换 id 会
-- CASCADE 清 (MVP 接受; v2 若要持久迁稳定身份键 entity.slug+excerpt hash, 见 QUALITY_LAYER §9).
CREATE TABLE IF NOT EXISTS mention_qualities (
    id             INTEGER PRIMARY KEY,
    mention_id     INTEGER NOT NULL REFERENCES entity_mentions(id) ON DELETE CASCADE,
    quality_id     INTEGER NOT NULL REFERENCES qualities(id),
    rationale      TEXT NOT NULL,
    evidence_quote TEXT,                     -- 切自 entity_mentions.excerpt
    confidence     REAL NOT NULL DEFAULT 0.0,
    review_status  TEXT NOT NULL DEFAULT 'draft',
    reviewed_by    TEXT,
    note           TEXT,
    extractor      TEXT DEFAULT 'llm',
    UNIQUE(mention_id, quality_id)
);
CREATE INDEX IF NOT EXISTS idx_mq_mention ON mention_qualities(mention_id);
CREATE INDEX IF NOT EXISTS idx_mq_quality ON mention_qualities(quality_id);
CREATE INDEX IF NOT EXISTS idx_mq_status  ON mention_qualities(review_status);

-- 故事 → 品质 (长尾, v2; 建表先就位). story_id ON DELETE CASCADE: 重切篇随之清 (同 story_places).
CREATE TABLE IF NOT EXISTS story_qualities (
    id             INTEGER PRIMARY KEY,
    story_id       INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    quality_id     INTEGER NOT NULL REFERENCES qualities(id),
    rationale      TEXT NOT NULL,
    raw_text_id    INTEGER REFERENCES raw_texts(id),  -- 支撑情节所在段 (段级溯源)
    evidence_quote TEXT,                     -- 原文摘录 (代码切片自该 story 真文本)
    confidence     REAL NOT NULL DEFAULT 0.0,
    review_status  TEXT NOT NULL DEFAULT 'draft',
    reviewed_by    TEXT,
    note           TEXT,
    extractor      TEXT DEFAULT 'llm',
    UNIQUE(story_id, quality_id)
);
CREATE INDEX IF NOT EXISTS idx_sq_story   ON story_qualities(story_id);
CREATE INDEX IF NOT EXISTS idx_sq_quality ON story_qualities(quality_id);
CREATE INDEX IF NOT EXISTS idx_sq_status  ON story_qualities(review_status);
"""
SCHEMA += _QUALITY_DDL


# 历史加列 (版本化之前的迁移). CREATE TABLE IF NOT EXISTS 不会给【已存在】的旧表加列,
# 故对既有库幂等补这些列. 【新增结构性变更请走下面的版本化迁移 _VERSIONED (P-5), 勿再堆这里.】
_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "stories": [
        ("chapter_seq", "INTEGER"),
        ("gist", "TEXT"),
        ("source_citation", "TEXT"),
    ],
    "places": [
        ("keywords", "TEXT"),       # 多粒度搜索词(省/市/区县); 既有库幂等加列
    ],
    "place_aliases": [
        ("approved_by", "TEXT"),    # human|consensus; 区分人审/机审, 既有库幂等加列 (功能B机审)
    ],
    "entities": [
        ("home_seq", "INTEGER"),    # 身份卡: 本传篇序, 既有库幂等加列 (同名异指消歧)
        ("aliases", "TEXT"),        # 别名/称谓 (顿号分隔)
        ("era", "TEXT"),            # 时代
        ("anchors", "TEXT"),        # 共现锚点 (顿号分隔)
        ("disambig_note", "TEXT"),  # 易混同名人物负锚点
    ],
}


def _add_column(conn: sqlite3.Connection, table: str, col: str, decl: str) -> None:
    """Idempotent ALTER ADD COLUMN (SQLite errors if a column is added twice)."""
    existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if col not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def _mig_v1(conn: sqlite3.Connection) -> None:
    """ADR-001/002: events 归属书 (NULL=多书合并占位) + entities 本传所属书 (多书消歧维度)."""
    _add_column(conn, "events", "book_id", "INTEGER")
    _add_column(conn, "entities", "home_book_id", "INTEGER")


def _mig_v2(conn: sqlite3.Connection) -> None:
    """web 浏览热路径索引: 首页/篇页按 (book_id, chapter_seq) 查 stories, 旧库全表扫."""
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stories_book_chapter "
                 "ON stories(book_id, chapter_seq)")


def _mig_v3(conn: sqlite3.Connection) -> None:
    """person_candidates.reviewed_by: 候选评审痕迹 (human|rules:*|llm:*), 机审/人审可区分、
    机器永不覆盖 human (P1.5 机器评审)."""
    _add_column(conn, "person_candidates", "reviewed_by", "TEXT")


def _mig_v4(conn: sqlite3.Connection) -> None:
    """品质层 (功能C): qualities 受控词表 + event/mention/story_qualities 人审映射边.
    DDL 单一来源 _QUALITY_DDL (新库经 SCHEMA 建, 旧库经此补; IF NOT EXISTS 幂等)."""
    conn.executescript(_QUALITY_DDL)


# 版本化迁移 (P-5): 按 PRAGMA user_version 顺序应用未跑过的步骤; 每步幂等、可执行任意 SQL
# (含 SQLite 改约束的「建新表→拷数据→换名」套路). 加新步骤: 追加 (递增版本号, 描述, 函数).
_VERSIONED = [
    (1, "events.book_id + entities.home_book_id (ADR-001/002)", _mig_v1),
    (2, "idx_stories_book_chapter (web 浏览热路径)", _mig_v2),
    (3, "person_candidates.reviewed_by (机审/人审痕迹)", _mig_v3),
    (4, "品质层 qualities + event/mention/story_qualities (功能C)", _mig_v4),
]


def _migrate(conn: sqlite3.Connection) -> None:
    # 1) 历史加列 (向后兼容既有库)
    for table, cols in _MIGRATIONS.items():
        for name, decl in cols:
            _add_column(conn, table, name, decl)
    # 2) 版本化迁移 (P-5)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for ver, _desc, fn in _VERSIONED:
        if version < ver:
            fn(conn)
            conn.execute(f"PRAGMA user_version = {ver}")   # ver 为内部 int 常量, 无注入风险
    conn.commit()


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30.0)
    conn.execute("PRAGMA foreign_keys = ON")
    # 并发: WAL 让读不阻塞写、写不阻塞读 (多书并行 ingest/aggregate 不再 database is locked);
    # busy_timeout 写锁竞争时等待而非立即抛错 (与 sqlite3 connect timeout 互补, 覆盖纯 SQLite 层)。
    # WAL 是库级持久属性, 内存库 (:memory:) 不支持, 故仅对落盘库设置。
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def load_book(conn: sqlite3.Connection, book: Book) -> dict:
    """Idempotent: upserts the book and replaces its raw_texts."""
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO books(slug, title, author, dynasty, genre, license)
           VALUES(?,?,?,?,?,?)
           ON CONFLICT(slug) DO UPDATE SET
               title=excluded.title, author=excluded.author,
               dynasty=excluded.dynasty, genre=excluded.genre,
               license=excluded.license""",
        (book.slug, book.title, book.author, book.dynasty, book.genre, book.license),
    )
    book_id = cur.execute("SELECT id FROM books WHERE slug=?", (book.slug,)).fetchone()[0]
    cur.execute("DELETE FROM raw_texts WHERE book_id=?", (book_id,))

    n_para = n_trans = 0
    for seq, ch in enumerate(book.chapters, start=1):
        for p in ch.paragraphs:
            cur.execute(
                """INSERT INTO raw_texts
                   (book_id, category, chapter_seq, chapter, paragraph_no,
                    original, vernacular, translation_source)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (book_id, ch.category, seq, ch.title, p.paragraph_no,
                 p.original, p.vernacular, p.translation_source),
            )
            n_para += 1
            if p.vernacular:
                n_trans += 1
    conn.commit()
    return {
        "book_id": book_id,
        "chapters": len(book.chapters),
        "paragraphs": n_para,
        "with_translation": n_trans,
    }
