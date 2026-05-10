#!/usr/bin/env python3
"""
Matrix Bot Joiner — Invite all PB bots to their rooms and force-join them.
Run with delay between each operation to avoid rate limits.
"""

import requests, json, time, csv

BASE = "http://127.0.0.1:8008"
TOKEN = "syt_bWFzdGVy_cMLdxcPfovGMvuBtcsNU_2cxuut"

# Room defs
ROOMS = {
    "pb-debate": "!kWhMoqDcKqrIBNIBOD:slox.local",
    "pb-advisory": "!MiGXSTASgzPjOUiuaF:slox.local",
    "pb-report": "!ylAIzJAznJmIWvqddg:slox.local",
}

ALL_USERS = ["@grace:slox.local", "@marcus:slox.local", "@julia:slox.local",
             "@doria:slox.local", "@catherine:slox.local", "@oscar:slox.local",
             "@nadia:slox.local", "@victor:slox.local", "@xavier:slox.local",
             "@seraphina:slox.local", "@ava:slox.local"]

# Credentials
creds = {}
with open("/srv/slox_sb/local/slox_credentials.csv") as f:
    reader = csv.DictReader(f)
    for row in reader:
        creds[row["handle"]] = row["password"]

def rate_limited_request(method, url, json_data=None, token=TOKEN):
    """Retry on 429 with proper backoff."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    for attempt in range(5):
        if method == "POST":
            r = requests.post(url, json=json_data, headers=headers)
        elif method == "PUT":
            r = requests.put(url, json=json_data, headers=headers)
        elif method == "GET":
            r = requests.get(url, headers=headers)
        
        if r.status_code == 429:
            retry = r.json().get("retry_after_ms", 2000) / 1000 + 1
            print(f"  Rate limited, retrying in {retry:.0f}s...")
            time.sleep(retry)
            continue
        return r
    return r

def bot_login(handle, password):
    """Login as a bot user and get token."""
    r = requests.post(f"{BASE}/_matrix/client/v3/login", json={
        "type": "m.login.password",
        "user": handle,
        "password": password
    })
    if r.status_code == 429:
        retry = r.json().get("retry_after_ms", 5000) / 1000 + 1
        print(f"  Bot {handle} rate limited, waiting {retry:.0f}s...")
        time.sleep(retry)
        return bot_login(handle, password)
    if r.status_code == 200:
        return r.json().get("access_token")
    print(f"  Bot {handle} login FAILED: {r.status_code} {r.text[:100]}")
    return None

# Phase 1: Invite all users to all rooms
print("=== Phase 1: Invite all users to rooms ===")
for room_alias, room_id in ROOMS.items():
    users_to_invite = ALL_USERS if room_alias != "pb-report" else ["@ava:slox.local", "@master:slox.local"]
    for user in users_to_invite:
        r = rate_limited_request("POST", f"{BASE}/_matrix/client/v3/rooms/{room_id}/invite",
                                {"user_id": user})
        if r.status_code == 200:
            print(f"  Invited {user} to #{room_alias}")
        else:
            err = r.json().get("errcode", "UNKNOWN")
            if err == "M_FORBIDDEN":
                print(f"  {user} already in #{room_alias} or forbidden")
            else:
                print(f"  {user} -> {err} ({r.status_code})")
        time.sleep(0.5)

# Phase 2: Login as each bot and auto-accept (join) their rooms
print("\n=== Phase 2: Auto-join bots to rooms ===")
for handle, password in creds.items():
    bot_token = bot_login(handle, password)
    if not bot_token:
        continue
    
    for room_alias, room_id in ROOMS.items():
        if room_alias == "pb-report" and handle != "ava":
            continue
        # Join room
        r = rate_limited_request("POST", f"{BASE}/_matrix/client/v3/rooms/{room_id}/join",
                                {}, bot_token)
        if r.status_code == 200:
            print(f"  {handle} joined #{room_alias}")
        else:
            err = r.json().get("errcode", "UNKNOWN")
            if err == "M_FORBIDDEN":
                print(f"  {handle} not invited to #{room_alias}")
            else:
                print(f"  {handle} join #{room_alias} -> {err}")
        time.sleep(1)

print("\nDone! Bots joined to rooms.")
