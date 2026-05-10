# Slox_sb Status — 11 May 2026 00:02 SGT

## Active

- **Supervisor**: Running as systemd service `slox_sb-supervisor.service` (user: hp)
- **Matrix**: 3 rooms provisioned:
  - `#pb-debate:slox.local` (ID: `!kWhMoqDcKqrIBNIBOD`) - all 10 PB agents + Ava
  - `#pb-advisory:slox.local` (ID: `!MiGXSTASgzPjOUiuaF`) - all agents
  - `#pb-synthesis:slox.local` (ID: `!ylAIzJAznJmIWvqddg`) - Ava only (report output)
- **Bot users**: 11 PB agent Matrix users registered and joined to rooms
- **LLM**: DeepSeek Chat via existing API key
- **Token cache**: Pre-generated tokens in `local/slox_tokens.csv` (avoids login rate limits)
- **Config**: `/srv/slox_sb/config/three_room_pb.json` with 8 command routing rules
- **Client data**: 15,000 synthetic UHNW profiles in SQLite (24.9 MB)
- **Instruments**: 5,200 public securities, 200 structured products, 100 PE funds, 50 credit termsheets, 50 insurance policies
- **Repo**: Public on GitHub → `github.com/HXr8/slox-sb`

## Directory Structure

```
/srv/slox_sb/
├── config/three_room_pb.json
├── personas/{grace,marcus,julia,doria,catherine,oscar,nadia,victor,xavier,seraphina,ava}/SOUL.md
├── supervisor/slox-supervisor.py (forked from main slox, config path + token cache patched)
├── scripts/{generate_clients.py, generate_instruments.py, market_data.py, join_bots.py}
├── data/
│   ├── client_profiles/clients.db
│   └── instruments/*.json
└── local/{slox_credentials.csv, slox_tokens.csv, slox_rooms.json, slox_supervisor_state.json, slox_supervisor.log}
```

## Tested

- Cognitive graph debate pipeline confirmed working (sub-questions generated, DeepSeek calls succeeding)
- Task detection from Matrix room working
- Synthesis agent path configured (Ava)
- Victor adjudicator path configured

## Known Gaps / Next

1. The supervisor has hardcoded `qing` refs in several places - alias added as fallback
2. `persona_path` in config is loaded but not yet used by the system prompt builder (uses display_name only)
3. Data generators use free public APIs with synthetic fallback
4. Victor's adjudicator logic hasn't been tested with a compliance-triggering scenario
5. Market data module is written but not yet integrated as a pre-debate context injector
