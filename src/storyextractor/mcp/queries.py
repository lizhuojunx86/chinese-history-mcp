"""四轴只读查询 (纯函数: conn + 参数 → JSON-可序列化 dict)。

对应 web/server.py 的四个查询出口, 但返回结构化数据而非 HTML, 且每条如实带
review_status + 出处。诚实约束 (见各函数 _HONESTY 注): events 的 approved 是
"机审批量过审的可信推定"非逐条人审; entities 画像全 draft; 白话/标点机器生成。
"""
from __future__ import annotations

import sqlite3

from .db import chapter_name, citation

# 消费门槛: 进对外查询的 review_status 白名单 (与 web 层一致)
_CONSUMABLE = ("approved", "auto_approved")

# 诚实标注 (随返回透出, 客户端/LLM 不得据此宣称逐条人审)
HONESTY = {
    "events": ("events.status='approved' 多为机审批量过审的【可信推定】, 非逐条人审; "
               "canonical_summary 是 LLM 融合多源生成的机器叙述 (要点标来源)。"),
    "person": ("人物画像 (profile) 由 LLM 综合生成、全部为 draft (未逐条人核); "
               "他者评价 excerpt 是公版原文照抄, 可溯源。"),
    "place": ("古今地名映射多为多 LLM 机审共识 (auto_approved), 少量人审 (approved); "
              "confidence 折高/中/存疑三档。story 为机器分割产物。"),
    "quality": ("品质→事件/人物 是【判断】非事实: auto_approved=机审高置信, draft=待人审; "
                "evidence_quote 是原文真子串, rationale 是 LLM 归纳理由。"),
    "text": "原文为公版白文, 标点/分段为机器生成; 白话译文全部机器翻译 (machine-generated)。",
}


def _clamp(n, lo, hi, default):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _strength(conf) -> str:
    """代表强度分档 (不显数字, 防伪精确)。"""
    if conf is None:
        return "unknown"
    if conf >= 0.85:
        return "typical"      # 典型
    if conf >= 0.7:
        return "strong"       # 较强
    return "weak"             # 弱


def _conf_tier(conf, note) -> str:
    """置信折高/中/存疑三档 (地点映射)。"""
    if note or (conf is not None and conf < 0.6):
        return "doubtful"     # 存疑
    if conf is not None and conf >= 0.85:
        return "high"         # 高
    return "medium"           # 中


