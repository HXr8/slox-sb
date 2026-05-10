# Slox Innovation Implementation Roadmap

## Phasing Strategy

**Constraint:** New config fields must extend `two_room_lounge.json` without breaking existing reads.

## Phase 0: Foundation (pre-req for everything)
- [ ] Extend `initial_state()` with new KV stores (task_history, trust_db, knowledge_deltas, counter_memories, curiosity_state, fork_tree, audit_msg_counter)
- [ ] Add `load_json`/`save_json` helpers for per-db files
- [ ] Add new config schema to `two_room_lounge.json` defaults
- [ ] Add `send_message()` variant for room targeting

## Phase 1: Structural Upgrades (Medium complexity, high impact)
### Feature #7: Meta-Cognitive Audit Trail
- New audit room in config
- Structured log per agent LLM call → post to audit room
- Include system prompt hash, latency, tokens, fallback flag
- **Boss has full access** to the audit room (Matrix-readable)

### Feature #6: Boss-Signal Pulse
- `SLOX PULSE:` prefix handler
- 1-sentence-per-agent with max_tokens=100
- qing produces 3-bullet verdict
- Reaction pinning

### Feature #12: Dynamic Token Allocation
- Complexity formula for token budgeting
- Per-agent max_tokens calculated per task

## Phase 2: Cognitive Upgrades (High complexity)
### Feature #3: Opinion Heatmap
- Dimension classifier (regex-based)
- 5×5 matrix → qing renders emoji heatmap
- Inline after synthesis

### Feature #8: Cognitive Graph Chaining (Feature #8)
- qing generates sub-question plan JSON
- Thread pool executor for parallel sub-debates
- Master synthesis includes all sub-synthesis

**Feature #8 modification for Boss-accessible logs:**
→ Each sub-debate writes to the audit room with `[SUB-DEBATE {i}/{n}: {sub_question}]` header
→ Full agent replies per sub-debate are logged to audit room as `> {agent}: {reply}`
→ Sub-question plan JSON stored in audit room before execution

## Phase 3: Learning Systems (High/Extreme complexity)
### Feature #1: Active Learning (knowledge deltas)
- Post-debate knowledge mining
- ChromaDB/SQLite-vss vector store
- Knowledge context injection into future prompts

### Feature #9: Resonance Scoring
- sentence-transformers embed calls
- 5×5 similarity matrix per debate
- Synthesis warning footer

### Feature #11: Counter-Memory Gate
- Counter-delta storage
- Embedding distance check <0.2
- Devil's advocate injection

## Phase 4: Advanced (High complexity)
### Feature #2: Echo-Location (INVERT mode)
- State cloning + prompt patching
- Dual synthesis + diff summary

### Feature #4: Trust Credentials
- Trust score maintenance per domain
- Weighted synthesis
- Boss reaction feedback

### Feature #5: Temporal Slicing
- SLOX FORECAST: parallel time-based debates
- Meta-synthesis

## Phase 5: Premium (Medium-High)
### Feature #10: Curiosity Drive
- Background idle-detect thread
- Auto topic generation
- Mini-debate on uncertainty gaps

### Feature #13: Synthesis-in-Screenshot
- HTML template → snapshot
- Flux pipeline or Pillow rendering
- Dual delivery (text + image)

### Feature #15: Time-Travel Fork
- task_history persistence
- SLOX FORK: handler
- Fork tree tracking

## Phase 6: Stretch
### Feature #14: Voice-Reply
- Whisper transcription
- Edge-TTS / ElevenLabs
- Per-agent voice profiles
