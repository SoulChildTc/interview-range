#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
面试靶场 - 数据存储层
======================
分拆 JSON 文件存储，避免单文件无限膨胀：

  data/
  ├── questions/
  │   ├── _meta.json          # 题库元数据 + 方向列表
  │   ├── linux.json           # 每个方向一个文件（题目数组）
  │   ├── network.json
  │   └── ...
  ├── history/
  │   ├── index.json           # 场次索引（轻量摘要，供列表页）
  │   └── sessions/
  │       └── <session_id>.json  # 单场完整明细（逐题对话+评分）
  ├── state.json               # 用户学习状态（已掌握/收藏/上次方向）
  └── logs/
      └── app.log              # 轮转应用日志

零第三方依赖，纯标准库。首次启动自动从旧的单文件结构迁移。
"""

import json
import logging
import hashlib
import os
import time
from collections import defaultdict
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ---------------------------------------------------------------- 路径

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
QUESTIONS_DIR = DATA_DIR / "questions"
HISTORY_DIR = DATA_DIR / "history"
SESSIONS_DIR = HISTORY_DIR / "sessions"
FOLLOWUP_DIR = DATA_DIR / "followups"   # 追问对话会话缓存（按题目 id 分文件）
LOG_DIR = DATA_DIR / "logs"
STATE_FILE = DATA_DIR / "state.json"
HISTORY_INDEX = HISTORY_DIR / "index.json"
QUESTIONS_META = QUESTIONS_DIR / "_meta.json"

# 旧版单文件（迁移源，迁移后保留为备份不删除）
LEGACY_QUESTIONS = DATA_DIR / "questions.json"
LEGACY_HISTORY = DATA_DIR / "history.json"


def ensure_dirs():
    for d in (DATA_DIR, QUESTIONS_DIR, HISTORY_DIR, SESSIONS_DIR, FOLLOWUP_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- 日志

_logger = None


def get_logger():
    """获取轮转日志器（同时输出到文件和控制台）。"""
    global _logger
    if _logger is not None:
        return _logger
    ensure_dirs()
    lg = logging.getLogger("interview-range")
    lg.setLevel(logging.INFO)
    lg.propagate = False
    # 文件：512KB × 3 份轮转
    fh = RotatingFileHandler(
        LOG_DIR / "app.log", maxBytes=512 * 1024, backupCount=3, encoding="utf-8"
    )
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    ))
    lg.addHandler(fh)
    # 控制台
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
    ))
    lg.addHandler(ch)
    _logger = lg
    return lg


def log_info(msg, *args):
    get_logger().info(msg, *args)


def log_warn(msg, *args):
    get_logger().warning(msg, *args)


def log_error(msg, *args):
    get_logger().error(msg, *args)


# ---------------------------------------------------------------- 迁移

_migrated = False


def migrate_if_needed():
    """从旧的单文件结构迁移到分拆结构。幂等，只执行一次。"""
    global _migrated
    if _migrated:
        return
    _migrated = True
    ensure_dirs()
    log = get_logger()

    # ---- 题库迁移 ----
    if LEGACY_QUESTIONS.exists() and not QUESTIONS_META.exists():
        try:
            data = json.loads(LEGACY_QUESTIONS.read_text(encoding="utf-8"))
            meta = {"meta": data.get("meta", {}), "directions": data.get("directions", [])}
            QUESTIONS_META.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            by_dir = defaultdict(list)
            for q in data.get("questions", []):
                by_dir[q.get("direction", "unknown")].append(q)
            for did, qs in by_dir.items():
                (QUESTIONS_DIR / f"{did}.json").write_text(
                    json.dumps({"direction": did, "questions": qs},
                               ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            log.info("题库迁移完成: %d 题 -> %d 个方向文件",
                     len(data.get("questions", [])), len(by_dir))
        except Exception as e:
            log.error("题库迁移失败: %s", e)

    # ---- 历史迁移 ----
    if LEGACY_HISTORY.exists() and not HISTORY_INDEX.exists():
        try:
            data = json.loads(LEGACY_HISTORY.read_text(encoding="utf-8"))
            sessions = data.get("sessions", [])
            index = []
            for s in sessions:
                sid = s.get("session_id") or f"legacy-{int(time.time()*1000)}"
                (SESSIONS_DIR / f"{sid}.json").write_text(
                    json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                index.append(_summary_entry(s, sid))
            HISTORY_INDEX.write_text(
                json.dumps({"sessions": index}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            log.info("历史迁移完成: %d 场 -> 分拆文件 + 索引", len(sessions))
        except Exception as e:
            log.error("历史迁移失败: %s", e)


def _summary_entry(s, sid=None):
    """从完整场次对象提取轻量索引条目。"""
    sid = sid or s.get("session_id", "")
    dec = s.get("decision") or {}
    return {
        "session_id": sid,
        "direction": s.get("direction", ""),
        "direction_name": s.get("direction_name", ""),
        "created_at": s.get("created_at", ""),
        "ended_at": s.get("ended_at", ""),
        "items": s.get("items", 0),
        "total_score": s.get("total_score", 0),
        "decision": {
            "decision": dec.get("decision", "unknown"),
            "decision_label": dec.get("decision_label", "未判定"),
            "level": dec.get("level", ""),
            "confidence": dec.get("confidence", ""),
            "reason": dec.get("reason", ""),
            "strong_points": dec.get("strong_points", []),
            "blocking_issues": dec.get("blocking_issues", []),
            "advice": dec.get("advice", []),
        },
    }


# ---------------------------------------------------------------- 题库

_questions_cache = None

# 自定义方向的颜色池（与前端 .dir-chip.chip-* 对应）
DIR_COLOR_POOL = ["indigo", "teal", "orange", "violet", "red", "green", "cyan"]


def add_direction(name, keyword, desc=""):
    """新增自定义方向：写入 _meta.json 的 directions 列表 + 创建空题库文件。

    - id 自动生成 `custom-N`（文件名安全，不与现有方向冲突）
    - 分配一个颜色用于前端 chip 展示
    - keyword 供联网找题时构造搜索词（没有专属配置的方向用 keyword 搜索）
    返回新方向 dict；名称空/重名时抛 ValueError。
    """
    migrate_if_needed()
    if not QUESTIONS_META.exists():
        raise RuntimeError(f"题库元数据不存在: {QUESTIONS_META}")
    meta = json.loads(QUESTIONS_META.read_text(encoding="utf-8"))
    directions = meta.get("directions", [])
    name = (name or "").strip()
    keyword = (keyword or "").strip()
    if not name:
        raise ValueError("方向名称不能为空")
    # 回收站里有同名方向 → 直接还原并更新信息（相当于"重建"）
    deleted_same = next((d for d in directions if d.get("name") == name and d.get("deleted")), None)
    if deleted_same:
        deleted_same["deleted"] = False
        deleted_same.pop("deleted_at", None)
        if keyword:
            deleted_same["keyword"] = keyword
        if desc:
            deleted_same["desc"] = desc.strip()
        meta.setdefault("meta", {})["updated"] = time.strftime("%Y-%m-%d")
        QUESTIONS_META.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        reload_questions()
        log_info("还原回收站中的同名方向 %s（%s）", deleted_same["id"], name)
        return deleted_same
    if any(d.get("name") == name for d in directions):
        raise ValueError(f"已存在同名方向「{name}」")
    used = {d.get("id") for d in directions}
    n = 1
    while f"custom-{n}" in used:
        n += 1
    did = f"custom-{n}"
    color = DIR_COLOR_POOL[len(directions) % len(DIR_COLOR_POOL)]
    entry = {
        "id": did, "name": name, "desc": desc.strip(),
        "keyword": keyword, "color": color, "custom": True,
    }
    directions.append(entry)
    meta["directions"] = directions
    meta.setdefault("meta", {})["updated"] = time.strftime("%Y-%m-%d")
    QUESTIONS_META.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # 空题库文件
    (QUESTIONS_DIR / f"{did}.json").write_text(
        json.dumps({"direction": did, "questions": []}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    reload_questions()
    log_info("新增自定义方向 %s（%s），联网关键词：%s", did, name, keyword)
    return entry


def delete_direction(direction_id):
    """删除方向：标记为已删除（进回收站），题库文件与题目保留，可随时还原。

    至少保留一个方向，否则抛 ValueError。返回 {"moved": bool, "question_count": int}。
    """
    migrate_if_needed()
    if not QUESTIONS_META.exists():
        return {"moved": False, "question_count": 0}
    meta = json.loads(QUESTIONS_META.read_text(encoding="utf-8"))
    directions = meta.get("directions", [])
    target = next((d for d in directions if d.get("id") == direction_id), None)
    if not target or target.get("deleted"):
        return {"moved": False, "question_count": 0}
    if len([d for d in directions if not d.get("deleted")]) <= 1:
        raise ValueError("至少保留一个方向")
    target["deleted"] = True
    target["deleted_at"] = time.strftime("%Y-%m-%d %H:%M")
    meta.setdefault("meta", {})["updated"] = time.strftime("%Y-%m-%d")
    QUESTIONS_META.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    reload_questions()
    # 统计该方向题目数（文件还保留着）
    qcount = 0
    f = QUESTIONS_DIR / f"{direction_id}.json"
    if f.exists():
        try:
            qcount = len(json.loads(f.read_text(encoding="utf-8")).get("questions", []))
        except Exception:
            qcount = 0
    log_info("方向 %s（%s）已移入回收站（%d 题）", direction_id, target.get("name"), qcount)
    return {"moved": True, "question_count": qcount}


def restore_direction(direction_id):
    """从回收站还原方向（题目文件一直在，直接恢复可见）。"""
    migrate_if_needed()
    if not QUESTIONS_META.exists():
        return False
    meta = json.loads(QUESTIONS_META.read_text(encoding="utf-8"))
    directions = meta.get("directions", [])
    target = next((d for d in directions if d.get("id") == direction_id and d.get("deleted")), None)
    if not target:
        return False
    target["deleted"] = False
    target.pop("deleted_at", None)
    meta.setdefault("meta", {})["updated"] = time.strftime("%Y-%m-%d")
    QUESTIONS_META.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    reload_questions()
    log_info("方向 %s（%s）已从回收站还原", direction_id, target.get("name"))
    return True


def purge_direction(direction_id):
    """彻底删除：从 _meta.json 移除 + 删除题库文件（该方向的题级联删除，不可恢复）。"""
    migrate_if_needed()
    if not QUESTIONS_META.exists():
        return False
    meta = json.loads(QUESTIONS_META.read_text(encoding="utf-8"))
    directions = meta.get("directions", [])
    target = next((d for d in directions if d.get("id") == direction_id), None)
    if not target or not target.get("deleted"):
        return False
    meta["directions"] = [d for d in directions if d.get("id") != direction_id]
    meta.setdefault("meta", {})["updated"] = time.strftime("%Y-%m-%d")
    QUESTIONS_META.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    f = QUESTIONS_DIR / f"{direction_id}.json"
    if f.exists():
        f.unlink()
    reload_questions()
    log_info("方向 %s（%s）已彻底删除（题目文件已移除）", direction_id, target.get("name"))
    return True


def purge_all_trash():
    """清空回收站：彻底删除所有已删除方向及其题目文件。返回删除数量。"""
    migrate_if_needed()
    if not QUESTIONS_META.exists():
        return 0
    meta = json.loads(QUESTIONS_META.read_text(encoding="utf-8"))
    directions = meta.get("directions", [])
    deleted = [d for d in directions if d.get("deleted")]
    if not deleted:
        return 0
    meta["directions"] = [d for d in directions if not d.get("deleted")]
    QUESTIONS_META.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for d in deleted:
        f = QUESTIONS_DIR / f"{d['id']}.json"
        if f.exists():
            f.unlink()
    reload_questions()
    log_info("清空回收站：%d 个方向彻底删除", len(deleted))
    return len(deleted)


def get_trash():
    """回收站列表：已删除方向 + 各自题目数（题目文件仍保留）。"""
    migrate_if_needed()
    if not QUESTIONS_META.exists():
        return []
    meta = json.loads(QUESTIONS_META.read_text(encoding="utf-8"))
    out = []
    for d in meta.get("directions", []):
        if not d.get("deleted"):
            continue
        qcount = 0
        f = QUESTIONS_DIR / f"{d['id']}.json"
        if f.exists():
            try:
                qcount = len(json.loads(f.read_text(encoding="utf-8")).get("questions", []))
            except Exception:
                qcount = 0
        out.append({**d, "question_count": qcount})
    return out


def reorder_directions(ids):
    """按给定 id 顺序重排活跃方向（回收站方向保持原样、不受影响）。

    未提及的方向按原顺序追加到末尾；已存在的 id 去重。返回新顺序（仅活跃方向）。
    """
    migrate_if_needed()
    if not QUESTIONS_META.exists():
        return []
    meta = json.loads(QUESTIONS_META.read_text(encoding="utf-8"))
    all_dirs = meta.get("directions", [])
    active = [d for d in all_dirs if not d.get("deleted")]
    deleted = [d for d in all_dirs if d.get("deleted")]
    by_id = {d["id"]: d for d in active}
    new_active, seen = [], set()
    for did in ids:
        if did in by_id and did not in seen:
            new_active.append(by_id[did])
            seen.add(did)
    for d in active:
        if d["id"] not in seen:
            new_active.append(d)
    meta["directions"] = new_active + deleted
    QUESTIONS_META.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    reload_questions()
    log_info("方向顺序已更新: %s", " > ".join(d["id"] for d in new_active))
    return new_active


def load_questions():
    """从分拆文件加载完整题库（合并所有方向）。"""
    migrate_if_needed()
    # 空库降级：题库目录/元数据不存在（如 clone 后未建方向）时返回空结构，不抛异常
    if not QUESTIONS_META.exists():
        return {"meta": {}, "directions": [], "questions": []}
    meta = json.loads(QUESTIONS_META.read_text(encoding="utf-8"))
    # 跳过已删除（回收站）的方向：题目不加载、不显示
    directions = [d for d in meta.get("directions", []) if not d.get("deleted")]
    questions = []
    for d in directions:
        f = QUESTIONS_DIR / f"{d['id']}.json"
        if f.exists():
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                questions.extend(data.get("questions", []))
            except Exception as e:
                log_warn("读取题库文件 %s 失败: %s", f, e)
    return {
        "meta": meta.get("meta", {}),
        "directions": directions,
        "questions": questions,
    }


def get_questions():
    global _questions_cache
    if _questions_cache is None:
        _questions_cache = load_questions()
    return _questions_cache


def reload_questions():
    global _questions_cache
    _questions_cache = None


def get_directions():
    return get_questions()["directions"]


def find_question(qid):
    for q in get_questions()["questions"]:
        if q["id"] == qid:
            return q
    return None


def questions_by_direction(direction):
    return [q for q in get_questions()["questions"] if q["direction"] == direction]


def append_questions(direction, new_questions):
    """向指定方向文件追加题目，更新元数据时间戳，刷新缓存。"""
    migrate_if_needed()
    f = QUESTIONS_DIR / f"{direction}.json"
    if f.exists():
        data = json.loads(f.read_text(encoding="utf-8"))
    else:
        data = {"direction": direction, "questions": []}
    data["questions"].extend(new_questions)
    f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    # 更新元数据时间戳
    if QUESTIONS_META.exists():
        meta = json.loads(QUESTIONS_META.read_text(encoding="utf-8"))
        meta.setdefault("meta", {})["updated"] = time.strftime("%Y-%m-%d")
        QUESTIONS_META.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    reload_questions()
    log_info("题库追加 %d 题到方向 %s（现有 %d 题）",
             len(new_questions), direction, len(data["questions"]))


# ---------------------------------------------------------------- 历史

def load_history_index():
    """加载历史索引（轻量摘要，不含逐题明细）。"""
    migrate_if_needed()
    if HISTORY_INDEX.exists():
        try:
            return json.loads(HISTORY_INDEX.read_text(encoding="utf-8"))
        except Exception as e:
            log_warn("读取历史索引失败: %s", e)
    return {"sessions": []}


def load_session(session_id):
    """加载单场面试完整明细（含逐题对话与评分）。"""
    f = SESSIONS_DIR / f"{session_id}.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            log_warn("读取场次 %s 失败: %s", session_id, e)
    return None


def save_session(session_detail):
    """保存一场面试完整明细，并更新索引。索引中已存在则替换。"""
    migrate_if_needed()
    sid = session_detail["session_id"]
    (SESSIONS_DIR / f"{sid}.json").write_text(
        json.dumps(session_detail, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # 更新索引
    index = load_history_index()
    entry = _summary_entry(session_detail, sid)
    sessions = index.get("sessions", [])
    for i, s in enumerate(sessions):
        if s.get("session_id") == sid:
            sessions[i] = entry
            break
    else:
        sessions.append(entry)
    index["sessions"] = sessions
    HISTORY_INDEX.write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log_info("场次 %s 已保存（%d 题，%.1f 分）",
             sid, session_detail.get("items", 0), session_detail.get("total_score", 0))


def delete_session(session_id):
    """删除一场面试（索引 + 明细文件）。返回是否删除成功。"""
    index = load_history_index()
    sessions = index.get("sessions", [])
    new_sessions = [s for s in sessions if s.get("session_id") != session_id]
    if len(new_sessions) == len(sessions):
        return False
    index["sessions"] = new_sessions
    HISTORY_INDEX.write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    f = SESSIONS_DIR / f"{session_id}.json"
    if f.exists():
        f.unlink()
    log_info("场次 %s 已删除", session_id)
    return True


# ---------------------------------------------------------------- 学习状态

def load_state():
    """加载用户学习状态（已掌握题目、收藏题目、上次选择的方向、混考排除方向）。"""
    ensure_dirs()
    if STATE_FILE.exists():
        try:
            s = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            s.setdefault("mastered", [])
            s.setdefault("bookmarks", [])
            s.setdefault("last_direction", None)
            s.setdefault("mix_exclude", [])
            return s
        except Exception as e:
            log_warn("读取学习状态失败: %s", e)
    return {"mastered": [], "bookmarks": [], "last_direction": None, "mix_exclude": []}


def set_mix_exclude(dirs):
    """设置综合混考要排除的方向列表。返回最新列表。"""
    state = load_state()
    state["mix_exclude"] = [d for d in (dirs or []) if isinstance(d, str)]
    save_state(state)
    return state["mix_exclude"]


def save_state(state):
    ensure_dirs()
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def toggle_mastered(question_id):
    """切换题目的已掌握状态。返回 (action, mastered_list)。"""
    state = load_state()
    mastered = set(state.get("mastered", []))
    if question_id in mastered:
        mastered.remove(question_id)
        action = "removed"
    else:
        mastered.add(question_id)
        action = "added"
    state["mastered"] = sorted(mastered)
    save_state(state)
    log_info("题目 %s 已掌握状态: %s（共 %d 题已掌握）",
             question_id, action, len(state["mastered"]))
    return action, state["mastered"]


def toggle_bookmark(question_id):
    """切换题目的收藏状态。返回 (action, bookmarks_list)。"""
    state = load_state()
    bookmarks = set(state.get("bookmarks", []))
    if question_id in bookmarks:
        bookmarks.remove(question_id)
        action = "removed"
    else:
        bookmarks.add(question_id)
        action = "added"
    state["bookmarks"] = sorted(bookmarks)
    save_state(state)
    log_info("题目 %s 收藏状态: %s（共 %d 题收藏）",
             question_id, action, len(state["bookmarks"]))
    return action, state["bookmarks"]


def set_last_direction(direction_id):
    state = load_state()
    state["last_direction"] = direction_id
    save_state(state)


# ---------------------------------------------------------------- 追问对话会话（按题目 id 分文件缓存）

def get_question_by_id(question_id):
    """按题目 id 在题库中查找题目（跨所有方向文件）。找不到返回 None。"""
    if not question_id:
        return None
    for f in QUESTIONS_DIR.glob("*.json"):
        if f.name == "_meta.json":
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for q in data.get("questions", []):
            if q.get("id") == question_id:
                return q
    return None


def _followup_path(question_id, topic=""):
    """追问会话文件路径。常见追问（topic 非空）按 题目+追问 分文件，彼此独立会话；
    手动输入（无 topic）归到题目默认会话。"""
    if topic:
        h = hashlib.sha1(topic.encode("utf-8")).hexdigest()[:8]
        return FOLLOWUP_DIR / f"{question_id}__{h}.json"
    return FOLLOWUP_DIR / f"{question_id}.json"


def get_followup_session(question_id, topic=""):
    """读取某题目某常见追问的对话会话。无会话时返回带空 messages 的默认结构。"""
    if not question_id:
        return {"question_id": "", "question": "", "topic": topic, "messages": [], "updated_at": ""}
    p = _followup_path(question_id, topic)
    if not p.exists():
        return {"question_id": question_id, "question": "", "topic": topic, "messages": [], "updated_at": ""}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        data.setdefault("question_id", question_id)
        data.setdefault("question", "")
        data.setdefault("topic", topic)
        data.setdefault("messages", [])
        data.setdefault("updated_at", "")
        return data
    except Exception:
        return {"question_id": question_id, "question": "", "topic": topic, "messages": [], "updated_at": ""}


def append_followup_message(question_id, question, role, content, topic=""):
    """向某常见追问的会话追加一条消息并落盘。返回更新后的会话 dict。"""
    sess = get_followup_session(question_id, topic)
    sess["question_id"] = question_id
    sess["topic"] = topic
    if question:
        sess["question"] = question
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    sess.setdefault("messages", []).append({
        "role": role,
        "content": content,
        "ts": now,
    })
    sess["updated_at"] = now
    FOLLOWUP_DIR.mkdir(parents=True, exist_ok=True)
    (_followup_path(question_id, topic)).write_text(
        json.dumps(sess, ensure_ascii=False, indent=2), encoding="utf-8")
    log_info("追问会话 %s(%s) 追加 %s 消息（共 %d 条）", question_id, topic[:12] or "默认", role, len(sess["messages"]))
    return sess


def remove_last_followup_message(question_id, topic=""):
    """AI 生成失败时撤回某常见追问会话的最后一条消息（避免脏数据）。"""
    sess = get_followup_session(question_id, topic)
    if sess["messages"]:
        sess["messages"].pop()
        sess["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        (_followup_path(question_id, topic)).write_text(
            json.dumps(sess, ensure_ascii=False, indent=2), encoding="utf-8")
    return sess


def reset_followup_session(question_id, topic=""):
    """清空某常见追问的会话（删除缓存文件）。"""
    p = _followup_path(question_id, topic)
    if p.exists():
        p.unlink()
        log_info("追问会话 %s(%s) 已重置（清空）", question_id, topic[:12] or "默认")
    return True