def _like_escape(s: str) -> str:
    """转义用户输入里的 LIKE 通配符 (%/_/\\), 配合 ESCAPE '\\'。防 '%' 匹配全库的语义泄漏。"""
    return (s or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# review_status 优先级 (小=更可信): 人审 > 机审共识 > 审校中 > 待审
_STATUS_PRI = {"approved": 0, "auto_approved": 1, "reviewing": 2, "draft": 3}


# ---------- 事件轴 ----------
def _event_sources(conn: sqlite3.Connection, eid: int) -> list[dict]:
    rows = conn.execute(
        "SELECT es.chapter_seq, es.para_start, es.para_end, es.role, es.excerpt, "
        "b.title, b.slug FROM event_sources es JOIN books b ON b.id=es.book_id "
        "WHERE es.event_id=? ORDER BY es.id", (eid,)).fetchall()
    out = []
    for seq, ps, pe, role, excerpt, btitle, bslug in rows:
        chn = chapter_name(conn, bslug, seq)
        out.append({
            "role": role,
            "excerpt": excerpt,
            "citation": citation(btitle, chn, ps, pe),
            "book_slug": bslug,
        })
    return out


def search_events(conn, keyword=None, book=None, person=None, limit=10) -> dict:
    """跨书融合事件 + 逐源出处。keyword/person 走 title|摘要匹配, book 按来源书 slug 过滤。"""
    limit = _clamp(limit, 1, 50, 10)
    where = ["e.status = 'approved'"]
    params: list = []
    if keyword:
        kw = f"%{_like_escape(keyword)}%"
        where.append("(e.title LIKE ? ESCAPE '\\' OR e.canonical_summary LIKE ? ESCAPE '\\')")
        params += [kw, kw]
    if person:
        pw = f"%{_like_escape(person)}%"
        where.append("(e.title LIKE ? ESCAPE '\\' OR e.canonical_summary LIKE ? ESCAPE '\\' OR EXISTS("
                     "SELECT 1 FROM event_sources es WHERE es.event_id=e.id AND es.excerpt LIKE ? ESCAPE '\\'))")
        params += [pw, pw, pw]
    if book:
        where.append("EXISTS(SELECT 1 FROM event_sources es JOIN books b ON b.id=es.book_id "
                     "WHERE es.event_id=e.id AND b.slug=?)")
        params.append(book)
    total = conn.execute(
        f"SELECT COUNT(*) FROM events e WHERE {' AND '.join(where)}", params).fetchone()[0]
    rows = conn.execute(
        "SELECT e.id, e.slug, e.title, e.kind, e.time_label, e.status, e.canonical_summary, e.book_id "
        f"FROM events e WHERE {' AND '.join(where)} "
        "ORDER BY (SELECT COUNT(*) FROM event_sources es WHERE es.event_id=e.id) DESC, e.id "
        "LIMIT ?", params + [limit]).fetchall()
    events = []
    for eid, slug, title, kind, tl, status, summary, book_id in rows:
        events.append({
            "slug": slug,
            "title": title,
            "kind": kind,
            "time_label": tl,
            "review_status": status,
            "cross_book": book_id is None,
            "canonical_summary": summary,
            "summary_note": "machine-generated (LLM 融合多源)",
            "sources": _event_sources(conn, eid),
        })
    return {
        "axis": "events",
        "query": {"keyword": keyword, "book": book, "person": person, "limit": limit},
        "total_matches": total,
        "returned": len(events),
        "events": events,
        "honesty": HONESTY["events"] + " " + HONESTY["text"],
    }


# ---------- 人物轴 ----------
_ENTITY_COLS = "id, slug, name, profile, status, era, aliases, disambig_note, home_book_id, home_seq"


def _resolve_person(conn, name: str):
    """解析人物: 精确 name/slug 优先; 否则【整词】别名匹配 (顿号 token, 非子串)。
    返回 (row, candidates)。candidates 非空 = 需消歧 (同名异指 / 多人共用一别名)。
    整词匹配杜绝 '霸'→西楚霸王 一类子串误配; 命中>1 时不静默 LIMIT 1 任意取。"""
    exact = conn.execute(
        f"SELECT {_ENTITY_COLS} FROM entities WHERE name=? OR slug=?", (name, name)).fetchall()
    if len(exact) == 1:
        return exact[0], []
    if len(exact) > 1:
        return None, exact          # 同名异指: 交调用方消歧
    esc = _like_escape(name)         # 别名串两端补顿号, 查 '、token、' 整词
    alias = conn.execute(
        f"SELECT {_ENTITY_COLS} FROM entities "
        "WHERE ('、'||IFNULL(aliases,'')||'、') LIKE '%、'||?||'、%' ESCAPE '\\'", (esc,)).fetchall()
    if len(alias) == 1:
        return alias[0], []
    if len(alias) > 1:
        return None, alias
    return None, []


def _person_home(conn, home_bid, home_seq):
    if home_bid and home_seq:
        b = conn.execute("SELECT title, slug FROM books WHERE id=?", (home_bid,)).fetchone()
        if b:
            return citation(b[0], chapter_name(conn, b[1], home_seq))
    return None


def get_person(conn, name: str) -> dict:
    """人物画像 + 他者评价 (逐条带出处) + 品质 + 参与事件。"""
    if not name or not name.strip():
        return {"axis": "person", "error": "name 不能为空", "person": None}
    name = name.strip()
    row, candidates = _resolve_person(conn, name)
    if candidates:                   # 同名异指 / 多人共别名: 返回候选让调用方选, 绝不静默替选
        return {
            "axis": "person", "query": name, "person": None,
            "disambiguation": [
                {"name": c[2], "slug": c[1], "era": c[5],
                 "aliases": [a for a in (c[6] or "").split("、") if a],
                 "home_biography": _person_home(conn, c[8], c[9])}
                for c in candidates],
            "message": f"『{name}』对应 {len(candidates)} 个人物 (同名异指或多人共此称谓), 请按 name/slug 指定其一。",
            "honesty": HONESTY["person"] + " " + HONESTY["text"],
        }
    if not row:
        return {"axis": "person", "query": name, "person": None,
                "message": f"未找到人物『{name}』(库内 9 部书 先秦-汉魏, 或用别名/本名再试)。",
                "honesty": HONESTY["person"]}
    eid, slug, pname, profile, status, era, aliases, disambig, home_bid, home_seq = row
    home = _person_home(conn, home_bid, home_seq)
    # 他者评价 (跨篇)
    apps = conn.execute(
        "SELECT m.chapter_seq, m.para_start, m.para_end, m.excerpt, m.note, m.aspect, b.title, b.slug "
        "FROM entity_mentions m JOIN books b ON b.id=m.book_id "
        "WHERE m.entity_id=? AND m.aspect='评价' ORDER BY m.id", (eid,)).fetchall()
    appraisals = [{
        "excerpt": ex, "gist": note, "aspect": asp,
        "citation": citation(bt, chapter_name(conn, bs, seq), ps, pe),
    } for seq, ps, pe, ex, note, asp, bt, bs in apps]
    # 史料评为的品质 (据他者评价机器抽取, 非 rejected)。同品质多条边 → 取最可信一条为代表,
    # 逐字透出其 review_status (不折成布尔), strength 亦取该代表边 (不被 draft 边虚抬)。
    qrows = conn.execute(
        "SELECT q.slug, q.name, q.polarity, mq.review_status, mq.confidence "
        "FROM mention_qualities mq JOIN entity_mentions em ON em.id=mq.mention_id "
        "JOIN qualities q ON q.id=mq.quality_id "
        "WHERE em.entity_id=? AND mq.review_status<>'rejected'", (eid,)).fetchall()
    best: dict = {}
    for qs, qn, pol, rs, conf in qrows:
        keyv = (_STATUS_PRI.get(rs, 4), -(conf or 0.0))
        if qs not in best or keyv < best[qs][0]:
            best[qs] = (keyv, {"slug": qs, "name": qn, "polarity": pol,
                               "review_status": rs, "strength": _strength(conf)})
    qualities = [d for _k, d in sorted(best.values(), key=lambda kd: kd[0])]
    # 提及此人的事件 (按姓名匹配, 未必主角)
    evs = conn.execute(
        "SELECT slug, title FROM events WHERE status='approved' "
        "AND (title LIKE ? OR canonical_summary LIKE ?) ORDER BY id LIMIT 30",
        (f"%{pname}%", f"%{pname}%")).fetchall()
    events = [{"slug": s, "title": t} for s, t in evs]
    return {
        "axis": "person",
        "query": name,
        "person": {
            "slug": slug,
            "name": pname,
            "era": era,
            "aliases": [a for a in (aliases or "").split("、") if a],
            "home_biography": home,
            "disambiguation_note": disambig,
            "review_status": status,
            "profile": profile,
            "profile_note": "machine-generated (LLM 综合画像, draft 未逐条人核)",
            "appraisals_by_others": appraisals,
            "qualities": qualities,
            "qualities_note": "据他者评价机器抽取, 每条带真实 review_status (含未人审 draft)",
            "events_mentioning": events,
            "events_note": "按姓名字符串匹配, 未必是主角",
        },
        "honesty": HONESTY["person"] + " " + HONESTY["text"],
    }


# ---------- 地点轴 ----------
def _place_candidates(conn, name):
    esc = f"%{_like_escape(name)}%"       # 转义 %/_ 防 '%' 匹配全库
    return conn.execute(
        "SELECT p.id, p.slug, p.modern_name, p.province, p.admin_level, "
        "COUNT(DISTINCT sp.story_id) AS n FROM places p "
        "JOIN place_aliases pa ON pa.place_id=p.id AND pa.review_status IN ('approved','auto_approved') "
        " AND pa.is_vague=0 "
        "JOIN story_places sp ON sp.ancient_name=pa.ancient_name AND sp.is_vague=0 "
        "WHERE p.modern_name LIKE ? ESCAPE '\\' OR p.keywords LIKE ? ESCAPE '\\' OR pa.ancient_name=? "
        "GROUP BY p.id HAVING n>0 ORDER BY n DESC, p.modern_name", (esc, esc, name)).fetchall()


def _candidates_for_ancient(conn, ancient):
    return conn.execute(
        "SELECT p.modern_name, pa.confidence, pa.uncertainty_note "
        "FROM place_aliases pa JOIN places p ON p.id=pa.place_id "
        "WHERE pa.ancient_name=? AND pa.review_status IN ('approved','auto_approved') AND pa.is_vague=0 "
        "ORDER BY pa.confidence DESC", (ancient,)).fetchall()


def _place_stories(conn, pid, modern_name, limit) -> list[dict]:
    rows = conn.execute(
        "SELECT st.id, st.title, st.gist, st.story_type, st.reality_status, st.source_citation, "
        "st.book_id, st.chapter_seq, sp.ancient_name, sp.mention_ctx, pa.confidence, "
        "pa.uncertainty_note, pa.review_status, pa.approved_by, "
        "(SELECT COUNT(DISTINCT pa2.place_id) FROM place_aliases pa2 "
        " WHERE pa2.ancient_name=sp.ancient_name AND pa2.review_status IN ('approved','auto_approved') "
        " AND pa2.is_vague=0 AND pa2.place_id IS NOT NULL) AS n_cand "
        "FROM place_aliases pa "
        "JOIN story_places sp ON sp.ancient_name=pa.ancient_name AND sp.is_vague=0 "
        "JOIN stories st ON st.id=sp.story_id "
        "WHERE pa.place_id=? AND pa.review_status IN ('approved','auto_approved') AND pa.is_vague=0 "
        "ORDER BY pa.confidence DESC, st.book_id, st.chapter_seq, st.id", (pid,)).fetchall()
    seen: dict = {}
    order: list = []
    for r in rows:
        if r[0] not in seen:
            seen[r[0]] = r
            order.append(r[0])
    stories = []
    for sid in order[:limit]:
        (_sid, title, gist, stype, rs, cite, bid, seq, anc, ctx, conf, note,
         review_status, approved_by, n_cand) = seen[sid]
        brow = conn.execute("SELECT title, slug FROM books WHERE id=?", (bid,)).fetchone()
        btitle, bslug = (brow[0], brow[1]) if brow else ("?", "")
        mapping = {
            "ancient_name": anc,
            "confidence_tier": _conf_tier(conf, note),
            "review_status": review_status,
            "approved_by": approved_by or ("consensus" if review_status == "auto_approved" else None),
            "uncertainty_note": note,
        }
        if n_cand and n_cand > 1:                 # 多解: 并列全部候选, 绝不单值伪精确
            mapping["multiple_candidates"] = [
                {"modern_name": cmn, "confidence_tier": _conf_tier(cconf, cnote)}
                for cmn, cconf, cnote in _candidates_for_ancient(conn, anc)]
        stories.append({
            "title": title,
            "gist": gist,
            "story_type": stype,
            "reality_status": rs,
            "citation": {"book": btitle, "chapter": chapter_name(conn, bslug, seq), "text": cite},
            "mention_context": ctx,
            "place_mapping": mapping,
        })
    return stories


def query_by_place(conn, place: str, limit=15) -> dict:
    """今地名 → 发生在这块土地上的古籍故事 + 出处。多解不替选, 泛称拒收。"""
    limit = _clamp(limit, 1, 50, 15)
    if not place or not place.strip():
        return {"axis": "place", "error": "place 不能为空", "stories": []}
    place = place.strip()
    cands = _place_candidates(conn, place)
    if not cands:
        vague = conn.execute(
            "SELECT 1 FROM place_aliases WHERE ancient_name=? AND is_vague=1 LIMIT 1",
            (place,)).fetchone()
        msg = (f"『{place}』是方向/区域泛称 (如江东/关中), 不对应单一今地, 不作可查地点收录。"
               if vague else
               f"『{place}』暂未收录, 或其古今映射尚在审校中。库内为先秦-汉魏 9 部书。")
        return {"axis": "place", "query": place, "resolved": None, "stories": [],
                "message": msg, "honesty": HONESTY["place"]}
    if len(cands) > 1:                            # 同名异地/古名多解: 让调用方消歧, 绝不替选
        return {
            "axis": "place", "query": place, "resolved": None,
            "disambiguation": [
                {"modern_name": mn, "province": pv, "admin_level": lvl, "story_count": n}
                for _pid, _slug, mn, pv, lvl, n in cands],
            "message": f"『{place}』对应 {len(cands)} 个今地 (同名异地或古名多解), 请指定其一再查。",
            "honesty": HONESTY["place"],
        }
    pid, _slug, mn, pv, lvl, n = cands[0]
    stories = _place_stories(conn, pid, mn, limit)
    return {
        "axis": "place",
        "query": place,
        "resolved": {"modern_name": mn, "province": pv, "admin_level": lvl},
        "total_stories": n,
        "returned": len(stories),
        "stories": stories,
        "honesty": HONESTY["place"] + " " + HONESTY["text"],
    }


# ---------- 品质轴 ----------
def _resolve_quality(conn, q: str):
    # 精确 slug/name 优先; 否则【整词】别名匹配 (顿号 token, 非子串) —— 防 '忍'→隐忍、'武'→勇武
    # 一类子串误配命中不相干品质。受控词表, 精确优先足够, 不做多解分支。
    esc = _like_escape(q)
    row = conn.execute(
        "SELECT id, slug, name, polarity, category, gloss, antonym_slug FROM qualities "
        "WHERE status='active' AND (slug=? OR name=? "
        "OR ('、'||IFNULL(aliases,'')||'、') LIKE '%、'||?||'、%' ESCAPE '\\') "
        "ORDER BY (name=? OR slug=?) DESC LIMIT 1",
        (q, q, esc, q, q)).fetchone()
    return row


def query_by_quality(conn, quality: str, limit=10, include_draft=False) -> dict:
    """品质名/slug → 代表性最强的事件/人物 + 原文证据。默认只出 approved/auto_approved。"""
    limit = _clamp(limit, 1, 30, 10)
    if not quality or not quality.strip():
        return {"axis": "quality", "error": "quality 不能为空", "events": [], "persons": []}
    quality = quality.strip()
    q = _resolve_quality(conn, quality)
    if not q:
        return {"axis": "quality", "query": quality, "resolved": None, "events": [], "persons": [],
                "message": f"未找到品质『{quality}』。品质来自 55 词受控词表 (如 忠/谋略/勇/仁/残暴)。",
                "honesty": HONESTY["quality"]}
    qid, slug, name, pol, cat, gloss, anto = q
    statuses = list(_CONSUMABLE) + (["draft"] if include_draft else [])
    ph = ",".join("?" * len(statuses))
    order = ("ORDER BY CASE review_status WHEN 'approved' THEN 0 WHEN 'auto_approved' THEN 1 "
             "ELSE 2 END, confidence DESC")
    # 体现此品质的事件
    erows = conn.execute(
        f"SELECT e.slug, e.title, eq.rationale, eq.evidence_quote, eq.review_status, eq.confidence, "
        f"eq.event_id FROM event_qualities eq JOIN events e ON e.id=eq.event_id "
        f"WHERE eq.quality_id=? AND eq.review_status IN ({ph}) "
        f"{order.replace('review_status', 'eq.review_status').replace('confidence','eq.confidence')} "
        f"LIMIT ?", [qid] + statuses + [limit]).fetchall()
    events = []
    for eslug, title, rat, ev, rstatus, conf, eid in erows:
        events.append({
            "event_slug": eslug, "title": title,
            "rationale": rat, "rationale_note": "machine-generated (LLM 归纳)",
            "evidence_quote": ev, "review_status": rstatus,
            "strength": _strength(conf),
            "sources": _event_sources(conn, eid),
        })
    # 被评为此品质的人物 (他者评价)。同一人可有多条评价边 → 按身份去重, 保留最强代表
    # (order 已按 status/confidence 降序, 首见即最强); 多取些行再折叠到 limit 个不同人。
    mrows = conn.execute(
        f"SELECT en.name, en.slug, mq.evidence_quote, mq.rationale, mq.review_status, mq.confidence, "
        f"em.book_id, em.chapter_seq, em.para_start, em.para_end "
        f"FROM mention_qualities mq JOIN entity_mentions em ON em.id=mq.mention_id "
        f"JOIN entities en ON en.id=em.entity_id "
        f"WHERE mq.quality_id=? AND mq.review_status IN ({ph}) "
        f"{order.replace('review_status','mq.review_status').replace('confidence','mq.confidence')}, en.name "
        f"LIMIT ?", [qid] + statuses + [limit * 4]).fetchall()
    persons = []
    seen_person: set = set()
    for pname, pslug, ev, rat, rstatus, conf, bid, seq, ps, pe in mrows:
        key = pslug or pname
        if key in seen_person:
            continue
        seen_person.add(key)
        bt = conn.execute("SELECT title, slug FROM books WHERE id=?", (bid,)).fetchone()
        cit = citation(bt[0], chapter_name(conn, bt[1], seq), ps, pe) if bt else None
        persons.append({
            "name": pname, "person_slug": pslug,
            "evidence_quote": ev, "rationale": rat,
            "review_status": rstatus, "strength": _strength(conf),
            "citation": cit,
        })
        if len(persons) >= limit:
            break
    return {
        "axis": "quality",
        "query": quality,
        "resolved": {"slug": slug, "name": name, "polarity": pol, "category": cat, "gloss": gloss,
                     "antonym_slug": anto},
        "include_draft": include_draft,
        "events": events,
        "persons": persons,
        "honesty": HONESTY["quality"] + " " + HONESTY["text"],
    }
