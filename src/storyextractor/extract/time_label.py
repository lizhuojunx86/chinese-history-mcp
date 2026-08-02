"""事件时间标签派生 (A2, ADR-009 收尾遗留③): events.time_label 手填列全库 0 填充,
权威时间来源是 event_time_refs → time_aliases(已审) → time_points (EVENT_DIMENSIONS §9)。
本模块把三层 join 收敛成【可展示标签】, 供 web/export/MCP 消费。

语义约定:
- 手填 events.time_label 非空 → 它赢 (人工覆盖位, db.py 注释'语义降级为展示缓存');
  派生标签只做空值回退。
- 只信已审 time_aliases (approved/auto_approved), scope 优先级: 篇级命中 > 书级 > 全局
  (裸相对纪年'十年'跨篇歧义的查询侧解法, 不在写入时收敛)。
- 一个事件多条 refs 可能指向多个 time_points (含 皋陶/卫青 式人名锚点噪音):
  按 time_point 分组, 组分 = (scope 特异性最高值, 引用次数, confidence 最高值),
  取最优组; 若次优组在前两项上打平 (真歧义) → 前两名并列『A／B』诚实呈现, 不假装单值。
- time_points.label 本身已含模糊表达 ('秦·约公元前221年'), 不再二次加工年份。
"""
from __future__ import annotations


def _candidate_groups(conn, event_id: int) -> list[tuple]:
    """[(spec_max, n_refs, conf_max, tp_id, label, start_year)] 降序。"""
    rows = conn.execute(
        "SELECT tp.id, tp.label, tp.start_year, ta.confidence, "
        "  CASE WHEN ta.scope_chapter_seq IS NOT NULL AND ta.scope_chapter_seq = es.chapter_seq "
        "         THEN 2 "
        "       WHEN ta.scope_chapter_seq IS NULL AND ta.scope_book_id IS NOT NULL "
        "         AND ta.scope_book_id = es.book_id THEN 1 "
        "       WHEN ta.scope_chapter_seq IS NULL AND ta.scope_book_id IS NULL THEN 0 "
        "       ELSE -1 END AS spec "
        "FROM event_time_refs etr "
        "LEFT JOIN event_sources es ON es.id = etr.evidence_src "
        "JOIN time_aliases ta ON ta.raw_text = etr.raw_text "
        "  AND ta.review_status IN ('approved','auto_approved') "
        "JOIN time_points tp ON tp.id = ta.time_point_id "
        "WHERE etr.event_id = ?", (event_id,)).fetchall()
    groups: dict = {}
    for tp_id, label, sy, conf, spec in rows:
        if spec < 0:                      # scope 声明了但与该来源不符 → 该候选不适用
            continue
        g = groups.setdefault(tp_id, {"label": label, "sy": sy, "spec": 0, "n": 0, "conf": 0.0})
        g["spec"] = max(g["spec"], spec)
        g["n"] += 1
        g["conf"] = max(g["conf"], conf or 0.0)
    out = [(g["spec"], g["n"], g["conf"], tp_id, g["label"], g["sy"])
           for tp_id, g in groups.items()]
    # 降序: 特异性/次数/置信度; 末位 tie-break 用 start_year+tp_id 保证确定性
    out.sort(key=lambda t: (-t[0], -t[1], -t[2], t[5] if t[5] is not None else 10**9, t[3]))
    return out


def derive_time_label(conn, event_id: int) -> str | None:
    """事件的派生时间标签; 无已审候选 → None。真歧义 (前两组 特异性+次数 双打平) →
    『A／B』并列。"""
    groups = _candidate_groups(conn, event_id)
    if not groups:
        return None
    best = groups[0]
    if len(groups) > 1:
        second = groups[1]
        if second[0] == best[0] and second[1] == best[1]:
            return f"{best[4]}／{second[4]}"
    return best[4]


def derive_year(conn, event_id: int) -> int | None:
    """最优候选组的 start_year (时间轴排序用; 公元前为负)。无候选/无数字锚点 → None。"""
    groups = _candidate_groups(conn, event_id)
    return groups[0][5] if groups else None


def display_time_label(conn, event_id: int, manual: str | None) -> tuple[str | None, bool]:
    """(标签, 是否派生)。手填非空 → (手填, False); 否则 (派生或 None, True)。"""
    if manual and manual.strip():
        return manual.strip(), False
    return derive_time_label(conn, event_id), True


def batch_labels(conn, event_ids: list[int]) -> dict:
    """列表页批量: {event_id: 派生标签} (只含有结果的)。逐事件调用 (事件数几百, 每次 1 小查,
    列表页均分页 ≤50, 不构成热路径; 真到万级再改整体 join)。"""
    out = {}
    for eid in event_ids:
        lbl = derive_time_label(conn, eid)
        if lbl:
            out[eid] = lbl
    return out
