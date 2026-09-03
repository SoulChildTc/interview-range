#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
面试靶场 (Interview Range) - 本地 AI 面试模拟与刷题服务器
==========================================================
纯 Python 标准库实现，零第三方依赖。

启动: python3 server.py
打开: http://localhost:8787

功能:
  1. 题库刷题 (GET /api/questions)
  2. AI 模拟面试 (会话式): 出题 -> 答题 -> AI 评分/点评 -> 下一题 -> 总结报告
  3. 弱点诊断: 按方向/topic 统计历史分数

AI 模型: 从 ~/.config/opencode/opencode.json 读取 sense provider 配置
         (baseURL + apiKey)，默认模型 glm-5.2，429/5xx 自动指数退避重试。
"""

import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import store
from store import (
    get_logger, log_info, log_warn, log_error,
    get_questions, get_directions, find_question, questions_by_direction,
    append_questions, reload_questions, add_direction, delete_direction, delete_question,
    restore_direction, purge_direction, purge_all_trash, get_trash,
    reorder_directions,
    load_history_index, load_session, save_session, delete_session,
    load_state, toggle_mastered, toggle_bookmark, set_last_direction, set_mix_exclude,
    get_question_by_id, get_followup_session, append_followup_message,
    remove_last_followup_message, reset_followup_session,
    load_notes, save_note,
)

# ---------------------------------------------------------------- 路径与配置

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"

HOST = "127.0.0.1"
PORT = int(os.environ.get("INTERVIEW_RANGE_PORT", "8787"))

# 默认模型与重试策略（可用环境变量覆盖）
MODEL = os.environ.get("INTERVIEW_RANGE_MODEL", "glm-5.2")
MAX_RETRIES = int(os.environ.get("INTERVIEW_RANGE_MAX_RETRIES", "5"))
RETRY_BACKOFF_BASE = float(os.environ.get("INTERVIEW_RANGE_BACKOFF", "1.0"))
TIMEOUT = float(os.environ.get("INTERVIEW_RANGE_TIMEOUT", "120"))

# anysearch CLI 路径（用于 AI 联网找题）
_ANYSEARCH_DEFAULT = str(Path.home() / ".agents" / "skills" / "anysearch" / "scripts" / "anysearch_cli.js")
ANYSEARCH_CLI = os.environ.get("ANYSEARCH_CLI", _ANYSEARCH_DEFAULT)
SEARCH_MAX_RESULTS = int(os.environ.get("INTERVIEW_RANGE_SEARCH_MAX", "5"))

# ---------------------------------------------------------------- 内存会话

SESSIONS = {}          # session_id -> dict（运行中的面试会话，结束后落盘到 store）
LIVE_DIR = store.DATA_DIR / "live"   # 进行中面试的阶段性落盘（断点续面）


def _persist_live(s):
    """把进行中的会话落到磁盘，服务重启后可恢复。"""
    if not s or not s.get("session_id"):
        return
    try:
        LIVE_DIR.mkdir(parents=True, exist_ok=True)
        (LIVE_DIR / f"{s['session_id']}.json").write_text(
            json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _drop_live(sid):
    try:
        f = LIVE_DIR / f"{sid}.json"
        if f.exists():
            f.unlink()
    except Exception:
        pass


def _load_live_sessions():
    """启动时恢复未结束的面试会话。"""
    try:
        if not LIVE_DIR.exists():
            return
        for f in LIVE_DIR.glob("*.json"):
            try:
                s = json.loads(f.read_text(encoding="utf-8"))
                if s.get("status") == "running" and s.get("session_id"):
                    SESSIONS[s["session_id"]] = s
            except Exception:
                continue
    except Exception:
        pass


# ---------------------------------------------------------------- AI 调用 (sense)

def _load_sense_config():
    """从 OpenCode 配置文件读取 baseURL=https://token.sensenova.cn/v1 的 provider。
    遍历所有 provider，第一个命中目标 baseURL 即采用（provider 名称不写死）。
    优先级: 环境变量 INTERVIEW_RANGE_API_KEY / INTERVIEW_RANGE_BASE_URL > opencode.json
    """
    api_key = os.environ.get("INTERVIEW_RANGE_API_KEY", "")
    base_url = os.environ.get("INTERVIEW_RANGE_BASE_URL", "")

    if api_key and base_url:
        return api_key, base_url

    TARGET_BASE = "https://token.sensenova.cn/v1"
    candidates = [
        Path.home() / ".config" / "opencode" / "opencode.json",
    ]
    for cfg in candidates:
        if cfg.exists():
            try:
                conf = json.loads(cfg.read_text(encoding="utf-8"))
                providers = conf.get("provider") or {}
                for name, p in providers.items():
                    if not isinstance(p, dict):
                        continue
                    opts = p.get("options") or {}
                    pb = str(opts.get("baseURL") or "").rstrip("/")
                    if pb != TARGET_BASE.rstrip("/"):
                        continue
                    if not api_key:
                        api_key = opts.get("apiKey", "")
                    if not base_url:
                        base_url = pb
                    if api_key and base_url:
                        print(f"[ai] 使用 provider: {name} ({pb})")
                        return api_key, base_url
            except Exception as e:
                print(f"[warn] 读取 {cfg} 失败: {e}")
    raise RuntimeError("未找到 baseURL 为 token.sensenova.cn/v1 的 provider 配置。请检查 ~/.config/opencode/opencode.json")


def chat_completion(messages, temperature=0.4, max_tokens=None, json_output=False):
    """调用 OpenAI 兼容 chat/completions 接口，对 429/5xx 做指数退避重试。
    json_output=True 时加 response_format=json_object，让服务端强制输出合法 JSON。
    max_tokens=None 时不传该参数，由模型使用默认（最大）输出上限。"""
    api_key, base_url = _load_sense_config()
    url = base_url.rstrip("/") + "/chat/completions"

    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if json_output:
        payload["response_format"] = {"type": "json_object"}
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            retryable = e.code in (429, 500, 502, 503, 504)
            last_err = f"HTTP {e.code}: {e.reason}"
            if not retryable:
                # 读一下错误体，尽力保留信息
                try:
                    last_err += " | " + e.read().decode("utf-8", "ignore")[:500]
                except Exception:
                    pass
                raise RuntimeError(last_err)
        except urllib.error.URLError as e:
            retryable = True  # 网络抖动也重试
            last_err = f"URLError: {e.reason}"
        except (TimeoutError, OSError) as e:
            retryable = True
            last_err = f"{type(e).__name__}: {e}"

        if attempt >= MAX_RETRIES:
            break
        # 429/5xx 一律按 1/2/4/8/16s 指数退避重试
        sleep = RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
        print(f"[ai] {last_err} -> 重试 {attempt}/{MAX_RETRIES}，等待 {sleep:.1f}s")
        time.sleep(sleep)

    raise RuntimeError(f"模型调用失败（已重试 {MAX_RETRIES} 次）: {last_err}")


def chat_completion_stream(messages, temperature=0.4, max_tokens=None, json_output=False):
    """流式调用，生成器。yield (kind, text)，kind ∈ {"reasoning", "content"}。
    对 429/5xx 在收到首个字节前做指数退避重试。
    max_tokens=None 时不传该参数，由模型使用默认（最大）输出上限。"""
    api_key, base_url = _load_sense_config()
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if json_output:
        payload["response_format"] = {"type": "json_object"}
    body = json.dumps(payload).encode("utf-8")

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}",
                     "Accept": "text/event-stream"},
            method="POST")
        started = False
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8", "ignore").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        obj = json.loads(data)
                    except Exception:
                        continue
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    rc = delta.get("reasoning_content")
                    if rc:
                        started = True
                        yield ("reasoning", rc)
                    c = delta.get("content")
                    if c:
                        started = True
                        yield ("content", c)
                    fr = choices[0].get("finish_reason")
                    if fr and fr not in ("", "null"):
                        return
            return
        except urllib.error.HTTPError as e:
            retryable = e.code in (429, 500, 502, 503, 504)
            last_err = f"HTTP {e.code}: {e.reason}"
            if started or not retryable:
                raise RuntimeError(last_err)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = f"{type(e).__name__}: {e}"
            if started:
                raise RuntimeError(last_err)
        if attempt >= MAX_RETRIES:
            break
        sleep = RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
        print(f"[ai-stream] {last_err} -> 重试 {attempt}/{MAX_RETRIES}，等待 {sleep:.1f}s")
        time.sleep(sleep)
    raise RuntimeError(f"模型流式调用失败（已重试 {MAX_RETRIES} 次）: {last_err}")


def _extract_json(text):
    """从模型输出里稳健地提取 JSON 对象。"""
    text = text.strip()
    # 去掉可能的 markdown 代码块围栏
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    # 直接整体尝试
    try:
        return json.loads(text)
    except Exception:
        pass
    # 截取第一个 { 到最后一个 }
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            pass
    raise ValueError(f"无法解析模型 JSON 输出: {text[:300]}")


# ---------------------------------------------------------------- AI 联网找题

# 各方向的搜索 query（用于找真题）
SEARCH_QUERIES = {
    "linux": ["Linux 运维 面试题 进程 文件系统", "Linux 性能排查 面试题", "Shell 脚本 运维 面试题"],
    "network": ["网络 TCP 面试题 运维", "Nginx 负载均衡 面试题", "Kubernetes Service 网络 面试题"],
    "database": ["MySQL 面试题 索引 优化", "数据库 存储 面试题 运维", "PV PVC 存储 面试题"],
    "cloud": ["Kubernetes 面试题 高频 必考", "K8s 容器编排 面试题", "Docker 容器 面试题"],
    "cicd": ["CI/CD 面试题 高频 DevOps", "Jenkins GitLab CI 面试题", "GitOps 发布策略 面试题"],
    "sre": ["SRE 面试题 面经", "监控 告警 可观测性 面试题", "SLO 错误预算 故障 面试题"],
    "ai": ["AI Agent 面试题 大模型应用", "RAG MCP 工具调用 面试题", "LangChain LangGraph 面试题"],
    "all": ["运维工程师 面试题 综合", "DevOps 云原生 面试题", "大厂运维 面经 2026"],
}


def anysearch_batch(queries, max_results=None):
    """调用 anysearch CLI 批量搜索，返回原始 markdown 文本。失败返回空串。"""
    cli = Path(ANYSEARCH_CLI)
    if not cli.exists():
        print(f"[warn] anysearch CLI 不存在: {cli}")
        return ""
    max_results = max_results or SEARCH_MAX_RESULTS
    cmd = ["node", str(cli), "batch_search", "--max_results", str(max_results)]
    for q in queries:
        cmd += ["--query", q]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        out = (proc.stdout or "").strip()
        if not out:
            print(f"[warn] anysearch 空输出: {proc.stderr[:200]}")
        return out
    except FileNotFoundError:
        print("[warn] 未找到 node，无法联网搜索")
        return ""
    except subprocess.TimeoutExpired:
        print("[warn] anysearch 搜索超时")
        return ""
    except Exception as e:
        print(f"[warn] anysearch 调用失败: {e}")
        return ""


# 各方向命题人设定（找题时用于引导 AI 筛选真题）
FINDER_INSTRUCTION = {
    "linux": "Linux 系统运维工程师",
    "network": "网络 / Nginx 工程师",
    "database": "数据库管理员",
    "cloud": "云原生 / Kubernetes 工程师",
    "cicd": "DevOps / CI-CD 工程师",
    "sre": "SRE 站点可靠性工程师",
    "ai": "AI 智能体 / 大模型应用工程师",
}


def ai_find_questions(direction, count=5):
    """联网搜索真题 -> AI 提炼新题 -> 返回候选题列表（不入库，待前端确认）。"""
    # 内置方向用写死的搜索词；自定义方向用其 keyword / 名称构造搜索词
    dir_info = next((d for d in get_directions() if d["id"] == direction), None)
    if direction in SEARCH_QUERIES:
        queries = SEARCH_QUERIES[direction]
        role = FINDER_INSTRUCTION[direction]
    else:
        kw = ((dir_info or {}).get("keyword") or (dir_info or {}).get("name") or direction)
        queries = [f"{kw} 面试题 真题", f"{kw} 面经", f"{kw} 面试考点 高频"]
        role = f"{kw} 领域工程师"
    search_text = anysearch_batch(queries)
    if not search_text:
        raise RuntimeError("联网搜索失败：anysearch 不可用或超时")
    # 已存在的题目（全库去重：跨方向也拦，丢给 AI 让它避开）
    existing = [q["question"] for q in get_questions()["questions"]]
    existing_text = "\n".join(f"- {q}" for q in existing)

    prompt = (
        f"你是资深{role}面试题收集员。下面是刚联网搜索到的面试资料（面经/题库/帖子摘要），\n"
        f"从中提炼出 {count} 道新的、真实的面试题。\n\n"
        f"【要求】\n"
        "1. 只从搜索资料里提炼，不要编造搜索里没有的题\n"
        "2. 必须是与已有题目不重复的新题（已有题目见下）\n"
        "3. 每题给出参考答案要点（3-6条，要专业准确）和常见追问\n"
        "4. 尽量覆盖不同知识点，不要全是同一类\n\n"
        f"【已有题目（请避开）】\n{existing_text}\n\n"
        f"【搜索资料】\n{search_text[:12000]}\n\n"
        "直接输出 JSON 数组，每项格式：\n"
        '[{"topic": "知识点标签", "difficulty": "easy|medium|hard", "importance": "高频|中频|低频", '
        '"question": "题目", "answer": ["要点1", "要点2"], "followups": ["追问1"]}]'
    )
    raw = chat_completion(
        [{"role": "system", "content": "你是严格的面试题库编辑，只输出合法 JSON，不输出任何其他文字。"},
         {"role": "user", "content": prompt}],
        temperature=0.3,
        json_output=True,
    )
    try:
        items = _extract_json(raw)
    except ValueError:
        # 尝试提取 JSON 数组
        m = re.search(r"\[.*\]", raw, re.S)
        if not m:
            raise RuntimeError("AI 返回内容无法解析为题目列表")
        items = json.loads(m.group(0))
    if not isinstance(items, list):
        raise RuntimeError("AI 返回格式错误：不是列表")

    # 清理与去重（保守策略：合并已有 question 文本）
    existing_set = set(existing)
    cleaned = []
    for it in items:
        if not isinstance(it, dict) or not it.get("question"):
            continue
        q_text = str(it["question"]).strip()
        if q_text in existing_set:
            continue
        if any(q_text[:20] in e for e in existing):
            continue
        it["question"] = q_text
        it["topic"] = str(it.get("topic", "未分类")).strip() or "未分类"
        it.setdefault("difficulty", "medium")
        it.setdefault("importance", "中频")
        it["answer"] = [str(a).strip() for a in it.get("answer", []) if str(a).strip()][:8]
        it["followups"] = [str(f).strip() for f in it.get("followups", []) if str(f).strip()][:4]
        if it["answer"]:
            cleaned.append(it)
    return cleaned[:count]


def import_questions(direction, new_questions):
    """把确认的新题写入对应方向的题库文件，刷新缓存。返回 (added_count, 最新题库)。"""
    data = get_questions()
    existing_ids = {q["id"] for q in data["questions"]}

    added = 0
    to_append = []
    existing_qlist = [q["question"] for q in data["questions"]]
    existing_qset = set(existing_qlist)
    for it in new_questions:
        q_text = str(it.get("question", "")).strip()
        # 完全重复 + 前缀相似（前20字符）双重过滤，压低 AI 换说法的近似重题
        if not q_text or q_text in existing_qset:
            continue
        if any(q_text[:20] in e for e in existing_qlist):
            continue
        qid = f"{direction}-new-{added:03d}"
        while qid in existing_ids:
            added += 1
            qid = f"{direction}-new-{added:03d}"
        q = {
            "id": qid,
            "direction": direction,
            "topic": str(it.get("topic", "未分类")).strip() or "未分类",
            "difficulty": it.get("difficulty", "medium")
                if it.get("difficulty") in ("easy", "medium", "hard") else "medium",
            "importance": it.get("importance", "中频")
                if it.get("importance") in ("高频", "中频", "低频") else "中频",
            "question": q_text,
            "answer": [str(a).strip() for a in it.get("answer", []) if str(a).strip()][:8],
            "followups": [str(f).strip() for f in it.get("followups", []) if str(f).strip()][:4],
            "source": "AI 联网补充",
        }
        to_append.append(q)
        existing_ids.add(qid)
        existing_qset.add(q_text)
        added += 1

    if to_append:
        append_questions(direction, to_append)
    return added, get_questions()

# ---------------------------------------------------------------- AI 会话逻辑 (面试官)

DIRECTION_INSTRUCTION = {
    "linux": "你是资深 Linux 系统运维工程师面试官。考察进程线程、文件系统、Shell、系统性能与排查。",
    "network": "你是资深网络/运维工程师面试官。考察 TCP/IP、Nginx 负载均衡、K8s Service/Ingress 网络模型。",
    "database": "你是资深数据库工程师面试官。考察 MySQL、索引优化、慢查询、存储方案。",
    "cloud": "你是资深云原生/Kubernetes 工程师面试官。考察 Docker、K8s 原理、控制器、调度、etcd、探针、自动扩缩容。",
    "cicd": "你是资深 DevOps/CI-CD 工程师面试官。考察 DevOps 文化、CI/CD 流程、流水线设计、Jenkins、GitOps、发布策略。",
    "sre": "你是资深 SRE（站点可靠性工程师）面试官。考察 SLO/错误预算、监控告警、故障管理、可观测性、混沌工程、容量规划。",
    "ai": "你是资深 AI 智能体/大模型应用工程师面试官。考察 Agent 原理、RAG、工具调用、MCP、记忆、工程化、安全。",
    "all": "你是资深互联网公司技术面试官，负责综合技术面试，考察候选人全栈运维能力：操作系统、网络、数据库、容器云原生、CI/CD、监控稳定性、AI 应用等。",
}

def batch_grade(session, batch_size=10):
    """一次 AI 调用评完全场题目（10 题以内一次搞定），避免多次串行调用。
    当前模型输出上限足够大，max_tokens 已放宽，无需分批。
    按题目顺序合并结果。返回 results 列表。"""
    answers = session.get("answers") or []
    if not answers:
        return []
    direction = session["direction"]
    role = DIRECTION_INSTRUCTION.get(direction, DIRECTION_INSTRUCTION["linux"])

    def _grade_chunk(chunk, start_no):
        blocks = []
        for i, a in enumerate(chunk, start_no):
            hint = "；".join(a.get("hint", []) or []) or "（无）"
            blocks.append(
                f"【第{i}题】\n题目：{a['question']}\n"
                f"核心采分点期望：{hint}\n"
                f"候选人回答：{a['answer']}\n"
            )
        body = "\n\n".join(blocks)
        prompt = (
            f"{role}\n"
            f"下面是候选人在一场面试中对其中 {len(chunk)} 道题的完整回答（含面试官追问、候选人多轮补充）。\n"
            "请基于候选人全部轮次综合评分，只要对话里覆盖了关键点，就不应视为遗漏。\n"
            "重要：面试官没有追问到的知识点属于「未考察」，绝不能判为候选人的遗漏或弱点；"
            "只有面试官明确追问了、而候选人答错/答不出/反复避开的点，才算遗漏或弱点。"
            "开放式问题知识点无穷无尽，不要因为候选人没有主动提及某个知识点就扣分。\n"
            "请对每一道题分别打分，严格按照题目顺序输出。\n"
            "评分标准：60 及格，80 良好，90 优秀，95 顶级。\n"
            "每题必须给出：具体点评、优点、弱点（候选人真正的不足）、该题核心采分点、候选人遗漏的点、一个考官式追问。\n"
            "\n"
            f"{body}\n\n"
            "只输出一个 JSON 对象（不要 markdown 代码块、不要任何其他文字）：\n"
            '{"results": [{"score": <0-100整数>, "comment": "...", "strengths": ["..."], '
            '"weaknesses": ["..."], "key_points": ["..."], "missed_points": ["..."], '
            '"followup": "..."}, ...]}  // results 数组长度必须等于题目数，顺序一一对应'
        )
        raw = chat_completion(
            [{"role": "system", "content": "你是严格的面试官，只输出合法 JSON，不输出任何其他文字。"},
             {"role": "user", "content": prompt}],
            temperature=0.3,
            json_output=True,
        )
        try:
            data = _extract_json(raw)
        except ValueError:
            data = {}
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            results = []
        # 长度不足则补空
        if len(results) < len(chunk):
            results += [{}] * (len(chunk) - len(results))
        return results

    all_results = []
    for start in range(0, len(answers), batch_size):
        chunk = answers[start:start + batch_size]
        all_results.extend(_grade_chunk(chunk, start + 1))

    out = []
    for a, r in zip(answers, all_results):
        if not isinstance(r, dict):
            r = {}
        try:
            score = int(r.get("score", 0))
        except (TypeError, ValueError):
            score = 0
        score = max(0, min(100, score))
        out.append({
            "question_id": a.get("question_id", ""),
            "topic": a.get("topic", "未分类"),
            "question": a.get("question", ""),
            "answer": a.get("answer", ""),
            "score": score,
            "comment": r.get("comment", ""),
            "strengths": r.get("strengths", []) or [],
            "weaknesses": r.get("weaknesses", []) or [],
            "key_points": r.get("key_points", []) or [],
            "missed_points": r.get("missed_points", []) or [],
            "followup": r.get("followup", ""),
        })
    return out


def final_decision(session):
    """AI 当终面官，综合整场面试给出录用判定（取代固定分数线）。
    返回 {decision, decision_label, level, reason, strong_points, blocking_issues, advice}"""
    answers = session.get("answers") or []
    results = session.get("results") or []
    if not answers:
        return {"decision": "no_data", "decision_label": "无数据", "reason": "本场没有可评估的回答",
                "level": "", "strong_points": [], "blocking_issues": [], "advice": []}

    direction = session["direction"]
    role = DIRECTION_INSTRUCTION.get(direction, DIRECTION_INSTRUCTION["linux"])
    scores = [r.get("score", 0) for r in results] or [0]
    avg = round(sum(scores) / len(scores), 1)

    # 拼整场对话纪要
    blocks = []
    for i, (a, r) in enumerate(zip(answers, results), 1):
        blocks.append(
            f"【第{i}题 · {a.get('topic','')} · 得分{r.get('score',0)}】\n"
            f"题目：{a.get('question','')}\n"
            f"完整对话：\n{a.get('answer','')}\n"
            f"点评：{r.get('comment','')}\n"
            f"弱点：{'；'.join(r.get('weaknesses', []) or []) or '无'}\n"
            f"遗漏：{'；'.join(r.get('missed_points', []) or []) or '无'}"
        )
    body = "\n\n".join(blocks)

    prompt = (
        f"{role}\n"
        "现在你是这场面试的终面官 / 招聘决策人，需要给出**录用判定**。\n"
        "不要只看平均分，要像真实招聘那样综合判断：技术深度、实战经验、思路是否清晰、\n"
        "被追问后的表现（能不能补上来）、有没有一票否决的硬伤、以及岗位匹配度。\n"
        "参考：本场共 {} 题，平均分 {}（仅作参考，不是录取线）。\n\n".format(len(answers), avg) +
        f"{body}\n\n"
        "请给出录用判定，只输出一个 JSON 对象（不要 markdown、不要其他文字）：\n"
        '{"decision": "hire" 或 "borderline" 或 "no_hire", '
        '"decision_label": "录用 / 待定（建议加面） / 不录用 三选一的中文", '
        '"level": "按当前表现建议的档位，如：可过初面/可过终面/差一档/差距明显", '
        '"confidence": "高/中/低", '
        '"reason": "作为决策人的综合判断理由，3-5句，要具体、敢下结论", '
        '"strong_points": ["让面试官愿意发 offer 的亮点", ...], '
        '"blocking_issues": ["一票否决或必须补的硬伤", ...], '
        '"advice": ["下一步具体怎么补，按优先级排", ...]}'
    )
    raw = chat_completion(
        [{"role": "system", "content": "你是资深技术招聘决策人，只输出合法 JSON，不输出任何其他文字。"},
         {"role": "user", "content": prompt}],
        temperature=0.4, json_output=True,
    )
    try:
        obj = _extract_json(raw)
    except ValueError:
        obj = {}
    if not isinstance(obj, dict):
        obj = {}
    decision = obj.get("decision")
    if decision not in ("hire", "borderline", "no_hire"):
        decision = "borderline"
    label_map = {"hire": "录用", "borderline": "待定（建议加面）", "no_hire": "不录用"}
    return {
        "decision": decision,
        "decision_label": obj.get("decision_label") or label_map[decision],
        "level": str(obj.get("level", "")).strip(),
        "confidence": str(obj.get("confidence", "")).strip(),
        "reason": str(obj.get("reason", "")).strip(),
        "strong_points": [str(x).strip() for x in (obj.get("strong_points") or []) if str(x).strip()],
        "blocking_issues": [str(x).strip() for x in (obj.get("blocking_issues") or []) if str(x).strip()],
        "advice": [str(x).strip() for x in (obj.get("advice") or []) if str(x).strip()],
    }


# ---------------------------------------------------------------- AI 在线出题

AI_QUESTION_INSTRUCTION = {
    "linux": "资深 Linux 系统运维工程师面试官",
    "network": "资深网络/运维工程师面试官",
    "database": "资深数据库工程师面试官",
    "cloud": "资深云原生/Kubernetes 工程师面试官",
    "cicd": "资深 DevOps/CI-CD 工程师面试官",
    "sre": "资深 SRE（站点可靠性工程师）面试官",
    "ai": "资深 AI 智能体/大模型应用工程师面试官",
    "all": "资深全栈运维技术面试官",
    "default": "资深面试官",
}


def _parse_marker(raw):
    """解析面试官输出的标记行 -> (action, text)。默认 followup（由轮数上限兜底收尾）。"""
    raw = (raw or "").strip()
    first_nl = raw.find("\n")
    head = (raw[:first_nl] if first_nl != -1 else raw).strip().upper()
    body = (raw[first_nl + 1:] if first_nl != -1 else "").strip()
    if "DONE" in head:
        action = "done"
    elif "FOLLOWUP" in head or "FOLLOW" in head:
        action = "followup"
    else:
        # 没写标记：整段当正文，默认继续追问
        action = "followup"
        body = raw
    if not body:
        body = "好，这道题先到这，我们进入下一题。" if action == "done" else "能再具体说说吗？"
    return action, body


def interviewer_followup_stream(session, question_obj, dialogue, max_followups=4):
    """流式版面试官追问。yield 事件：
       ("reasoning", text) 思考片段
       ("content", text)   正文片段（已剥离标记行）
       ("meta", {action, text})  结束时给出最终判定
    """
    direction = session["direction"]
    role = DIRECTION_INSTRUCTION.get(direction, DIRECTION_INSTRUCTION["linux"])
    cand_rounds = sum(1 for d in dialogue if d.get("role") == "candidate")

    if cand_rounds >= max_followups:
        text = "好，这道题聊得差不多了，进入下一题。"
        yield ("content", text)
        yield ("meta", {"action": "done", "text": text})
        return

    history_lines = []
    for d in dialogue:
        who = "面试官" if d.get("role") == "interviewer" else "候选人"
        history_lines.append(f"{who}：{d['text']}")
    history = "\n".join(history_lines)
    hint = "；".join(question_obj.get("answer", []) or []) or "（无）"

    prompt = _interviewer_prompt(role, question_obj, hint, history, cand_rounds, max_followups)
    buf = ""          # 原始 content 累积
    head_done = False # 标记行是否已确定
    action = "followup"
    body_out = ""

    for kind, piece in chat_completion_stream(
        [{"role": "system", "content": INTERVIEWER_SYSTEM},
         {"role": "user", "content": prompt}],
        temperature=0.6, max_tokens=600,
    ):
        if kind == "reasoning":
            yield ("reasoning", piece)
            continue
        buf += piece
        if not head_done:
            nl = buf.find("\n")
            if nl == -1:
                # 标记行还没收完；若已明显超长（模型没写标记）则放弃等待
                if len(buf) > 24:
                    head_done = True
                    action = "followup"
                    body_out = buf
                    yield ("content", buf)
                continue
            head = buf[:nl].strip().upper()
            rest = buf[nl + 1:]
            if "DONE" in head:
                action = "done"
            elif "FOLLOWUP" in head or "FOLLOW" in head:
                action = "followup"
            else:
                action = "followup"
                rest = buf  # 没写标记，整段当正文
            head_done = True
            body_out = rest
            if rest:
                yield ("content", rest)
        else:
            body_out += piece
            yield ("content", piece)

    text = body_out.strip()
    if not text:
        text = "好，这道题先到这，我们进入下一题。" if action == "done" else "能再具体说说吗？"
    yield ("meta", {"action": action, "text": text})


INTERVIEWER_SYSTEM = "你是严格的面试官。第一行只输出 [FOLLOWUP] 或 [DONE]，第二行开始说要对候选人说的话。"


def _interviewer_dialogue(dialogue):
    lines = []
    for d in dialogue:
        who = "面试官" if d.get("role") == "interviewer" else "候选人"
        lines.append(f"{who}：{d['text']}")
    return "\n".join(lines)


def _interviewer_prompt(role, question_obj, hint, history, cand_rounds, max_followups):
    return (
        f"{role}\n"
        f"【你现在正在考察的这一道题】{question_obj.get('question', '')}\n"
        f"【参考采分点（只给你判断用，绝不能念给候选人）】{hint}\n\n"
        f"【本题的对话记录】（下面所有\"候选人：\"都是针对**本题**的回答）\n{history}\n\n"
        "你是本场考官，现在轮到你说话。严格遵守：\n"
        "1. 只能围绕**本题**说话。绝对不要在本轮发言里提出新题目或跳到别的知识点——下一题会由系统另外发出。\n"
        "2. 如果候选人答非所问（回答的内容跟本题无关），必须当场指出他跑题了，并把本题重新问一遍让他回答。\n"
        "   不许替他圆场、不许夸他答得好、不许顺着他的跑题内容往下聊。\n"
        "3. 如果回答切题但有明显遗漏/错误/太浅：追问一个具体、有深度的问题，针对他缺的那块刨根问底。\n"
        "   不要提示答案，不要替候选人总结要点。\n"
        "4. 如果回答已覆盖大部分采分点、或你已追问过两轮以上：收尾，只说一句简短的收束语，例如\"好，这道题先到这。\"\n\n"
        f"（本题候选人已答 {cand_rounds} 轮，最多 {max_followups} 轮）\n\n"
        "【输出格式，严格遵守】\n"
        "第一行只写一个标记：继续追问写 [FOLLOWUP]，收尾结束本题写 [DONE]\n"
        "第二行开始写你要对候选人说的话（不要写标记以外的任何格式、不要加引号）\n"
        "另外：你的思考过程会展示给候选人，所以思考时不要逐条复述参考采分点原文，只写你的判断依据。"
    )


def interviewer_followup(session, question_obj, dialogue, max_followups=4):
    """AI 面试官根据候选人的回答决定下一步：追问 or 收尾进入下一题。
    dialogue: 当前题的对话记录 [{role, text}]，role=candidate/interviewer
    返回 (action, text)，action ∈ {"followup", "done"}"""
    direction = session["direction"]
    role = DIRECTION_INSTRUCTION.get(direction, DIRECTION_INSTRUCTION["linux"])
    cand_rounds = sum(1 for d in dialogue if d.get("role") == "candidate")
    if cand_rounds >= max_followups:
        return "done", "好，这道题聊得差不多了，进入下一题。"
    history = _interviewer_dialogue(dialogue)
    hint = "；".join(question_obj.get("answer", []) or []) or "（无）"
    prompt = _interviewer_prompt(role, question_obj, hint, history, cand_rounds, max_followups)
    raw = chat_completion(
        [{"role": "system", "content": INTERVIEWER_SYSTEM},
         {"role": "user", "content": prompt}],
        temperature=0.6,
        max_tokens=600,
    )
    return _parse_marker(raw)


def ai_expand_directions(area_name, count=6):
    """把用户输入的一个领域名自动拆解成多个子方向（类目）。

    例如「运维」→ 操作系统 / 网络 / 数据库 / 容器云原生 / CI/CD / 监控 / AI。
    用户无需自己构思子方向，只需输入一个领域名。返回 [{name, desc, keyword}]，不入库。
    """
    prompt = (
        f"你是一名招聘领域拆解专家。用户输入了一个职业领域：「{area_name}」。\n"
        "这个领域的从业者往往要掌握多个子方向/子类目。"
        "（例：运维工程师要会操作系统、网络、数据库、容器云原生、CI/CD、监控稳定性、AI 智能体）\n"
        f"请把「{area_name}」拆解成 {count} 个最核心、最常用的子方向。\n\n"
        "【要求】\n"
        "1. 每个子方向是一个能独立刷题、独立面试的知识领域，名称简洁（2-6 字，如「渗透测试」「逆向工程」）\n"
        "2. 覆盖这个领域招聘与日常工作的主流方向，互相不重复\n"
        "3. 每个子方向给出 desc（一句话描述：学什么/做什么）和 keyword（联网搜题关键词，3-6 个词、空格分隔，用于搜索真实面经）\n\n"
        "【示例】领域「运维」→\n"
        '[{"name": "操作系统", "desc": "Linux、进程线程、文件系统、Shell 脚本", "keyword": "Linux 运维 面试题 进程 文件系统"}, '
        '{"name": "网络", "desc": "TCP/IP、负载均衡、Nginx、网络排查", "keyword": "网络 TCP 面试题 负载均衡 Nginx"}, '
        '{"name": "容器云原生", "desc": "Docker、Kubernetes、etcd、服务编排", "keyword": "Kubernetes 容器云原生 面试题 Docker"}, '
        '{"name": "监控稳定性", "desc": "SLO、告警、可观测性、故障管理", "keyword": "SRE 监控 可观测性 告警 面试题"}]\n\n'
        "直接输出 JSON 数组，每项格式：{\"name\": \"子方向名\", \"desc\": \"一句话描述\", \"keyword\": \"搜索关键词\"}"
    )
    raw = chat_completion(
        [{"role": "system", "content": "你是严谨的方向拆解专家，只输出合法 JSON 数组，不输出任何其他文字。"},
         {"role": "user", "content": prompt}],
        temperature=0.4,
        json_output=True,
    )
    try:
        items = _extract_json(raw)
    except ValueError:
        m = re.search(r"\[.*\]", raw, re.S)
        if not m:
            raise RuntimeError("AI 拆解失败：返回内容无法解析")
        items = json.loads(m.group(0))
    if isinstance(items, dict):
        items = items.get("items") or items.get("directions") or []
    if not isinstance(items, list):
        raise RuntimeError("AI 拆解失败：返回格式错误")
    cleaned, seen = [], set()
    for it in items:
        if not isinstance(it, dict) or not it.get("name"):
            continue
        name = str(it["name"]).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        cleaned.append({
            "name": name,
            "desc": str(it.get("desc", "")).strip(),
            "keyword": str(it.get("keyword", "")).strip() or name,
        })
    # 过滤已存在的方向（前端也过滤，这里兜底）
    existing = {d["name"] for d in get_directions()}
    cleaned = [it for it in cleaned if it["name"] not in existing]
    return cleaned[:count]


def ai_generate_question(direction, asked_topics=None):
    """AI 现场出一道新题（不入库）。返回题目对象（含 id=topic摘要，answer 为参考采分点）。"""
    role = AI_QUESTION_INSTRUCTION.get(direction)
    if not role:
        d = next((x for x in get_directions() if x["id"] == direction), None)
        role = f"资深「{(d or {}).get('name', direction)}」方向面试官"
    asked = asked_topics or []
    asked_line = "、".join(asked) if asked else "（首题，无历史）"

    prompt = (
        f"你是{role}，正在主持一场真实面试。\n"
        f"已考察的知识点：{asked_line}\n\n"
        "请出一道与上面已考察知识点不重复的新题，要求：\n"
        "1. 贴近真实面试的高频考点，不要出偏题怪题\n"
        "2. 最好考察候选人容易忽略的细节，或对其薄弱点的追问（若有）\n"
        "3. 给出参考答案要点（3-6条，专业准确）\n"
        "直接输出 JSON：\n"
        '{"topic": "知识点标签", "difficulty": "easy|medium|hard", "importance": "高频|中频|低频", '
        '"question": "题目", "answer": ["要点1", "要点2"], "followups": ["追问1"]}'
    )
    raw = chat_completion(
        [{"role": "system", "content": "你是严格的命题考官，只输出合法 JSON，不输出任何其他文字。"},
         {"role": "user", "content": prompt}],
        temperature=0.7,
        json_output=True,
    )
    obj = _extract_json(raw)
    if not isinstance(obj, dict) or not obj.get("question"):
        raise RuntimeError("AI 出题失败：返回格式错误")
    q = {
        "id": "ai-" + re.sub(r"[^a-zA-Z0-9]", "", obj["question"][:12]) + "-" + uuid.uuid4().hex[:6],
        "topic": str(obj.get("topic", "AI 随机题")).strip() or "AI 随机题",
        "difficulty": obj.get("difficulty", "medium") if obj.get("difficulty") in ("easy", "medium", "hard") else "medium",
        "importance": obj.get("importance", "中频") if obj.get("importance") in ("高频", "中频", "低频") else "中频",
        "question": str(obj["question"]).strip(),
        "answer": [str(a).strip() for a in obj.get("answer", []) if str(a).strip()][:8],
        "followups": [str(f).strip() for f in obj.get("followups", []) if str(f).strip()][:4],
        "ai_generated": True,
    }
    return q


def summarize_session(session):
    """生成整场面试总结报告：总分、按方向/topic 弱点。"""
    items = session["results"]  # [{question_id, score, comment, strengths, weaknesses}]
    if not items:
        return {"total_score": 0, "verdict": "未完成任何题目"}
    scores = [it["score"] for it in items]
    avg = round(sum(scores) / len(scores), 1)
    best = max(scores)
    worst = min(scores)
    # 按 topic 聚合（AI 出的题不在题库，直接用 record 里的 topic）
    topic_scores = {}
    for it in items:
        topic = it.get("topic") or "未知"
        topic_scores.setdefault(topic, []).append(it["score"])
    weakness_topics = []
    for topic, scs in topic_scores.items():
        t_avg = sum(scs) / len(scs)
        if t_avg < 80:
            weakness_topics.append({"topic": topic, "avg_score": round(t_avg, 1)})
    weakness_topics.sort(key=lambda x: x["avg_score"])

    # 汇聚所有 weaknesses，找出最高频的
    from collections import Counter
    wc = Counter()
    for it in items:
        for w in it.get("weaknesses", []):
            wc[w.strip()] += 1
    top_weaknesses = [{"weakness": w, "count": c} for w, c in wc.most_common(5)]

    verdict = f"共 {len(scores)} 题 · 场均 {avg} 分"

    return {
        "total_score": avg,
        "best": best,
        "worst": worst,
        "items": len(items),
        "verdict": verdict,
        "weakness_topics": weakness_topics,
        "top_weaknesses": top_weaknesses,
    }


# ---------------------------------------------------------------- HTTP 服务

class Handler(BaseHTTPRequestHandler):
    server_version = "InterviewRange/1.0"

    # ---------- 工具 ----------
    def _send(self, status, data, content_type="application/json; charset=utf-8"):
        if isinstance(data, (dict, list)):
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        elif isinstance(data, str):
            body = data.encode("utf-8")
        else:
            body = data
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _sse_start(self):
        """开启 SSE 响应（无 Content-Length，靠连接关闭结束）"""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def _sse(self, event, data):
        try:
            payload = json.dumps(data, ensure_ascii=False)
            self.wfile.write(f"event: {event}\ndata: {payload}\n\n".encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            raise

    def _serve_static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        file_path = (WEB_DIR / path.lstrip("/")).resolve()
        # 安全：不允许越出 web 目录
        if not str(file_path).startswith(str(WEB_DIR.resolve())):
            self._send(403, "forbidden", "text/plain; charset=utf-8")
            return
        if not file_path.exists() or not file_path.is_file():
            self._send(404, "not found", "text/plain; charset=utf-8")
            return
        content_type = "application/octet-stream"
        suf = file_path.suffix.lower()
        if suf == ".html":
            content_type = "text/html; charset=utf-8"
        elif suf == ".js":
            content_type = "application/javascript; charset=utf-8"
        elif suf == ".css":
            content_type = "text/css; charset=utf-8"
        elif suf == ".json":
            content_type = "application/json; charset=utf-8"
        elif suf in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico"):
            content_type = {
                ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".gif": "image/gif", ".svg": "image/svg+xml", ".webp": "image/webp",
                ".ico": "image/x-icon",
            }[suf]
        self._send(200, file_path.read_bytes(), content_type)

    # ---------- 路由 ----------
    def do_GET(self):
        route = self.path.split("?")[0]
        if route == "/api/questions":
            self._send(200, get_questions())
        elif route == "/api/directions/trash":
            self._send(200, get_trash())
        elif route == "/api/directions":
            self._send(200, get_directions())
        elif route == "/api/health":
            try:
                _load_sense_config()
                self._send(200, {"ok": True, "model": MODEL, "provider": "sense"})
            except Exception as e:
                self._send(200, {"ok": False, "error": str(e)})
        elif route == "/api/history":
            self._send(200, load_history_index())
        elif route.startswith("/api/history/"):
            sid = route.split("/")[-1]
            s = load_session(sid)
            if not s:
                self._send(404, {"error": "场次不存在"})
            else:
                self._send(200, s)
        elif route.startswith("/api/followup/"):
            qid = route.split("/")[-1]
            topic = ""
            if "?" in self.path:
                from urllib.parse import parse_qs
                topic = (parse_qs(self.path.split("?", 1)[1]).get("topic") or [""])[0]
            self._send(200, get_followup_session(qid, topic))
        elif route == "/api/state":
            self._send(200, load_state())
        elif route == "/api/notes":
            self._send(200, {"notes": load_notes()})
        elif route == "/api/session/live":
            live = []
            for sid, s in SESSIONS.items():
                if s.get("status") != "running":
                    continue
                live.append({
                    "session_id": sid,
                    "direction": s.get("direction"),
                    "direction_name": s.get("direction_name"),
                    "total": s.get("total", 10),
                    "answered": len(s.get("answers") or []),
                    "current": s.get("current"),
                    "current_q": self._resolve_question(s, s["current"]) if s.get("current") else None,
                    "dialogue": s.get("dialogue") or [],
                    "answers": s.get("answers") or [],
                    "created_at": s.get("created_at"),
                })
            self._send(200, {"live": live})
        elif route.startswith("/api/session/"):
            sid = route.split("/")[-1]
            s = SESSIONS.get(sid)
            if not s:
                self._send(404, {"error": "会话不存在"})
            else:
                self._send(200, s)
        elif route.startswith("/api/"):
            self._send(404, {"error": f"未知接口 {route}"})
        else:
            self._serve_static(route)

    def do_POST(self):
        route = self.path.split("?")[0]
        body = self._read_json()
        try:
            if route == "/api/session/new":
                self._handle_new_session(body)
            elif route == "/api/session/retry":
                self._handle_session_retry(body)
            elif route == "/api/session/skip":
                self._handle_skip(body)
            elif route == "/api/session/end":
                self._handle_end(body)
            elif route == "/api/session/generate":
                self._handle_generate(body)
            elif route == "/api/review/chat":
                self._handle_review_chat(body)
            elif route == "/api/session/answer/stream":
                self._handle_answer_stream(body)
            elif route == "/api/review/chat/stream":
                self._handle_review_chat_stream(body)
            elif route == "/api/followup/ask":
                self._handle_followup_ask(body)
            elif route == "/api/followup/ask/stream":
                self._handle_followup_ask_stream(body)
            elif route == "/api/followup/reset":
                self._handle_followup_reset(body)
            elif route == "/api/find-questions":
                self._handle_find_questions(body)
            elif route == "/api/import-questions":
                self._handle_import_questions(body)
            elif route == "/api/questions/delete":
                self._handle_delete_question(body)
            elif route == "/api/directions/expand":
                self._handle_expand_directions(body)
            elif route == "/api/directions/batch-add":
                self._handle_batch_add_directions(body)
            elif route == "/api/directions/restore":
                self._handle_direction_restore(body)
            elif route == "/api/directions/purge":
                self._handle_direction_purge(body)
            elif route == "/api/directions/purge-all":
                self._handle_direction_purge_all(body)
            elif route == "/api/directions":
                self._handle_directions(body)
            elif route == "/api/state/mastered":
                self._handle_toggle_mastered(body)
            elif route == "/api/state/bookmarks":
                self._handle_toggle_bookmark(body)
            elif route == "/api/state":
                self._handle_update_state(body)
            elif route == "/api/notes":
                self._handle_save_note(body)
            elif route == "/api/history/delete":
                self._handle_delete_session(body)
            else:
                self._send(404, {"error": f"未知接口 {route}"})
        except RuntimeError as e:
            self._send(502, {"error": str(e)})
        except Exception as e:
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    # ---------- 会话动作 ----------
    def _handle_followup_reset(self, body):
        """清空某常见追问的会话，重新开始。"""
        question_id = str(body.get("question_id", "")).strip()
        topic = str(body.get("topic", "")).strip()
        if not question_id:
            self._send(400, {"error": "缺少 question_id"})
            return
        reset_followup_session(question_id, topic)
        self._send(200, {"message": "追问记录已清空"})

    def _handle_followup_ask(self, body):
        """题目常见追问的多轮对话（按 题目+常见追问 持久化独立会话）。
        带完整历史调 AI，生成失败会回滚刚写入的用户消息。"""
        question_id = str(body.get("question_id", "")).strip()
        message = str(body.get("message", "")).strip()
        topic = str(body.get("topic", "")).strip()
        if not question_id or not message:
            self._send(400, {"error": "缺少参数 question_id / message"})
            return
        q = get_question_by_id(question_id)
        if not q:
            self._send(404, {"error": "题目不存在"})
            return
        # 用户消息先入库，再带完整历史调 AI
        append_followup_message(question_id, q["question"], "user", message, topic)
        messages = self._build_followup_messages(question_id, q, topic)
        try:
            reply = chat_completion(messages, temperature=0.4, max_tokens=4096)
        except Exception:
            remove_last_followup_message(question_id, topic)   # 撤回用户消息，避免脏数据
            raise
        append_followup_message(question_id, q["question"], "assistant", reply, topic)
        sess = get_followup_session(question_id, topic)
        self._send(200, {
            "question_id": question_id,
            "question": q["question"],
            "messages": sess["messages"],
            "reply": reply,
        })

    def _build_followup_messages(self, question_id, q, topic=""):
        """构造追问 AI 调用的消息列表：题目上下文 + 完整历史。
        第一轮（还没有任何 AI 回复）聚焦"面试中怎么答"的要点；后续追问再深入细节。"""
        sess = get_followup_session(question_id, topic)
        is_first = not any(m["role"] == "assistant" for m in sess["messages"])
        if is_first:
            system = ("你是资深面试官，正在帮求职者准备这道面试题。"
                      "请站在真实面试场景的角度组织回答：给出这道题在面试中应如何回答的要点和话术框架，"
                      "覆盖核心得分点即可，不必展开过深的实现细节或长篇知识讲解；"
                      "用户如果对某部分不了解，会继续追问。用中文回答，要点清晰、口语化、便于口头复述。")
        else:
            system = ("你是资深面试官，正在给求职者讲解一道面试题及其常见追问。"
                      "请结合题目与已有对话，针对用户的最新提问给出深入、准确、条理清晰的解答，"
                      "必要时举例说明。用中文回答，简洁直接。")
        ans_points = "\n".join(f"- {a}" for a in (q.get("answer") or [])) or "(暂无)"
        ctx_parts = []
        if q.get("direction"):
            ctx_parts.append(f"【所属方向】{q['direction']}")
        if q.get("topic"):
            ctx_parts.append(f"【题目标题】{q['topic']}")
        ctx_parts.append(f"【题面】{q['question']}")
        ctx_parts.append(f"【参考答案要点】\n{ans_points}")
        ctx = "\n".join(ctx_parts)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": ctx + "\n\n这是针对该题的讨论背景，请直接回答用户后续提出的问题。"},
        ]
        for m in sess["messages"]:
            if m["role"] in ("user", "assistant"):
                messages.append({"role": m["role"], "content": m["content"]})
        return messages

    def _handle_followup_ask_stream(self, body):
        """追问流式回答（SSE）：reasoning/content/done/error 事件，完成后落盘完整回复。"""
        question_id = str(body.get("question_id", "")).strip()
        message = str(body.get("message", "")).strip()
        topic = str(body.get("topic", "")).strip()
        if not question_id or not message:
            self._send(400, {"error": "缺少参数 question_id / message"})
            return
        q = get_question_by_id(question_id)
        if not q:
            self._send(404, {"error": "题目不存在"})
            return
        append_followup_message(question_id, q["question"], "user", message, topic)
        messages = self._build_followup_messages(question_id, q, topic)
        self._sse_start()
        full = ""
        try:
            for kind, piece in chat_completion_stream(messages, temperature=0.4, max_tokens=4096):
                if kind == "reasoning":
                    self._sse("reasoning", {"t": piece})
                elif kind == "content":
                    full += piece
                    self._sse("content", {"t": piece})
        except (BrokenPipeError, ConnectionResetError):
            # 用户主动停止：保留已问出的用户消息，不落盘半截回答
            return
        except Exception as e:
            remove_last_followup_message(question_id, topic)   # 撤回用户消息，避免脏数据
            try:
                self._sse("error", {"message": str(e)})
            except Exception:
                pass
            return
        append_followup_message(question_id, q["question"], "assistant", full, topic)
        sess = get_followup_session(question_id, topic)
        try:
            self._sse("done", {"messages": sess["messages"]})
        except Exception:
            pass

    def _handle_generate(self, body):
        """AI 现场出道新题（不入库）并设为当前题。会先提交当前题的已答轮次。"""
        sid = body.get("session_id")
        s = SESSIONS.get(sid)
        if not s:
            self._send(404, {"error": "会话不存在"})
            return
        # 先提交当前题的对话
        self._finalize_current(s)
        # 已考过的 topic，避免重复出题（答案存在 answers 里）
        asked_topics = [a.get("topic") for a in s.get("answers", []) if a.get("topic")]
        q = ai_generate_question(s["direction"], asked_topics)
        # AI 出的题作为当前题（队列不动，答完回队列剩余题）
        s["ai_current"] = q
        s["current"] = q["id"]
        s["dialogue"] = []
        _persist_live(s)
        self._send(200, {"session_id": sid, "question": {
            "id": q["id"], "topic": q["topic"], "difficulty": q["difficulty"],
            "importance": q["importance"], "question": q["question"],
            "ai_generated": True,
        }})

    def _handle_find_questions(self, body):
        """联网搜真题 -> AI 提炼 -> 返回候选列表（SSE 流式推送进度，支持多方向）。"""
        valid_ids = [d["id"] for d in get_directions()]
        raw = body.get("directions") or (body.get("direction") if body.get("direction") else None)
        if isinstance(raw, str):
            dirs = [raw]
        else:
            dirs = list(raw or [])
        dirs = [d for d in dirs if d in valid_ids]
        if not dirs:
            self._send(400, {"error": "请选择至少一个有效方向"})
            return
        count = int(body.get("count", 5))
        count = max(1, min(10, count))
        self._sse_start()
        all_items, seen = [], set()
        name_of = {d["id"]: d["name"] for d in get_directions()}
        total = len(dirs)
        for i, d in enumerate(dirs):
            dname = name_of.get(d, d)
            self._sse("progress", {"stage": "start", "total": total, "index": i + 1, "dir": d, "dir_name": dname})
            try:
                items = ai_find_questions(d, count)
            except RuntimeError as e:
                self._sse("progress", {"stage": "error", "done": i + 1, "total": total, "dir": d,
                                       "dir_name": dname, "message": str(e)})
                continue
            accepted = []
            for it in items:
                qk = it.get("question", "")[:40]
                if not qk or qk in seen:
                    continue
                seen.add(qk)
                it["direction"] = d
                all_items.append(it)
                accepted.append(it)
            self._sse("progress", {"stage": "done", "done": i + 1, "total": total, "dir": d,
                                   "dir_name": dname, "items": accepted})
        if not all_items:
            self._sse("done", {"candidates": [], "message": "没有提炼出新题（可能搜索到的题都已有或搜索失败）"})
            return
        self._sse("done", {"candidates": all_items, "message": f"联网找到 {len(all_items)} 道候选新题"})

    def _handle_import_questions(self, body):
        """把前端确认的题写入题库。body: {direction, questions: [...]}"""
        direction = body.get("direction")
        new_qs = body.get("questions") or []
        if direction not in [d["id"] for d in get_directions()]:
            self._send(400, {"error": "无效方向"})
            return
        if not isinstance(new_qs, list) or not new_qs:
            self._send(400, {"error": "没有要导入的题"})
            return
        added, data = import_questions(direction, new_qs)
        self._send(200, {
            "added": added,
            "message": f"成功导入 {added} 道新题（题库现有 {len(data['questions'])} 题）",
            "total": len(data["questions"]),
        })

    def _handle_delete_question(self, body):
        """删除单道题目。body: {question_id}"""
        try:
            removed = delete_question(body.get("question_id"))
        except ValueError as e:
            self._send(400, {"error": str(e)})
            return
        self._send(200, {"removed": removed, "message": "已删除该题"})

    def _handle_directions(self, body):
        """新增 / 删除自定义方向。body.action: add | delete。"""
        action = body.get("action", "add")
        if action == "add":
            name = body.get("name")
            keyword = body.get("keyword")
            desc = body.get("desc", "")
            if not name or not str(name).strip():
                self._send(400, {"error": "方向名称不能为空"})
                return
            try:
                entry = add_direction(str(name), str(keyword or ""), str(desc or ""))
            except ValueError as e:
                self._send(400, {"error": str(e)})
                return
            self._send(200, {"direction": entry, "message": f"已创建方向「{entry['name']}」"})
        elif action == "delete":
            direction_id = body.get("direction_id")
            if not direction_id:
                self._send(400, {"error": "缺少 direction_id"})
                return
            try:
                res = delete_direction(direction_id)
            except ValueError as e:
                self._send(400, {"error": str(e)})
                return
            if not res.get("moved"):
                self._send(400, {"error": "该方向不存在或已在回收站"})
                return
            self._send(200, {
                "message": f"已移入回收站（含 {res['question_count']} 题，可随时还原）",
                "question_count": res["question_count"],
            })
        elif action == "reorder":
            ids = body.get("ids") or []
            if not isinstance(ids, list) or not ids:
                self._send(400, {"error": "缺少排序 id 列表"})
                return
            new_dirs = reorder_directions([str(i) for i in ids])
            self._send(200, {"directions": new_dirs, "message": "顺序已保存"})
        else:
            self._send(400, {"error": f"未知动作 {action}"})

    def _handle_expand_directions(self, body):
        """AI 把领域名拆解成候选子方向列表（不入库，前端确认后批量创建）。"""
        name = str(body.get("name", "")).strip()
        if not name:
            self._send(400, {"error": "领域名称不能为空"})
            return
        try:
            items = ai_expand_directions(name)
        except RuntimeError as e:
            self._send(502, {"error": str(e)})
            return
        if not items:
            self._send(200, {"candidates": [], "message": "没有拆出新的子方向（可能都已存在）"})
            return
        self._send(200, {"candidates": items, "message": f"拆出 {len(items)} 个子方向"})

    def _handle_batch_add_directions(self, body):
        """批量创建自定义方向。body: {directions: [{name, desc, keyword}]}"""
        items = body.get("directions") or []
        if not isinstance(items, list) or not items:
            self._send(400, {"error": "没有要创建的方向"})
            return
        created, skipped, existing = [], [], {d["name"] for d in get_directions()}
        for it in items:
            name = str(it.get("name", "")).strip()
            if not name or name in existing:
                skipped.append(name or "(空)")
                continue
            entry = add_direction(name, str(it.get("keyword", "") or name), str(it.get("desc", "")))
            created.append(entry)
            existing.add(name)
        msg = f"创建 {len(created)} 个方向"
        if skipped:
            msg += f"，跳过 {len(skipped)} 个已存在"
        self._send(200, {"directions": created, "skipped": skipped, "message": msg})

    def _handle_direction_restore(self, body):
        """从回收站还原方向。"""
        direction_id = body.get("direction_id")
        if not direction_id:
            self._send(400, {"error": "缺少 direction_id"})
            return
        if not restore_direction(direction_id):
            self._send(400, {"error": "该方向不在回收站"})
            return
        self._send(200, {"message": "方向已还原"})

    def _handle_direction_purge(self, body):
        """彻底删除回收站中的某个方向（含其题目，不可恢复）。"""
        direction_id = body.get("direction_id")
        if not direction_id:
            self._send(400, {"error": "缺少 direction_id"})
            return
        if not purge_direction(direction_id):
            self._send(400, {"error": "该方向不在回收站"})
            return
        self._send(200, {"message": "已彻底删除（题目已移除）"})

    def _handle_direction_purge_all(self, body):
        """清空回收站：彻底删除全部已删除方向及其题目。"""
        n = purge_all_trash()
        self._send(200, {"message": f"已彻底删除 {n} 个方向" if n else "回收站已是空的"})

    def _handle_toggle_mastered(self, body):
        qid = body.get("question_id")
        if not qid:
            self._send(400, {"error": "缺少 question_id"})
            return
        action, mastered = toggle_mastered(qid)
        self._send(200, {"action": action, "mastered": mastered, "question_id": qid})

    def _handle_toggle_bookmark(self, body):
        qid = body.get("question_id")
        if not qid:
            self._send(400, {"error": "缺少 question_id"})
            return
        action, bookmarks = toggle_bookmark(qid)
        self._send(200, {"action": action, "bookmarks": bookmarks, "question_id": qid})

    def _handle_update_state(self, body):
        """更新学习状态（last_direction / mix_exclude）。"""
        state = load_state()
        if "last_direction" in body:
            state["last_direction"] = body["last_direction"]
            set_last_direction(body["last_direction"])
        if "mix_exclude" in body:
            state["mix_exclude"] = set_mix_exclude(body["mix_exclude"])
        self._send(200, state)

    def _handle_save_note(self, body):
        """保存/更新/清空某题的刷题笔记。body: {question_id, note}"""
        try:
            has, notes = save_note(body.get("question_id"), body.get("note"))
        except ValueError as e:
            self._send(400, {"error": str(e)})
            return
        self._send(200, {"notes": notes, "saved": has, "message": "笔记已保存" if has else "笔记已清除"})

    def _handle_delete_session(self, body):
        sid = body.get("session_id")
        if not sid:
            self._send(400, {"error": "缺少 session_id"})
            return
        ok = delete_session(sid)
        if ok:
            self._send(200, {"ok": True, "message": "场次已删除"})
        else:
            self._send(404, {"error": "场次不存在"})

    def _handle_new_session(self, body):
        direction = body.get("direction")
        dir_ids = [d["id"] for d in get_directions()]
        if direction not in dir_ids and direction != "all":
            self._send(400, {"error": "无效方向"})
            return
        import random

        if direction == "all":
            # 综合混考：从每个方向均匀抽题，保证覆盖面；可排除指定方向（exclude_dirs）
            exclude = set(body.get("exclude_dirs") or [])
            picked = []
            for did in dir_ids:
                if did in exclude:
                    continue
                pool = questions_by_direction(did)
                random.shuffle(pool)
                picked.extend(pool[:3])  # 每方向最多3题
            random.shuffle(picked)
            qs = picked[:10]
            direction_name = "综合混考"
        else:
            qs = questions_by_direction(direction)
            random.shuffle(qs)
            direction_name = next(d["name"] for d in get_directions() if d["id"] == direction)
        if not qs:
            self._send(400, {"error": f"方向 {direction} 暂无题目"})
            return
        sid = uuid.uuid4().hex[:12]
        session = {
            "session_id": sid,
            "direction": direction,
            "direction_name": direction_name,
            "queue": [q["id"] for q in qs[:10]],   # 每场最多 10 题
            "total": len(qs[:10]),               # 本场目标题数（AI 插入题不计入）
            "current": None,
            "dialogue": [],   # 当前题的对话记录 [{role, text}]：一问一答追问
            "results": [],
            "answers": [],
            "status": "running",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        SESSIONS[sid] = session
        _persist_live(session)
        next_qid = session["queue"].pop(0)
        session["current"] = next_qid
        q = find_question(next_qid)
        _persist_live(session)
        self._send(200, {
            "session_id": sid,
            "direction": session["direction_name"],
            "total": session["total"],
            "answered": 0,
            "question": {"id": q["id"], "topic": q["topic"], "difficulty": q["difficulty"],
                          "importance": q["importance"], "question": q["question"]},
        })

    def _finalize_current(self, s):
        """把当前题的完整对话合并为一条答案，存入 answers。"""
        if not s.get("current") or not s.get("dialogue"):
            return None
        q = self._resolve_question(s, s["current"])
        if not q:
            return None
        lines = []
        for d in s["dialogue"]:
            who = "面试官" if d.get("role") == "interviewer" else "候选人"
            lines.append(f"{who}：{d['text']}")
        answer = "\n".join(lines)
        s.setdefault("answers", []).append({
            "question_id": s["current"],
            "topic": q.get("topic", "未分类"),
            "question": q.get("question", ""),
            "answer": answer,
            "hint": q.get("answer", []),
            "followups": q.get("followups", []),
        })
        s["dialogue"] = []
        return s["current"]

    def _resolve_question(self, s, qid):
        q = find_question(qid)
        if q is None:
            q = s.get("ai_current") or {}
        return q or None

    def _next_question(self, s):
        """从队列取下一题设为当前题（跳过已被删除的题）。返回 (next_question, finished)。"""
        while s["queue"]:
            next_qid = s["queue"].pop(0)
            nq = find_question(next_qid)
            if nq is None:
                continue  # 题目被删了，跳过继续取下一题
            s["current"] = next_qid
            s["dialogue"] = []
            return ({"id": nq["id"], "topic": nq["topic"], "difficulty": nq["difficulty"],
                     "importance": nq["importance"], "question": nq["question"]}, False)
        s["current"] = None
        s["dialogue"] = []
        s["status"] = "finished"
        return (None, True)

    def _handle_session_retry(self, body):
        """重发当前题最后一条回答：若最后一条是候选人的回答（尚未收到面试官回复），回滚它，
        前端即可原样重新发送。"""
        sid = body.get("session_id")
        s = SESSIONS.get(sid)
        if not s:
            self._send(404, {"error": "会话不存在"})
            return
        dl = s.get("dialogue") or []
        removed = False
        if dl and dl[-1].get("role") == "candidate":
            dl.pop()
            _persist_live(s)
            removed = True
        self._send(200, {"ok": True, "removed": removed})

    def _handle_skip(self, body):
        """跳过当前题（不追问），提交已有对话并进入下一题。"""
        sid = body.get("session_id")
        s = SESSIONS.get(sid)
        if not s:
            self._send(404, {"error": "会话不存在"})
            return
        self._finalize_current(s)
        answered = len(s["answers"])
        next_question, finished = self._next_question(s)
        _persist_live(s)
        self._send(200, {
            "session_id": sid, "answered": answered, "total": s.get("total", 10),
            "finished": finished, "next_question": next_question,
        })

    def _handle_end(self, body):
        sid = body.get("session_id")
        s = SESSIONS.get(sid)
        if not s:
            self._send(404, {"error": "会话不存在"})
            return
        # 一道题都没作答：不评分、不生成空报告、不落盘，前端直接返回面试首页
        if not s.get("answers"):
            SESSIONS.pop(sid, None)
            _drop_live(sid)
            self._send(200, {"session_id": sid, "empty": True})
            return
        # 提交未收尾的当前题（避免最后一题被丢弃）
        self._finalize_current(s)
        # 尚未评分则进行批量评分（分批 AI 调用，避免超长截断）
        if not s.get("results") and s.get("answers"):
            s["results"] = batch_grade(s)
        report = summarize_session(s)
        # 终面官录用判定（取代固定分数线）
        try:
            report["decision"] = final_decision(s)
        except Exception as e:
            print(f"[warn] 录用判定失败: {e}")
            report["decision"] = {"decision": "unknown", "decision_label": "判定失败",
                                  "reason": str(e), "level": "", "strong_points": [],
                                  "blocking_issues": [], "advice": []}
        s["report"] = report
        s["status"] = "ended"

        # 落盘：单场完整明细写入独立文件 + 更新索引
        detail = []
        for r in s["results"]:
            detail.append({
                "question_id": r.get("question_id", ""),
                "topic": r.get("topic", ""),
                "question": r.get("question", ""),
                "answer": r.get("answer", ""),
                "score": r.get("score", 0),
                "comment": r.get("comment", ""),
                "strengths": r.get("strengths", []),
                "weaknesses": r.get("weaknesses", []),
                "key_points": r.get("key_points", []),
                "missed_points": r.get("missed_points", []),
                "followup": r.get("followup", ""),
            })
        session_detail = {
            "session_id": sid,
            "direction": s["direction"],
            "direction_name": s["direction_name"],
            "created_at": s["created_at"],
            "ended_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "items": len(s["results"]),
            "total_score": report["total_score"],
            "decision": report.get("decision", {}),
            "report": {k: v for k, v in report.items() if k != "decision"},
            "results": detail,
        }
        save_session(session_detail)
        s["status"] = "ended"
        SESSIONS.pop(sid, None)
        _drop_live(sid)

        self._send(200, {"session_id": sid, "report": report, "results": s["results"]})

    def _handle_review_chat(self, body):
        """复盘对话：针对某道已评分的题，AI 以导师口吻继续回应（可追问、反驳、讲解）。
        body: {question, my_answer, score, comment, messages:[{role,text}]}"""
        question = (body.get("question") or "").strip()
        my_answer = (body.get("my_answer") or "").strip()
        score = body.get("score")
        comment = (body.get("comment") or "").strip()
        messages = body.get("messages") or []
        if not question:
            self._send(400, {"error": "缺少题目"})
            return

        system = (
            "你是资深技术面试官兼复盘导师。现在在帮候选人复盘一道已经评分过的面试题。\n"
            "背景：题目、候选人的回答、当时的得分和点评已经给出。\n"
            "你的任务：像一位耐心的导师，和候选人深入交流这道题。\n"
            "支持三种情形：\n"
            "1. 候选人追问知识点细节 -> 深入讲解，举例子、给命令/配置/代码\n"
            "2. 候选人对评分有异议/反驳 -> 认真核对，说得对就承认并修正，说得不对就解释为什么\n"
            "3. 候选人想练习 -> 出一道相关的追问让他回答\n"
            "风格：口语化、有针对性、不啰嗦，像真人在辅导。回答用中文。"
        )
        ctx = (
            f"【题目】{question}\n"
            f"【候选人当时的回答】{my_answer or '（未记录）'}\n"
            f"【当时得分】{score if score is not None else '未知'}\n"
            f"【当时点评】{comment or '（无）'}\n"
            "【注意】现在不是重新评分，而是帮助候选人真正学会这道题。"
        )
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": ctx}]
        for m in messages[-20:]:
            role = m.get("role")
            text = (m.get("text") or "").strip()
            if not text:
                continue
            if role == "user":
                msgs.append({"role": "user", "content": text})
            elif role == "assistant":
                msgs.append({"role": "assistant", "content": text})
        raw = chat_completion(msgs, temperature=0.6, max_tokens=1200)
        self._send(200, {"text": raw.strip()})

    def _handle_answer_stream(self, body):
        """流式答题：思考片段 + 正文片段 + 最终 meta（action/下一题）。"""
        sid = body.get("session_id")
        qid = body.get("question_id")
        answer = (body.get("answer") or "").strip()
        s = SESSIONS.get(sid)
        if not s:
            self._send(404, {"error": "会话不存在"}); return
        if not qid or (qid != s["current"] and qid != (s.get("ai_current") or {}).get("id")):
            self._send(400, {"error": "题目状态不匹配"}); return
        if not answer:
            self._send(400, {"error": "回答不能为空"}); return
        q = self._resolve_question(s, qid)
        if not q:
            self._send(400, {"error": "题目不存在"}); return

        s.setdefault("dialogue", []).append({"role": "candidate", "text": answer})
        self._sse_start()
        action, itext = "followup", ""
        try:
            for kind, payload in interviewer_followup_stream(s, q, s["dialogue"]):
                if kind == "reasoning":
                    self._sse("reasoning", {"t": payload})
                elif kind == "content":
                    self._sse("content", {"t": payload})
                elif kind == "meta":
                    action = payload.get("action", "followup")
                    itext = payload.get("text", "")
        except Exception as e:
            # 流中途失败：回滚这轮回答，让前端可重试
            if s.get("dialogue") and s["dialogue"][-1].get("role") == "candidate":
                s["dialogue"].pop()
            self._sse("error", {"message": str(e)})
            return

        s["dialogue"].append({"role": "interviewer", "text": itext})
        meta = {"action": action, "interviewer_text": itext,
                "answered": len(s["answers"]), "total": s.get("total", 10),
                "finished": False, "next_question": None}
        if action == "done":
            self._finalize_current(s)
            meta["answered"] = len(s["answers"])
            nq, finished = self._next_question(s)
            meta["finished"] = finished
            meta["next_question"] = nq
        _persist_live(s)
        self._sse("meta", meta)
        self._sse("done", {})

    def _handle_review_chat_stream(self, body):
        """流式复盘对话：思考 + 正文逐段推送。"""
        question = (body.get("question") or "").strip()
        my_answer = (body.get("my_answer") or "").strip()
        score = body.get("score")
        comment = (body.get("comment") or "").strip()
        messages = body.get("messages") or []
        if not question:
            self._send(400, {"error": "缺少题目"}); return

        system = (
            "你是资深技术面试官兼复盘导师。现在在帮候选人复盘一道已经评分过的面试题。\n"
            "背景：题目、候选人的回答、当时的得分和点评已经给出。\n"
            "你的任务：像一位耐心的导师，和候选人深入交流这道题。\n"
            "支持三种情形：\n"
            "1. 候选人追问知识点细节 -> 深入讲解，举例子、给命令/配置/代码\n"
            "2. 候选人对评分有异议/反驳 -> 认真核对，说得对就承认并修正，说得不对就解释为什么\n"
            "3. 候选人想练习 -> 出一道相关的追问让他回答\n"
            "风格：口语化、有针对性、不啰嗦，像真人在辅导。回答用中文。"
        )
        ctx = (
            f"【题目】{question}\n"
            f"【候选人当时的回答】{my_answer or '（未记录）'}\n"
            f"【当时得分】{score if score is not None else '未知'}\n"
            f"【当时点评】{comment or '（无）'}\n"
            "【注意】现在不是重新评分，而是帮助候选人真正学会这道题。"
        )
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": ctx}]
        for m in messages[-20:]:
            role = m.get("role"); text = (m.get("text") or "").strip()
            if not text: continue
            if role == "user":
                msgs.append({"role": "user", "content": text})
            elif role == "assistant":
                msgs.append({"role": "assistant", "content": text})

        self._sse_start()
        try:
            for kind, piece in chat_completion_stream(msgs, temperature=0.6, max_tokens=1200):
                self._sse(kind, {"t": piece})
        except Exception as e:
            self._sse("error", {"message": str(e)}); return
        self._sse("done", {})



def main():
    log = get_logger()
    # 恢复未结束的面试会话（断点续面）
    _load_live_sessions()
    if SESSIONS:
        log.info("已恢复 %d 个未完成的面试会话", len(SESSIONS))
    # 预检：题库与 AI 配置（首次调用触发旧数据迁移）
    try:
        qs = get_questions()
        log.info("题库加载成功: %d 题 / %d 个方向", len(qs['questions']), len(qs['directions']))
    except Exception as e:
        log.error("题库加载失败: %s", e)
        sys.exit(1)
    try:
        _load_sense_config()
        log.info("AI 配置就绪: provider=sense, model=%s", MODEL)
    except Exception as e:
        log.warning("AI 配置不可用: %s", e)
        log.warning("题库与刷题功能正常，但 AI 模拟面试不可用，请先配置 sense provider")
        log.warning("或设置环境变量 INTERVIEW_RANGE_API_KEY / INTERVIEW_RANGE_BASE_URL")

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    log.info("面试靶场已启动: http://%s:%d", HOST, PORT)
    log.info("数据目录: %s", store.DATA_DIR)
    log.info("随时 Ctrl+C 停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("已停止.")
        server.server_close()


if __name__ == "__main__":
    main()