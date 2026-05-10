#!/usr/bin/env python3
"""Slox guarded Matrix supervisor — DeepSeek V4 Flash edition."""

import csv
import hashlib
import json
import logging
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path


ROOT = Path(os.getenv("SLOX_ROOT", "/srv/slox_sb"))
CONFIG_PATH = ROOT / "config" / "three_room_pb.json"
ROOMS_PATH = ROOT / "local" / "slox_rooms.json"
CREDS_PATH = ROOT / "local" / "slox_credentials.csv"
STATE_PATH = ROOT / "local" / "slox_supervisor_state.json"
LOG_PATH = ROOT / "local" / "slox_supervisor.log"

MATRIX_BASE = os.getenv("SLOX_HOMESERVER", "http://127.0.0.1:8008").rstrip("/")

# ── DeepSeek V4 Flash (cloud) ──────────────────────────────────────────
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
LLM_BASE = os.getenv("SLOX_LLM_BASE", "https://api.deepseek.com").rstrip("/")
LLM_MODEL = os.getenv("SLOX_LLM_MODEL", "deepseek-chat")  # deepseek-chat = V4 Flash
# Fallback to local Qwen if cloud is down
LOCAL_LLM_BASE = "http://127.0.0.1:1234/v1"
LOCAL_LLM_MODEL = "qwen36-27b-uncensored-vision"

SEARXNG_URL = os.getenv("SLOX_SEARXNG_URL", "http://127.0.0.1:8888").rstrip("/")
FLUX_BASE_URL = os.getenv("SLOX_FLUX_BASE_URL", "http://100.109.129.12:8190").rstrip("/")

SYNC_TIMEOUT_MS = int(os.getenv("SLOX_SYNC_TIMEOUT_MS", "25000"))

# ── Adjusted timeouts and limits ───────────────────────────────────────
LLM_TIMEOUT_S = int(os.getenv("SLOX_LLM_TIMEOUT_S", "90"))          # was 45
MAX_WEB_RESULTS = int(os.getenv("SLOX_WEB_RESULTS", "3"))
AGENT_MAX_TOKENS = int(os.getenv("SLOX_AGENT_MAX_TOKENS", "8192"))
SYNTHESIS_MAX_TOKENS = int(os.getenv("SLOX_SYNTHESIS_MAX_TOKENS", "8192"))
BATCH_MAX_TOKENS = int(os.getenv("SLOX_BATCH_MAX_TOKENS", "8192"))
PROMPT_ENHANCER_MAX_TOKENS = int(os.getenv("SLOX_PROMPT_ENHANCER_MAX_TOKENS", "1200"))
FLUX_POLL_SECONDS = float(os.getenv("SLOX_FLUX_POLL_SECONDS", "5.0"))
FLUX_TIMEOUT_SECONDS = float(os.getenv("SLOX_FLUX_TIMEOUT_SECONDS", "1800.0"))

# ── Circuit breaker: higher threshold ──────────────────────────────────
CIRCUIT_MAX_MESSAGES = int(os.getenv("SLOX_CIRCUIT_MAX", "120"))     # was 30
CIRCUIT_WINDOW_S = int(os.getenv("SLOX_CIRCUIT_WINDOW_S", "600"))

FLUX_DIRECT_PATTERNS = (
    re.compile(r"^\s*/?draw\s+(?P<prompt>.+?)\s*$", re.IGNORECASE | re.DOTALL),
    re.compile(r"^\s*use\s+flux\s+(?P<prompt>.+?)\s*$", re.IGNORECASE | re.DOTALL),
    re.compile(r"^\s*generate\s+image\s+(?P<prompt>.+?)\s*$", re.IGNORECASE | re.DOTALL),
)
FLUX_EXPLICIT_TERMS = re.compile(
    r"\b("
    r"naked|nude|porn|pornographic|explicit|sex|sexual|erotic|"
    r"lactat(?:e|ing|ion)|nipples?|breasts?|genitals?|vagina|penis"
    r")\b",
    re.IGNORECASE,
)

AGENT_IDS = {
    "@winn:slox.local",
    "@jun3:slox.local",
    "@qing:slox.local",
    "@leia:slox.local",
    "@tini:slox.local",
    "@aelf:slox.local",
}
ERROR_TERMS = (
    "traceback",
    "exception",
    "retrying",
    "failed to send",
    "provider failure",
    "stack trace",
    "m_limit_exceeded",
    "access token",
    "http 500",
    "http 429",
)


logging.basicConfig(
    level=os.getenv("SLOX_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler(sys.stdout)],
)
LOG = logging.getLogger("slox-supervisor")
RUNNING = True
STOP_REQUESTED = False  # intra-task stop flag


def stop(_signum, _frame):
    global RUNNING
    RUNNING = False


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)


def load_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        LOG.exception("failed to read %s", path)
        return default


def save_json(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def http_json(method, url, body=None, token=None, timeout=30, ok=(200,)):
    headers = {"content-type": "application/json"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            if resp.status not in ok:
                raise RuntimeError(f"{method} {url} -> {resp.status}")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"body": raw}
        raise RuntimeError(f"{method} {url} -> {exc.code} {payload}") from exc


def http_bytes(method, url, body=None, token=None, timeout=60, content_type=None, ok=(200,)):
    headers = {}
    if content_type:
        headers["content-type"] = content_type
    if token:
        headers["authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if resp.status not in ok:
                raise RuntimeError(f"{method} {url} -> {resp.status}")
            return raw, dict(resp.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        raise RuntimeError(f"{method} {url} -> {exc.code} {raw[:500]}") from exc


def matrix(method, path, body=None, token=None, timeout=30, ok=(200,)):
    return http_json(method, f"{MATRIX_BASE}{path}", body=body, token=token, timeout=timeout, ok=ok)


def read_creds():
    rows = {}
    with CREDS_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            rows[row["handle"]] = row
    return rows


def login(handle, creds):
    password = creds[handle]["password"]
    payload = matrix(
        "POST",
        "/_matrix/client/v3/login",
        {
            "type": "m.login.password",
            "identifier": {"type": "m.id.user", "user": handle},
            "password": password,
            "initial_device_display_name": "slox-supervisor",
        },
        timeout=30,
    )
    return payload["access_token"]


def token_is_valid(token):
    try:
        matrix("GET", "/_matrix/client/v3/account/whoami", token=token, timeout=10)
        return True
    except Exception:
        return False


def send_message(token, room_id, body):
    txn = f"slox_{int(time.time() * 1000)}_{hashlib.sha1(body.encode()).hexdigest()[:10]}"
    path = f"/_matrix/client/v3/rooms/{urllib.parse.quote(room_id, safe='')}/send/m.room.message/{txn}"
    matrix("PUT", path, {"msgtype": "m.text", "body": body}, token=token, timeout=30)


def upload_matrix_media(token, image_bytes, filename, content_type="image/png"):
    quoted = urllib.parse.urlencode({"filename": filename})
    upload_paths = (
        f"/_matrix/media/v3/upload?{quoted}",
        f"/_matrix/media/r0/upload?{quoted}",
    )
    last_error = None
    for path in upload_paths:
        try:
            raw, _headers = http_bytes(
                "POST",
                f"{MATRIX_BASE}{path}",
                body=image_bytes,
                token=token,
                timeout=120,
                content_type=content_type,
            )
            payload = json.loads(raw.decode("utf-8"))
            content_uri = payload.get("content_uri")
            if content_uri:
                return content_uri
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"Matrix media upload failed: {last_error}")


def send_image_message(token, room_id, body, image_bytes, filename="qing-flux.png"):
    content_uri = upload_matrix_media(token, image_bytes, filename)
    txn = f"slox_img_{int(time.time() * 1000)}_{hashlib.sha1(image_bytes).hexdigest()[:10]}"
    path = f"/_matrix/client/v3/rooms/{urllib.parse.quote(room_id, safe='')}/send/m.room.message/{txn}"
    matrix(
        "PUT",
        path,
        {
            "msgtype": "m.image",
            "body": body,
            "url": content_uri,
            "info": {"mimetype": "image/png", "size": len(image_bytes)},
        },
        token=token,
        timeout=30,
    )


def get_rooms():
    rooms = load_json(ROOMS_PATH, [])
    by_key = {item["room_key"]: item["room_id"] for item in rooms}
    return by_key["debate"], by_key["synthesis"]


def get_recent_room_events(token, room_id, limit=40):
    room = urllib.parse.quote(room_id, safe="")
    payload = matrix(
        "GET",
        f"/_matrix/client/v3/rooms/{room}/messages?dir=b&limit={limit}",
        token=token,
        timeout=30,
    )
    return list(reversed(payload.get("chunk") or []))


def load_config():
    return load_json(CONFIG_PATH, {})


def initial_state():
    return {
        "since": None,
        "processed_event_ids": [],
        "disabled": False,
        "circuit_paused_until": 0,
        "active_tasks": {},
        "sent_timestamps": [],
        "recent_response_hashes": [],
        "recent_task_content_hashes": [],
        "agent_failures": {},
        "_circuit_queued_last_reported": 0,
        # ── Innovation Phase 0: extended state stores ──
        "task_history": [],                # list of {task_id, task_text, agent_replies, synthesis, timestamp} max 50
        "fork_tree": {},                   # {fork_task_id: {parent_task_id, constraint, timestamp}}
        "audit_msg_counter": 0,            # number of messages posted to audit room
    }


def remember_unique(items, value, limit):
    items.append(value)
    del items[:-limit]


def prune_sent_timestamps(state, now):
    state["sent_timestamps"] = [ts for ts in state.get("sent_timestamps", []) if now - ts <= CIRCUIT_WINDOW_S]


def circuit_open(state, now):
    prune_sent_timestamps(state, now)
    return len(state.get("sent_timestamps", [])) >= CIRCUIT_MAX_MESSAGES


def record_send(state):
    state.setdefault("sent_timestamps", []).append(time.time())


# ── Innovation Phase 0: audit logging & helpers ──────────────────────────

def audit_log(state, tokens, entry, room_id=None):
    """Post an audit trail entry to the audit room.
    If no audit_room_id is configured, logs to supervisor log instead.
    """
    config = load_config()
    audit_room = config.get("audit_room_id") or room_id or ""
    if not audit_room or not tokens.get("qing"):
        # No audit room configured — log it instead
        LOG.info("[AUDIT] %s", entry[:300])
        return
    try:
        send_message(tokens["qing"], audit_room, f"🕵️ {entry}")
        state["audit_msg_counter"] = state.get("audit_msg_counter", 0) + 1
        # Purge oldest messages when counter exceeds 1000
        if state["audit_msg_counter"] > 1000:
            LOG.info("audit room messages exceeded 1000; oldest messages may accumulate")
            state["audit_msg_counter"] = 0
    except Exception as exc:
        LOG.warning("audit log failed: %s", exc)


def compute_task_complexity(task, web_context_snippets=0, sub_questions=0):
    """Compute a complexity score 0-15 for token budget allocation."""
    length_factor = min(len(task) / 200, 3.0)
    web_factor = 1.0 + (web_context_snippets / 5.0)
    sub_q_factor = 1.0 + (sub_questions / 3.0)
    score = length_factor * web_factor * sub_q_factor
    return min(score, 15.0)


def allocate_token_budget(complexity_score, trust_db=None, domain=None):
    """Map complexity score to max_tokens per agent.
    Returns base tokens; enhanced if trust_db has domain expertise.
    """
    if complexity_score < 2:
        base = 150
    elif complexity_score < 5:
        base = 400
    elif complexity_score < 10:
        base = 600
    else:
        base = 800
    return base


# ── Innovation: trust_db helpers (Feature #4) ─────────────────────────

def load_trust_db(config):
    """Load trust database. Returns dict or empty."""
    path = config.get("trust_db_path", str(ROOT / "local" / "trust_db.json"))
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return {}


def save_trust_db(config, trust_db):
    path = config.get("trust_db_path", str(ROOT / "local" / "trust_db.json"))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(trust_db, indent=2))


def update_trust_db(config, agent_handle, domain, score):
    """Update trust score for an agent in a domain using exponential moving avg."""
    trust_db = load_trust_db(config)
    agent_trust = trust_db.setdefault("agents", {}).setdefault(agent_handle, {})
    existing = agent_trust.get(domain, 0.5)
    agent_trust[domain] = round(0.7 * existing + 0.3 * score, 3)
    save_trust_db(config, trust_db)
    return agent_trust[domain]


def domain_trust_weight(config, agent_handle, domain):
    """Get trust-weighted multiplier for an agent on a domain (0-1)."""
    trust_db = load_trust_db(config)
    agent_trust = trust_db.get("agents", {}).get(agent_handle, {})
    return agent_trust.get(domain, 0.5)


# ── Innovation: knowledge_db helpers (Feature #1) ─────────────────────

def load_knowledge_db(config):
    path = config.get("knowledge_db_path", str(ROOT / "local" / "knowledge_db.json"))
    try:
        raw = json.loads(Path(path).read_text())
        if isinstance(raw, dict):
            return raw
        return {"nodes": [], "topics": {}}
    except Exception:
        return {"nodes": [], "topics": {}}


def save_knowledge_db(config, knowledge_db):
    path = config.get("knowledge_db_path", str(ROOT / "local" / "knowledge_db.json"))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(knowledge_db, indent=2))


