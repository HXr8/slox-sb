#!/usr/bin/env python3
"""
Slox_sb Client Batch Runner

Queries synthetic UHNW profiles from clients.db, submits each as a SLOX TASK:
to the Matrix debate room, waits for synthesis output in the report room,
and saves structured results.

Usage:
    python3 run_client_batch.py --random 5
    python3 run_client_batch.py --archetype first_gen_entrepreneur --limit 3
    python3 run_client_batch.py --aum-tier "$100M-$300M" --limit 2
    python3 run_client_batch.py --uuid "12c4a80e-..."              # single client
    python3 run_client_batch.py --random 3 --watch                  # tail synthesis live
"""

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

# ── Paths ──────────────────────────────────────────────────────────────
ROOT = Path(os.getenv("SLOX_ROOT", "/srv/slox_sb"))
CLIENTS_DB = ROOT / "data" / "client_profiles" / "clients.db"
RESULTS_DIR = ROOT / "data" / "client_profiles" / "batch_results"
ROOMS_FILE = ROOT / "local" / "slox_rooms.json"
CREDS_FILE = ROOT / "local" / "slox_credentials.csv"
STATE_FILE = ROOT / "local" / "slox_supervisor_state.json"

MATRIX_BASE = os.getenv("SLOX_HOMESERVER", "http://127.0.0.1:8008").rstrip("/")

# ── Constants ──────────────────────────────────────────────────────────
POLL_INTERVAL_S = 30           # how often to check synthesis room for output
TASK_SPACING_S = 90            # gap between task submissions
TASK_TIMEOUT_S = 600           # max wait per task (10 min)

ARCHETYPE_TASK_MAP = {
    "first_gen_entrepreneur": "Full private banking setup, portfolio diversification, estate planning, trust structures",
    "inheritance_receiver": "Wealth preservation, multi-generational planning, foundation setup, liquidity management",
    "retired_c_suite": "Income generation, capital preservation, healthcare planning, charitable giving",
    "multi_gen_family_office": "Family office structuring, next-gen education, governance framework, cross-border optimization",
    "tech_exec_ipo": "IPO liquidity event management, concentration risk, philanthropic vehicle, DAF setup",
    "real_estate_dynast": "REIT conversion, property diversification, leverage optimization, succession planning",
    "cross_border_exec": "Multi-jurisdiction tax optimization, residency planning, forex hedging, immigration-linked structures",
    "credit_user": "Leveraged portfolio strategy, margin optimization, private credit access, covenant advisory",
    "insurance_buyer": "Insurance wrapper review, PPLI structuring, premium financing, ILIT setup",
    "global_citizen": "Multi-passport wealth structuring, treaty election review, global mobility portfolio, digital nomad estate plan",
    "pe_vc_partner": "Carried interest planning, GP stake liquidity, co-investment vehicle, management company restructuring",
    "philanthropist": "Grant-making vehicle optimization, donor-advised fund, impact investing allocation, charitable remainder trust",
    "athlete_entertainer": "Short career peak planning, IP monetization, bond/insurance for future income, asset protection trust",
    "dynasty_founder": "Perpetual trust design, dynastic governance, family constitution, legacy asset carve-out",
    "derivative_sophisticated": "Structured product overlay, options-based hedging, bespoke derivative strategy, margin efficiency",
}

ARCHETYPE_AGENT_FOCUS = {
    "first_gen_entrepreneur": ["grace", "marcus", "julia", "xavier", "catherine"],
    "inheritance_receiver": ["catherine", "nadia", "grace", "oscar", "victor"],
    "retired_c_suite": ["marcus", "nadia", "catherine", "grace", "oscar"],
    "multi_gen_family_office": ["julia", "catherine", "xavier", "grace", "oscar"],
    "tech_exec_ipo": ["marcus", "seraphina", "julia", "xavier", "catherine"],
    "real_estate_dynast": ["julia", "xavier", "catherine", "grace", "doria"],
    "cross_border_exec": ["xavier", "catherine", "grace", "victor", "nadia"],
    "credit_user": ["oscar", "grace", "victor", "doria", "marcus"],
    "insurance_buyer": ["nadia", "catherine", "grace", "xavier", "victor"],
    "global_citizen": ["xavier", "catherine", "grace", "victor", "nadia"],
    "pe_vc_partner": ["julia", "marcus", "xavier", "catherine", "seraphina"],
    "philanthropist": ["catherine", "nadia", "grace", "julia", "xavier"],
    "athlete_entertainer": ["nadia", "catherine", "grace", "oscar", "xavier"],
    "dynasty_founder": ["catherine", "julia", "xavier", "grace", "victor"],
    "derivative_sophisticated": ["doria", "seraphina", "marcus", "oscar", "victor"],
}


