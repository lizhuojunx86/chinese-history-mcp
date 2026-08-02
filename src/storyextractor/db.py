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
-- kind 取值 (ADR-009 起扩至三种): 事件 | 场景 | 评价(他者评价, 见 docs/EVENT_DIMENSIONS.md §4).
-- time_label 保留但语义降级为展示缓存 (权威时间来源改为 event_time_refs→time_points, ADR-009);
-- dimensions_reviewed: 0=尚未针对 time/place/person 三维做过确认式复核, 1=已确认(即使三张
-- event_*_refs 一行都没有, 也是"考证到底查无可考"的正结论, 不是抽取遗漏 — 直接解决现状
-- time_label 100% NULL 时"没抽"和"抽了发现真不可考"无法区分的问题).
-- reality_status: 复用 stories 已有的三态词表(historical|legendary|fictional), 不新造概念;
-- 聚合时可从贡献 stories 取最谨慎值, 允许人工改 (大禹治水例, 见 EVENT_DIMENSIONS §10).
CREATE TABLE IF NOT EXISTS events (
    id                   INTEGER PRIMARY KEY,
    slug                 TEXT UNIQUE,      -- evt-<hash8>; 内容寻址稳定键 (ADR-001)
    title                TEXT,             -- 人类可读事件名 (LLM 生成, 可编辑, 可重名)
    kind                 TEXT,             -- 事件 | 场景 | 评价
    time_label           TEXT,             -- 如 "秦王政二十年" (展示缓存, 见上)
    canonical_summary    TEXT,             -- LLM 融合多源生成的完整叙述 (要点标来源)
    book_id              INTEGER REFERENCES books(id),  -- 单书=该书; NULL=多书合并 (P1)
    status               TEXT DEFAULT 'draft',
    dimensions_reviewed  INTEGER NOT NULL DEFAULT 0,
    reality_status       TEXT              -- historical|legendary|fictional (同 stories.reality_status)
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
-- chgis_pt_id (ADR-009): 可空, 对齐哈佛 CHGIS 地名点位 id — CBDB 与识典古籍两条外部生态都
-- 用 CHGIS 做地点对齐, 加这一列零成本换未来两条互操作路径, 不需要现在做全量映射.
-- place_kind (ADR-009): 与 admin_level 正交 —— admin_level 只在 place_kind='admin_unit' 时
-- 有意义。"长江流域"这类自然地理单元、"江东/关中"这类历史文化区域不是行政层级, 硬塞进
-- admin_level 是范畴错误; 借鉴 Wikidata 把"属于行政区"(P131)和"属于自然地理实体"(P706)
-- 拆成正交属性的做法, 见 EVENT_DIMENSIONS §11、place_hierarchy 表.
CREATE TABLE IF NOT EXISTS places (
    id            INTEGER PRIMARY KEY,
    slug          TEXT UNIQUE NOT NULL,      -- 稳定键, e.g. suzhou/shaoxing/kaifeng
    modern_name   TEXT NOT NULL,             -- 规范今地名: 江苏苏州/浙江绍兴/河南开封
    admin_level   TEXT NOT NULL,             -- commandery(郡)|city(地级市,粒度封顶)|ancient_capital(古都)|district(区县,仅参考)|n/a(place_kind非admin_unit时不适用)
    province      TEXT,                      -- 省, 同名异地消歧
    authority_url TEXT,                      -- §2留缝: 外链权威源(谭其骧/CHGIS/聚典); 本系统永不生成沿革文本
    note          TEXT,                      -- 人工消歧备注 (禁写政区沿革)
    keywords      TEXT,                      -- 搜索词(空格分隔): 省/地级市/区县/今区名/别名, 支持多粒度查
    chgis_pt_id   INTEGER,                   -- CHGIS 地名点位 id (可空, 外部对齐用, 非本系统权威)
    place_kind    TEXT NOT NULL DEFAULT 'admin_unit',  -- admin_unit|physical_feature|cultural_region
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
-- reality_status (ADR-009): 复用 stories.reality_status 同一词表, 表达"这个人物本身的历史
-- 真实性存疑"(如大禹), 与其生卒年是否可考(entity_time_refs)是两个独立判断.
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
    status        TEXT DEFAULT 'draft',
    reality_status TEXT                   -- historical|legendary|fictional (同 stories.reality_status)
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

-- 事件 → 品质 (主表, 高质量成品载体). UNIQUE(event_id, quality_id, subject_entity_id) = 幂等
-- 身份键——同一事件可以有多条不同主体的品质判断 (如一个评价事件同时挂"甲评乙有仁"和"乙同场
-- 反驳丙无信", 二者 quality_id 相同、subject_entity_id 不同, 不该互相冲突). subject_entity_id
-- 为 NULL 的事件级判断 (品质由 rationale 隐含指向哪个参与者, 不变) 靠写入端显式 SELECT 判重,
-- 不指望 UNIQUE 对 NULL 列生效 (SQLite 多个 NULL 互不相等, 同 time_aliases 的既有教训)。
-- kind='评价' 的事件 MUST 填 subject_entity_id (见 EVENT_DIMENSIONS §4)。evidence_src/
-- evidence_quote 两列保留 (向后兼容, 降级为首条证据快照); 新证据一律走 quality_evidence。
CREATE TABLE IF NOT EXISTS event_qualities (
    id                 INTEGER PRIMARY KEY,
    event_id           INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    quality_id         INTEGER NOT NULL REFERENCES qualities(id),
    rationale          TEXT NOT NULL,           -- LLM 归纳理由"为何该事件体现该品质"(落库可审, 防脑补核心)
    evidence_src       INTEGER REFERENCES event_sources(id),  -- 证据指向的来源行 (复用既有溯源边)
    evidence_quote     TEXT,                     -- 原文摘录 (代码从来源段范围切, 保证真子串)
    confidence         REAL NOT NULL DEFAULT 0.0,-- 0..1; UI 折 高/中/存疑 三档
    review_status      TEXT NOT NULL DEFAULT 'draft',  -- draft|reviewing|approved|rejected; 进查询=approved
    reviewed_by        TEXT,                     -- human|llm:<provider>|consensus (机器永不覆盖 human)
    note               TEXT,
    extractor          TEXT DEFAULT 'llm',
    subject_entity_id  INTEGER REFERENCES entities(id),  -- 该判断具体归属的人物 (可空, 见上)
    UNIQUE(event_id, quality_id, subject_entity_id)
);
CREATE INDEX IF NOT EXISTS idx_eq_event   ON event_qualities(event_id);
CREATE INDEX IF NOT EXISTS idx_eq_quality ON event_qualities(quality_id);
CREATE INDEX IF NOT EXISTS idx_eq_status  ON event_qualities(review_status);
-- 局部唯一索引 idx_eq_no_subject (event_id, quality_id) WHERE subject_entity_id IS NULL 不放
-- 在这里, 只在 _mig_v8 里建——旧库这时 event_qualities 可能还没有 subject_entity_id 列
-- (要等 _mig_v6 的 _add_column 补), executescript(SCHEMA) 在任何迁移前无条件跑一遍, 对着
-- 不存在的列建索引会直接报错(IF NOT EXISTS 只挡"同名索引已存在", 挡不住"引用列不存在"),
-- 已用旧库连接实测踩到过这个顺序坑。新库走 SCHEMA 一次建表已含该列, 之后 _mig_v8 仍会照常
-- 建索引(幂等), 两条路径最终收敛到同一状态, 不需要在这里重复建。

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


# 时间/地点/人物一等边 + 事件递归复合 (ADR-009): 时间获得与 places 对等的两层结构
# (time_points 规范层 + time_aliases 人审映射层); 事件(含子事件/评价事件) → 时间/地点/人物
# 三张事实层 junction 表 (event_*_refs, 镜像 story_places/story_mentions, 无独立人审门控,
# 判断门控在 events.status 本身); 品质证据从单条 evidence_src 升级为 quality_evidence 1:N
# 聚合 (品质"像事件"落地①); event_composition 用带 scheme 标签的边表表达事件的多维度/多
# 颗粒度切分 (七国之乱可同时按战场/阶段/阵营切, 互不冲突), 取代单一 parent_event_id 自引用
# 列; entity_relations 是应用层(人物关系图谱)驱动出的新维度, 受控词表 relation_types 借鉴
# CBDB KINSHIP_CODES/ASSOC_CODES 的互逆配对惯例, 但从小词表起步 (仿 qualities 当年 55 条
# 起步, 非照搬 CBDB 500+ 条量级). 纯加法: 不删不改任何既有表/列. 详见 docs/EVENT_DIMENSIONS.md.
# month: 农历原文月序 1-12; 或季编码 21/22/23/24 = 春/夏/秋/冬 (借 EDTF Level 1 惯例, 用月位
# 表达季, 不另开一列). day: 农历原文日序 1-30. 两者均【不做阳历换算】——阴阳合历精确换算
# 需要历朔表级别的工程(如陈垣《二十史朔闰表》), 硬算等于伪造精度, 明确排除在本系统范围外;
# 真实原文形式(含干支纪日等)保真存 reign_label/label, month/day 只是可比较排序的粗数值.
# range_kind 区分两种性质不同的区间: duration(真实延续跨度, 如在位期间) 与 uncertain_point
# (未知具体值但有可信边界, 如生卒年区间/事件年代区间) —— 时间轴 UI 该画成柱状还是模糊云,
# 靠这一列区分, 不能靠 start_year!=end_year 一概而论. 呼应 CIDOC-CRM E52 Time-Span 用
# "at some time within"表达模糊时间的做法(P82), 不是本系统独创.
# 去重靠 slug (内容寻址, P-2/P-9, 同 events/entities), 不设 raw 列的 compound UNIQUE ——
# SQLite 对含 NULL 列的 UNIQUE 本来就不生效(多个 NULL 互不相等, 年份/月日大量为 NULL 的场景
# 下形同虚设), reign_label 又是展示字段不该参与身份判定(同一年可能有多种纪年表达方式).
_TPE_DDL = """
CREATE TABLE IF NOT EXISTS time_points (
    id          INTEGER PRIMARY KEY,
    slug        TEXT UNIQUE NOT NULL,   -- tp-<hash8>, 内容寻址 (P-2) —— 真正的去重/幂等机制
    label       TEXT NOT NULL,          -- 人类可读规范标签: "汉景帝三年（前一五四）"
    start_year  INTEGER,                -- 公元前为负; 与 end_year 同 NULL = 只有纪元桶无数字锚点 (合法状态)
    end_year    INTEGER,
    month       INTEGER,                -- 1-12 农历月序, 或 21-24 季编码 (见上); 可空
    day         INTEGER,                -- 1-30 农历日序; 可空
    precision   TEXT NOT NULL DEFAULT 'unknown',  -- year|season|month|day|reign_period|era|unknown
    range_kind  TEXT NOT NULL DEFAULT 'exact',    -- exact|duration|uncertain_point|era_bucket (见上)
    era         TEXT,                   -- 粗纪元桶, 词表对齐 era_anchors.json 的 era 字段
    reign_label TEXT,                   -- 原始纪年/干支形式保真: "景帝三年" (展示用, 不参与身份寻址)
    note        TEXT
);
CREATE INDEX IF NOT EXISTS idx_time_points_range ON time_points(start_year, end_year);

-- 原文纪年表达 → 规范时间点 的人审映射边 (镜像 place_aliases, 同一四态 review_status).
-- scope_book_id/scope_chapter_seq: 裸相对纪年("十年"/"三月")脱离书/篇上下文无法解析, 必须
-- 限定作用域才能消歧; 表达自解释("秦王政二十年")时两列皆 NULL, 全局桥接 (同 place_aliases
-- 的 ancient_name 全局语义). UNIQUE 四列联合 = 幂等 upsert 目标 (P-9).
CREATE TABLE IF NOT EXISTS time_aliases (
    id                INTEGER PRIMARY KEY,
    raw_text          TEXT NOT NULL,
    scope_book_id     INTEGER REFERENCES books(id),
    scope_chapter_seq INTEGER,
    time_point_id     INTEGER REFERENCES time_points(id),  -- 未定时可空 (同 place_aliases.place_id)
    confidence        REAL NOT NULL DEFAULT 0.0,
    is_vague          INTEGER NOT NULL DEFAULT 0,
    review_status     TEXT NOT NULL DEFAULT 'draft',  -- draft|auto_approved|needs_human|approved
    approved_by       TEXT,                    -- human | consensus | seed:cbdb_nianhao
    uncertainty_note  TEXT,
    evidence          TEXT,
    UNIQUE(raw_text, scope_book_id, scope_chapter_seq, time_point_id)
);
CREATE INDEX IF NOT EXISTS idx_time_aliases_text  ON time_aliases(raw_text);
CREATE INDEX IF NOT EXISTS idx_time_aliases_point ON time_aliases(time_point_id);

-- 时间维度的论证留痕表 (镜像 alias_reviews). 置信度不是一个可以被静默覆盖的数字——考古新
-- 发现/断代工程新结论到来时, 是新增一条论证记录(reason 里可写依据), 而不是 UPDATE 掉旧值;
-- time_aliases 的"当前最佳值"随之更新, 但历史论证全部留痕可查 (呼应"随考古发现推进置信度
-- 应变化, 且变化要可追溯"的要求, 见 EVENT_DIMENSIONS §9).
CREATE TABLE IF NOT EXISTS time_alias_reviews (
    id              INTEGER PRIMARY KEY,
    run_id          TEXT,
    raw_text        TEXT NOT NULL,
    mode            TEXT,                    -- propose | check
    alias_id        INTEGER REFERENCES time_aliases(id),
    provider        TEXT,
    model           TEXT,
    verdict         TEXT,
    candidates_json TEXT,
    confidence      REAL,
    reason          TEXT,                    -- 一句话依据, 可含"据XX年考古发现/断代工程结论"
    latency_s       REAL,
    cost_usd        REAL,
    created_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_timealiasreviews_text ON time_alias_reviews(raw_text);
CREATE INDEX IF NOT EXISTS idx_timealiasreviews_run  ON time_alias_reviews(run_id);

-- 事件(含子事件/评价事件) → 时间引用 的事实层. 回退为纯文本桥接 (与 event_place_refs 同款
-- 哲学), 不在写入时收敛成单一 time_point_id —— 早期草稿这样做过, 是用错误工具修复裸纪年
-- ("十年")的 scope 歧义 bug, 真正的修复应是查询时用 INNER JOIN + scope 优先级过滤(篇级>
-- 书级>全局), 不是提前收敛。这样"多候选并存"的合法灰度状态(如大禹治水的年代仍有学术争议)
-- 才不会被误伤成假精确: 若只有一条 approved 候选, 查询自然只返回一行; 若确有争议, 人审可以
-- 同时批准多条 time_aliases, 查询自然展开全部候选, 各自带自己的 confidence. 详见 EVENT_DIMENSIONS §9。
CREATE TABLE IF NOT EXISTS event_time_refs (
    id           INTEGER PRIMARY KEY,
    event_id     INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    raw_text     TEXT NOT NULL,         -- 原文纪年表达, 或学术推断场景的占位描述("禹之时")
    role         TEXT,                  -- point|start|end|mentioned
    evidence_src INTEGER REFERENCES event_sources(id),
    extractor    TEXT DEFAULT 'llm',
    UNIQUE(event_id, raw_text)
);
CREATE INDEX IF NOT EXISTS idx_evttime_event ON event_time_refs(event_id);
CREATE INDEX IF NOT EXISTS idx_evttime_text  ON event_time_refs(raw_text);

-- 人物 → 时间引用 (生卒/活跃年代) 的事实层. 与 event_time_refs 同一套桥接+查询模式, 复用
-- 同一套 time_points/time_aliases 基础设施, 不另起炉灶 (呼应 CBDB "同一套日期词表复用到
-- 所有实体类型"的做法). "生于1025-1030年"就是一条 role='birth' 的引用, 桥接到一条
-- time_aliases, 解析到一个 range_kind='uncertain_point' 的 time_point.
CREATE TABLE IF NOT EXISTS entity_time_refs (
    id           INTEGER PRIMARY KEY,
    entity_id    INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    raw_text     TEXT NOT NULL,
    role         TEXT,                  -- birth|death|floruit|mentioned
    evidence_src INTEGER REFERENCES event_sources(id),
    extractor    TEXT DEFAULT 'llm',
    UNIQUE(entity_id, raw_text, role)
);
CREATE INDEX IF NOT EXISTS idx_enttime_entity ON entity_time_refs(entity_id);
CREATE INDEX IF NOT EXISTS idx_enttime_text   ON entity_time_refs(raw_text);

-- 事件(含子事件/评价事件) → 古地名 的事实层. ancient_name 文本桥接键, 与 place_aliases
-- 是同一套哲学 (同名异地/多解不提前消歧, 查询时展开全部 approved 候选) —— 与
-- event_time_refs 的写入时解析刻意不同: 地点的一对多是合理歧义 (同一古名真的可能对应
-- 多个今地), 时间的裸纪年歧义靠 scope 就能唯一定, 不需要延迟消歧.
CREATE TABLE IF NOT EXISTS event_place_refs (
    id           INTEGER PRIMARY KEY,
    event_id     INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    ancient_name TEXT NOT NULL,
    role         TEXT,                   -- origin|setting|destination|mention
    evidence_src INTEGER REFERENCES event_sources(id),
    mention_ctx  TEXT,                   -- 代码切片, 真子串 (防脑补)
    extractor    TEXT DEFAULT 'llm',
    UNIQUE(event_id, ancient_name)
);
CREATE INDEX IF NOT EXISTS idx_evtplace_event   ON event_place_refs(event_id);
CREATE INDEX IF NOT EXISTS idx_evtplace_ancient ON event_place_refs(ancient_name);

-- 地点之间的静态归属关系 (与 event_place_refs/place_aliases 是两件事: 后两者管"某次判断
-- 该给多少置信度", 这张表管"地点结构本身怎么互相包含"). 用带 relation 标签的边表表达多套
-- 正交的归属体系, 不用单一层级——借鉴 Wikidata 把"属于行政区"(P131)和"属于自然地理实体"
-- (P706/P206)拆成独立属性、CBDB ADDR_BELONGS_DATA 把行政隶属存成带起止年的边(而非写死在
-- 地点行上的固定父级)两处做法。一个地点可以同时有多条边: 苏州→(administrative)→江苏省、
-- 苏州→(physical)→长江流域、苏州→(cultural)→江南, 互不冲突; 隶属关系随朝代改易/河道变迁
-- 而变时, 加一条新 start_year/end_year 区间的边即可, 不用改写整棵树。不做坐标/GIS 几何计算
-- (流域归属靠人工/LLM 维护 + 人审, 同其余映射边, 不引入空间扩展违反 P-10)。详见
-- docs/EVENT_DIMENSIONS.md §11。
CREATE TABLE IF NOT EXISTS place_hierarchy (
    id            INTEGER PRIMARY KEY,
    place_id      INTEGER NOT NULL REFERENCES places(id),
    parent_id     INTEGER NOT NULL REFERENCES places(id),
    relation      TEXT NOT NULL DEFAULT 'administrative',  -- administrative | physical | cultural
    start_year    INTEGER,                -- 该归属关系生效起始年 (可空=自古如此/起点不可考)
    end_year      INTEGER,                -- 该归属关系结束年 (可空=至今未变)
    evidence      TEXT,
    review_status TEXT NOT NULL DEFAULT 'draft',  -- LLM 归属判断须门控 (P-6); 确定性派生直接 auto_approved
    extractor     TEXT DEFAULT 'llm',    -- derived:province | llm:<provider> (各写入方只碰自己的行)
    UNIQUE(place_id, parent_id, relation, start_year)
);
CREATE INDEX IF NOT EXISTS idx_placehier_place  ON place_hierarchy(place_id);
CREATE INDEX IF NOT EXISTS idx_placehier_parent ON place_hierarchy(parent_id);

-- 事件(含子事件/评价事件) → 人物 的事实层. entities 已是审定规范层 (不像古地名要过
-- place_aliases 判定), 故直接 FK entity_id, 不需要文本桥接. role 含 speaker/subject 两个
-- 评价事件专用角色 (谁说的/评价的是谁, 见 docs/EVENT_DIMENSIONS.md §4 他者评价落地).
CREATE TABLE IF NOT EXISTS event_person_refs (
    id           INTEGER PRIMARY KEY,
    event_id     INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    entity_id    INTEGER NOT NULL REFERENCES entities(id),
    role         TEXT,                   -- 主角|统帅|谋士|受害者|speaker|subject|mention…
    evidence_src INTEGER REFERENCES event_sources(id),
    extractor    TEXT DEFAULT 'llm',
    UNIQUE(event_id, entity_id, role)
);
CREATE INDEX IF NOT EXISTS idx_evtperson_event  ON event_person_refs(event_id);
CREATE INDEX IF NOT EXISTS idx_evtperson_entity ON event_person_refs(entity_id);

-- 品质判断的多源证据聚合 (品质"像事件"落地①: 一条判断可以像事件聚合 event_sources 一样
-- 聚合多条摘录). event_qualities.evidence_src/evidence_quote 两列保留不删 (向后兼容, 降级
-- 为首条证据快照), 新证据一律走这张表.
-- evidence_quote 可空(Phase 3 迁移实测发现: 5529 条 mention_qualities 里 528 条(9.5%)
-- 连 entity_mentions.excerpt 都是空的, 没有任何可核验原文可摘——honest NULL 好过塞一句
-- rationale(LLM 归纳复述, 非真子串)冒充证据, 那正是 D5 防脑补要防的事)。有值时仍必须是
-- 真子串代码切片, 这条纪律不因为允许 NULL 而放松。
CREATE TABLE IF NOT EXISTS quality_evidence (
    id               INTEGER PRIMARY KEY,
    event_quality_id INTEGER NOT NULL REFERENCES event_qualities(id) ON DELETE CASCADE,
    evidence_src     INTEGER REFERENCES event_sources(id),
    evidence_quote   TEXT,                -- 真子串, 代码切片 (防脑补, 同 D5); 无可核验原文时留 NULL
    extractor        TEXT DEFAULT 'llm'
);
CREATE INDEX IF NOT EXISTS idx_qevidence_eq ON quality_evidence(event_quality_id);

-- 事件的多维度/多颗粒度切分边 (取代单一 parent_event_id 自引用列). 子事件本身仍是普通
-- events 一行 (slug/status/event_sources/event_qualities 四件套天然适用, 零新增机制) ——
-- 变化的只是"谁是谁的子事件"从一列变成一张边表, scheme 让同一顶层事件可以同时按战场/
-- 阶段/阵营等不同维度切分, 互不冲突 (七国之乱例, 见 EVENT_DIMENSIONS §3). 深度软限制 2 层
-- (不设 DB 约束, 写入端控制, YAGNI). 不设 ON DELETE CASCADE: 对 approved 子事件的保护同
-- event_sources.event_id 现状 (逼写者过 P-3 检查, 不靠 SQLite 静默清除).
CREATE TABLE IF NOT EXISTS event_composition (
    id              INTEGER PRIMARY KEY,
    parent_event_id INTEGER NOT NULL REFERENCES events(id),
    child_event_id  INTEGER NOT NULL REFERENCES events(id),
    scheme          TEXT NOT NULL DEFAULT 'default',  -- geographic|chronological_phase|faction|default
    seq             INTEGER,              -- 同一 (parent, scheme) 内展示顺序
    note            TEXT,
    UNIQUE(parent_event_id, child_event_id, scheme)
);
CREATE INDEX IF NOT EXISTS idx_evtcomp_parent ON event_composition(parent_event_id, scheme);
CREATE INDEX IF NOT EXISTS idx_evtcomp_child  ON event_composition(child_event_id);

-- 人物关系受控词表 (应用层"人物关系图谱"驱动新增, 借鉴 CBDB KINSHIP_CODES/ASSOC_CODES 的
-- 互逆配对惯例, 但从小词表起步, 仿 qualities 当年 55 条起步的做法, 非照搬 CBDB 500+ 条量级).
CREATE TABLE IF NOT EXISTS relation_types (
    slug            TEXT PRIMARY KEY,      -- 父子/师徒/君臣/政敌/姻亲…
    label           TEXT NOT NULL,
    category        TEXT,                  -- 血缘|姻亲|师承|政治|社会交往
    reciprocal_slug TEXT,                  -- 配对关系 (父→子 的 reciprocal 是 子→父)
    symmetric       INTEGER NOT NULL DEFAULT 0  -- 1=对称关系如"挚友"
);

-- 人物↔人物 长期性关系认定 (判断层, 同 event_qualities 五态门控). 与"评价事件"
-- (event_person_refs role=speaker/subject) 互补不重复: 后者是【某一次】具体评价, 这张表是
-- 【长期性】关系归纳 (可能由多次评价/多次共现归纳得出). 建表先就位, 抽取器是独立后续工作
-- (同 story_qualities 当年"建表先就位"的先例).
CREATE TABLE IF NOT EXISTS entity_relations (
    id            INTEGER PRIMARY KEY,
    entity_id_a   INTEGER NOT NULL REFERENCES entities(id),
    entity_id_b   INTEGER NOT NULL REFERENCES entities(id),
    relation_slug TEXT NOT NULL REFERENCES relation_types(slug),
    evidence_src  INTEGER REFERENCES event_sources(id),
    rationale     TEXT,
    confidence    REAL NOT NULL DEFAULT 0.0,
    review_status TEXT NOT NULL DEFAULT 'draft',  -- draft|reviewing|approved|rejected|auto_approved
    reviewed_by   TEXT,
    note          TEXT,
    extractor     TEXT DEFAULT 'llm',
    UNIQUE(entity_id_a, entity_id_b, relation_slug)
);
CREATE INDEX IF NOT EXISTS idx_entrel_a      ON entity_relations(entity_id_a);
CREATE INDEX IF NOT EXISTS idx_entrel_b      ON entity_relations(entity_id_b);
CREATE INDEX IF NOT EXISTS idx_entrel_status ON entity_relations(review_status);
"""
SCHEMA += _TPE_DDL


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


def _mig_v5(conn: sqlite3.Connection) -> None:
    """发表台账 (公众号内容线): 哪篇稿已发表(key/标题/链接/日期) + 用到哪些事件
    (event_ids JSON 数组, 供选题排除已发表用过的故事)。人工经 web /publish 标记。"""
    conn.execute("""CREATE TABLE IF NOT EXISTS publications (
        id           INTEGER PRIMARY KEY,
        key          TEXT UNIQUE NOT NULL,   -- 成稿/草稿 key (如 jingke-finished, yigezi-孝)
        title        TEXT,
        url          TEXT,                   -- 公众号文章链接 (可空)
        published_at TEXT,                   -- ISO 日期
        event_ids    TEXT                    -- JSON int 数组; 选题避重用
    )""")


def _mig_v6(conn: sqlite3.Connection) -> None:
    """时间/地点/人物一等边 + 事件递归复合 + 人物关系 + 时间/地点颗粒度精修 (ADR-009).
    DDL 单一来源 _TPE_DDL (新库经 SCHEMA 建, 旧库经此补; IF NOT EXISTS 幂等). 纯加法:
    不删不改任何既有表/列/数据, 不重置任何既有 review_status。详见 docs/EVENT_DIMENSIONS.md。"""
    conn.executescript(_TPE_DDL)
    _add_column(conn, "events", "dimensions_reviewed", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "events", "reality_status", "TEXT")
    _add_column(conn, "entities", "reality_status", "TEXT")
    _add_column(conn, "event_qualities", "subject_entity_id", "INTEGER REFERENCES entities(id)")
    _add_column(conn, "places", "chgis_pt_id", "INTEGER")
    _add_column(conn, "places", "place_kind", "TEXT NOT NULL DEFAULT 'admin_unit'")


def _mig_v7(conn: sqlite3.Connection) -> None:
    """时间/地点颗粒度精修补丁, 只对已跑过【旧版】_mig_v6 的库补缺口 (P-5 教训留档): db.py
    开发过程中曾就地改写 _mig_v6 的函数体两次 (理由是"当时没有真实库跑过它")——但
    tests/test_entities_disambig.py 的 TestRealCorpus 会在本机存在 data/corpus.db 时直接
    connect() 它做集成校验, 而 connect() 天然触发迁移, 这个副作用在本地把 data/corpus.db
    stamp 成了 user_version=6(旧版形状: time_points 无 month/day/range_kind, 无
    entity_time_refs/time_alias_reviews/place_hierarchy, places 无 place_kind)。旧版一旦
    stamp 过 6, `_migrate` 的 `version < ver` 版本闸门就不会重跑 _mig_v6 补齐——教训: 迁移
    步骤一旦有【任何】真实库(哪怕是本地 gitignored 的)跑过, 就只能追加新版本号, 不能再就地
    改写。CREATE TABLE IF NOT EXISTS 不会给已存在的表加列, 新表(entity_time_refs 等)倒是能建;
    新库经 SCHEMA 直接拿到完整形状, 此步对新库全部是 no-op。event_time_refs.time_point_id 是
    早期 v6 遗留列, 保留不删(SQLite 删列需重建表, 收益不值得; 新库/新代码不再写它)。"""
    conn.executescript(_TPE_DDL)
    _add_column(conn, "time_points", "month", "INTEGER")
    _add_column(conn, "time_points", "day", "INTEGER")
    _add_column(conn, "time_points", "range_kind", "TEXT NOT NULL DEFAULT 'exact'")
    _add_column(conn, "places", "place_kind", "TEXT NOT NULL DEFAULT 'admin_unit'")
    _add_column(conn, "events", "reality_status", "TEXT")
    _add_column(conn, "entities", "reality_status", "TEXT")


def _mig_v8(conn: sqlite3.Connection) -> None:
    """event_qualities 的身份键从 UNIQUE(event_id, quality_id) 放宽为
    UNIQUE(event_id, quality_id, subject_entity_id) —— Phase 3(他者评价迁移, ADR-009)首次
    出现"同一事件挂多个不同主体的同一品质判断"的真实场景(如一个评价事件里甲评乙"仁"、同场
    另一人物反驳丙"不仁"，quality_id 相同、subject_entity_id 不同), 旧约束会让第二条因
    UNIQUE 冲突静默失败——真实数据丢失风险，不是收尾细节。SQLite 不支持 ALTER 改 UNIQUE
    约束，走 P-5 允许的「建新表→拷数据→换名」套路，保留原表全部行的 id/内容/review_status
    不变（quality_evidence.event_quality_id 的 FK 目标 id 不变）。

    重建期间【必须】临时关闭 `PRAGMA foreign_keys`：实测 DROP TABLE 一张被
    `ON DELETE CASCADE` 引用的父表时，SQLite 会先把子表(quality_evidence)里悬空的行级联
    删掉（即使子表当前是空表也要显式改这个 pragma，防止以后 quality_evidence 有数据时
    这条迁移悄悄清空它——已用最小复现实测验证：foreign_keys=ON 时子表行被清空，OFF 时
    完整保留)。**第二个更隐蔽的坑**：`PRAGMA foreign_keys` 在有未提交事务时是 no-op
    （SQLite 文档明载，但实测才踩到）——`INSERT ... SELECT` 会隐式开启事务，此后任何改
    该 pragma 的语句都静默不生效；若不在改回 ON 之前先 `commit()`，foreign_keys 会一路
    保持 OFF 到连接结束，且 `_migrate()` 末尾的 `conn.commit()` 不会补救（提交动作本身
    不会重置 pragma 状态）。曾在此漏掉这个 commit，导致 FK 约束全局失效（
    `tests/test_quality_layer.py::test_mapping_quality_fk_is_closed_set` 转红才发现）。"""
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("""
            CREATE TABLE event_qualities_v8new (
                id                 INTEGER PRIMARY KEY,
                event_id           INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                quality_id         INTEGER NOT NULL REFERENCES qualities(id),
                rationale          TEXT NOT NULL,
                evidence_src       INTEGER REFERENCES event_sources(id),
                evidence_quote     TEXT,
                confidence         REAL NOT NULL DEFAULT 0.0,
                review_status      TEXT NOT NULL DEFAULT 'draft',
                reviewed_by        TEXT,
                note               TEXT,
                extractor          TEXT DEFAULT 'llm',
                subject_entity_id  INTEGER REFERENCES entities(id),
                UNIQUE(event_id, quality_id, subject_entity_id)
            )
        """)
        conn.execute(
            "INSERT INTO event_qualities_v8new(id,event_id,quality_id,rationale,evidence_src,"
            "evidence_quote,confidence,review_status,reviewed_by,note,extractor,subject_entity_id) "
            "SELECT id,event_id,quality_id,rationale,evidence_src,evidence_quote,confidence,"
            "review_status,reviewed_by,note,extractor,subject_entity_id FROM event_qualities")
        conn.execute("DROP TABLE event_qualities")
        conn.execute("ALTER TABLE event_qualities_v8new RENAME TO event_qualities")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_eq_event   ON event_qualities(event_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_eq_quality ON event_qualities(quality_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_eq_status  ON event_qualities(review_status)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_eq_no_subject "
                     "ON event_qualities(event_id, quality_id) WHERE subject_entity_id IS NULL")
        conn.commit()  # 必须在这里提交, 否则下面把 foreign_keys 改回 ON 是 no-op (见上)
        bad = conn.execute("PRAGMA foreign_key_check").fetchall()
        if bad:
            raise RuntimeError(f"_mig_v8: foreign_key_check 发现悬空引用, 中止: {bad}")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def _mig_v9(conn: sqlite3.Connection) -> None:
    """quality_evidence.evidence_quote 从 NOT NULL 放宽为可空 (Phase 3 迁移实测发现: 5529 条
    mention_qualities 里 528 条(9.5%)连 entity_mentions.excerpt 都是空的, 没有任何可核验原文
    可摘——honest NULL 好过塞一句 rationale(LLM 归纳复述, 非真子串)冒充证据)。quality_evidence
    是叶子表(只有 FK 指出去, 没有别的表 FK 指进来), 不像 _mig_v8 的 event_qualities 那样有
    ON DELETE CASCADE 级联风险, 不需要临时关 foreign_keys。生产库这张表目前是空表, 重建零数据
    风险, 但仍按标准「建新表→拷数据→换名」套路写(而不是直接 DROP 重 CREATE), 以防某些环境
    已经有数据。"""
    conn.execute("""
        CREATE TABLE quality_evidence_v9new (
            id               INTEGER PRIMARY KEY,
            event_quality_id INTEGER NOT NULL REFERENCES event_qualities(id) ON DELETE CASCADE,
            evidence_src     INTEGER REFERENCES event_sources(id),
            evidence_quote   TEXT,
            extractor        TEXT DEFAULT 'llm'
        )
    """)
    conn.execute(
        "INSERT INTO quality_evidence_v9new(id,event_quality_id,evidence_src,evidence_quote,extractor) "
        "SELECT id,event_quality_id,evidence_src,evidence_quote,extractor FROM quality_evidence")
    conn.execute("DROP TABLE quality_evidence")
    conn.execute("ALTER TABLE quality_evidence_v9new RENAME TO quality_evidence")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_qevidence_eq ON quality_evidence(event_quality_id)")


def _mig_v10(conn: sqlite3.Connection) -> None:
    """① event_qualities(subject_entity_id) 补索引: Phase 4 的 get_person/query_by_quality
    按该列过滤人物层判断, EXPLAIN 实测走全表扫 (现 7430 行仍毫秒级, 但该列就是为按主体
    查询而加的, 表随抽取持续增长)。索引不能放无条件 SCHEMA 串——旧库在 _mig_v6 补列之前
    没有 subject_entity_id, CREATE INDEX 对不存在的列直接报错 (IF NOT EXISTS 只守卫索引名
    不守卫列, 同 idx_eq_no_subject 的教训); 新库经 SCHEMA 建全形状表后仍会依序跑本步, 两路
    都覆盖。② 清孤立索引 idx_evttime_point: 已 stamp 旧 v6 形状的库残留 (指向恒为 NULL 的
    废弃 event_time_refs.time_point_id 列; 列本身保留不删——纯加法原则), 新库从未建过它,
    DROP IF EXISTS 幂等。"""
    conn.execute("CREATE INDEX IF NOT EXISTS idx_eq_subject ON event_qualities(subject_entity_id)")
    conn.execute("DROP INDEX IF EXISTS idx_evttime_point")


def _mig_v11(conn: sqlite3.Connection) -> None:
    """place_hierarchy 补人审门控列 (P-6): review_status(draft 默认) + extractor。
    Phase 0 建表时按'静态事实'设计漏了门控, 但流域/文化区归属是 LLM 判断, 须 draft→人审。
    新库经 _TPE_DDL 已带两列 (本步 no-op); 已 stamp 旧形状的库经 _add_column 补
    (同 _mig_v7 双路径模式)。"""
    _add_column(conn, "place_hierarchy", "review_status", "TEXT NOT NULL DEFAULT 'draft'")
    _add_column(conn, "place_hierarchy", "extractor", "TEXT DEFAULT 'llm'")


# 版本化迁移 (P-5): 按 PRAGMA user_version 顺序应用未跑过的步骤; 每步幂等、可执行任意 SQL
# (含 SQLite 改约束的「建新表→拷数据→换名」套路). 加新步骤: 追加 (递增版本号, 描述, 函数).
_VERSIONED = [
    (1, "events.book_id + entities.home_book_id (ADR-001/002)", _mig_v1),
    (2, "idx_stories_book_chapter (web 浏览热路径)", _mig_v2),
    (3, "person_candidates.reviewed_by (机审/人审痕迹)", _mig_v3),
    (4, "品质层 qualities + event/mention/story_qualities (功能C)", _mig_v4),
    (5, "publications 发表台账 (公众号内容线, 选题避重用)", _mig_v5),
    (6, "时间/地点/人物一等边 + 事件递归复合 + 人物关系 + 颗粒度精修 (ADR-009)", _mig_v6),
    (7, "颗粒度精修补丁 (补已 stamp v6 旧形状的库, 新库 no-op, 见 _mig_v7 注释)", _mig_v7),
    (8, "event_qualities 身份键放宽到含 subject_entity_id (Phase 3 前置)", _mig_v8),
    (9, "quality_evidence.evidence_quote 放宽为可空 (Phase 3 前置)", _mig_v9),
    (10, "event_qualities(subject_entity_id) 索引 + 清孤立 idx_evttime_point", _mig_v10),
    (11, "place_hierarchy 补 review_status/extractor 门控列 (P-6, ADR-009 收尾②)", _mig_v11),
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