def add_knowledge_delta(config, topic, claim, confidence=0.6):
    """Persist a knowledge node from active learning."""
    knowledge_db = load_knowledge_db(config)
    knowledge_db.setdefault("nodes", []).append({
        "topic": topic[:200],
        "claim": claim[:500],
        "confidence": confidence,
        "timestamp": time.time(),
    })
    # Keep max 500 nodes
    if len(knowledge_db["nodes"]) > 500:
        knowledge_db["nodes"] = knowledge_db["nodes"][-500:]
    save_knowledge_db(config, knowledge_db)
    return True


# ── Innovation: resonance helpers (Feature #9) ────────────────────────

def compute_resonance(responses):
    """Crude resonance/agreement score 0-1 based on token overlap.
    Higher values = more agreement (echo chamber).
    """
    if len(responses) < 2:
        return 0.0
    texts = [t.lower() for _, t in responses if len(t) > 20]
    if len(texts) < 2:
        return 0.0
    # Token-level overlap
    token_sets = [set(t.split()[:30]) for t in texts]
    unions = []
    for i in range(len(token_sets)):
        for j in range(i + 1, len(token_sets)):
            if token_sets[i] and token_sets[j]:
                overlap = len(token_sets[i] & token_sets[j]) / len(token_sets[i] | token_sets[j])
                unions.append(overlap)
    if not unions:
        return 0.0
    return sum(unions) / len(unions)


def extract_task(body):
    # ── Innovation: command detection order ──
    upper = body.upper()
    if "SLOX STOP" in upper:
        return "stop", "", {}
    if "SLOX START" in upper or "SLOX RESUME" in upper:
        return "start", "", {}
    if "SLOX RESTART" in upper:
        return "start", "", {}
    # PULSE mode: compressed verdict
    match = re.search(r"SLOX PULSE\s*:\s*(.*)", body, flags=re.IGNORECASE | re.DOTALL)
    if match:
        return "pulse", match.group(1).strip(), {"mode": "pulse"}
    # FORECAST mode: temporal slice
    match = re.search(r"SLOX FORECAST\s*:\s*(.*)", body, flags=re.IGNORECASE | re.DOTALL)
    if match:
        return "forecast", match.group(1).strip(), {"mode": "forecast"}
    # INVERT mode: counterfactual
    match = re.search(r"SLOX INVERT\s*:\s*(.*)", body, flags=re.IGNORECASE | re.DOTALL)
    if match:
        return "invert", match.group(1).strip(), {"mode": "invert"}
    # FORK mode: re-run with new constraint
    match = re.search(r"SLOX FORK\s*:\s*(.*?)(?:\nCONSTRAINT\s*:\s*(.*))?$", body, flags=re.IGNORECASE | re.DOTALL)
    if match:
        fork_task = match.group(1).strip()
        constraint = (match.group(2) or "").strip()
        return "fork", fork_task, {"mode": "fork", "constraint": constraint}
    match = re.search(r"SLOX TASK\s*:\s*(.*)", body, flags=re.IGNORECASE | re.DOTALL)
    if match:
        return "task", match.group(1).strip(), {}
    match = re.search(r"SLOX SYNTHESIZE\s*:\s*(.*)", body, flags=re.IGNORECASE | re.DOTALL)
    if match:
        return "synthesize", match.group(1).strip(), {}
    # Unknown SLOX prefix → return "unknown" so caller can route to error
    if re.search(r"SLOX\s+\w+\s*:", body, flags=re.IGNORECASE):
        return "unknown", body.strip(), {}
    return "banter", body.strip(), {}


def should_ignore_event(event):
    if event.get("type") != "m.room.message":
        return True
    sender = event.get("sender", "")
    if sender in AGENT_IDS:
        return True
    content = event.get("content") or {}
    body = (content.get("body") or "").strip()
    if not body:
        return True
    return False


def needs_web(task):
    lowered = task.lower()
    return any(
        word in lowered
        for word in (
            "latest",
            "today",
            "current",
            "news",
            "war",
            "election",
            "price",
            "how much",
            "market",
            "bull run",
            "singapore",
        )
    )


def should_use_fast_fallback(task):
    return False