# ── Helpers ────────────────────────────────────────────────────────────

def get_token():
    """Get master token from token cache or credentials CSV."""
    # Try token cache first
    token_file = ROOT / "local" / "slox_tokens.csv"
    if token_file.exists():
        for line in token_file.read_text().strip().splitlines():
            if line.startswith("master,"):
                return line.split(",", 1)[1]
    # Fall back — login via password
    with open(CREDS_FILE) as f:
        for line in f:
            parts = line.strip().split(",")
            if parts[0] == "master":
                pw = parts[2]
                payload = {
                    "type": "m.login.password",
                    "identifier": {"type": "m.id.user", "user": "master"},
                    "password": pw,
                    "initial_device_display_name": "slox_sb-client-batcher",
                }
                result = http_json("POST", f"{MATRIX_BASE}/_matrix/client/v3/login", body=payload, timeout=15)
                return result["access_token"]
    raise RuntimeError("Cannot obtain master token")


def http_json(method, url, body=None, token=None, timeout=30, ok=(200,)):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode()
        raise RuntimeError(f"{method} {url} -> {exc.code} {payload}") from exc


def fetch_source_rooms(token):
    """Load room IDs from the rooms file, with Matrix alias fallback."""
    rooms_file = ROOMS_FILE
    if not rooms_file.exists():
        raise RuntimeError(f"Rooms file not found: {rooms_file}")

    rooms_data = json.loads(rooms_file.read_text())
    rooms = {}
    for r in rooms_data:
        rooms[r["room_key"]] = r["room_id"]
    return rooms


def send_task(token, room_id, task_text):
    """Post a SLOX TASK: message to the debate room."""
    payload = {
        "msgtype": "m.text",
        "body": task_text,
    }
    url = f"{MATRIX_BASE}/_matrix/client/v3/rooms/{quote(room_id, safe='')}/send/m.room.message"
    result = http_json("POST", url, body=payload, token=token)
    return result["event_id"]


def get_recent_messages(token, room_id, limit=50):
    """Fetch recent messages from a room."""
    url = f"{MATRIX_BASE}/_matrix/client/v3/rooms/{quote(room_id, safe='')}/messages?dir=b&limit={limit}"
    result = http_json("GET", url, token=token)
    return result.get("chunk", [])


def build_task_text(client):
    """Construct a SLOX TASK: from a client profile row."""
    aa = json.loads(client["asset_allocation"])

    archetype = client["archetype"]
    task_base = ARCHETYPE_TASK_MAP.get(archetype, "Comprehensive portfolio review and wealth planning")

    # Parse risk profile
    rp = client["risk_profile"]

    # Parse life events for urgency signals
    life_events = json.loads(client.get("life_events", "[]"))
    recent_events = [e for e in life_events if e.get("year", 0) >= max(e.get("year", 0) for e in life_events) - 5][:3]
    event_context = ""
    if recent_events:
        event_desc = "; ".join(f"{e['event']} ({e.get('severity','')})" for e in recent_events)
        event_context = f" Recent life events: {event_desc}."

    # Asset allocation summary
    top_alloc = sorted(aa.items(), key=lambda x: x[1], reverse=True)[:4]
    alloc_summary = ", ".join(f"{k.replace('_',' ').title()} {v*100:.0f}%" for k, v in top_alloc)

    # Existing advisory
    existing = client.get("existing_advisory", "independent")

    # Fee sensitivity
    fee_note = ""
    fs = client.get("fee_sensitivity", "medium")
    if fs == "low":
        fee_note = " Fee-sensitive client — cost-efficient structures preferred."

    aum_display = f"${client['total_aum']:,.0f}"

    task = (
        f"SLOX TASK: {archetype.replace('_', ' ').title()} — {client['full_name']}, "
        f"{client['age']}, {client['nationality']}, resident {client['residency']}. "
        f"AUM {aum_display}. "
        f"Risk profile: {rp}. "
        f"Current allocation: {alloc_summary}. "
        f"Wealth source: {client['wealth_source']} ({client['wealth_year']}). "
        f"Existing advisory: {existing}. "
        f"Marital status: {client['marital_status']}, {client['dependents']} dependents."
        f"{event_context}{fee_note}"
        f"\n\nScope: {task_base}."
    )
    return task


