# Slox_sb Feature Audit — 11 May 2026

## Legend
- ✅ Delivered / Verified Working
- ⚠️ Partially delivered or needs tuning
- ❌ Missing
- 🔧 Not applicable (slox_sb-specific)

## Infrastructure

| Item | Status | Notes |
|------|--------|-------|
| `/srv/slox_sb/` directory tree | ✅ | mirrors slox layout |
| 11 persona SOUL.md files | ✅ | all PB roles present |
| 3-room config | ✅ | debate, advisory, synthesis |
| 15K synthetic UHNW clients | ✅ | SQLite, 9 AUM tiers, 50 nationalities, life events |
| 5 synthetic instrument catalogs | ✅ | public, structured, PE, credit, insurance |
| Market data module | ✅ | written but not integrated into debate pipeline |
| Git repo | ✅ | pushed to github.com/HXr8/slox-sb |
| Matrix users + rooms | ✅ | 11 bot users, 3 rooms, aliases set |
| Systemd service | ✅ | `slox_sb-supervisor.service`, enabled, running |
| DeepSeek API integration | ✅ | V4 Flash, token cache, no rate limit issues |
| Token cache | ✅ | pre-generated tokens, avoids login rate limits |

## Supervisor (Forked from slox)

| Feature | Status | Notes |
|---------|--------|-------|
| Sync loop (Matrix → events) | ✅ | working |
| Task detection (`SLOX TASK:`) | ✅ | triggers full pipeline |
| Command routing (8 trigger types) | ⚠️ | TASK works; IC, WP, CREDIT, INS, RISK, COMPLIANCE, S&T not tested |
| Task enhancement (qing → improve) | ✅ | 2 rounds of LLM-based task expansion |
| Cognitive graph | ✅ | 3 sub-questions, multi-agent sub-debates |
| Synthesis generation | ✅ | Ava posts to synthesis room |
| Synthesis posted to Matrix | ✅ | verified in `#pb-synthesis` |
| Victor adjudicator | ❌ | not triggered once in any test — compliance edge case needed |
| Cross-agent banter | ⚠️ | banter replies fail after ~3 rounds (timeout/failure threshold) |
| Audit trail | ❌ | no audit_room_id configured; no audit logging visible |
| Active Learning (F1) | ❌ | never observed triggering |
| INVERT (F2) | ❌ | never observed (condition not met) |
| Heatmap (F3) | ❌ | never observed |
| Trust Credentials (F4) | ❌ | never observed |
| FORECAST (F5) | ❌ | never observed |
| Pulse (F6) | ❌ | never observed |
| Resonance (F9) | ❌ | never observed |
| Curiosity (F10) | ❌ | never observed (no multi-turn with Boss yet) |
| Counter-Memory (F11) | ❌ | never observed |
| Dynamic Tokens (F12) | ❌ | disabled in config (dynamic_tokens not set) |
| Screenshot (F13) | ❌ | never observed |
| Voice (F14) | ❌ | Matrix doesn't support voice; audio handler exists |
| Fork (F15) | ❌ | never observed |

## Innovation Features Status Explanation

The original slox supervisor has 15 innovation features (F1–F15). Most are *reactive* — they trigger under specific conditions (multi-turn conversation, user hesitation, specific keywords, heat thresholds). The slox_sb supervisor *inherits the code* for all 15, but in our testing they haven't triggered because:

1. **We only tested single-shot tasks.** Features like Curiosity, Resonance, Active Learning need multi-turn interaction.
2. **Advisory room routing** is configured but we haven't sent `SLOX IC:` or `SLOX RISK:` commands directly.
3. **Audit room** needs a room ID set in config — currently absent.
4. **Victor adjudicator** needs a compliance-triggering client or asset.

## Gaps That Matter

| Gap | Impact | Fix Effort |
|-----|--------|------------|
| Cross-agent banter failures | Replies truncated after sub-debates | Small — increase timeout/failure threshold |
| No market data injection | Agents argue from persona knowledge only | Medium — hook market_data.py into pre-debate context |
| No client profile lookup | 15K profiles exist but unused during debates | Medium — query DB for matching client before task |
| No audit room | No structured logging to Matrix | Small — create audit room, add ID to config |
| Victor never adjudicates | Compliance oversight path untested | Small — craft a test case with red flags |
| Advisory room routing untested | Only #pb-debate used so far | Small — test SLOX RISK: / SLOX CREDIT: commands |
| Spectral Purity | agents have identical system prompts: `"Stay completely in character"` | Medium — inject persona SOUL.md content into each agent's system prompt |
| Innovation features dormant | 15 features exist but never fire | Medium-high — need multi-turn, specific triggers |

## Updated Needs

Based on what works and what doesn't, the priority stack is:

1. **Fix banter timeout** — agents stop replying after sub-debate 3
2. **Inject SOUL.md into agent prompts** — currently agents only get `display_name` and `role_name` generic prompt
3. **Market data pre-debate context** — bring inflation/rates/geopolitical data into debate
4. **Client profile lookup** — query SQLite before debate, inject as context
5. **Test Victor** — send a compliance hot case
6. **Audit room** — structured logging
7. **Advisory room test** — send →#pb-advisory targeted commands
8. **Innovation feature triggers** — need interactive multi-turn with Boss to fire