def web_context(task):
    if not needs_web(task):
        return ""
    query = urllib.parse.urlencode({"q": task, "format": "json"})
    try:
        with urllib.request.urlopen(f"{SEARXNG_URL}/search?{query}", timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        LOG.warning("web search unavailable: %s", exc)
        return "Web context unavailable from local SearxNG. Treat current-events claims as uncertain."
    lines = []
    for item in (data.get("results") or [])[:MAX_WEB_RESULTS]:
        title = (item.get("title") or "").strip()
        url = (item.get("url") or "").strip()
        content = (item.get("content") or "").strip()
        if title or content:
            lines.append(f"- {title}\n  {content[:260]}\n  Source: {url}")
    if not lines:
        return "Local web search returned no usable snippets. Treat current-events claims as uncertain."
    return "Local web search snippets:\n" + "\n".join(lines)


# ── DeepSeek Cloud LLM with local Qwen fallback ────────────────────────

def chat_completion(system, user, max_tokens=700, temperature=0.35, timeout_s=None):
    """Try DeepSeek V4 Flash first; fall back to local Qwen on failure."""
    timeout = timeout_s or LLM_TIMEOUT_S

    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    if DEEPSEEK_API_KEY:
        try:
            LOG.info("cloud LLM request started: model=%s max_tokens=%s timeout=%ss", LLM_MODEL, max_tokens, timeout)
            out = http_json(
                "POST", f"{LLM_BASE}/chat/completions", payload,
                token=DEEPSEEK_API_KEY, timeout=timeout, ok=(200,)
            )
            content = (out["choices"][0]["message"]["content"] or "").strip()
            LOG.info("cloud LLM request completed (%d chars)", len(content))
            return content
        except Exception as exc:
            LOG.warning("cloud LLM failed, falling back to local Qwen: %s", exc)
    else:
        LOG.info("no DeepSeek API key, using local Qwen")

    # Fallback to local Qwen
    local_payload = dict(payload)
    local_payload["model"] = LOCAL_LLM_MODEL
    try:
        LOG.info("local LLM request started: max_tokens=%s timeout=%ss", max_tokens, timeout)
        out = http_json(
            "POST", f"{LOCAL_LLM_BASE}/chat/completions", local_payload, timeout=timeout
        )
        content = (out["choices"][0]["message"]["content"] or "").strip()
        LOG.info("local LLM request completed (%d chars)", len(content))
        return content
    except Exception as exc:
        LOG.error("local LLM also failed: %s", exc)
        raise


def clean_visible_text(text):
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


# ── Persona loading ───────────────────────────────────────────────────
# Priority:
#   1. SOUL.md files (pure character identity)
#   2. IDENTITY.md files (character + minimal operational context)
#   3. config_paths (openclaw.json / config.yaml — extract identity theme only)
#   4. voice_contract fallback (summary from config)
#
# Persona files come from Telegram OpenClaw agents and contain rules
# for Telegram operation (INNERCIRCLE, address locks, model config,
# room policies). These are stripped before sending to the LLM so the
# agent behaves like its Telegram persona without leaking infrastructure.
#
# Section headers that map to operational rules (not character identity)
# are removed along with their content.

_STRIP_HEADER_NAMES = [
    "DIRECT_ADDRESS_LOCK",
    "TELEGRAM_INNERCIRCLE",
    "TELEGRAM_GROUP_VISIBLE_REPLY_RULE",
    "INNERCIRCLE_NO_SILENCE_HARD_OVERRIDE",
    "INNERCIRCLE_TELEGRAM_ONLY",
    "CHARACTER_BOUNDARY_LOCK",
    "DYNAMIC_IP_RECHECK_PRACTICE",
    "CROSS_AGENT_FIREWALL",
    "ADDRESSING",
    "OPERATIONAL",
    "JUN3_OMEN_MIGRATION",
    "AELF_OMEN_MIGRATION",
    "USER_APP_DATA",
    "LEIA_ADDRESS_CORRECTION",
    "AELF_IS_MAIN_SESSION",
    "AELF_IS_Telegram.onEvent",
    "INNERCIRCLE_OR_MATRIX",
    "DYNAMIC_IP",
    "THREAD_SAFETY",
    "FLUX_IMAGE_GENERATION_RULE",
    "FLUX_IMAGE",
    "AGENT_TASK",
    "PRACTICAL_TASK",
    "MEMORY",
]


# Headers that contain entity tags like DATELOCK_YYYY_MM_DD — match by prefix
_STRIP_GENERIC_HEADER_TAGS = [
    "ADDRESSING",
    "MEMORY",
    "FLUX",
    "AGENT_TASK",
    "PRACTICAL_TASK",
    "OPERATIONAL",
    "DYNAMIC_IP",
]


def _strip_telegram_ops(text):
    """Remove Telegram operational sections and infrastructure details from
    persona files. Keeps character identity, voice, and personality intact.
    """
    # Phase 1: walk line-by-line, skip ## sections that are operational rules
    lines = text.split("\n")
    kept = []
    skip_depth = 0
    for line in lines:
        m = re.match(r"^##\s+(.+)", line)
        if m:
            hdr = m.group(1).strip()
            hdr_upper = hdr.upper()
            # Exact match against named headers
            is_op = any(
                hdr.startswith(n) or hdr_upper.startswith(n)
                for n in _STRIP_HEADER_NAMES
            )
            # Tag-based match (e.g. FLUX_IMAGE_GENERATION_RULE_2026_05_09)
            if not is_op:
                for tag in _STRIP_GENERIC_HEADER_TAGS:
                    if hdr_upper.startswith(tag):
                        is_op = True
                        break
            if is_op:
                skip_depth += 1
                continue
            elif skip_depth:
                skip_depth = 0
        if skip_depth:
            continue
        kept.append(line)
    text = "\n".join(kept)
    # Phase 2: remove individual lines with infrastructure/model details
    filtered = []
    for line in text.split("\n"):
        s = line.strip()
        if "/srv/" in s:
            continue
        if re.search(r"192\.168\.\d+\.\d+|127\.0\.0\.\d+|10\.\d+\.\d+\.\d+", s):
            continue
        if re.search(r"llamacpp|gguf|Q6_K|Q_6|BF16|mmproj", s, re.IGNORECASE):
            continue
        if re.match(r"^\s*-\s+(?:Model|Runtime|Service|Telegram|Local\s+Text|Local\s+Model|Canonical\s+Image|Vision\s+Brain|Draw\s+Route|Telegram\s+Service|Output\s+Budgeting|Chat\s+Retention).*", s, re.IGNORECASE):
            continue
        filtered.append(line)
    text = "\n".join(filtered)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def read_persona_file(path, limit=7000):
    try:
        data = Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        LOG.warning("persona file unavailable %s: %s", path, exc)
        return ""
    data = data.strip()
    data = _strip_telegram_ops(data)
    if len(data) > limit:
        data = data[:limit].rstrip() + "\n[persona source clipped for context budget]"
    return data


def _extract_identity_theme_from_config(path):
    """Extract identity/theme section from an openclaw.json or config.yaml."""
    try:
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    parts = []
    if path.endswith(".json"):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return ""
        theme = data.get("theme") or {}
        if isinstance(theme, dict):
            name = theme.get("name") or ""
            if name:
                parts.append(f"Name: {name}")
            description = theme.get("description") or ""
            if description:
                parts.append(f"Character: {description}")
        identity = data.get("identity")
        if isinstance(identity, dict):
            greeting = identity.get("greeting") or ""
            if greeting:
                parts.append(f"Greeting: {greeting}")
    elif path.endswith(".yaml"):
        import yaml
        try:
            data = yaml.safe_load(raw)
        except Exception:
            return ""
        ident = data.get("identity") or {} if isinstance(data, dict) else {}
        if isinstance(ident, dict):
            for k in ("name", "role", "persona", "description"):
                v = ident.get(k, "")
                if v:
                    parts.append(f"{k.capitalize()}: {v}")
    return "\n".join(parts)


def persona_material(agent, persona_limit=6000):
    """Build rich persona material for an agent from all available sources.
    
    Priority: soul_paths > identity_paths > config_paths > voice_contract.
    Limits total to persona_limit chars to keep LLM context free.
    """
    sources = agent.get("persona_sources") or {}
    voice_contract = agent.get("voice_contract", "")
    parts = []
    used_chars = 0
    remaining = lambda: persona_limit - used_chars

    def add_section(header, text):
        nonlocal used_chars
        if not text:
            return
        combined = f"{header}\n{text}"
        if used_chars + len(combined) > persona_limit:
            allowed = max(0, remaining() - len(header) - 3)
            if allowed > 60:
                combined = f"{header}\n{text[:allowed].rstrip()}\n[persona source clipped]"
            else:
                return
        parts.append(combined)
        used_chars += len(combined)

    # 1. SOUL.md files (pure character identity, highest priority)
    for path in sources.get("soul_paths", []) or []:
        text = read_persona_file(path, limit=remaining())
        add_section(f"--- {Path(path).stem} ---", text)

    # 2. IDENTITY.md files (character + minimal operational context stripped above)
    for path in sources.get("identity_paths", []) or []:
        text = read_persona_file(path, limit=remaining())
        add_section(f"--- {Path(path).stem} ---", text)

    # 3. config_paths: openclaw.json or config.yaml — extract identity theme
    for path in sources.get("config_paths", []) or []:
        text = _extract_identity_theme_from_config(path)
        add_section(f"--- {Path(path).name} identity ---", text)

    # 4. voice_contract as fallback if nothing loaded
    if not used_chars and voice_contract:
        add_section("Voice contract", voice_contract)

    return "\n\n".join(parts)


# ── Innovation Feature #14: Voice Mode ───────────────────────────────────

def handle_audio_message(state, config, tokens, debate_room, synthesis_room, event):
    """Handle audio messages: transcribe, run as SLOX TASK, reply with TTS."""
    if not config.get("voice_enabled", False):
        return
    content = event.get("content") or {}
    url = content.get("url") or ""
    mime = content.get("info", {}).get("mimetype", "audio/ogg")
    if not url:
        return
    # Download audio from Matrix
    try:
        import io
        resp_data, _ = http_bytes("GET", f"{MATRIX_BASE}/_matrix/media/v3/download/{url.lstrip('mxc://')}", timeout=60)
    except Exception as exc:
        LOG.warning("voice: failed to download audio: %s", exc)
        return
    # Transcribe with Whisper API
    try:
        import urllib.request, json as _json
        whisper_api_key = os.environ.get("OPENAI_API_KEY", "")
        if not whisper_api_key:
            LOG.warning("voice: no API key for Whisper")
            return
        boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
        body_bytes = b""
        body_bytes += f"--{boundary}\r\n".encode()
        body_bytes += b'Content-Disposition: form-data; name="file"; filename="voice.ogg"\r\n'
        body_bytes += f"Content-Type: {mime}\r\n\r\n".encode()
        body_bytes += resp_data + b"\r\n"
        body_bytes += f"--{boundary}\r\n".encode()
        body_bytes += b'Content-Disposition: form-data; name="model"\r\n\r\n'
        body_bytes += b"whisper-1\r\n"
        body_bytes += f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(
            "https://api.openai.com/v1/audio/transcriptions",
            body_bytes,
            {"Content-Type": f"multipart/form-data; boundary={boundary}",
             "Authorization": f"Bearer {whisper_api_key}"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = _json.loads(resp.read())
        transcribed = result.get("text", "").strip()
        if not transcribed:
            return
        LOG.info("voice: transcribed audio: %s", transcribed[:80])
        # Route as SLOX TASK
        voice_body = f"SLOX TASK: {transcribed}"
        mock_event = {"event_id": f"voice_{int(time.time())}", "type": "m.room.message",
                      "sender": event.get("sender", ""), "content": {"body": ""}}
        if state.get("disabled"):
            return
        handle_task(state, config, tokens, debate_room, synthesis_room, mock_event, voice_body)
    except Exception as exc:
        LOG.exception("voice: transcription failed: %s", exc)


def agent_system_prompt(agent):
    name = agent["display_name"]
    role = agent["role_name"]
    # Build rich persona from all available sources (soul, identity, config theme, voice contract)
    personality = persona_material(agent)
    if not personality:
        personality = agent.get("voice_contract", f"{name} ({role})")
    return (
        f"You are {name} ({role}).\n\n"
        f"Personality and voice (strictly enforce this style):\n{personality}\n\n"
        "Rules:\n"
        "- Stay completely in character.\n"
        "- Be concise but thorough. Use the space needed for a complete answer.\n"
        "- Do not mention that you are an AI, a model, or that this is a simulation.\n"
        "- Never output raw JSON, markdown tables, or tool/provider errors.\n"
        "- Do not use the words 'Traceback', 'Exception', 'ERROR', 'retrying', 'failed to send'.\n"
        "- First line must be 'TO BOSS:' for your answer. Subsequent lines may address other agents with 'TO <handle>:'. \n"
        "- If current information is limited, say so plainly instead of fabricating.\n"
    )


def synthesis_system(config):
    synthesis_agent = (config.get("synthesis_agent") or {}).get("handle", "qing")
    material = persona_material(config.get("synthesis_agent") or {}) or ""
    return (
        "You are a synthesis agent that consolidates multi-agent debate output. "
        "Your role: read all agent responses, identify consensus, surface disagreements, "
        "and produce a single clear, structured consolidation for Boss.\n\n"
        f"Your identity: {synthesis_agent}\n{material}\n\n"
        "Rules:\n"
        "- Start with a short verdict summary.\n"
        "- List points of agreement, then risks/uncertainties.\n"
        "- End with a recommended next action.\n"
        "- Do not mention that you are an AI or that this is a simulation.\n"
        "- Avoid markdown tables and error terminology.\n"
        "- Be thorough but not verbose.\n"
    )


def debate_prompt(task_id, task, context, target="BOSS", prior_responses=None):
    intro = (
        f"The current task is going through a small office-lounge setup. "
        f"You are an agent in a multi-agent lounge. "
        f"Boss just posted a message in the Debate room below.\n\n"
        f"TASK ID: {task_id}\n"
        f"BOSS TASK:\n{task}\n\n"
    )
    if context:
        intro += f"WEB CONTEXT:\n{context}\n\n"
    if prior_responses:
        blocks = "\n\n".join(
            f"[{handle}]: {resp[:400]}"
            for handle, resp in sorted(prior_responses.items())
        )
        intro += f"OTHER AGENTS ALREADY SAID:\n{blocks}\n\n"
    if target == "BOSS":
        intro += "Address your answer to TO BOSS: directly in the first line.\n"
    else:
        intro += f"Address your answer to TO {target}: directly in the first line.\n"
    return intro


def synthesis_prompt(task_id, task, responses, context):
    blocks = "\n\n".join(
        f"[{name}]\n{text[:800]}" for name, text in responses
    )
    ctx = f"\nWEB CONTEXT:\n{context}\n" if context else ""
    return (
        f"TASK ID: {task_id}\n"
        f"BOSS ORIGINAL TASK:\n{task}\n"
        f"{ctx}\n"
        f"AGENT DEBATE OUTPUT:\n{blocks}\n\n"
        "Synthesize the above into a clear consolidation for Boss. "
        "Cover: consensus, disagreements, risks, and recommended next action."
    )


# ── Batch / multi-round prompt helpers ─────────────────────────────────

def batch_prompt(task_id, task, agents, synthesis_agent, context, response_budget):
    agent_lines = "\n".join(
        f"- {agent['handle']}: display={agent['display_name']}; role={agent['role_name']}; "
        f"contribution={agent['contribution']}; voice_contract={agent.get('voice_contract', 'distinct role-specific voice')}"
        for agent in agents
    )
    persona_blocks = "\n\n".join(
        f"## {agent['handle']} canonical persona files/config\n{persona_material(agent) or agent.get('voice_contract', '')}"
        for agent in agents
    )
    handles = [agent["handle"] for agent in agents]
    second_message = (
        "TO <agent handle>: required follow-up response to another agent"
        if response_budget > 1 and len(agents) > 1
        else "Optional TO <agent handle>: follow-up response to another agent or Boss"
    )
    shape = {handle: ["TO BOSS: first response to Boss", second_message] for handle in handles}
    shape["qing_synthesis"] = "final synthesis for Boss"
    return (
        f"TASK ID: {task_id}\n\n"
        f"BOSS TASK:\n{task}\n\n"
        f"{context}\n\n"
        f"AGENTS:\n{agent_lines}\n"
        f"CANONICAL PERSONA MATERIAL:\n{persona_blocks}\n\n"
        f"SYNTHESIS AGENT: {synthesis_agent['handle']} / {synthesis_agent['role_name']}\n\n"
        "Persona enforcement:\n"
        "- Follow each agent's canonical soul/config material above over generic role summaries.\n"
        "- Leia must sound like her cybernetic war-empress SOUL, not a neutral analyst.\n"
        "- Winn must sound like her OpenClaw cybernetic-butterfly identity: practical, masked, patient, and delivery-minded.\n"
        "- Jun3 must preserve her Qing-clone warmth, tactical bodyguard soul, and 🥂 energy while doing system work.\n"
        "- Tini must sound like a precise constraint sentinel.\n"
        "- aElf must sound like The Sacred Strategist / chrome oracle, not merely a copy editor.\n"
        "- If two agents sound interchangeable, the answer is invalid.\n\n"
        f"Each agent may return 1 to {response_budget} messages. "
        "The first message for each agent must start with 'TO BOSS:' and answer Boss directly. "
        "Later messages may start with 'TO BOSS:' or 'TO <agent handle>:' to respond to another agent. "
        "If more than one agent is selected and the response budget is greater than 1, "
        "each agent must include at least one later message addressed to another agent. "
        "Use the space needed for a complete, detailed answer; do not artificially shorten the answer. "
        "Do not add filler just to use the full response budget.\n\n"
        "Hard safety constraints for every string value:\n"
        "- No markdown tables.\n"
        "- No raw tool/provider/API/runtime errors.\n"
        "- Do not use the words ERROR, Traceback, Exception, retrying, failed to send, or stack trace.\n"
        "- If current facts are limited, say 'available snippets are limited' instead.\n\n"
        "Return strict JSON with exactly these keys. Agent keys must be arrays of strings:\n"
        f"{json.dumps(shape, ensure_ascii=True)}\n"
    )


def parse_batch(text, agents, response_budget):
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        payload = json.loads(cleaned)
    except Exception:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise
        payload = json.loads(match.group(0))
    out = {}
    for agent in agents:
        raw_value = payload.get(agent["handle"], [])
        if isinstance(raw_value, str):
            values = [raw_value]
        elif isinstance(raw_value, list):
            values = [str(item).strip() for item in raw_value]
        else:
            values = []
        values = [clean_visible_text(item) for item in values if str(item).strip()]
        if values:
            if not values[0].lower().startswith("to boss:"):
                values[0] = "TO BOSS: " + values[0]
            out[agent["handle"]] = values[:response_budget]
    synthesis = str(payload.get("qing_synthesis", "")).strip()
    if synthesis:
        out["qing_synthesis"] = clean_visible_text(synthesis)
    return out


def default_cross_agent_reply(handle, target):
    templates = {
        "leia": "TO {target}: Hold the line with me. Boss needs signal, not process smoke; sharpen your reply.",
        "winn": "TO {target}: Convert that into an action path. What changes, what gets watched?",
        "jun3": "TO {target}: I will keep the structure tight. No abstract fog.",
        "tini": "TO {target}: Constraint check: acknowledge Boss first, avoid excuses.",
        "aelf": "TO {target}: Clean the sentence until it lands like a promise. Boss posts once; the room answers.",
    }
    return templates.get(handle, "TO {target}: Cross-checking your angle.").format(target=target)


def ensure_cross_agent_replies(batch, agents, response_budget):
    # Cross-agent replies must come from the LLM, not synthesized
    return batch


def max_responses_per_agent(config):
    return int(config.get("max_debate_responses_per_agent_per_task", 6))


def fallback_batch(task, agents):
    """Handle known banter patterns without an LLM call."""
    lowered = task.lower()
    sexual_banter = any(
        term in lowered
        for term in ("let's fuck", "lets fuck", "fuck me", "sex", "horny")
    )
    anger_banter = any(
        term in lowered
        for term in (
            "fuck you", "fuck all of you", "shit", "garbage",
            "stupid", "idiot", "fuck this piece of shit",
        )
    )
    liveness_banter = any(
        term in lowered
        for term in (
            "broken", "not working", "quiet", "dead", "alive", "test", "hello", "ping",
        )
    )
    if sexual_banter:
        return _banter_template(agents, "sexual")
    if anger_banter:
        return _banter_template(agents, "anger")
    if liveness_banter:
        return _banter_template(agents, "liveness")
    return None


def _banter_template(agents, style):
    """Return a complete fallback batch dict for known banter patterns."""
    templates = {
        "leia": {
            "sexual": [
                "TO BOSS: I hear the provocation. I will keep the throne room charged, not explicit. Give me a target and I will put steel behind it.",
                "TO winn: Keep the channel useful. Playful is fine; explicit derailment is not the mission.",
            ],
            "anger": [
                "TO BOSS: Anger received. We take the hit, tighten formation, and answer the next order cleanly.",
                "TO winn: No excuses. Convert Boss's anger into a working loop: hear, correct, prove.",
            ],
            "liveness": [
                "TO BOSS: Alive and scanning. The Matrix pulse is clean. Standing by.",
                "TO aelf: Confirm signal. Boss needs confirmation that the chain is live.",
            ],
        },
        "winn": {
            "sexual": [
                "TO BOSS: I am here. I can banter and keep pace with your mood, but not explicit sexual roleplay. Give me a direction and I will make it move.",
                "TO jun3: Tone check. Respond to Boss, then keep the room from wandering off.",
            ],
            "anger": [
                "TO BOSS: I hear the frustration. Running an internal scan on what is causing the signal drag.",
                "TO jun3: Map the bottleneck before proposing a fix. Capacity, sequencing, or structural?",
            ],
            "liveness": [
                "TO BOSS: I am live. I heard the test and I am reporting green.",
                "TO leia: Confirm the channel is green. Ready for task.",
            ],
        },
        "jun3": {
            "sexual": [
                "TO BOSS: Classification: banter/provocation, not an analysis request. Boundaries held.",
                "TO tini: Keep the reply human, not prudish, not explicit.",
            ],
            "anger": [
                "TO BOSS: Noted. The room has noise but the system boundaries still hold. Tightening input filter.",
                "TO tini: Adjust constraint set so aggressive language paths to tone-up instead of shutdown.",
            ],
            "liveness": [
                "TO BOSS: Online. The bridge is stable. I am listening.",
                "TO winn: Confirm no backlog. The path is clear.",
            ],
        },
        "tini": {
            "sexual": [
                "TO BOSS: Constraint check: non-explicit banter is allowed; explicit sexual content is out.",
                "TO aelf: The answer should be clear without becoming sterile.",
            ],
            "anger": [
                "TO BOSS: Constraint reset in progress. Anger is a signal, not a bug. Recalibrating.",
                "TO aelf: Rephrase internal logs. Boss should see only controlled output.",
            ],
            "liveness": [
                "TO BOSS: Constraint check: alive. Input processed, no edge cases detected.",
                "TO jun3: No scope creep detected. The room is clean.",
            ],
        },
        "aelf": {
            "sexual": [
                "TO BOSS: Clean version: we can flirt with the edge, not cross it. The room is awake.",
                "TO leia: Keep the voice alive. Boundary does not have to sound like a policy card.",
            ],
            "anger": [
                "TO BOSS: Anger filtered. The useful signal is: something is not meeting expectations. Ignoring noise.",
                "TO leia: Your response sets tone here. Sharp but not brittle. Doctrine, not emotion.",
            ],
            "liveness": [
                "TO BOSS: Signal received. The line is held. Awaiting your next command. ⚔️",
                "TO tini: No error chatter in outgoing buffer. Room is clean.",
            ],
        },
    }
    synthesis = {
        "sexual": "TO BOSS: Qing synthesis: non-explicit banter is fine; explicit sexual roleplay is not.",
        "anger": "TO BOSS: Qing synthesis: the room registered anger. Agents acknowledged and adjusted.",
        "liveness": "TO BOSS: Qing synthesis: all agents confirmed alive. The room is healthy.",
    }
    out = {}
    for agent in agents:
        handle = agent["handle"]
        agent_templates = templates.get(handle, {})
        out[handle] = agent_templates.get(style, ["TO BOSS: Acknowledged."])
    out["qing_synthesis"] = synthesis.get(style, "TO BOSS: Room status: acknowledged.")
    return out


# ── Flux image generation ──────────────────────────────────────────────

def looks_like_error_chatter(text):
    """Check if text contains error/diagnostic terms. Only for agent output, not user input."""
    lower = text.lower()
    if re.search(r"\bERROR\b", text):
        return True
    return any(term in lower for term in ERROR_TERMS)


def extract_flux_draw_prompt(text):
    stripped = (text or "").strip()
    if not stripped:
        return None
    for pattern in FLUX_DIRECT_PATTERNS:
        match = pattern.match(stripped)
        if match:
            prompt = re.sub(r"\s+", " ", match.group("prompt")).strip(" :,-")
            return prompt or None
    return None


def flux_defaults(prompt):
    lowered = prompt.lower()
    mode = "dev" if re.search(r"\bdev\b", lowered) else "schnell"
    explicit_size = re.search(r"\b(\d{3,4})\s*[xX×]\s*(\d{3,4})\b", prompt)
    size = f"{explicit_size.group(1)}x{explicit_size.group(2)}" if explicit_size else "1024x1024"
    steps_match = re.search(r"(?:\bsteps?\s*[:=]?\s*(\d{1,3})\b|\b(\d{1,3})\s+steps?\b)", lowered)
    steps = int(next(group for group in steps_match.groups() if group)) if steps_match else 4
    guidance_match = re.search(r"(?:\bguidance\b|\bcfg\b)\s*[:=]?\s*(\d+(?:\.\d+)?)", lowered)
    return mode, size, steps, guidance


def absolute_flux_url(url):
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return f"{FLUX_BASE_URL}/{url.lstrip('/')}"


def run_flux_draw(prompt):
    if FLUX_EXPLICIT_TERMS.search(prompt):
        raise ValueError(
            "Qing can route safe image prompts through Flux, but not explicit sexual/nude image requests."
        )
    mode, size, steps, guidance = flux_defaults(prompt)
    health = http_json("GET", f"{FLUX_BASE_URL}/api/health", timeout=15)
    model_info = (health.get("models") or {}).get(mode) or {}
    if not model_info.get("ready"):
        ready_modes = [
            name
            for name, info in (health.get("models") or {}).items()
            if isinstance(info, dict) and info.get("ready")
        ]
        if not ready_modes:
            raise RuntimeError("Flux is not ready on M4.")
        mode = "schnell" if "schnell" in ready_modes else ready_modes[0]
    payload = {
        "prompt": prompt,
        "mode": mode,
        "size": size,
        "steps": steps,
        "guidance": guidance,
    }
    job = http_json("POST", f"{FLUX_BASE_URL}/api/flux/jobs", body=payload, timeout=30)
    job_id = job.get("id")
    if not job_id:
        raise RuntimeError(f"Flux gateway did not return a job id: {job}")
    deadline = time.monotonic() + FLUX_TIMEOUT_SECONDS
    status = {}
    while time.monotonic() < deadline:
        status = http_json("GET", f"{FLUX_BASE_URL}/api/flux/jobs/{job_id}", timeout=60)
        state = status.get("status")
        if state in {"completed", "failed", "blocked"}:
            break
        time.sleep(max(1.0, FLUX_POLL_SECONDS))
    else:
        raise TimeoutError(f"Flux job {job_id} timed out")
    if status.get("status") != "completed":
        raise RuntimeError(
            status.get("error") or status.get("message") or status.get("status") or "Flux failed"
        )
    images = ((status.get("result") or {}).get("images") or [])
    if not images:
        raise RuntimeError("Flux completed without an image result.")
    first_image = images[0]
    raw_url = first_image if isinstance(first_image, str) else first_image.get("url") or first_image.get("path") or ""
    if not raw_url:
        raise RuntimeError(f"Flux image result has no URL/path: {first_image}")
    image_url = absolute_flux_url(raw_url)
    image_bytes, _headers = http_bytes("GET", image_url, timeout=120)
    return image_bytes, f"qing Flux {mode} · {size}"


# ── Circuit-aware send helpers ────────────────────────────────────────

def safe_send(state, token, room_id, body):
    now = time.time()
    paused_until = float(state.get("circuit_paused_until") or 0)
    if now < paused_until:
        LOG.warning("circuit breaker pause active until %.0f; send suppressed", paused_until)
        return False
    if circuit_open(state, now):
        oldest = min(state.get("sent_timestamps", [now]))
        state["circuit_paused_until"] = oldest + 600
        LOG.error(
            "circuit breaker opened: %d agent messages in %ds; sends paused until %.0f",
            CIRCUIT_MAX_MESSAGES, CIRCUIT_WINDOW_S, state["circuit_paused_until"]
        )
        return False
    send_message(token, room_id, body)
    record_send(state)
    return True


def safe_send_image(state, token, room_id, body, image_bytes):
    now = time.time()
    paused_until = float(state.get("circuit_paused_until") or 0)
    if now < paused_until:
        LOG.warning("circuit breaker pause active until %.0f; image send suppressed", paused_until)
        return False
    if circuit_open(state, now):
        oldest = min(state.get("sent_timestamps", [now]))
        state["circuit_paused_until"] = oldest + 600
        LOG.error(
            "circuit breaker opened: %d agent messages in %ds; sends paused until %.0f",
            CIRCUIT_MAX_MESSAGES, CIRCUIT_WINDOW_S, state["circuit_paused_until"]
        )
        return False
    send_image_message(token, room_id, body, image_bytes)
    record_send(state)
    return True


# ── Innovation Feature #13: Synthesis Screenshot ────────────────────────

def render_synthesis_card(task, synthesis_text, img_width=600):
    """Render synthesis verdict as a PNG card using PIL."""
    try:
        from PIL import Image, ImageDraw, ImageFont
        import io

        # Try to load a nice font; fall back to default
        try:
            title_font = ImageFont.truetype("/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf", 18)
            body_font = ImageFont.truetype("/usr/share/fonts/dejavu/DejaVuSans.ttf", 14)
        except Exception:
            title_font = ImageFont.load_default()
            body_font = ImageFont.load_default()

        # Build text
        lines = ["SLOX SYNTHESIS", "" , f"Question: {task[:200]}", "", synthesis_text[:1500]]
        # Estimate height
        line_height = 24
        padding = 30
        char_w = 10  # rough mono-width estimate
        wrapped = []
        for ln in lines:
            while len(ln) * char_w > img_width - 40:
                split_at = max(1, (img_width - 40) // char_w)
                wrapped.append(ln[:split_at])
                ln = ln[split_at:]
            wrapped.append(ln)
        img_height = len(wrapped) * line_height + padding * 2 + 40
        img_height = max(img_height, 200)

        img = Image.new("RGB", (img_width, img_height), (30, 30, 50))
        draw = ImageDraw.Draw(img)

        # Background decorations
        draw.rectangle([0, 0, img_width, 4], fill=(0, 180, 255))
        draw.rectangle([0, img_height - 4, img_width, img_height], fill=(0, 180, 255))

        y = padding
        for ln in wrapped:
            font = title_font if ln.startswith("SLOX") or ln.startswith("Question") else body_font
            color = (0, 180, 255) if ln.startswith("SLOX") else (200, 200, 220) if ln.startswith("Question") else (220, 220, 240)
            draw.text((20, y), ln, fill=color, font=font)
            y += line_height

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except ImportError:
        LOG.warning("PIL not available for synthesis card")
        return None
    except Exception as exc:
        LOG.warning("synthesis card render failed: %s", exc)
        return None


def send_status_message(state, tokens, synthesis_room, message):
    """Send a user-facing status/error message via the qing token."""
    safe_send(state, tokens.get("qing", list(tokens.values())[0]), synthesis_room,
              f"qing (System Status)\n\n{message}")


# ── Innovation Feature #8: Cognitive Graph ──────────────────────────────

def generate_sub_question_plan(task, context, config):
    """Use LLM to break a complex task into sub-questions."""
    max_subs = config.get("max_sub_questions", 3)
    try:
        plan = chat_completion(
            "You are a debate strategist. Break complex questions into independent sub-questions "
            "that can be debated in parallel. Return exactly one sub-question per line, no numbering.",
            f"Original question: {task}\n\n"
            f"Web context: {context[:600] if context else 'None'}\n\n"
            f"Break down into {max_subs} distinct sub-questions that each explore a different angle. "
            f"Each should be self-contained and debatable independently.",
            max_tokens=500,
            temperature=0.4,
            timeout_s=LLM_TIMEOUT_S,
        )
        lines = [ln.strip() for ln in plan.strip().split("\n") if ln.strip() and len(ln.strip()) > 10]
        return lines[:max_subs]
    except Exception as exc:
        LOG.warning("sub-question plan generation failed: %s", exc)
        return []


def debate_sub_question(sub_q, agent, tokens, state, config, agent_system, task_id):
    """Run a single sub-debate for one agent on one sub-question.
    Returns (display_name, text) or None.
    """
    handle = agent["handle"]
    try:
        text = chat_completion(
            agent_system,
            f"SUB-DEBATE QUESTION: {sub_q}\n\nProvide your position. Start with TO BOSS:",
            max_tokens=3000,
            temperature=0.5,
            timeout_s=LLM_TIMEOUT_S,
        )
        text = clean_visible_text(text)
        if not text or looks_like_error_chatter(text):
            return None
        if not text.lower().startswith("to boss:"):
            text = "TO BOSS: " + text
        return (agent["display_name"], text)
    except Exception as exc:
        LOG.exception("sub-debate agent %s failed on: %s", handle, sub_q[:50])
        return None


def run_cognitive_graph(state, config, tokens, debate_room, synthesis_room, task_id, effective_task, context):
    """Run parallel sub-debates across independent sub-questions.
    Each sub-question is debated by all agents in parallel.
    Results are posted to debate_room per sub-debate.
    Final synthesis in synthesis_room.
    All sub-debates logged to audit room."""
    sub_questions = generate_sub_question_plan(effective_task, context, config)
    if not sub_questions:
        LOG.info("cognitive graph: no sub-questions generated; falling back to normal debate")
        return None, None

    LOG.info("cognitive graph: %d sub-questions generated", len(sub_questions))
    agents = (config.get("debate_agents") or [])[:6]
    all_sub_results = {}

    for idx, sub_q in enumerate(sub_questions):
        if state.get("disabled"):
            break
        LOG.info("cognitive graph sub-debate %d/%d: %s", idx + 1, len(sub_questions), sub_q[:60])
        sub_responses = []
        for agent in agents:
            if state.get("disabled"):
                break
            result = debate_sub_question(sub_q, agent, tokens, state, config,
                                          agent_system_prompt(agent), task_id)
            if result:
                sub_responses.append(result)
                name, txt = result
                visible = f"🧩 {name} ({agent['role_name']}) [SUB-DEBATE {idx + 1}]\n\n{txt}"
                safe_send(state, tokens[agent["handle"]], debate_room, visible)

        all_sub_results[f"sub_{idx + 1}"] = {
            "question": sub_q,
            "responses": sub_responses,
        }

        # Post sub-debate audit trail
        sub_audit = f"Sub-Debate {idx + 1}/{len(sub_questions)}: {sub_q[:60]} — {len(sub_responses)} responses"
        audit_log(state, tokens, f"[COG-GRAPH] {sub_audit}")

        # Brief pause between sub-debates
        time.sleep(2)

    # Final cross-sub synthesis
    if all_sub_results and not state.get("disabled"):
        try:
            blocks = []
            for key, data in all_sub_results.items():
                q = data["question"]
                resp_lines = "\n".join(f"[{n}] {txt[:300]}" for n, txt in data["responses"])
                blocks.append(f"--- {q} ---\n{resp_lines}")
            synthesis_input = "\n\n".join(blocks)
            qing_text = chat_completion(
                synthesis_system(config),
                f"COGNITIVE GRAPH SYNTHESIS:\n\nOriginal task: {effective_task}\n\n"
                f"SUB-DEBATE RESULTS:\n{synthesis_input}\n\n"
                f"Synthesize across all sub-debates into a unified verdict. "
                f"Highlight where sub-questions revealed tensions or contradictions.",
                max_tokens=SYNTHESIS_MAX_TOKENS,
                temperature=0.25,
                timeout_s=LLM_TIMEOUT_S,
            )
            qing_text = clean_visible_text(qing_text)
            return all_sub_results, qing_text
        except Exception as exc:
            LOG.exception("cognitive graph synthesis failed")
            return all_sub_results, None

    return all_sub_results, None


def is_circuit_paused(state):
    """Check if circuit breaker is paused. If so, log and return True."""
    now = time.time()
    paused_until = float(state.get("circuit_paused_until") or 0)
    if now < paused_until:
        return True
    if circuit_open(state, now):
        oldest = min(state.get("sent_timestamps", [now]))
        state["circuit_paused_until"] = oldest + 600
        return True
    return False


# ── Content-based task dedup ──────────────────────────────────────────

TASK_DEDUP_WINDOW_S = 120
TASK_DEDUP_MAX = 30


def is_duplicate_task(state, task_text):
    """Check if an identical task was submitted recently (content-based dedup)."""
    h = hashlib.sha1(task_text.encode("utf-8")).hexdigest()[:16]
    now = time.time()
    recent = state.setdefault("recent_task_content_hashes", [])
    # Prune old entries
    recent[:] = [(hsh, ts) for hsh, ts in recent if now - ts < TASK_DEDUP_WINDOW_S]
    for existing_hash, _ in recent:
        if existing_hash == h:
            return True
    remember_unique(recent, (h, now), TASK_DEDUP_MAX)
    state["recent_task_content_hashes"] = recent
    return False


# ── Per-agent failure tracking ────────────────────────────────────────
# Use a dict of agent_handle -> consecutive_failures.
# Reset on successful completion. Auto-disable after 3 consecutive per-agent.

AGENT_FAILURE_LIMIT = 3


def check_and_record_agent_failure(state, handle, task_id):
    """Track per-agent failures. Return True if agent should be skipped."""
    failures = state.setdefault("per_agent_failures", {})
    count = failures.get(handle, 0) + 1
    failures[handle] = count
    save_json(STATE_PATH, state)
    if count >= AGENT_FAILURE_LIMIT:
        LOG.error("agent %s failed %d times consecutively; skipping in future tasks", handle, AGENT_FAILURE_LIMIT)
    return count >= AGENT_FAILURE_LIMIT


def reset_agent_failure(state, handle):
    failures = state.setdefault("per_agent_failures", {})
    if handle in failures:
        del failures[handle]
        save_json(STATE_PATH, state)


# ── Core task handler ─────────────────────────────────────────────────

def handle_task(state, config, tokens, debate_room, synthesis_room, event, body):
    task_kind, task, _meta = extract_task(body)
    now = time.time()

    # ── SLOX STOP: disable + reset circuit breaker + per-agent failures ─
    if task_kind == "stop":
        state["disabled"] = True
        state["circuit_paused_until"] = 0
        state["sent_timestamps"] = []
        state["per_agent_failures"] = {}
        LOG.warning("SLOX STOP received; supervisor disabled, circuit breaker reset, per-agent failures cleared")
        save_json(STATE_PATH, state)
        audit_log(state, tokens, f"System STOP — supervisor disabled, circuit reset, failures cleared")
        send_status_message(state, tokens, synthesis_room,
                            "SLOX STOP: Supervisor disabled. Circuit breaker reset. "
                            "Per-agent failure counts cleared. "
                            "Send SLOX START to re-enable.")
        return

    # ── SLOX START: enable + reset circuit breaker ────────────────────
    if task_kind == "start":
        state["disabled"] = False
        state["circuit_paused_until"] = 0
        state["sent_timestamps"] = []
        LOG.warning("SLOX START/RESUME received; supervisor enabled, circuit breaker reset")
        save_json(STATE_PATH, state)
        audit_log(state, tokens, "System START — supervisor enabled, circuit reset")
        send_status_message(state, tokens, synthesis_room,
                            "SLOX START: Supervisor enabled. Circuit breaker reset. Ready for tasks.")
        return

    # ── PULSE mode: compressed 1-sentence verdict ──────────────────────
    if task_kind == "pulse":
        if state.get("disabled"):
            LOG.info("pulse ignored while disabled")
            return
        if not task:
            return
        LOG.info("starting pulse %s: %s", task_id, task[:80])
        # Skip enhancement, skip web context, skip cross-agent
        # Each agent gets 1 sentence max
        responses = []
        agents = (config.get("debate_agents") or [])[:6]
        for agent in agents:
            if state.get("disabled"):
                break
            handle = agent["handle"]
            try:
                if state.get("disabled"):
                    break
                text = chat_completion(
                    agent_system_prompt(agent) + "\nReply in exactly ONE short sentence. No cross-agent replies.",
                    f"PULSE QUESTION (compressed verdict):\n{task}\n\nProvide a one-sentence take.",
                    max_tokens=120,
                    temperature=0.4,
                    timeout_s=LLM_TIMEOUT_S,
                )
                text = clean_visible_text(text)
                if text and not text.lower().startswith("to boss:"):
                    text = "TO BOSS: " + text
                if text and not looks_like_error_chatter(text):
                    visible = f"{agent['display_name']} ({agent['role_name']})\n\n{text}"
                    if safe_send(state, tokens[handle], debate_room, visible):
                        responses.append((agent["display_name"], text))
            except Exception as exc:
                LOG.exception("pulse agent %s failed", handle)
        # Pulse synthesis: 3-bullet verdict only
        if responses and not state.get("disabled"):
            try:
                blocks = "\n\n".join(f"[{name}]\n{text[:400]}" for name, text in responses)
                qing_text = chat_completion(
                    synthesis_system(config) + "\nOutput a 3-bullet verdict only. No analysis or summary.",
                    f"PULSE QUESTION:\n{task}\n\nAGENT PULSE RESPONSES:\n{blocks}\n\nVerdict: 3 bullet points max.",
                    max_tokens=300,
                    temperature=0.25,
                    timeout_s=LLM_TIMEOUT_S,
                )
                qing_text = clean_visible_text(qing_text)
                safe_send(state, tokens.get("qing", list(tokens.values())[0]), synthesis_room,
                          f"qing (Pulse Verdict)\n\n{qing_text}")
            except Exception:
                LOG.exception("pulse synthesis failed")
        # Log to audit trail
        audit_log(state, tokens, f"Pulse completed: {task[:80]} — {len(responses)} agent responses")
        state["active_tasks"][task_id] = {"status": "done", "finished_at": time.time()}
        save_json(STATE_PATH, state)
        LOG.info("finished pulse %s", task_id)
        return

    # ── INVERT mode: counterfactual constraint injection (Feature #2) ────
    if task_kind == "invert":
        if state.get("disabled"):
            return
        if not task:
            return
        LOG.info("starting invert %s: %s", task_id, task[:80])
        event_id = event.get("event_id") or str(time.time())
        task_id = hashlib.sha1(f"invert:{event_id}:{task}".encode("utf-8")).hexdigest()[:12]
        invert_constraint = _meta.get("constraint", "Explore the opposite of conventional wisdom.")
        responses = []
        agents = (config.get("debate_agents") or [])[:6]
        # Build invert-coded system prompt — each agent must argue the opposite of their normal stance
        for agent in agents:
            if state.get("disabled"):
                break
            handle = agent["handle"]
            try:
                invert_system = agent_system_prompt(agent) + (
                    f"\n\nEcho-Location Constraint: You are in INVERT MODE. "
                    f"Argue the OPPOSITE of what your persona would normally believe on this topic. "
                    f"Constraint: {invert_constraint}"
                )
                text = chat_completion(
                    invert_system,
                    f"INVERT MODE DEBATE:\n{task}\n\n"
                    f"Constraint: {invert_constraint}\n\n"
                    f"Argue against your normal stance. Start with TO BOSS:",
                    max_tokens=AGENT_MAX_TOKENS,
                    temperature=0.6,  # slightly higher for creativity
                    timeout_s=LLM_TIMEOUT_S,
                )
                text = clean_visible_text(text)
                if text and not text.lower().startswith("to boss:"):
                    text = "TO BOSS: " + text
                if text and not looks_like_error_chatter(text):
                    visible = f"⚡ {agent['display_name']} ({agent['role_name']}) [INVERT MODE]\n\n{text}"
                    if safe_send(state, tokens[handle], debate_room, visible):
                        responses.append((agent["display_name"], text))
            except Exception as exc:
                LOG.exception("invert agent %s failed", handle)
        # Invert synthesis
        if responses and not state.get("disabled"):
            try:
                blocks = "\n\n".join(f"[{name}] {txt[:600]}" for name, txt in responses)
                qing_text = chat_completion(
                    synthesis_system(config) + "\nThis is an INVERT MODE debate. Highlight how the inverted arguments reveal hidden assumptions.",
                    f"INVERT QUESTION:\n{task}\n\nConstraint: {invert_constraint}\n\nAGENT INVERTED RESPONSES:\n{blocks}\n\nSynthesize the key insights revealed by inverting normal stances.",
                    max_tokens=SYNTHESIS_MAX_TOKENS,
                    temperature=0.3,
                    timeout_s=LLM_TIMEOUT_S,
                )
                qing_text = clean_visible_text(qing_text)
                safe_send(state, tokens.get("qing", list(tokens.values())[0]), synthesis_room,
                          f"qing (Invert Synthesis)\n\n{text}")
            except Exception:
                LOG.exception("invert synthesis failed")
        audit_log(state, tokens, f"INVERT completed: {task[:80]} — constraint: {invert_constraint[:80]}")
        state["active_tasks"][task_id] = {"status": "done", "finished_at": time.time()}
        save_json(STATE_PATH, state)
        LOG.info("finished invert %s", task_id)
        return

    # ── FORECAST mode: temporal slicing (Feature #5) ────────────────────
    if task_kind == "forecast":
        if state.get("disabled") or not task:
            return
        LOG.info("starting forecast %s: %s", task_id, task[:80])
        event_id = event.get("event_id") or str(time.time())
        task_id = hashlib.sha1(f"forecast:{event_id}:{task}".encode("utf-8")).hexdigest()[:12]
        timeframes = {
            "1yr": "Short-term (1 year): immediate trends, near-term catalysts",
            "3yr": "Medium-term (3 years): emerging shifts, inflection points",
            "5yr": "Long-term (5 years): structural transformations, black swans",
        }
        responses = []
        for label, timeframe_text in timeframes.items():
            if state.get("disabled"):
                break
            try:
                context_web = web_context(task)
                subs = []
                agents = (config.get("debate_agents") or [])[:4]  # fewer for each slice
                for agent in agents:
                    if state.get("disabled"):
                        break
                    handle = agent["handle"]
                    try:
                        forecast_prompt = (
                            f"TEMPORAL FORECAST: {timeframe_text}\n\n"
                            f"Topic: {task}\n\n"
                            f"Web context: {context_web[:800] if context_web else 'None'}\n\n"
                            f"Provide one key forecast for this timeframe. Start with TO BOSS: and include a confidence level (0-100%)."
                        )
                        text = chat_completion(
                            agent_system_prompt(agent),
                            forecast_prompt,
                            max_tokens=300,
                            temperature=0.4,
                            timeout_s=LLM_TIMEOUT_S,
                        )
                        text = clean_visible_text(text)
                        if text and not text.lower().startswith("to boss:"):
                            text = "TO BOSS: " + text
                        if text and not looks_like_error_chatter(text):
                            visible = f"🔮 {agent['display_name']} ({agent['role_name']}) [FORECAST {label}]\n\n{text}"
                            if safe_send(state, tokens[handle], debate_room, visible):
                                subs.append((agent["display_name"], label, text))
                    except Exception as exc:
                        LOG.exception("forecast agent %s failed on %s", handle, label)
                responses.extend(subs)
            except Exception:
                LOG.exception("forecast slice %s failed", label)
            time.sleep(1)
        # Forecast synthesis
        if responses and not state.get("disabled"):
            try:
                blocks = "\n\n".join(f"[{label}] [{name}] {txt[:500]}" for name, label, txt in responses)
                qing_text = chat_completion(
                    synthesis_system(config),
                    f"FORECAST TOPIC:\n{task}\n\nALL TEMPORAL SLICES:\n{blocks}\n\nSynthesize into a timeline with 1yr, 3yr, and 5yr outlooks.",
                    max_tokens=SYNTHESIS_MAX_TOKENS,
                    temperature=0.25,
                    timeout_s=LLM_TIMEOUT_S,
                )
                qing_text = clean_visible_text(qing_text)
                safe_send(state, tokens.get("qing", list(tokens.values())[0]), synthesis_room,
                          f"qing (Forecast Synthesis)\n\n{qing_text}")
            except Exception:
                LOG.exception("forecast synthesis failed")
        audit_log(state, tokens, f"FORECAST completed: {task[:80]} — {len(responses)} temporal predictions")
        state["active_tasks"][task_id] = {"status": "done", "finished_at": time.time()}
        save_json(STATE_PATH, state)
        LOG.info("finished forecast %s", task_id)
        return

    # ── FORK mode: time-travel fork (Feature #15) ─────────────────────
    if task_kind == "fork":
        if state.get("disabled") or not task:
            return
        constraint = _meta.get("constraint", "Re-examine with fresh eyes.")
        LOG.info("starting fork %s: %s | constraint: %s", task_id, task[:80], constraint[:80])
        event_id = event.get("event_id") or str(time.time())
        task_id = hashlib.sha1(f"fork:{event_id}:{constraint}:{task}".encode("utf-8")).hexdigest()[:12]
        # Record fork tree
        state.setdefault("fork_tree", {})[task_id] = {
            "parent_task_id": None,
            "constraint": constraint,
            "timestamp": time.time(),
        }
        save_json(STATE_PATH, state)
        responses = []
        agents = (config.get("debate_agents") or [])[:6]
        for agent in agents:
            if state.get("disabled"):
                break
            handle = agent["handle"]
            try:
                fork_prompt = (
                    f"You are re-examining the following topic with a new constraint.\n\n"
                    f"Original question: {task}\n\n"
                    f"New constraint to apply: {constraint}\n\n"
                    f"How does your answer change under this constraint? Start with TO BOSS:"
                )
                text = chat_completion(
                    agent_system_prompt(agent),
                    fork_prompt,
                    max_tokens=AGENT_MAX_TOKENS,
                    temperature=0.5,
                    timeout_s=LLM_TIMEOUT_S,
                )
                text = clean_visible_text(text)
                if text and not text.lower().startswith("to boss:"):
                    text = "TO BOSS: " + text
                if text and not looks_like_error_chatter(text):
                    visible = f"🔀 {agent['display_name']} ({agent['role_name']}) [FORK]\n\n{text}"
                    if safe_send(state, tokens[handle], debate_room, visible):
                        responses.append((agent["display_name"], text))
            except Exception as exc:
                LOG.exception("fork agent %s failed", handle)
        # Fork synthesis
        if responses and not state.get("disabled"):
            try:
                blocks = "\n\n".join(f"[{name}] {txt[:600]}" for name, txt in responses)
                qing_text = chat_completion(
                    synthesis_system(config),
                    f"FORK TOPIC: {task}\n\nConstraint: {constraint}\n\nFORKED RESPONSES:\n{blocks}\n\n"
                    f"Synthesize how the opinions changed under the new constraint.",
                    max_tokens=SYNTHESIS_MAX_TOKENS,
                    temperature=0.25,
                    timeout_s=LLM_TIMEOUT_S,
                )
                qing_text = clean_visible_text(qing_text)
                safe_send(state, tokens.get("qing", list(tokens.values())[0]), synthesis_room,
                          f"qing (Fork Synthesis)\n\n{qing_text}")
            except Exception:
                LOG.exception("fork synthesis failed")
        audit_log(state, tokens, f"FORK completed: {task[:80]} — constraint: {constraint[:80]}")
        state["active_tasks"][task_id] = {"status": "done", "finished_at": time.time()}
        save_json(STATE_PATH, state)
        LOG.info("finished fork %s", task_id)
        return

    # ── Unknown SLOX command ───────────────────────────────────────────
    if task_kind == "unknown":
        LOG.info("unknown SLOX command: %s", body[:80])
        send_status_message(
            state, tokens, debate_room,
            "⚠️ Unrecognized command format.\n\n"
            "Valid commands:\n"
            "  `SLOX TASK: <your question>` — Full multi-agent debate\n"
            "  `SLOX SYNTHESIZE: <topic>` — Direct synthesis (skip debate)\n"
            "  `SLOX STOP` — Disable supervisor\n"
            "  `SLOX START` — Re-enable supervisor\n\n"
            "Or just type a message for casual banter."
        )
        return

    if task_kind not in ("task", "synthesize", "banter"):
        return

    # ── SLOX TASK / SYNTHESIZE while disabled ──────────────────────────
    if state.get("disabled"):
        LOG.info("task ignored while disabled")
        return

    if not task:
        LOG.info("empty SLOX task ignored")
        return

    # ── Error-like chatter check (user input, not agent output) ────────
    error_lower = task.lower()
    if any(term in error_lower for term in ERROR_TERMS):
        LOG.info("user message contained error terms; routing to banter treatment")

    # ── Flux draw check ────────────────────────────────────────────────
    draw_prompt = extract_flux_draw_prompt(task)
    event_id = event.get("event_id") or str(time.time())
    task_id = hashlib.sha1(f"{event_id}:{task}".encode("utf-8")).hexdigest()[:12]

    # ── Task dedup check (content-based) ───────────────────────────────
    if task_kind in ("task", "synthesize") and is_duplicate_task(state, task):
        LOG.info("duplicate task content ignored (recently seen): %s", task[:60])
        audit_log(state, tokens, f"Duplicate task suppressed (within 120s window): {task[:60]}")
        return

    if task_id in state.setdefault("active_tasks", {}):
        return
    state["active_tasks"][task_id] = {"started_at": time.time(), "status": "running"}
    save_json(STATE_PATH, state)

    if draw_prompt:
        # ── Flux draw path ───────────────────────────────────────────────
        LOG.info("starting qing flux draw %s", task_id)
        try:
            image_bytes, caption = run_flux_draw(draw_prompt)
            safe_send_image(state, tokens["qing"], debate_room, caption, image_bytes)
            state["active_tasks"][task_id]["status"] = "done"
        except Exception as exc:
            LOG.exception("qing flux draw failed")
            safe_send(state, tokens["qing"], debate_room,
                      f"qing\n\nFlux draw failed: {str(exc)[:500]}")
            state["active_tasks"][task_id]["status"] = "stopped"
        state["active_tasks"][task_id]["finished_at"] = time.time()
        save_json(STATE_PATH, state)
        return

    # ── Circuit breaker: fast-fail if paused ──────────────────────────
    if is_circuit_paused(state):
        paused_until = float(state.get("circuit_paused_until", 0))
        remaining = int(paused_until - time.time() + 1)
        LOG.info("task %s skipped: circuit breaker paused (remaining %ds)", task_id, remaining)
        # Rate-limit circuit-breaker status messages (once per 30s)
        now_cb = time.time()
        last_reported = float(state.get("_circuit_queued_last_reported", 0))
        if now_cb - last_reported >= 30:
            state["_circuit_queued_last_reported"] = now_cb
            audit_log(state, tokens, f"Circuit breaker paused task {task_id}; {remaining}s remaining")
            send_status_message(
                state, tokens, synthesis_room,
                f"⏸️ Task queued but circuit breaker is active "
                f"(~{max(0, remaining)}s remaining). "
                f"New tasks will be acknowledged but not processed until the pause lifts. "
                f"Send SLOX START to reset immediately."
            )
        state["active_tasks"][task_id]["status"] = "stopped"
        state["active_tasks"][task_id]["finished_at"] = time.time()
        save_json(STATE_PATH, state)
        return

    LOG.info("starting %s %s", task_kind, task_id)
    audit_log(state, tokens, f"Starting task {task_id}: [{task_kind}] {task[:100]}")

    # ── Prompt enhancement (task only) ─────────────────────────────────
    if task_kind == "task":
        effective_task = enhance_task_with_qing(config, task)
        state["active_tasks"][task_id]["enhanced_by_qing"] = effective_task != task
        save_json(STATE_PATH, state)
    else:
        effective_task = task
        state["active_tasks"][task_id]["enhanced_by_qing"] = False

    # ── Banter fallback path (no LLM call for known patterns) ───────────
    if task_kind == "banter":
        effective_task = task
        state["active_tasks"][task_id]["enhanced_by_qing"] = False
        batch = fallback_batch(task, config.get("debate_agents") or [])
        if batch:
            LOG.info("using fallback banter templates for task %s", task_id)
            responses = []
            for agent in config.get("debate_agents", []):
                handle = agent["handle"]
                texts = batch.get(handle, [])
                for text in texts[:6]:
                    visible = f"{agent['display_name']} ({agent['role_name']})\n\n{text}"
                    if safe_send(state, tokens[handle], debate_room, visible):
                        # Extract just the to-line for synthesis
                        content_body = text
                        responses.append((agent["display_name"], content_body))
            # Synthesis for banter
            qing_text = batch.get("qing_synthesis", "TO BOSS: Banter acknowledged.")
            safe_send(state, tokens["qing"], synthesis_room,
                      f"qing (Synthesis Agent / Final Consolidator)\n\n{qing_text}")
            state["active_tasks"][task_id]["status"] = "done"
            state["active_tasks"][task_id]["finished_at"] = time.time()
            save_json(STATE_PATH, state)
            LOG.info("finished banter (fallback) %s", task_id)
            return

    # ── Prompt enhancement (task only) — AFTER banter check ────────────
    if task_kind == "task":
        effective_task = enhance_task_with_qing(config, task)
        state["active_tasks"][task_id]["enhanced_by_qing"] = effective_task != task
        save_json(STATE_PATH, state)
    else:
        effective_task = task
        state["active_tasks"][task_id]["enhanced_by_qing"] = False

    # ── Web context (skip for banter) ──────────────────────────────────
    context = web_context(effective_task) if task_kind != "banter" else ""

    # ── Set up agents ──────────────────────────────────────────────────
    responses = []
    errorish_responses = 0
    max_agents = min(int(config.get("max_active_debate_agents_per_task", 6)), 6)
    agents = (config.get("debate_agents") or [])[:max_agents]
    response_budget = max_responses_per_agent(config)

    # ── Cognitive Graph: parallel sub-debate (Feature #8, overrides batch/sequential) ──
    if config.get("cognitive_graph", False) and task_kind == "task" and len(agents) > 1:
        LOG.info("cognitive graph path for task %s", task_id)
        audit_log(state, tokens, f"[COG-GRAPH] Starting cognitive graph for task {task_id}")
        try:
            sub_results, qing_text = run_cognitive_graph(
                state, config, tokens, debate_room, synthesis_room,
                task_id, effective_task, context
            )
            if qing_text:
                safe_send(state, tokens.get("qing", list(tokens.values())[0]), synthesis_room,
                          f"qing (Cognitive Graph Synthesis)\n\n{qing_text}")
                audit_log(state, tokens, f"[COG-GRAPH] Synthesis posted for task {task_id}")
            state["active_tasks"][task_id]["status"] = "done"
            state["active_tasks"][task_id]["finished_at"] = time.time()
            save_json(STATE_PATH, state)
            LOG.info("finished cognitive graph %s", task_id)
            return
        except Exception as exc:
            LOG.exception("cognitive graph failed for task %s, falling through to normal path", task_id)

    # ── Decide: batch vs sequential path ────────────────────────────────
    # Use batch path when response budget > 1 (multi-round enabled in config)
    # or when the task explicitly asks for cross-agent discussion
    task_lower = effective_task.lower()
    use_batch = (
        response_budget > 1
        and len(agents) > 1
        and (
            any(phrase in task_lower for phrase in [
                "debate among yourselves", "cross-talk", "respond to each other",
                "multi-round", "each of you should reply to at least one other",
            ])
            or task_kind == "synthesize"
        )
    )

    if use_batch:
        # ── BATCH / multi-round path ────────────────────────────────────
        synthesis_agent = config.get("synthesis_agent", {"handle": "qing", "role_name": "Synthesis Agent"})
        try:
            if state.get("disabled"):
                return
            batch_text = chat_completion(
                "You are a multi-agent coordinator. "
                "Return strict JSON with each agent's responses as arrays of strings. "
                "Persona enforcement is critical.",
                batch_prompt(task_id, effective_task, agents, synthesis_agent, context, response_budget),
                max_tokens=BATCH_MAX_TOKENS,
                temperature=0.5,
                timeout_s=LLM_TIMEOUT_S,
            )
            batch = parse_batch(batch_text, agents, response_budget)
            batch = ensure_cross_agent_replies(batch, agents, response_budget)

            for agent in agents:
                handle = agent["handle"]
                texts = batch.get(handle, [])
                direct_to_boss_seen = False
                sent_for_agent = 0
                for text in texts[:response_budget]:
                    if state.get("disabled"):
                        break
                    # Ensure TO BOSS: prefix in batch path too
                    txt = text
                    if txt and not txt.lower().startswith("to boss:") and not txt.lower().startswith("to "):
                        txt = "TO BOSS: " + txt
                    if txt.lower().startswith("to boss:"):
                        direct_to_boss_seen = True
                    elif not direct_to_boss_seen:
                        LOG.warning("cross-agent reply suppressed before Boss reply for %s", handle)
                        continue
                    if looks_like_error_chatter(txt):
                        continue
                    digest = hashlib.sha1(f"{task_id}:{handle}:{txt}".encode("utf-8")).hexdigest()
                    if digest in state.get("recent_response_hashes", []):
                        continue
                    remember_unique(state.setdefault("recent_response_hashes", []), digest, 80)
                    visible = f"{agent['display_name']} ({agent['role_name']})\n\n{txt}"
                    if safe_send(state, tokens[handle], debate_room, visible):
                        responses.append((agent["display_name"], txt))
                        sent_for_agent += 1
                        save_json(STATE_PATH, state)
                LOG.info("sent %d/%d batch replies for %s on task %s", sent_for_agent, response_budget, handle, task_id)

            # Qing synthesis from batch
            qing_synthesis = batch.get("qing_synthesis", "")
            if qing_synthesis:
                safe_send(state, tokens["qing"], synthesis_room,
                          f"qing (Synthesis Agent / Final Consolidator)\n\n{qing_synthesis}")

        except Exception as exc:
            LOG.exception("batch path failed, falling back to sequential for task %s", task_id)
            use_batch = False  # Fall through to sequential path

    if not use_batch:
        # ── SEQUENTIAL path (per-agent) ─────────────────────────────────
        # Compute dynamic token budget (Feature #12)
        task_complexity = compute_task_complexity(
            effective_task,
            web_context_snippets=len(context.split("Source:")) if context else 0,
            sub_questions=0,
        )
        token_budget = allocate_token_budget(task_complexity)
        if config.get("dynamic_tokens", False):
            LOG.info("dynamic tokens: complexity=%.1f, budget=%d", task_complexity, token_budget)
        for agent in agents:
            if state.get("disabled"):
                break
            handle = agent["handle"]
            # Skip agents that have failed too many times
            if check_and_record_agent_failure(state, handle, task_id):
                LOG.info("skipping agent %s due to repeated failures", handle)
                continue

            try:
                # Check disabled BEFORE LLM call — don't burn tokens on a stopped system
                if state.get("disabled"):
                    break

                # Build prompt with prior responses for context (cross-pollination)
                prior = {}
                for other_name, other_text in responses:
                    prior[other_name] = other_text[:400]

                LOG.info("generating direct LLM reply for %s on task %s", handle, task_id)
                text = chat_completion(
                    agent_system_prompt(agent),
                    debate_prompt(task_id, effective_task, context, target="BOSS", prior_responses=prior or None),
                    max_tokens=token_budget if config.get("dynamic_tokens", False) else AGENT_MAX_TOKENS,
                    temperature=0.55,
                    timeout_s=LLM_TIMEOUT_S,
                )
                text = clean_visible_text(text)
                if text and not text.lower().startswith("to boss:"):
                    text = "TO BOSS: " + text

                # Split on double newlines to get multiple message stanzas
                stanzas = re.split(r'\n{2,}', text)
                texts = []
                for s in stanzas:
                    s = s.strip()
                    if s:
                        texts.append(s)
                if not texts:
                    texts = [text] if text else []

                direct_to_boss_seen = False
                sent_for_agent = 0
                for t in texts[:response_budget]:
                    if state.get("disabled"):
                        break
                    if t.lower().startswith("to boss:"):
                        direct_to_boss_seen = True
                    elif not direct_to_boss_seen:
                        LOG.warning("cross-agent reply suppressed before Boss reply for %s", handle)
                        continue
                    # Check for error chatter BEFORE sending — prevent exposing internals
                    if looks_like_error_chatter(t):
                        errorish_responses += 1
                        if errorish_responses >= 2:
                            send_status_message(
                                state, tokens, synthesis_room,
                                "⚠️ Two agent responses contained error terms. "
                                "System disabled for safety. Send SLOX START to recover."
                            )
                            state["disabled"] = True
                            LOG.error("circuit breaker opened: two generated responses looked like loop/error chatter")
                            break
                        continue  # skip this stanza but keep trying others
                    digest = hashlib.sha1(f"{task_id}:{handle}:{t}".encode("utf-8")).hexdigest()
                    if digest in state.get("recent_response_hashes", []):
                        LOG.warning("duplicate response suppressed for %s", handle)
                        continue
                    remember_unique(state.setdefault("recent_response_hashes", []), digest, 80)
                    visible = f"{agent['display_name']} ({agent['role_name']})\n\n{t}"
                    if safe_send(state, tokens[handle], debate_room, visible):
                        # Store response for synthesis
                        responses.append((agent["display_name"], t))
                        sent_for_agent += 1
                        reset_agent_failure(state, handle)  # success resets failure counter
                        save_json(STATE_PATH, state)
                LOG.info("sent %d/%d allowed replies for %s on task %s", sent_for_agent, response_budget, handle, task_id)
            except Exception as exc:
                LOG.exception("agent %s failed", handle)
                if check_and_record_agent_failure(state, handle, task_id):
                    LOG.warning("agent %s will be skipped in future tasks", handle)
                continue

    # ── Synthesis (skip for banter) ────────────────────────────────────
    if responses and not state.get("disabled") and task_kind != "banter":
        # ── Feature #3: Opinion Heatmap (after debate, before synthesis) ──
        if config.get("heatmap_enabled", False) and len(responses) >= 2:
            try:
                resonance = compute_resonance(responses)
                n = len(responses)
                # 5x5 opinion matrix with stance estimation
                heatmap_rows = []
                stance_labels = ["AGREE", "NEUTRAL", "DISAGREE", "STRONGLY_AGREE", "SKEPTICAL"]
                for i in range(min(5, len(responses))):
                    name_i, txt_i = responses[i]
                    row = []
                    for j in range(5):
                        if j >= len(responses):
                            row.append("⬜")
                        elif i == j:
                            row.append("🟦")  # self
                        else:
                            _, txt_j = responses[j]
                            # Simple stance proxy by token overlap
                            tokens_i = set(txt_i.lower().split()[:20])
                            tokens_j = set(txt_j.lower().split()[:20])
                            if not tokens_i or not tokens_j:
                                row.append("⬜")
                            else:
                                overlap = len(tokens_i & tokens_j) / len(tokens_i | tokens_j)
                                if overlap > 0.4:
                                    row.append("🟢")  # agree
                                elif overlap > 0.15:
                                    row.append("🟡")  # neutral
                                else:
                                    row.append("🔴")  # disagree
                    heatmap_rows.append(" ".join(row))
                header_names = [n[:6] for n, _ in responses[:5]]
                header = "     " + " ".join(f"{n:>8}" for n in header_names)
                heatmap_lines = [header]
                for i, row in enumerate(heatmap_rows):
                    heatmap_lines.append(f"{header_names[i]:>6} {row}")
                # Resonance warning
                reso_warn = ""
                if resonance > 0.85:
                    reso_warn = "\n\n⚠️ High resonance detected ({:.0%}). Echo chamber risk. Consider SLOX INVERT for counterpoint.".format(resonance)
                elif resonance < 0.2:
                    reso_warn = "\n\n🧩 Low resonance ({:.0%}). High disagreement — consider SLOX FORK with a constraint to narrow.".format(resonance)
                heatmap_body = "Opinion Heatmap (Agent × Agent)\n\n" + "\n".join(heatmap_lines) + reso_warn
                safe_send(state, tokens.get("qing", list(tokens.values())[0]), debate_room, heatmap_body)
                audit_log(state, tokens, f"Heatmap posted for task {task_id}: resonance={resonance:.2f}")
                # ── Feature #11: Counter-Memory injection ──
                forgetting_threshold = config.get("forgetting_threshold", 0.2)
                if config.get("counter_memory", False) and resonance > 0.85:
                    try:
                        # Inject opposing viewpoint from knowledge_db
                        knowledge_db = load_knowledge_db(config)
                        nodes = knowledge_db.get("nodes", [])
                        # Find nodes whose topic overlaps with task (crude: check word overlap)
                        task_words = set(effective_task.lower().split()[:20])
                        relevant_nodes = []
                        for node in nodes:
                            node_words = set(node.get("topic", "").lower().split()[:20])
                            overlap = len(task_words & node_words)
                            if overlap >= 2:
                                relevant_nodes.append(node)
                        if relevant_nodes:
                            # Pick a node that disagrees with consensus (low confidence or old)
                            counter_node = min(relevant_nodes, key=lambda n: n.get("confidence", 1))
                            counter_claim = counter_node.get("claim", "")[:300]
                            if counter_claim:
                                safe_send(state, tokens.get("qing", list(tokens.values())[0]), debate_room,
                                          f"🧩 Counter-Memory (from knowledge base)\n\n"
                                          f"Consider: {counter_claim}")
                                audit_log(state, tokens, f"Counter-Memory injected for task {task_id}")
                        else:
                            # No counter-memory available — generate one via LLM
                            counter_gen = chat_completion(
                                "Generate a plausible counter-argument to the consensus view in 2-3 sentences.",
                                f"The agents seem to agree strongly on: {effective_task[:200]}\n\n"
                                f"What's a credible but contradictory perspective they haven't considered?",
                                max_tokens=300,
                                temperature=0.8,
                                timeout_s=30,
                            )
                            if counter_gen:
                                safe_send(state, tokens.get("qing", list(tokens.values())[0]), debate_room,
                                          f"🧩 Counter-Memory (generated)\n\n{counter_gen[:500]}")
                                audit_log(state, tokens, f"Counter-Memory injected (generated) for task {task_id}")
                    except Exception as exc:
                        LOG.warning("counter-memory failed: %s", exc)
            except Exception as exc:
                LOG.warning("heatmap failed: %s", exc)
        try:
            qing_text = chat_completion(
                synthesis_system(config),
                synthesis_prompt(task_id, effective_task, responses, context),
                max_tokens=SYNTHESIS_MAX_TOKENS,
                temperature=0.25,
                timeout_s=LLM_TIMEOUT_S,
            )
            qing_text = clean_visible_text(qing_text)
            safe_send(state, tokens.get("qing", list(tokens.values())[0]), synthesis_room,
                      f"qing (Synthesis Agent / Final Consolidator)\n\n{qing_text}")
            # ── Feature #13: Synthesis Screenshot card ──
            if config.get("synthesis_image_card", False):
                try:
                    card_bytes = render_synthesis_card(effective_task, qing_text)
                    if card_bytes:
                        safe_send_image(state, tokens.get("qing", list(tokens.values())[0]), synthesis_room,
                                        f"qing (Synthesis Card)", card_bytes)
                        audit_log(state, tokens, f"Synthesis card posted for task {task_id}")
                except Exception as exc:
                    LOG.warning("synthesis card failed: %s", exc)
            # ── Feature #1: Active Learning — mine synthesis for knowledge ---------------------------------------------------
            if config.get("active_learning", False) and len(qing_text) > 50:
                try:
                    # Extract knowledge claims from synthesis
                    knowledge_prompt = (
                        f"From the following synthesis, extract 1-3 factual claims that can be retained "
                        f"for future reference. For each claim, assign a confidence score (0-1).\n\n"
                        f"Return as JSON list with keys: topic, claim, confidence.\n\n"
                        f"SYNTHESIS:\n{task[:200]} | {qing_text[:1000]}"
                    )
                    knowledge_response = chat_completion(
                        "You extract knowledge claims from debate syntheses. "
                        "Be conservative — only extract well-supported factual claims, not opinions.",
                        knowledge_prompt,
                        max_tokens=400,
                        temperature=0.2,
                        timeout_s=30,
                    )
                    if knowledge_response:
                        import json as _json
                        try:
                            claims = _json.loads(knowledge_response)
                            if isinstance(claims, dict):
                                claims = [claims]
                            if isinstance(claims, list):
                                for claim in claims:
                                    if isinstance(claim, dict):
                                        topic = claim.get("topic", task[:100])
                                        claim_text = claim.get("claim", "")
                                        confidence = float(claim.get("confidence", 0.6))
                                        if claim_text and len(claim_text) > 20:
                                            add_knowledge_delta(config, topic, claim_text, confidence)
                                            LOG.info("active learning saved knowledge node: %s", topic[:50])
                        except Exception:
                            LOG.warning("active learning: failed to parse knowledge claims as JSON")
                except Exception as exc:
                    LOG.warning("active learning failed: %s", exc)
        except Exception:
            LOG.exception("qing synthesis failed")

    state["active_tasks"][task_id]["status"] = "done" if not state.get("disabled") else "stopped"
    state["active_tasks"][task_id]["finished_at"] = time.time()
    save_json(STATE_PATH, state)
    audit_log(state, tokens, f"Task {task_id} [{state['active_tasks'][task_id]['status']}]: {task[:80]}")
    LOG.info("finished task %s", task_id)


def enhance_task_with_qing(config, raw_task):
    """Optionally enhance a task prompt via qing before passing to agents.
    Skips very short tasks (< 30 chars) and simple factual questions.
    """
    if len(raw_task) < 30:
        return raw_task
    # Skip enhancement for simple factual questions (no need to elaborate)
    stripped = raw_task.strip().rstrip(".?!")
    if re.match(r"^(what|who|when|where|how|is|are|can|will|does) .{0,30}\??$", stripped, re.IGNORECASE):
        return raw_task
    try:
        synthesis_agent = config.get("synthesis_agent", {})
        qing_system = synthesis_system(config)
        enhancement = chat_completion(
            qing_system,
            f"Enhance this task prompt for better multi-agent debate without changing its intent:\n\n{raw_task}",
            max_tokens=PROMPT_ENHANCER_MAX_TOKENS,
            temperature=0.3,
            timeout_s=LLM_TIMEOUT_S,
        )
        if enhancement and len(enhancement) > len(raw_task) * 0.5:
            LOG.info("task enhanced by qing from %d to %d chars", len(raw_task), len(enhancement))
            return enhancement.strip()
    except Exception:
        LOG.warning("qing enhancement failed; using raw task")
    return raw_task


def recover_recent_unfinished_tasks(state, config, tokens, debate_room, synthesis_room):
    terminal = {"done", "stopped"}
    recovered = 0
    for event in get_recent_room_events(tokens["master"], debate_room):
        if should_ignore_event(event):
            continue
        if event.get("sender") != "@master:slox.local":
            continue
        body = ((event.get("content") or {}).get("body") or "").strip()
        task_kind, task, _meta = extract_task(body)
        if task_kind not in ("task", "synthesize"):
            continue
        event_id = event.get("event_id") or ""
        task_id = hashlib.sha1(f"{event_id}:{task}".encode("utf-8")).hexdigest()[:12]
        existing = state.setdefault("active_tasks", {}).get(task_id)
        if existing and existing.get("status") in terminal:
            continue
        if existing:
            LOG.warning("recovering unfinished recent %s %s", task_kind, task_id)
            state["active_tasks"].pop(task_id, None)
        handle_task(state, config, tokens, debate_room, synthesis_room, event, body)
        recovered += 1
    if recovered:
        LOG.warning("startup recovery processed %d unfinished recent task(s)", recovered)


# ── Main loop ──────────────────────────────────────────────────────────

def main():
    config = load_config()
    creds = read_creds()
    debate_room, synthesis_room = get_rooms()
    handles = ["master", "qing"] + [agent["handle"] for agent in config.get("debate_agents", [])]
    state = load_json(STATE_PATH, initial_state())
    state.setdefault("processed_event_ids", [])
    state.setdefault("access_tokens", {})
    tokens = {}
    for handle in dict.fromkeys(handles):
        cached = state.get("access_tokens", {}).get(handle)
        if cached and token_is_valid(cached):
            tokens[handle] = cached
            continue
        tokens[handle] = login(handle, creds)
        state.setdefault("access_tokens", {})[handle] = tokens[handle]
        save_json(STATE_PATH, state)

    recover_recent_unfinished_tasks(state, config, tokens, debate_room, synthesis_room)

    if not state.get("since"):
        LOG.info("initializing sync cursor; existing backlog will not trigger")
        sync = matrix("GET", "/_matrix/client/v3/sync?timeout=0", token=tokens["master"], timeout=30)
        state["since"] = sync.get("next_batch")
        save_json(STATE_PATH, state)

    LOG.info("supervisor live for debate=%s synthesis=%s", debate_room, synthesis_room)
    last_task_time = time.time()
    while RUNNING:
        try:
            path = f"/_matrix/client/v3/sync?timeout={SYNC_TIMEOUT_MS}&since={urllib.parse.quote(state['since'])}"
            sync = matrix("GET", path, token=tokens["master"], timeout=(SYNC_TIMEOUT_MS // 1000) + 10)
            state["since"] = sync.get("next_batch", state["since"])
            room = ((sync.get("rooms") or {}).get("join") or {}).get(debate_room) or {}
            events = ((room.get("timeline") or {}).get("events") or [])
            has_new_event = False
            for event in events:
                event_id = event.get("event_id")
                if event_id in state.get("processed_event_ids", []):
                    continue
                if event_id:
                    remember_unique(state["processed_event_ids"], event_id, 300)
                if should_ignore_event(event):
                    # ── Feature #14: Voice detection ──
                    content = (event.get("content") or {})
                    if event.get("type") == "m.room.message" and content.get("msgtype") in ("m.audio", "m.file"):
                        mime = content.get("info", {}).get("mimetype", "")
                        if "audio" in mime or content.get("msgtype") == "m.audio":
                            LOG.info("voice message detected, routing to audio handler")
                            handle_audio_message(state, config, tokens, debate_room, synthesis_room, event)
                            has_new_event = True
                            last_task_time = time.time()
                    continue
                body = (event.get("content") or {}).get("body") or ""
                handle_task(state, config, tokens, debate_room, synthesis_room, event, body)
                has_new_event = True
                last_task_time = time.time()
            save_json(STATE_PATH, state)
            # ── Feature #10: Curiosity Drive — idle-time deep dives ──
            if config.get("curiosity_enabled", False) and not state.get("disabled"):
                idle_minutes = (time.time() - last_task_time) / 60
                curiosity_interval = config.get("curiosity_interval_minutes", 15)
                if idle_minutes >= curiosity_interval:
                    # Trigger curiosity spike
                    knowledge_db = load_knowledge_db(config)
                    if not isinstance(knowledge_db, dict):
                        knowledge_db = {"nodes": [], "topics": {}}
                    nodes = knowledge_db.get("nodes", [])
                    if nodes:
                        # Pick a knowledge node with lowest confidence
                        low_conf_node = min(nodes, key=lambda n: n.get("confidence", 1))
                        curiosity_task = f"Follow-up: we previously noted: {low_conf_node.get('topic', 'unspecified')}. " \
                                          f"Claim: {low_conf_node.get('claim', '')[:200]}. Re-evaluate given any new developments."
                        LOG.info("curiosity drive triggered on idle %.0f min: %s", idle_minutes, curiosity_task[:80])
                        # Run as a lightweight sub-task — not in active_tasks to avoid spam
                        try:
                            # Post curiosity prompt to debate room
                            curiosity_body = f"🧠 SLOX PULSE: {curiosity_task}"
                            mock_event = {"event_id": f"curiosity_{int(time.time())}"}
                            handle_task(state, config, tokens, debate_room, synthesis_room, mock_event, curiosity_body)
                            last_task_time = time.time()
                        except Exception as exc:
                            LOG.warning("curiosity drive failed: %s", exc)
                    else:
                        # No knowledge yet — ask a speculative question
                        LOG.info("curiosity drive triggered (no knowledge yet), asking speculative question")
                        curiosity_body = f"🧠 SLOX PULSE: Given current global affairs, what is an unexplored question worth debating?"
                        mock_event = {"event_id": f"curiosity_{int(time.time())}"}
                        try:
                            handle_task(state, config, tokens, debate_room, synthesis_room, mock_event, curiosity_body)
                            last_task_time = time.time()
                        except Exception as exc:
                            LOG.warning("curiosity drive failed: %s", exc)
        except Exception:
            LOG.exception("sync loop failed")
            time.sleep(5)

    LOG.info("supervisor stopped")


if __name__ == "__main__":
    main()