def wait_for_synthesis(token, room_id, task_fragment, timeout_s=TASK_TIMEOUT_S):
    """Poll synthesis room for output containing the task fragment."""
    deadline = time.time() + timeout_s
    seen_ids = set()
    while time.time() < deadline:
        msgs = get_recent_messages(token, room_id, limit=50)
        for msg in msgs:
            content = msg.get("content", {})
            body = content.get("body", "")
            sender = msg.get("sender", "")
            event_id = msg.get("event_id", "")
            if event_id in seen_ids:
                continue
            seen_ids.add(event_id)

            # Look for synthesis messages from ava containing the task
            if "@ava" in sender and "Cognitive Graph Synthesis" in body:
                # Check if this is our task (by looking for the client name or key fragment)
                if task_fragment in body:
                    return {
                        "event_id": event_id,
                        "sender": sender,
                        "body": body,
                        "timestamp": msg.get("origin_server_ts", int(time.time() * 1000)),
                    }
        time.sleep(POLL_INTERVAL_S)
    return None


def load_clients(filter_args, limit):
    """Query clients from the SQLite database."""
    db = sqlite3.connect(str(CLIENTS_DB))
    db.row_factory = sqlite3.Row

    conditions = []
    params = []

    if filter_args.get("random"):
        order = "ORDER BY RANDOM()"
    else:
        order = "ORDER BY total_aum DESC"

    if filter_args.get("archetype"):
        conditions.append("archetype = ?")
        params.append(filter_args["archetype"])

    if filter_args.get("aum_tier"):
        conditions.append("aum_tier = ?")
        params.append(filter_args["aum_tier"])

    if filter_args.get("uuid"):
        conditions.append("client_uuid = ?")
        params.append(filter_args["uuid"])

    if filter_args.get("min_aum"):
        conditions.append("total_aum >= ?")
        params.append(filter_args["min_aum"])

    if filter_args.get("max_aum"):
        conditions.append("total_aum <= ?")
        params.append(filter_args["max_aum"])

    if filter_args.get("nationality"):
        conditions.append("nationality = ?")
        params.append(filter_args["nationality"])

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    cursor = db.execute(f"SELECT * FROM clients {where} {order} LIMIT ?", params + [limit])
    rows = [dict(row) for row in cursor]
    db.close()

    if not rows:
        print(f"⚠ No clients found matching filters: {filter_args}")
        sys.exit(1)

    print(f"✓ Loaded {len(rows)} client(s) from database")
    return rows


def format_duration(seconds):
    if seconds < 60:
        return f"{seconds:.0f}s"
    return f"{seconds / 60:.1f}m"


def main():
    parser = argparse.ArgumentParser(description="Run slox_sb client batch")
    parser.add_argument("--random", type=int, help="Pick N random clients")
    parser.add_argument("--limit", type=int, default=5, help="Max clients to process (default: 5)")
    parser.add_argument("--archetype", type=str, help="Filter by archetype")
    parser.add_argument("--aum-tier", type=str, help="Filter by AUM tier (e.g. '$100M-$300M')")
    parser.add_argument("--uuid", type=str, help="Single client UUID")
    parser.add_argument("--min-aum", type=float, help="Minimum AUM")
    parser.add_argument("--max-aum", type=float, help="Maximum AUM")
    parser.add_argument("--nationality", type=str, help="Filter by nationality")
    parser.add_argument("--watch", action="store_true", help="Watch synthesis room for live output")
    parser.add_argument("--dry-run", action="store_true", help="Print tasks without sending")

    args = parser.parse_args()

    if not (args.random or args.archetype or args.aum_tier or args.uuid or args.nationality):
        print("⚠ No filter specified. Use --random N or --archetype NAME or similar.")
        sys.exit(1)

    # Create results dir
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # Load clients
    filter_args = {
        "random": args.random,
        "archetype": args.archetype,
        "aum_tier": args.aum_tier,
        "uuid": args.uuid,
        "min_aum": args.min_aum,
        "max_aum": args.max_aum,
        "nationality": args.nationality,
    }
    limit = args.random or args.limit
    clients = load_clients(filter_args, limit)

    # Get Matrix tokens and rooms
    token = get_token()
    rooms = fetch_source_rooms(token)

    debate_room = rooms.get("debate")
    synthesis_room = rooms.get("synthesis")
    if not debate_room or not synthesis_room:
        raise RuntimeError(f"Cannot find debate/synthesis room in rooms file: {rooms}")

    print(f"  Debate room: {debate_room}")
    print(f"  Synthesis room: {synthesis_room}")
    print()

    # Process each client
    results = []
    start_time = time.time()

    for i, client in enumerate(clients, 1):
        task_text = build_task_text(client)
        uuid = client["client_uuid"]
        name = client["full_name"]
        archetype = client["archetype"]

        print(f"[{i}/{len(clients)}] {name} ({archetype}) — ${client['total_aum']:,.0f}")

        if args.dry_run:
            print("  ⏏ DRY RUN — task preview:")
            for line in task_text.strip().split("\n"):
                print(f"    {line}")
            print()
            results.append({
                "client_uuid": uuid,
                "full_name": name,
                "status": "previewed",
                "task_text": task_text,
            })
            continue

        # Send task
        print(f"  → Posting task...", end=" ", flush=True)
        try:
            event_id = send_task(token, debate_room, task_text)
            print(f"event {event_id[:20]}...")
        except Exception as e:
            print(f"FAILED: {e}")
            results.append({
                "client_uuid": uuid,
                "full_name": name,
                "status": "error",
                "error": str(e),
            })
            continue

        # Wait for synthesis output
        print(f"  ⏳ Waiting for synthesis (timeout {format_duration(TASK_TIMEOUT_S)})...", end=" ", flush=True)
        fragment = name.split(" ")[0]  # use first name as fragment matcher
        synthesis = wait_for_synthesis(token, synthesis_room, fragment, TASK_TIMEOUT_S)

        if synthesis:
            elapsed = time.time() - start_time
            body_len = len(synthesis["body"])
            print(f"✓ {body_len} chars in {format_duration(elapsed - (i-1)*TASK_SPACING_S - start_time)}")
        else:
            print(f"✗ TIMEOUT (no synthesis detected)")
            synthesis = None

        results.append({
            "client_uuid": uuid,
            "full_name": name,
            "archetype": archetype,
            "total_aum": client["total_aum"],
            "risk_profile": client["risk_profile"],
            "nationality": client["nationality"],
            "event_id": event_id,
            "synthesis_summary": synthesis["body"][:1000] if synthesis else None,
            "synthesis_length": len(synthesis["body"]) if synthesis else 0,
            "synthesis_event_id": synthesis["event_id"] if synthesis else None,
            "status": "completed" if synthesis else "timeout",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        # Spacing between tasks
        if i < len(clients):
            print(f"  💤 Waiting {TASK_SPACING_S}s before next client...")
            time.sleep(TASK_SPACING_S)
        print()

    # Save batch results
    batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    result_file = RESULTS_DIR / f"batch_{batch_id}.json"
    result_file.write_text(json.dumps({
        "batch_id": batch_id,
        "filter": filter_args,
        "total": len(clients),
        "completed": sum(1 for r in results if r.get("status") == "completed"),
        "timeout": sum(1 for r in results if r.get("status") == "timeout"),
        "errors": sum(1 for r in results if r.get("status") == "error"),
        "results": results,
        "run_duration_s": int(time.time() - start_time),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }, indent=2))
    print(f"📄 Results saved: {result_file}")

    # Summary
    total_time = format_duration(time.time() - start_time)
    completed = sum(1 for r in results if r.get("status") == "completed")
    print(f"\n{'='*50}")
    print(f"Batch complete: {completed}/{len(clients)} succeeded in {total_time}")
    if args.watch:
        print("(--watch mode: synthesis room can be checked manually)")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
