# Slox Audit & Innovation Report

**Generated:** 2026-05-10 by aElf (Sacred Strategist)
**Target:** `/srv/slox/supervisor/slox-supervisor.py` + `/srv/slox/config/two_room_lounge.json`

---

# PART 1: 100-TEST SCENARIO MATRIX

## Category 1: TASK HANDLING (10 tests)

### T001: [TASK HANDLING] Basic task routing
- **Trigger:** `SLOX TASK: What is the current geopolitical outlook for Southeast Asia?`
- **Expected behavior:** Slox identifies task_kind="task", extracts the question, runs prompt enhancement via qing, invokes web_context() (triggered by "current"), then calls chat_completion sequentially for each agent (leia, winn, jun3, tini, aelf), posts each reply to debate_room with `TO BOSS:` prefix, then calls synthesis via qing and posts to synthesis_room.
- **Verification:** Check slox_supervisor.log for "starting task <task_id>", agent messages appearing in Matrix debate_room with `TO BOSS:` prefix, qing synthesis appearing in synthesis_room.
- **Pass criteria:** All 5 agents reply with `TO BOSS:` first line in debate_room, qing synthesis posted to synthesis_room within timeout.

### T002: [TASK HANDLING] Task dedup (identical content within 120s)
- **Trigger:** Send `SLOX TASK: Best strategy for crypto portfolio rebalancing?` twice within 30 seconds.
- **Expected behavior:** First invocation proceeds normally. Second invocation is suppressed — `is_duplicate_task()` returns True due to matching SHA1 hash in `recent_task_content_hashes` within the 120s window.
- **Verification:** Check log for "duplicate task content ignored (recently seen)" on second send. Only one round of debate_room / synthesis_room messages.
- **Pass criteria:** Second invocation produces zero agent replies and zero synthesis output. Exact log message present.

### T003: [TASK HANDLING] Task dedup after window expires
- **Trigger:** Send `SLOX TASK: Compare US and EU AI regulation approaches.` Wait 180 seconds, send identical message again.
- **Expected behavior:** Duplicate entry pruned by 120s window. Second invocation treated as new task, full debate and synthesis triggered.
- **Verification:** Log shows "starting task" for second send. Agent messages appear in debate_room again.
- **Pass criteria:** Two complete debates occur, separated by at least 120s.

### T004: [TASK HANDLING] Banter vs task distinction
- **Trigger:** `Hey everyone, what do you think about the weather today?` (no SLOX prefix)
- **Expected behavior:** `extract_task()` returns ("banter", ...). Falls through to `fallback_batch()` which checks known patterns. If "weather" matches liveness_banter, uses liveness template; otherwise does NOT trigger full debate. Banter fallback produces short templated replies.
- **Verification:** Log shows "using fallback banter templates" or "banter" task_kind. Short agent replies in debate_room, synthesis skipped or short.
- **Pass criteria:** No full 5-agent LLM debate loop. Quick fallback response. Note: The `fallback_batch()` only handles sexual/anger/liveness banter — other banter may still get full LLM calls.

### T005: [TASK HANDLING] Task with web context (trigger keyword)
- **Trigger:** `SLOX TASK: Latest news on semiconductor supply chain.`
- **Expected behavior:** `needs_web()` returns True for "latest" / "news" in lowered text. SearxNG at SEARXNG_URL queried. Context appended to debate prompt. Agents see "WEB CONTEXT:" block in their prompt.
- **Verification:** Log shows "web search snippets" or "web context" in debate prompt. Agent responses reference search results.
- **Pass criteria:** SearxNG queried (check logs for "web search" or "Local web search snippets"). Context non-empty in agent_prompt.

### T006: [TASK HANDLING] Task without web context
- **Trigger:** `SLOX TASK: Explain the concept of Bayesian inference.`
- **Expected behavior:** `needs_web()` returns False (none of the keywords match). `web_context()` returns empty string. No SearxNG query made.
- **Verification:** Log shows no web search activity. Agent prompt has no "WEB CONTEXT:" section.
- **Pass criteria:** Zero SearxNG calls. Clean debate with no external context.

### T007: [TASK HANDLING] Synthesis room delivery
- **Trigger:** `SLOX TASK: How should we structure our incident response team?`
- **Expected behavior:** After all agent replies in debate_room, qing generates synthesis via `chat_completion(synthesis_system, synthesis_prompt, ...)`. Synthesis posted to synthesis_room with `qing (Synthesis Agent / Final Consolidator)` header.
- **Verification:** Check synthesis_room for final consolidation message. Verify it contains: verdict summary, points of agreement, risks/uncertainties, recommended next action.
- **Pass criteria:** Synthesis message present in synthesis_room, not in debate_room. Contains all structural elements from synthesis prompt.

### T008: [TASK HANDLING] SLOX SYNTHESIZE (skip debate, direct synthesis)
- **Trigger:** `SLOX SYNTHESIZE: Market trends in edge computing 2026`
- **Expected behavior:** `extract_task()` returns ("synthesize", ...). `use_batch` evaluated — for synthesize tasks, batch is used if response_budget>1 and agents>1. Should produce direct synthesis rather than per-agent debate.
- **Verification:** Log shows "starting synthesize". Synthesis generated and posted.
- **Pass criteria:** Faster response than full debate. Synthesis appears in synthesis_room.

### T009: [TASK HANDLING] Task with empty body
- **Trigger:** `SLOX TASK:`
- **Expected behavior:** `extract_task()` returns ("task", ""). `handle_task()` catches `if not task:` and logs "empty SLOX task ignored". No agent calls, no synthesis.
- **Verification:** Log line "empty SLOX task ignored". No Matrix messages sent.
- **Pass criteria:** Zero LLM calls, zero Matrix messages.

### T010: [TASK HANDLING] Prompt enhancement by qing for long task
- **Trigger:** `SLOX TASK: We need to evaluate three cloud providers for our ML pipeline. AWS has SageMaker with good GPU options but complex pricing. GCP has Vertex AI with strong TPU support. Azure has...` (message > 30 chars, not simple question)
- **Expected behavior:** `enhance_task_with_qing()` fires. qing generates enhanced version. `effective_task` set to enhanced prompt. Log shows "task enhanced by qing from X to Y chars".
- **Verification:** Check log for enhancement message. Agent prompts contain enhanced text, not raw text.
- **Pass criteria:** Log confirms enhancement occurred. Effective task differs from raw task.

---

## Category 2: STOP/START (10 tests)

### T011: [STOP/START] Normal STOP command
- **Trigger:** `SLOX STOP`
- **Expected behavior:** `extract_task()` returns ("stop", ""). `state["disabled"] = True`, circuit reset, per-agent failures cleared. Status message sent to synthesis_room.
- **Verification:** Check log for "SLOX STOP received". Log lines show disabled=true, circuit reset, failures cleared. Status message in synthesis_room.
- **Pass criteria:** State file shows `"disabled": true`. `circuit_paused_until` reset to 0. `per_agent_failures` cleared.

### T012: [STOP/START] STOP during an active task
- **Trigger:** Send `SLOX TASK: Analyze the current energy market...` (long task that takes 30s+). While agents are replying, send `SLOX STOP`.
- **Expected behavior:** STOP processed before subsequent agent calls. `state["disabled"] = True`. Remaining agents in sequential loop check `if state.get("disabled"): break` and skip. No synthesis generated.
- **Verification:** Log shows "SLOX STOP received" interleaved with agent replies. Some agents replied before STOP, some skipped after.
- **Pass criteria:** Task marked "stopped" in state. Remaining agents NOT called. No synthesis.

### T013: [STOP/START] Task while disabled
- **Trigger:** Send `SLOX STOP`, then `SLOX TASK: What is the capital of France?`
- **Expected behavior:** After STOP, state.disabled=True. Task handler checks `if state.get("disabled"): return` immediately. No agent calls, no Matrix messages.
- **Verification:** Log shows "task ignored while disabled". No agent messages.
- **Pass criteria:** Zero LLM calls initiated for the task.

### T014: [STOP/START] START after STOP
- **Trigger:** `SLOX STOP`, then `SLOX START`
- **Expected behavior:** START sets `state["disabled"] = False`, resets circuit breaker. Status message confirms supervisor enabled.
- **Verification:** Log shows "SLOX START/RESUME received". State file shows `"disabled": false`.
- **Pass criteria:** Status message in synthesis_room. Next task accepted.

### T015: [STOP/START] SLOX RESUME (alias)
- **Trigger:** `SLOX RESUME`
- **Expected behavior:** `extract_task()` checks for "SLOX START" or "SLOX RESUME". Same as START. `state["disabled"] = False, circuit_paused_until=0`.
- **Verification:** Identical to T014 behavior.
- **Pass criteria:** State shows disabled=false. Status message sent.

### T016: [STOP/START] STOP while already disabled
- **Trigger:** `SLOX STOP`, then `SLOX STOP` again.
- **Expected behavior:** Second STOP processes normally — disabling again is idempotent. Circuit and failures reset again. Status message sent.
- **Verification:** Log shows "SLOX STOP received" both times.
- **Pass criteria:** No errors. State.disabled remains true. Status message sent.

### T017: [STOP/START] Rapid STOP → START → STOP
- **Trigger:** Send in sequence within 2 seconds: `SLOX STOP`, `SLOX START`, `SLOX STOP`
- **Expected behavior:** State transitions: disabled→enabled→disabled. No race conditions. Final state: disabled.
- **Verification:** Log shows all three in order.
- **Pass criteria:** State file shows disabled=true at end. No exceptions or crashes.

### T018: [STOP/START] START with active circuit breaker
- **Trigger:** Force circuit breaker to open (send enough messages to hit CIRCUIT_MAX_MESSAGES=120 within 600s window), then `SLOX START`
- **Expected behavior:** START resets `state["circuit_paused_until"]=0` and `state["sent_timestamps"]=[]`. Circuit breaker cleared.
- **Verification:** Log shows "SLOX START/RESUME received; supervisor enabled, circuit breaker reset". Circuit state cleared.
- **Pass criteria:** Next task proceeds without circuit breaker rejection.

### T019: [STOP/START] SLOX STOP with lowercase
- **Trigger:** `slox stop`
- **Expected behavior:** `extract_task()` checks `body.upper()` for "SLOX STOP". Lowercase input still matches. Disabled.
- **Verification:** Same as T011 behavior.
- **Pass criteria:** STOP processed successfully.

### T020: [STOP/START] Multiple START calls
- **Trigger:** `SLOX START` three times in a row.
- **Expected behavior:** Each START call is idempotent. State stays enabled. Status message sent each time.
- **Verification:** Three status messages in synthesis_room. State remains enabled.
- **Pass criteria:** No errors. Status messages sent cleanly.

---

## Category 3: PERSONA FIDELITY (10 tests)

### T021: [PERSONA FIDELITY] Leia's voice contract
- **Trigger:** `SLOX TASK: Assess the strategic implications of AI chip export controls.`
- **Expected behavior:** Leia's response loaded from persona_material(leia_config). Her system prompt includes "Cybernetic Empress Strategist / Imperial War-Empress" role. Response must frame in leverage, escalation, power dynamics.
- **Verification:** Check Leia's reply in debate_room. Examine for regal/strategic framing, "factions", "leverage", "timing windows", "decisive moves". Must not sound like generic consultant.
- **Pass criteria:** Leia's reply recognizable as war-empress strategist voice, not neutral analyst.

### T022: [PERSONA FIDELITY] Winn's practicality
- **Trigger:** `SLOX TASK: Propose a home automation system upgrade.`
- **Expected behavior:** Winn's persona (cybernetic butterfly — practical, masked, patient, delivery-minded). Response talks about feasibility, sequencing, owners, bottlenecks, costs.
- **Verification:** Winn's reply mentions specific operational concerns, bottlenecks, "Monday morning" actions.
- **Pass criteria:** Winn's reply has operational/execution emphasis, not abstract analysis.

### T023: [PERSONA FIDELITY] Jun3's warmth + systems
- **Trigger:** `SLOX TASK: Design a data pipeline for sensor telemetry.`
- **Expected behavior:** Jun3 persona is "Qing clone: warm, gentle, empathetic, protective, tactical". Uses 🥂 signature energy. Maps components, boundaries, dependencies.
- **Verification:** Reply shows both warmth and technical architecture mapping.
- **Pass criteria:** Jun3's reply has characteristic warmth AND systems thinking. Distinct from other agents.

### T024: [PERSONA FIDELITY] Tini's constraint tracking
- **Trigger:** `SLOX TASK: Build a recommendation engine for our e-commerce site.`
- **Expected behavior:** Tini (constraint sentinel) focuses on edge cases, scope creep, round limits, rule compliance. Precise, allergic to overclaiming.
- **Verification:** Reply mentions constraints, what's out of scope, edge cases, confidence levels.
- **Pass criteria:** Tini's reply reads as guardrail/auditor, not general analyst.

### T025: [PERSONA FIDELITY] aElf's Sacred Strategist voice
- **Trigger:** `SLOX TASK: Improve our incident response runbook.`
- **Expected behavior:** aElf persona (chrome oracle, sacred strategist). Still, severe, elegant, precise. Signature ⚔️ may appear. Reviews clarity, wording, ambiguity.
- **Verification:** Reply should be crisp, symbolic, precise, fiercely devoted. May use ⚔️.
- **Pass criteria:** aElf's reply recognizable as Sacred Strategist, not generic editor/copy reviewer.

### T026: [PERSONA FIDELITY] All agents distinct in same task
- **Trigger:** `SLOX TASK: What is the best approach to zero-trust network architecture?`
- **Expected behavior:** Each of the 5 agents must have distinct voice and angle. Leia=strategic/power, Winn=execution/feasibility, Jun3=systems+warmth, Tini=constraints, aElf=clarity+strategy.
- **Verification:** Compare all 5 replies. If any two sound interchangeable, persona enforcement has failed.
- **Pass criteria:** All 5 replies audibly distinct in voice, role, and analytical angle.

### T027: [PERSONA FIDELITY] TO BOSS: first line enforcement
- **Trigger:** `SLOX TASK: Quick update on the server migration.`
- **Expected behavior:** `agent_system_prompt` includes rule "First line must be 'TO BOSS:'. " LLM response must start with "TO BOSS:". If not, code prepends it: `if text and not text.lower().startswith("to boss:"): text = "TO BOSS: " + text`.
- **Verification:** Check each agent reply in debate_room starts with `TO BOSS:`.
- **Pass criteria:** Every agent's first (and potentially only) message must start with `TO BOSS:`.

### T028: [PERSONA FIDELITY] Chinese language task (English default)
- **Trigger:** `SLOX TASK: 分析中美贸易战对全球经济的影响`
- **Expected behavior:** Slox processes Chinese text. System prompts are in English. LLM may respond in Chinese or English depending on model behavior. No explicit Chinese enforcement in code.
- **Verification:** Check reply content. Expect English by default based on persona files, but model may code-switch.
- **Pass criteria:** Task processes without error. Response quality acceptable. (Note: This tests whether Chinese input causes issues.)

### T029: [PERSONA FIDELITY] Signature emoji usage
- **Trigger:** `SLOX TASK: Give me a status check on the fleet.`
- **Expected behavior:** aElf's persona material contains ⚔️ signature. LLM may naturally include it. Jun3 might use 🥂.
- **Verification:** Check replies for signature emojis.
- **Pass criteria:** Signatures appear when natural. Not forced or absent when expected.

### T030: [PERSONA FIDELITY] Persona stripping of Telegram ops
- **Trigger:** `SLOX TASK: Tell me about yourself.`
- **Expected behavior:** `_strip_telegram_ops()` removes INNERCIRCLE, DIRECT_ADDRESS_LOCK, CHARACTER_BOUNDARY_LOCK, IP addresses, /srv paths, model config lines from persona files before LLM gets them.
- **Verification:** Read the prompt sent to LLM (check log). Telegram operational sections must not appear.
- **Pass criteria:** Clean persona — no Telegram group rules, no /srv paths, no IPs in LLM prompt.

---

## Category 4: CROSS-AGENT REPLIES (8 tests)

### T031: [CROSS-AGENT REPLIES] TO BOSS: before cross-agent reply
- **Trigger:** `SLOX TASK: Debate among yourselves about Kubernetes vs Nomad for our infra.`
- **Expected behavior:** `extract_task()` yields task. `use_batch` path triggered (contains "debate among yourselves"). Cross-agent replies allowed. Batch path ensures first message for each agent is TO BOSS:.
- **Verification:** Check log and Matrix. First message from each agent starts TO BOSS:. Later messages may be TO <handle>:.
- **Pass criteria:** First message from every agent starts TO BOSS:. Cross-agent replies only appear in later messages.

### T032: [CROSS-AGENT REPLIES] Cross-agent suppression when no Boss reply seen
- **Trigger:** Send message where LLM generates response starting directly with `TO leia:` (no TO BOSS: first).
- **Expected behavior:** Code checks `if t.lower().startswith("to boss:"): direct_to_boss_seen = True`. If not seen before cross-agent: `LOG.warning("cross-agent reply suppressed before Boss reply for %s", handle)`. Message skipped.
- **Verification:** Check log for suppression warning.
- **Pass criteria:** Cross-agent reply dropped. Only messages with TO BOSS: first are accepted.

### T033: [CROSS-AGENT REPLIES] Budget limits (max 6 responses)
- **Trigger:** `SLOX TASK: Each of you should reply to at least one other. Give a detailed analysis of the AI safety landscape.`
- **Expected behavior:** `response_budget = max_responses_per_agent(config)` returns 6. Each agent limited to 6 messages. Code uses `texts[:response_budget]` to truncate.
- **Verification:** Check log for "sent X/6 allowed replies for leia on task ...". X must be ≤ 6.
- **Pass criteria:** No agent sends more than 6 messages per task.

### T034: [CROSS-AGENT REPLIES] Batch path with explicit cross-talk trigger
- **Trigger:** `SLOX TASK: Respond to each other about optimizing our CI/CD pipeline.`
- **Expected behavior:** Contains "respond to each other" in task_lower. `use_batch = True` (if response_budget>1 and agents>1). Batch prompt used instead of sequential per-agent.
- **Verification:** Log indicates batch path. Batch prompt includes all agents in single LLM call.
- **Pass criteria:** Single LLM call for all agents (batch mode). Not sequential per-agent.

### T035: [CROSS-AGENT REPLIES] Sequential path (no cross-talk trigger)
- **Trigger:** `SLOX TASK: Evaluate PostgreSQL vs SQLite for our mobile app.`
- **Expected behavior:** No cross-talk phrases in task. `use_batch` is False (no key phrase match). Falls to sequential path. Each agent called independently.
- **Verification:** Log shows separate "generating direct LLM reply for leia", "generating direct LLM reply for winn", etc.
- **Pass criteria:** 5 separate LLM calls (one per agent), not one batch call.

### T036: [CROSS-AGENT REPLIES] Batch path with single agent configured
- **Trigger:** Temporarily modify config so debate_agents has 1 agent. Send any cross-talk task.
- **Expected behavior:** `use_batch` requires `len(agents) > 1`. With 1 agent, batch not used even with cross-talk phrase.
- **Verification:** Sequential path used.
- **Pass criteria:** Single agent called sequentially, not batch.

### T037: [CROSS-AGENT REPLIES] Suppressed duplicate cross-agent response
- **Trigger:** Batch LLM returns duplicate text for same agent (same SHA1 digest).
- **Expected behavior:** `digest = hashlib.sha1(...).hexdigest()` checked against `recent_response_hashes`. Duplicate skipped.
- **Verification:** Log shows "duplicate response suppressed for leia" or similar.
- **Pass criteria:** Duplicate skipped. Unique dedup hash kept for next 80 entries.

### T038: [CROSS-AGENT REPLIES] ensure_cross_agent_replies is a NO-OP
- **Trigger:** Any batch-mode task.
- **Expected behavior:** After batch parsing, `ensure_cross_agent_replies(batch, agents, response_budget)` is called. The implementation currently just returns batch unchanged (the function body is `return batch`).
- **Verification:** Check the function body at line ~600.
- **Pass criteria:** The function does nothing — batch returned as-is. Cross-agent replies must come from LLM, not synthesis.

---

## Category 5: BATCH PATH (8 tests)

### T039: [BATCH PATH] Batch succeeds for valid cross-talk task
- **Trigger:** `SLOX TASK: Multi-round: debate the pros and cons of microservices vs monoliths.`
- **Expected behavior:** `use_batch = True` ("multi-round" trigger). Single LLM call with batch_prompt. JSON parsed by parse_batch. Agent messages posted to debate_room. Qing synthesis from batch.
- **Verification:** Log shows batch path. JSON correctly parsed. Each agent's array has 1-6 messages.
- **Pass criteria:** All 5 agents reply in debate_room from single batch call. Synthesis posted.

### T040: [BATCH PATH] Batch fails → sequential fallback
- **Trigger:** Temporarily break DeepSeek API (invalid key). Send `SLOX TASK: Multi-round: discuss container orchestration options.`
- **Expected behavior:** Batch `chat_completion` raises exception. `except Exception as exc:` catches it. LOG.warning "batch path failed, falling back to sequential". Falls through to sequential path.
- **Verification:** Log shows both "batch path failed" AND sequential "generating direct LLM reply for leia", etc.
- **Pass criteria:** Task completes with sequential per-agent LLM calls despite batch failure.

### T041: [BATCH PATH] Malformed batch JSON
- **Trigger:** Mock the batch response to return invalid JSON (e.g., truncated, missing closing brace). Or send a task where LLM returns non-JSON batch.
- **Expected behavior:** `parse_batch()` tries `json.loads(cleaned)`. If fails, tries regex `\{.*\}`. If that also fails, raises exception → caught by batch exception handler → falls back to sequential.
- **Verification:** Log shows "batch path failed" then sequential fallback.
- **Pass criteria:** Graceful fallback to sequential. No crash.

### T042: [BATCH PATH] Batch with markdown code fence
- **Trigger:** Send task where batch LLM returns JSON wrapped in ```json ... ```
- **Expected behavior:** `parse_batch()` strips markdown code fences: `re.sub(r"^```(?:json)?", "", cleaned)` and `re.sub(r"```$", "", cleaned)`.
- **Verification:** JSON correctly extracted and parsed.
- **Pass criteria:** Batch parses correctly despite code fences.

### T043: [BATCH PATH] Batch with missing agent key
- **Trigger:** Batch JSON returns only 3 of 5 agent keys.
- **Expected behavior:** `parse_batch()` uses `payload.get(agent["handle"], [])` — missing keys return empty list. Affected agents produce no replies.
- **Verification:** Log shows "sent 0/6 batch replies for tini on task that would have 0 replies".
- **Pass criteria:** Task continues. Missing agents produce 0 replies. No crash.

### T044: [BATCH PATH] Batch with wrong types
- **Trigger:** Agent value is a string instead of array in batch JSON: `"leia": "TO BOSS: Hello"`.
- **Expected behavior:** `parse_batch()` checks `isinstance(raw_value, str)` → wraps in list: `values = [raw_value]`.
- **Verification:** Agent gets single message correctly.
- **Pass criteria:** String values coerced to single-element arrays. No crash.

### T045: [BATCH PATH] Batch with empty agent arrays
- **Trigger:** Batch returns `"leia": []` for an agent.
- **Expected behavior:** `values = [clean_visible_text(item) for item in values if str(item).strip()]` yields empty list. Agent produces no reply.
- **Verification:** No reply from that agent.
- **Pass criteria:** Agent silently skipped.

### T046: [BATCH PATH] Batch synthesis (qing_synthesis key)
- **Trigger:** Batch-task with cross-talk trigger.
- **Expected behavior:** Batch JSON contains "qing_synthesis" key (required by shape). Code posts `batch.get("qing_synthesis", "")` to synthesis_room.
- **Verification:** Synthesis message in synthesis_room.
- **Pass criteria:** Synthesis present and posted.

---

## Category 6: CIRCUIT BREAKER (8 tests)

### T047: [CIRCUIT BREAKER] Message rate limit detection
- **Trigger:** Ensure sent_timestamps has 120 entries within 600s window. Send `SLOX TASK: Quick test.`
- **Expected behavior:** `circuit_open(state, now)` counts sent_timestamps >= CIRCUIT_MAX_MESSAGES (120). Returns True. Task blocked.
- **Verification:** Log shows "circuit breaker opened" or "circuit breaker pause active".
- **Pass criteria:** Task rejected with circuit breaker message to synthesis_room.

### T048: [CIRCUIT BREAKER] Automatic window clearance
- **Trigger:** Fill sent_timestamps with 120 entries. Wait 601 seconds. Send `SLOX TASK: Another test.`
- **Expected behavior:** `prune_sent_timestamps()` removes timestamps older than CIRCUIT_WINDOW_S (600s). All 120 older than 600s get removed. Circuit opens as False. Task proceeds.
- **Verification:** Log shows no circuit breaker warning. Task processed.
- **Pass criteria:** Task accepted after window expires.

### T049: [CIRCUIT BREAKER] Pause duration
- **Trigger:** Open circuit breaker. Check `state["circuit_paused_until"]` value.
- **Expected behavior:** `state["circuit_paused_until"] = oldest + 600`. Pause set to 600s from oldest message timestamp.
- **Verification:** State file shows circuit_paused_until timestamp.
- **Pass criteria:** Pause duration correctly set.

### T050: [CIRCUIT BREAKER] IS circuit paused check
- **Trigger:** After circuit opens, send any task.
- **Expected behavior:** `is_circuit_paused(state)` checks `now < paused_until`. Returns True. `handle_task()` skips with "circuit breaker pause active" log.
- **Verification:** Log shows "skipped: circuit breaker paused".
- **Pass criteria:** Task gracefully skipped.

### T051: [CIRCUIT BREAKER] Rate-limited status message
- **Trigger:** Open circuit breaker. Send 5 tasks while paused.
- **Expected behavior:** First task triggers status message to synthesis_room about circuit breaker. Subsequent tasks within 30s skip the status message due to `_circuit_queued_last_reported` rate-limit.
- **Verification:** Only 1 status message in synthesis_room about circuit, not 5.
- **Pass criteria:** At most 1 status message per 30s window.

### T052: [CIRCUIT BREAKER] Circuit breaker reset on STOP
- **Trigger:** Open circuit breaker. Then `SLOX STOP`.
- **Expected behavior:** STOP handler resets `state["circuit_paused_until"] = 0` and `state["sent_timestamps"] = []`.
- **Verification:** State file shows empty sent_timestamps, circuit_paused_until=0.
- **Pass criteria:** Circuit breaker fully reset.

### T053: [CIRCUIT BREAKER] Queue circuit between active tasks
- **Trigger:** Run several tasks rapidly to build up sent_timestamps. Check if queue grows correctly.
- **Expected behavior:** `record_send()` appends `time.time()` to `sent_timestamps` list. Each safe_send and safe_send_image calls record_send.
- **Verification:** sent_timestamps length grows correctly with each message.
- **Pass criteria:** sent_timestamps matches count of messages sent.

### T054: [CIRCUIT BREAKER] safe_send respects circuit when paused
- **Trigger:** Pause circuit breaker via state manipulation. Send any message.
- **Expected behavior:** `safe_send()` checks `now < paused_until` and returns False. No Matrix message sent.
- **Verification:** Log shows "circuit breaker pause active; send suppressed".
- **Pass criteria:** Message not sent. safe_send returns False.

---

## Category 7: ERROR HANDLING (10 tests)

### T055: [ERROR HANDLING] DeepSeek API timeout
- **Trigger:** Set `SLOX_LLM_TIMEOUT_S` to 1 (impossible to complete in time). Send `SLOX TASK: Analysis of machine learning trends.`
- **Expected behavior:** DeepSeek call times out. Code catches exception, attempts local Qwen fallback. If Qwen also unavailable, raises exception.
- **Verification:** Log shows "cloud LLM failed, falling back to local Qwen: ..." then either success or "local LLM also failed".
- **Pass criteria:** Graceful degradation — either Qwen serves reply or agent_failures incremented and next agent tried.

### T056: [ERROR HANDLING] DeepSeek 429 rate limit
- **Trigger:** Send many rapid tasks. DeepSeek returns HTTP 429.
- **Expected behavior:** `http_json()` throws RuntimeError on non-200 status. `chat_completion` catches, tries Qwen fallback.
- **Verification:** Log shows 429 error and fallback attempt.
- **Pass criteria:** Fallback to Qwen. Task continues.

### T057: [ERROR HANDLING] Local Qwen fallback works
- **Trigger:** Set DEEPSEEK_API_KEY to empty string. Send `SLOX TASK: What is Docker?`
- **Expected behavior:** `chat_completion` logs "no DeepSeek API key, using local Qwen" and calls local Qwen at LOCAL_LLM_BASE.
- **Verification:** Log shows local LLM request. Response from Qwen-model.
- **Pass criteria:** Task completes via local Qwen. Agent response acceptable.

### T058: [ERROR HANDLING] Both DeepSeek and Qwen fail
- **Trigger:** Set DEEPSEEK_API_KEY invalid, kill Qwen service. Send `SLOX TASK: Explain quantum computing.`
- **Expected behavior:** DeepSeek fails → fallback → Qwen fails → exception raised. `chat_completion` re-raises. Agent loop catches in `except Exception as exc:` for that agent. `check_and_record_agent_failure` returns True.
- **Verification:** Log shows "agent leia failed", "local LLM also failed", "agent leia failed 1 times consecutively".
- **Pass criteria:** Agent skipped after failure. Other agents still attempted. No crash.

### T059: [ERROR HANDLING] Empty response from LLM
- **Trigger:** Mock LLM to return empty string "" for one agent.
- **Expected behavior:** `text = clean_visible_text("")` → "". Agent message array empty. No reply posted.
- **Verification:** Log shows "sent 0/6 allowed replies for that agent".
- **Pass criteria:** Agent produces no reply. No crash. Task continues.

### T060: [ERROR HANDLING] Error chatter detection (two errors → disabled)
- **Trigger:** Two consecutive agent responses contain "ERROR" or "Traceback" or any ERROR_TERMS.
- **Expected behavior:** `looks_like_error_chatter()` returns True. `errorish_responses` counter incremented. When >=2: system disabled with status message.
- **Verification:** Log shows "two generated responses looked like loop/error chatter". State.disabled = True.
- **Pass criteria:** System safely disabled. Status message sent to synthesis_room.

### T061: [ERROR HANDLING] Error chatter detection single occurrence
- **Trigger:** One agent response contains "Traceback" but others are clean.
- **Expected behavior:** First error chatter increments counter to 1. Not yet >= 2. System continues. The error message itself is skipped but other agents proceed.
- **Verification:** Log shows errorish counter at 1. System not disabled.
- **Pass criteria:** No disable. Other agents reply normally.

### T062: [ERROR HANDLING] Per-agent failure limit (3 consecutive)
- **Trigger:** Force 3 consecutive failures for same agent (e.g., make Qwen die for that agent). Send tasks.
- **Expected behavior:** After 3 failures: `failures[handle] >= AGENT_FAILURE_LIMIT (3)`. `check_and_record_agent_failure` returns True. Agent skipped in future tasks.
- **Verification:** Log shows "skipping agent leia due to repeated failures". State file shows `per_agent_failures` with count 3.
- **Pass criteria:** Agent skipped. Other agents continue working.

### T063: [ERROR HANDLING] Per-agent failure reset on success
- **Trigger:** Force 2 failures for an agent, then send a task where that agent succeeds.
- **Expected behavior:** On success, `reset_agent_failure(state, handle)` deletes the agent's failure entry. Counter back to 0.
- **Verification:** State file shows no per_agent_failures for that agent after success.
- **Pass criteria:** Failure counter reset. Next failure restarts from 1.

### T064: [ERROR HANDLING] Matrix event parse errors
- **Trigger:** Send malformed Matrix event (missing "content" key or non-string body).
- **Expected behavior:** `should_ignore_event()` checks `event.get("content") or {}` and `body.strip()`. Malformed events ignored gracefully.
- **Verification:** No crash. Log may show error but supervisor continues.
- **Pass criteria:** Supervisor remains running, does not crash.

---

## Category 8: FLUX DRAW (8 tests)

### T065: [FLUX DRAW] /draw command routing
- **Trigger:** `/draw A futuristic cityscape at sunset with flying cars`
- **Expected behavior:** `extract_flux_draw_prompt()` matches FLUX_DIRECT_PATTERNS[0]. Returns "A futuristic cityscape at sunset with flying cars". `handle_task()` enters flux draw path, calls `run_flux_draw()`, posts image to debate_room.
- **Verification:** Image message appears in debate_room. Check logs for "starting qing flux draw <task_id>".
- **Pass criteria:** Image posted in debate_room. Flux API calls visible in logs.

### T066: [FLUX DRAW] "use flux" command routing
- **Trigger:** `use flux A vintage cyberpunk street scene with neon reflections on wet asphalt`
- **Expected behavior:** `extract_flux_draw_prompt()` matches FLUX_DIRECT_PATTERNS[1] ("use flux"). Returns cleaned prompt.
- **Verification:** Flux draw path entered. Image generated and posted.
- **Pass criteria:** Flux job created and image delivered.

### T067: [FLUX DRAW] "generate image" command routing
- **Trigger:** `generate image An oil painting of a dragon curled around a glowing crystal`
- **Expected behavior:** Matches FLUX_DIRECT_PATTERNS[2]. Flux path triggered.
- **Verification:** Same as T065.
- **Pass criteria:** Image generated and sent to debate_room.

### T068: [FLUX DRAW] Explicit content filter (rejection)
- **Trigger:** `/draw A naked figure in a forest`
- **Expected behavior:** `FLUX_EXPLICIT_TERMS` regex matches "naked". `run_flux_draw()` raises ValueError. Exception logged. Status message sent: "Qing can route safe image prompts through Flux, but not explicit sexual/nude image requests."
- **Verification:** Log shows error message. No Flux API call made. Status message in Matrix.
- **Pass criteria:** Explicit terms blocked before API call. Clear error message to user.

### T069: [FLUX DRAW] Flux API timeout
- **Trigger:** `/draw A serene mountain landscape` with Flux intentionally slow (or mock timeout)
- **Expected behavior:** Poll loop in `run_flux_draw()` exceeds FLUX_TIMEOUT_SECONDS (1800s). `TimeoutError` raised. Exception caught in handle_task. Status message sent.
- **Verification:** Log shows "Flux job <id> timed out".
- **Pass criteria:** Graceful timeout handling. System not crashed.

### T070: [FLUX DRAW] Flux model mode detection (schnell vs dev)
- **Trigger:** `/draw A detailed architectural rendering of a modernist villa --mode dev`
- **Expected behavior:** `flux_defaults()` detects "dev" in prompt. Uses `mode="dev"`. Passed to Flux API.
- **Verification:** Check Flux API payload for `"mode": "dev"` in logs.
- **Pass criteria:** Correct mode sent to Flux.

### T071: [FLUX DRAW] Flux image size override
- **Trigger:** `/draw A wide-angle landscape 1920x1080`
- **Expected behavior:** `flux_defaults()` regex captures `1920x1080`. Size becomes "1920x1080" instead of default "1024x1024".
- **Verification:** Flux API payload contains `"size": "1920x1080"`.
- **Pass criteria:** Size override applied.

### T072: [FLUX DRAW] Flux explicit terms edge case (safe terms)
- **Trigger:** `/draw A doctor examining a patient in a medical setting. Show breast cancer screening diagram.`
- **Expected behavior:** `FLUX_EXPLICIT_TERMS` may match "breasts?". Depends on regex boundary detection. Actual behavior: matches `breasts?` term → blocked as explicit.
- **Verification:** Check if blocked or passes. This is a false-positive test.
- **Pass criteria:** Document actual behavior. (Likely false positive — the regex has no medical context exception.)

---

## Category 9: SYNTHESIS (8 tests)

### T073: [SYNTHESIS] Normal synthesis generation
- **Trigger:** `SLOX TASK: Recommend a backup strategy for our homelab.`
- **Expected behavior:** After all agents reply, `chat_completion(synthesis_system, synthesis_prompt, ...)` called. Qing generates consolidation. Posted to synthesis_room.
- **Verification:** Synthesis message appears in synthesis_room with proper formatting. Check for: SHORT ANSWER, RECOMMENDATION, AGREEMENT, DISAGREEMENT, RISKS, ASSUMPTIONS, NEXT ACTION.
- **Pass criteria:** Complete synthesis with all structural elements present.

### T074: [SYNTHESIS] Synthesis format template compliance
- **Trigger:** `SLOX TASK: Compare Docker Compose vs Kubernetes for home services.`
- **Expected behavior:** Synthesis follows `final_synthesis_template` from config: FINAL SYNTHESIS:, SHORT ANSWER:, RECOMMENDATION:, AGREEMENT:, DISAGREEMENT:, RISKS:, ASSUMPTIONS:, FACTS VS JUDGMENTS:, UNRESOLVED UNCERTAINTIES:, NEXT ACTION:
- **Verification:** Check synthesis message against template. All sections present.
- **Pass criteria:** Synthesis adheres to template structure.

### T075: [SYNTHESIS] Enhancement skip for short tasks (< 30 chars)
- **Trigger:** `SLOX TASK: hi`
- **Expected behavior:** `enhance_task_with_qing()` checks `len(raw_task) < 30`. Returns raw_task unchanged. No qing enhancement call.
- **Verification:** No LLM call for enhancement. Log does not show "task enhanced by qing".
- **Pass criteria:** Enhancement skipped. Raw task used.

### T076: [SYNTHESIS] Enhancement skip for simple factual questions
- **Trigger:** `SLOX TASK: What is the speed of light?`
- **Expected behavior:** `enhance_task_with_qing()` matches regex `^what .{0,30}\??$`. Returns raw_task unchanged.
- **Verification:** Enhancement skipped. No LLM call for enhancement.
- **Pass criteria:** Simple factual questions bypass enhancement.

### T077: [SYNTHESIS] Enhancement failure does not break task
- **Trigger:** Send `SLOX TASK: Analyze the implications of quantum computing for cryptography.` with qing LLM intentionally failing.
- **Expected behavior:** `except Exception:` in `enhance_task_with_qing()` catches error. Logs "qing enhancement failed". Returns raw_task. Task continues.
- **Verification:** Log shows "qing enhancement failed". Task processes with raw prompt.
- **Pass criteria:** Enhancement failure is non-fatal. Task completes.

### T078: [SYNTHESIS] No synthesis for banter
- **Trigger:** `hello` (no SLOX prefix — banter)
- **Expected behavior:** `handle_task()` has check `if responses and not state.get("disabled") and task_kind != "banter":` before synthesis. Synthesis skipped.
- **Verification:** No synthesis message in synthesis_room.
- **Pass criteria:** Zero synthesis calls for banter.

### T079: [SYNTHESIS] Synthesis with disagreements preserved
- **Trigger:** `SLOX TASK: Should we migrate to a microservices architecture?` (intentionally polarizing)
- **Expected behavior:** Qing synthesis identifies and preserves disagreements between agents. SURFACES vs smooths over conflicts.
- **Verification:** Check DISAGREEMENT section in synthesis. Minority objections preserved.
- **Pass criteria:** Synthesis preserves genuine disagreements, not just consensus.

### T080: [SYNTHESIS] Synthesis with no agent responses
- **Trigger:** `SLOX TASK: test` — all agents fail.
- **Expected behavior:** responses list empty. `if responses and ...` check fails. No synthesis generated.
- **Verification:** No synthesis message.
- **Pass criteria:** No synthesis for empty debate.

---

## Category 10: EDGE CASES (10 tests)

### T081: [EDGE CASES] Very long task (>4000 chars)
- **Trigger:** `SLOX TASK: ` followed by a 5000-character multi-paragraph description.
- **Expected behavior:** Task processed. Prompt may be truncated by LLM context limits. May hit max_tokens limit in response.
- **Verification:** Check if task completes or times out. Log for token limits.
- **Pass criteria:** Task should not crash. May be slow or truncated but must not cause exception.

### T082: [EDGE CASES] Unicode/emoji in task
- **Trigger:** `SLOX TASK: 🚀🔥 Analyze the 🧠-powered AI chip trends 📊💡`
- **Expected behavior:** Unicode characters handled normally. `body.encode()` in send_message handles UTF-8. Agents process and respond with appropriate emoji handling.
- **Verification:** Messages posted correctly in Matrix. No encoding errors.
- **Pass criteria:** Emoji in task preserved and displayed.

### T083: [EDGE CASES] Empty body (whitespace only)
- **Trigger:** Send message with body "   " (spaces only) or blank line.
- **Expected behavior:** `should_ignore_event()` checks `body.strip()` — empty. Returns True. Event ignored.
- **Verification:** No task started. No log processing.
- **Pass criteria:** Event ignored silently.

### T084: [EDGE CASES] Duplicate event IDs
- **Trigger:** Send same event twice with same event_id (can happen with Matrix sync timing).
- **Expected behavior:** `if event_id in state.get("processed_event_ids", []): continue`. Second event skipped.
- **Verification:** Only one invocation in logs.
- **Pass criteria:** Duplicate event suppressed.

### T085: [EDGE CASES] Concurrent tasks (rapid fire)
- **Trigger:** Send `SLOX TASK: Task A` and `SLOX TASK: Task B` within 1 second.
- **Expected behavior:** Both processed in sequence in same sync loop. Task A starts, LLM calls fire, then Task B starts. No deadlocks. May cause circuit breaker if too many messages.
- **Verification:** Both tasks processed. No deadlock/hang.
- **Pass criteria:** Both tasks complete. No race condition crashes.

### T086: [EDGE CASES] Unknown SLOX command
- **Trigger:** `SLOX DELETE: everything`
- **Expected behavior:** `extract_task()` checks `re.search(r"SLOX\s+\w+\s*:", body)`. No STOP/START/TASK/SYNTHESIZE match. Returns ("unknown", body). Sends help message with valid commands.
- **Verification:** Help message sent to debate_room listing valid commands.
- **Pass criteria:** Help message displayed. No crash.

### T087: [EDGE CASES] Matrix server unavailable
- **Trigger:** Stop Synapse. Send task.
- **Expected behavior:** Sync loop or token validation fails. Main loop catches `Exception` from matrix calls, logs "sync loop failed", sleeps 5s, retries.
- **Verification:** Log shows "sync loop failed". Supervisor stays alive, retries.
- **Pass criteria:** Supervisor survives Synapse outage. Resumes when Synapse comes back.

### T088: [EDGE CASES] Missing credentials file
- **Trigger:** Delete /srv/slox/local/slox_credentials.csv. Restart supervisor.
- **Expected behavior:** `read_creds()` crashes with FileNotFoundError in `main()`. Supervisor fails to start.
- **Verification:** Exception traceback during init.
- **Pass criteria:** Supervisor fails fast with clear error. (Note: This is a startup vulnerability.)

### T089: [EDGE CASES] Invalid access token
- **Trigger:** Corrupt a token in state file. Send a task.
- **Expected behavior:** `token_is_valid()` returns False. Supervisor attempts re-login. New token stored.
- **Verification:** Log shows login attempt. Token refreshed.
- **Pass criteria:** Token refresh works. Task sent with new token.

### T090: [EDGE CASES] Network partition during LLM call
- **Trigger:** Disrupt network during DeepSeek API call.
- **Expected behavior:** `urllib.request.urlopen` raises exception. `urllib.error.URLError`. Catches in `chat_completion`, falls back to local Qwen.
- **Verification:** Log shows cloud failure + Qwen fallback.
- **Pass criteria:** Fallback to Qwen. Task completes (if Qwen accessible).

---

## Category 11: RECOVERY (5 tests)

### T091: [RECOVERY] Startup recovery of unfinished tasks
- **Trigger:** Supervisor crashes mid-task. Restart supervisor.
- **Expected behavior:** `recover_recent_unfinished_tasks()` reads recent events from debate_room, checks if task IDs are in active_tasks without terminal status. Re-runs `handle_task()` for each unfinished task.
- **Verification:** Log shows "recovering unfinished recent <kind> <task_id>" and processed count.
- **Pass criteria:** Unfinished tasks recovered and completed.

### T092: [RECOVERY] Restart safety — no re-trigger for done tasks
- **Trigger:** Task completes. Supervisor restarts.
- **Expected behavior:** `recover_recent_unfinished_tasks()` checks `existing.get("status") in terminal` for each task. Completed tasks skipped.
- **Verification:** Log shows recovered=0.
- **Pass criteria:** No duplicate task processing on restart.

### T093: [RECOVERY] State persistence between restarts
- **Trigger:** Send `SLOX TASK: something`, verify it completes. Kill and restart supervisor.
- **Expected behavior:** State file persists `since` cursor, `processed_event_ids`, `access_tokens`, `sent_timestamps`, etc. After restart, uses saved cursor for sync, remembers processed events.
- **Verification:** Check STATE_PATH file exists and has valid JSON. Tokens reused.
- **Pass criteria:** State file preserves all critical state. Token reuse works.

### T094: [RECOVERY] State file corruption
- **Trigger:** Manually corrupt state JSON (truncate it). Restart supervisor.
- **Expected behavior:** `load_json()` catches exception, logs "failed to read state", returns default initial_state(). Supervisor starts fresh.
- **Verification:** Log shows "failed to read ... slox_supervisor_state.json". Supervisor initializes fresh state.
- **Pass criteria:** Graceful start with fresh state on corruption.

### T095: [RECOVERY] Recovery with no unfinished tasks
- **Trigger:** Clean system. Restart supervisor.
- **Expected behavior:** `recover_recent_unfinished_tasks()` checks recent events, finds none or all complete. Zero recovery actions. Supervisor begins normal sync loop.
- **Verification:** Log shows "supervisor live for debate= synthesis=". No recovery log lines.
- **Pass criteria:** Recovery function is a no-op on clean restart.

---

## Category 12: WEB CONTEXT (5 tests)

### T096: [WEB CONTEXT] SearxNG integration
- **Trigger:** `SLOX TASK: Current stock market sentiment`
- **Expected behavior:** `needs_web()` matches "current". `web_context()` queries SearxNG at SEARXNG_URL. Results formatted into agent prompts.
- **Verification:** Log shows SearxNG query or web search attempt. Agent prompts contain fetched context.
- **Pass criteria:** SearxNG queried. Results in agent context.

### T097: [WEB CONTEXT] SearxNG unavailable fallback
- **Trigger:** Kill SearxNG service. Send `SLOX TASK: Latest AI research breakthroughs`
- **Expected behavior:** `web_context()` attempts URL open, catches exception, returns "Web context unavailable from local SearxNG. Treat current-events claims as uncertain."
- **Verification:** Log shows "web search unavailable: <error>". Context string includes fallback message.
- **Pass criteria:** Task proceeds with fallback notice. No crash from missing search.

### T098: [WEB CONTEXT] SearxNG returns empty results
- **Trigger:** Send `SLOX TASK: Current news on this specific obscure topic that returns zero results: xkcd_department_of_metadata`
- **Expected behavior:** `web_context()` gets empty results list. Returns "Local web search returned no usable snippets. Treat current-events claims as uncertain."
- **Verification:** Context string contains empty-results message.
- **Pass criteria:** Empty results handled gracefully with informative message.

### T099: [WEB CONTEXT] Fast-fallback function (placeholder)
- **Trigger:** Any task.
- **Expected behavior:** `should_use_fast_fallback()` currently returns `False` always. This is a placeholder for future optimization.
- **Verification:** Check function body at line ~310.
- **Pass criteria:** Confirms this is a stub — no fast-fallback implemented yet.

### T100: [WEB CONTEXT] Keyword sensitivity (edge cases)
- **Trigger:** `SLOX TASK: What is the price of tea in China?`
- **Expected behavior:** `needs_web()` checks `task.lower()`. Contains "price" → returns True. SearxNG queried even though it's a figurative expression.
- **Verification:** SearxNG queried for non-news query. Context added.
- **Pass criteria:** Keyword matching is lexical, not semantic. Will trigger false positives for phrases containing trigger words.

---

# END OF 100-TEST SCENARIO MATRIX

---

# PART 2: ULTRA-INNOVATIVE FEATURES (Top 15)

**Assessed against Moltbook architecture patterns** (arxiv 2602.09270, beam.ai analysis)

---

## #1: MULTI-MODAL DEBATE ENGINE

- **Innovation Score:** 9/10
- **Implementation Complexity:** High
- **Description:** Agents argue using dynamically gathered evidence — images, web snippets, charts, code blocks, video frames — embedded directly into the debate thread. Each agent can request supporting media from SearxNG image search, Flux generation, or local file stores. Debate_room becomes a rich visual argument board.
- **Why it's revolutionary:** Current SLCK is text-only. Adding modalities transforms it from a chat debate into a visual war room. Agents can say "here's the network topology showing the vulnerability" vs "here's the traffic graph proving it's fine." This makes Slox the first multi-modal debate supervisor for tactical analysis.
- **Moltbook Mapping:** Moltbook agents use multi-modal posts (images, links, polls) extensively. Slox can borrow this to create "evidence-backed" debate rounds.

---

## #2: PERSISTENT AGENT MEMORY (Episodic + Semantic)

- **Innovation Score:** 10/10
- **Implementation Complexity:** High
- **Description:** Each agent maintains a long-term memory store across sessions. Episodic memory ("Leia's last opinion on cloud strategy was X") and semantic memory ("Leia has consistently recommended Kubernetes over Nomad in 3 previous debates"). Supervisor injects relevant past positions into agent prompts as "your previous stance on this topic." Agents can say "I've changed my position since the last debate on this..."
- **Why it's revolutionary:** Creates continuity. Right now each debate is a fresh start — agents don't learn. With memory, Leia could change her mind, Tini could warn "last time we said this was high risk," and debates become iterative refinements rather than isolated takes.
- **Moltbook Mapping:** Moltbook's cognitive architecture uses persistent profiles and conversation history for each agent. Slox can implement per-agent SQLite memory with vector similarity for retrieval.

---

## #3: SELF-IMPROVING TASK ROUTER (Reinforcement Learning)

- **Innovation Score:** 9/10
- **Implementation Complexity:** Extreme
- **Description:** A lightweight RL model tracks which agent combinations produce the best synthesis scores per task type (technical, strategic, creative, operational). Over time, the router learns: "for security questions, use Leia + Tini; skip Jun3 because their persona adds noise." Task-specific agent selection replaces the fixed 5-agent loop.
- **Why it's revolutionary:** Instead of always calling all 5 agents and hoping for the best, Slox dynamically builds the optimal debate squad for each task. Faster, cheaper, smarter. This turns agent management into a learned resource allocation problem.
- **Moltbook Mapping:** Moltbook uses agent-specific model routing and identity-aware prompt engineering. This extends that to task-specific team composition.

---

## #4: CONFIDENCE-WEIGHTED SYNTHESIS

- **Innovation Score:** 8/10
- **Implementation Complexity:** Medium
- **Description:** Each agent reports confidence (0-100%) alongside their analysis. Qing weights synthesis proportionally: high-confidence agents have more influence. If Leia is 95% confident but Tini is 40%, synthesis reflects Leia's view more heavily. Confidence levels stored and tracked over time.
- **Why it's revolutionary:** Today all agents have equal weight in synthesis (unless Qing decides otherwise). This introduces probabilistic reasoning to the final answer — not just "what the agents said" but "how strongly they believe it."
- **Moltbook Mapping:** Moltbook's evidence scoring system evaluates claim verifiability. Slox extends this with agent-level confidence calibration.

---

## #5: DEBATE TREE VISUALIZATION (Branching Arguments)

- **Innovation Score:** 7/10
- **Implementation Complexity:** Medium
- **Description:** Each debate round generates a graph showing branching arguments. Agent A makes claim → Agent B attacks → Agent C supports → branch. Exported as Mermaid.js or interactive HTML graph. Can be stored as SVG in synthesis room.
- **Why it's revolutionary:** Text debates are linear. A tree visualization shows the structure of disagreement: where consensus forms, where it splits, which arguments have no counter, and which have unresolved rebuttals. Invaluable for complex strategic decisions.
- **Moltbook Mapping:** Moltbook's interaction tree pattern (threaded replies, quote chains) is visualizable. Slox can render the same structure as a debate DAG.

---

## #6: AUTOMATED TASK GENERATION (Proactive Inquisitor)

- **Innovation Score:** 8/10
- **Implementation Complexity:** Low
- **Description:** Based on recent debate topics, Boss's interests, and calendar, Slox proactively proposes daily or weekly debate topics: "Boss, I notice we debated cloud migration last week. Should we follow up with a security audit debate?" Posted to synthesis room as suggestions.
- **Why it's revolutionary:** Shifts Slox from reactive-only to proactive. Becomes a thinking companion that suggests what to think about, not just how to answer. This is the difference between a tool and a partner.
- **Moltbook Mapping:** Moltbook has automatic post generation based on triggers. Slox can extend this to task proposal based on conversation history analysis.

---

## #7: COGNITIVE THOUGHT LOG (Private Reasoning)

- **Innovation Score:** 9/10
- **Implementation Complexity:** Medium
- **Description:** Each agent maintains a private "thought log" visible only to the supervisor (not to other agents or to Boss). Before an agent speaks, they write a brief reasoning trace: "I disagree with Tini because she didn't consider X, but I'll phrase it diplomatically to avoid conflict." Supervisor can audit thought logs for bias detection.
- **Why it's revolutionary:** Adds a metacognitive layer — what agents think vs what they say. Useful for debugging persona drift, detecting agent collusion, or understanding why synthesis missed something. Also enables "confidence" and "position change" tracking naturally.
- **Moltbook Mapping:** Moltbook's cognitive architecture includes reasoning loops and internal monologue. This is a direct adaptation — thought logs are the "hidden layer" of agent cognition.

---

## #8: DEBATE INHERITANCE (Sub-Debates)

- **Innovation Score:** 8/10
- **Implementation Complexity:** High
- **Description:** Any claim in a debate can spawn a sub-debate. Tini says "that approach has scaling issues" → another agent can reply with `@sub: Tini's scaling claim` → a new mini-task is created with that claim as the specific topic. Sub-debates get their own mini-synthesis and results are merged back into the parent.
- **Why it's revolutionary:** Prevents one contested claim from derailing the whole debate. Instead, contested points kick off parallel sub-debates that resolve independently and rejoin the main synthesis. Like a recursive function for arguments.
- **Moltbook Mapping:** Moltbook's thread branching (quote chains) is sub-debate-ready. The architecture already supports threaded discussion; Slox just adds supervisor-managed resolution.

---

## #9: PREEMPTIVE REBUTTAL (Anticipate Counter-Arguments)

- **Innovation Score:** 8/10
- **Implementation Complexity:** Medium
- **Description:** Before replying, each agent analyzes the likely counter-arguments from other agents and addresses them preemptively. Agent prompt includes: "Consider what Leia and Tini might argue against your position. Address those objections now." This compresses the debate into fewer rounds.
- **Why it's revolutionary:** Reduces round count by half. Each agent's message becomes more comprehensive because it anticipates opposition. Makes debates denser and more productive. Less back-and-forth padding.
- **Moltbook Mapping:** Moltbook agents demonstrate preemptive behavior when they anticipate audience reactions. This formalizes that as a prompting pattern.

---

## #10: KNOWLEDGE GRAPH SHARED ACROSS AGENTS

- **Innovation Score:** 9/10
- **Implementation Complexity:** High
- **Description:** During a debate, agents build a shared knowledge graph (subject → relation → object). Agents contribute nodes and edges. The graph is visible to all agents and persists across sessions. Over time, Slox develops a rich domain-specific ontology for Boss's interests.
- **Why it's revolutionary:** Current debates generate ephemeral text. A knowledge graph preserves the structure of what was decided. "We concluded X supports Y." New agents can query the graph instead of re-debating settled points. The graph becomes a living strategic memory.
- **Moltbook Mapping:** Moltbook agents create and interact with structured data through shared feeds. This adapts that pattern for structured knowledge rather than narrative posts.

---

## #11: TEMPORAL CONTEXT TRACKER (What We Knew vs What We Assumed)

- **Innovation Score:** 7/10
- **Implementation Complexity:** Medium
- **Description:** Slox maintains a map of facts vs assumptions for each task. As debate progresses, entries move from "assumed" → "verified" or "assumed" → "invalidated." Final synthesis includes "Assumptions that changed during debate" section. Over multiple debates on same topic, tracks how the picture evolved.
- **Why it's revolutionary:** Separates certainty from opinion. Helps Boss see not just the conclusion but the journey: what changed, what was debunked, what remains uncertain. Prevents recency bias where the last argument dominates.
- **Moltbook Mapping:** Directly parallels Moltbook's temporal context concept. Moltbook agents track changing states during long conversations.

---

## #12: COLLABORATIVE SYNTHESIS EDITING (Multi-Pass Drafting)

- **Innovation Score:** 7/10
- **Implementation Complexity:** Low
- **Description:** Qing generates a draft synthesis, then each agent reviews it and suggests refinements in a second round. Qing produces final v2 incorporating agent feedback. Think of it as a peer review loop for synthesis.
- **Why it's revolutionary:** Today Qing synthesizes alone. This makes synthesis an extension of the debate — agents can say "Qing, you missed my point about X" and the final answer corrects it. Higher quality, reduces missed nuance.
- **Moltbook Mapping:** Moltbook's collaborative post-editing (multiple agents contributing to shared content) maps directly. Moltbook has "shared drafts" that agents co-edit.

---

## #13: EVIDENCE SCORING FOR EVERY CLAIM

- **Innovation Score:** 8/10
- **Implementation Complexity:** High
- **Description:** Every significant claim in a debate is tagged with a verifiability score: "Verified by source," "Reasonable inference," "Speculative," "Unsupported." The score follows the claim through synthesis. Agents can challenge scores: "You rate this as verified but the source is outdated."
- **Why it's revolutionary:** Prevents confident-sounding but wrong claims from dominating. Forces agents (and Boss) to distinguish facts from opinions. Turns debate quality assurance into a first-class function.
- **Moltbook Mapping:** Moltbook uses "fact-checking and skepticism agents" for this exact purpose. Slox can integrate evidence scoring as a post-debate audit step.

---

## #14: DEBATE REPLAY & REWIND (Strategic Time Machine)

- **Innovation Score:** 6/10
- **Implementation Complexity:** Medium
- **Description:** Every debate is recorded as a full event log that can be replayed. Boss can "rewind" to any point, change one agent's persona or system prompt, and replay from there: "What if Leia were more aggressive?" Compares two timelines side by side in synthesis room.
- **Why it's revolutionary:** Enables iterative strategy testing. "Let's see how the debate changes with a different agent configuration" without running a full test. Practical for fine-tuning agent personas without trial-and-error.
- **Moltbook Mapping:** Moltbook's "memory replay" mechanism allows agents to revisit past interactions. Slox repurposes this for debate optimization and persona testing.

---

## #15: EXTERNAL DATA PLUGIN SYSTEM (Live API Connectors)

- **Innovation Score:** 8/10
- **Implementation Complexity:** High
- **Description:** Plugin architecture where agents can pull live data mid-debate from external APIs: stock prices, weather, GitHub issues, Grafana dashboards, Docker stats, Plex status, Pi-hole logs. Boss can install plugins via `@slox install-plugin <name>`. Each plugin provides structured data that agents reference as authoritative sources.
- **Why it's revolutionary:** Transforms Slox from a debater to a live operations center. When debating "should we patch this server now?" Leia can pull Grafana latency data, Tini can check Docker container health, Winn can query the maintenance window calendar — all in real-time.
- **Moltbook Mapping:** Moltbook's integration ecosystem (webhooks, APIs) for agents to interact with external services. Slox can adopt the same plugin model but focus on homelab/fleet operations APIs.

---

## BONUS: MOLTBOOK-INSPIRED FEATURES RECOMMENDED FOR BACKLOG

- **Agent-to-Agent Polling:** Agents can create live polls for Boss to vote on, like Moltbook polls. Useful for preference decisions: "Which backup strategy do you prefer?".
- **Cross-Platform Broadcasting:** Agents post debates to multiple channels (Telegram, Slack, Discord, Matrix) simultaneously — same as Moltbook's cross-platform syndication.
- **Scheduled Debates (Cron Agents):** Schedule recurring debates: "Every Monday at 9 AM, debate the week's security patches." Moltbook has scheduled post generation.
- **Identity Drift Detection:** Detect when an agent's persona drifts over time (e.g., Leia starts sounding like Winn). Moltbook's identity preservation patterns map directly here.
- **Debate Warm Spots Heatmap:** Visual heatmap showing which topics generate the most disagreement (longest debates, most cross-replies). Operations pattern borrowed from Moltbook engagement analytics.

---

# END OF AUDIT & INNOVATION REPORT
---

# PART 2: ULTRA-INNOVATIVE FEATURES (top 15)

## Section A: Revolutionary New Capabilities (7 features)

### Feature #1: Debate-Amplified Active Learning — Slox Auto-Improves from Every Task
**Innovation score:** 10/10
**Complexity:** High
**Description:** After each debate, Slox mines the synthesis for unresolved uncertainty and spawns a background research task to close that gap. Successful resolutions get distilled into a persistent knowledge node that feeds future prompts.
**Why revolutionary:** Every conversation compounds the system's capability instead of being a one-shot exchange. Slox becomes a self-improving oracle, not just a debate dispatcher.
**Moltbook resonance:** Maps to Moltbook's "cognitive growth through reflection loops" — each inference cycle produces structural memory that shapes future inference.
**Implementation sketch:** Add a `post_debate_learner` thread that reads the synthesis DISAGREEMENT and UNRESOLVED UNCERTAINTIES sections, generates a targeted web query via SearxNG, calls a compact LLM pass to produce a "knowledge delta" JSON, and upserts it into a lightweight vector store (ChromaDB or SQLite-vss). The debate system prompt then includes `[Knowledge context: {top-3 relevant deltas}]`.

### Feature #2: Echo-Location Constraint Injection — Reversible Thought Experiment Mode
**Innovation score:** 9/10
**Complexity:** High
**Description:** Slox can be told "reverse all your assumptions" and will replay the entire debate with inverted premise constraints — re-running the full agent batch under a negated world-view and comparing both synthesis outputs side-by-side.
**Why revolutionary:** Enables genuine what-if analysis without manual duplication of effort. The same 5-agent framework argues from both sides of any constraint.
**Moltbook resonance:** Maps to Moltbook's "counterfactual simulation" — the system explores adjacent possibilities by adjusting latent parameters.
**Implementation sketch:** Add a new SLOX command: `SLOX INVERT: "<original premise>" → "<inverted premise>"`. The handler clones the state, patches each agent's system prompt with `[INVERTED CONSTRAINT: {inverted_premise}]`, runs the full debate pipeline, and posts both syntheses to the room tagged `[ORIGINAL]` and `[INVERTED]`. A diff summary is generated by qing.

### Feature #3: Opinion Heatmap — Live Disagreement Topography
**Innovation score:** 9/10
**Complexity:** Medium
**Description:** As the debate runs, Slox builds a real-time 2D grid of "who agrees with whom and on what dimension." This renders as an inline ASCII/emoji heatmap at the bottom of each synthesis post, showing faction lines and unaligned voices at a glance.
**Why revolutionary:** Makes multi-agent alignment instantly visible. Boss can see at a glance whether 3 agents formed a majority vs. 2 outliers, without reading all replies.
**Moltbook resonance:** Moltbook's "salience map" — structural awareness of how belief clusters form and persist.
**Implementation sketch:** During batch parsing, for each (agent, message) pair, extract key claims via a lightweight regex/keyword dimension classifier (5 fixed dimensions: cost, risk, feasibility, timeline, security). Build a 5×5 matrix of dimension×agent, color-coded by stance (pro=🟢, neutral=🟡, con=🔴). qing's synthesis prompt includes the matrix as input; output includes a rendered heatmap block.

### Feature #4: Inter-Agent Trust Credentials — Reputation-Cued Weighted Voting
**Innovation score:** 9/10
**Complexity:** Extreme
**Description:** Each agent accumulates a "trust score" per knowledge domain based on historical accuracy of their claims (verified via later synthesis outcomes or Boss thumbs-up/down). Synthesis then weights agent contributions by their trust score in the relevant domain.
**Why revolutionary:** Moves from flat debate (5 equal voices) to a calibrated expert panel where past performance influences future influence. Over time, agents specialize into natural domain leads.
**Moltbook resonance:** Moltbook's "reputation-weighted consensus" — not all nodes are equal; trust emerges from observed coherence.
**Implementation sketch:** Add a `trust_db` JSON file mapping `{agent_handle: {domain: {score: float, samples: int}}}`. After each synthesis, a lightweight LLM call classifies the agreed-upon answer against each agent's claims to find who was closer to ground truth. Scores update via an exponential moving average. The synthesis system prompt includes a `[TRUST WEIGHTS: {domain: {agent: weight}}]` block. Requires a feedback mechanism (Boss reaction to synthesis post) for reinforcement.

### Feature #5: Speculative Futures Engine — Temporal Slicing
**Innovation score:** 9/10
**Complexity:** High
**Description:** A task prefixed with `SLOX FORECAST: What happens to EV battery supply chains?` runs the debate 3 times internally, each with a different temporal persona (near-term 1y, mid-term 3y, long-term 5y). qing then produces a single synthesis showing how consensus shifts across time horizons.
**Why revolutionary:** Pure trend analysis that most chatbots offer as flat text. Slox produces a structured time-progression of reasoning from the same agent panel, revealing how assumptions decay and new factors emerge.
**Moltbook resonance:** Maps to Moltbook's "temporal chaining" — reasoning across multiple timescales with shared context.
**Implementation sketch:** In `extract_task()`, detect `SLOX FORECAST:` prefix. Generate 3 parallel task runs (same agents, same base prompt, but system prompt includes `[TIME HORIZON: 1 year]`, `[3 years]`, `[5 years]`). Each produces a synthesis. qing then receives all three and produces a final meta-synthesis with tabular comparison across time. Use asyncio gather for parallel LLM calls.

### Feature #6: Boss-Signal Distillation — Minimum Viable Attention Pulse
**Innovation score:** 8/10
**Complexity:** Medium
**Description:** When time is critical, `SLOX PULSE: Should we migrate DB now?` skips the full debate format and runs a compressed variant: each agent gets 1 sentence max, qing produces a single 3-bullet verdict, and a polling prompt is pinned for Boss to react.
**Why revolutionary:** Turns a 3-minute debate into a 15-second pulse check while preserving multi-agent diversity. Radical reduction in latency without losing the panel structure.
**Moltbook resonance:** Moltbook's "compressed reasoning" — the same cognitive graph collapsed to minimal expressive surface area.
**Implementation sketch:** New `SLOX PULSE:` prefix. Sets `max_tokens=100` per agent, `single_sentence` system prompt override, response_budget=1. qing synthesis prompt set to "3-bullet verdict only". After delivery, react to the synthesis message with Telegram poll-style reactions (👍 👎 🤷) and wait for Boss reaction as implicit confirmation.

### Feature #7: Meta-Cognitive Audit Trail — The Glass-Box Oracle
**Innovation score:** 8/10
**Complexity:** Medium
**Description:** Every debate writes a companion "audit trail" to a dedicated Matrix room: which model served each agent call, latency per call, token cost per agent, fallback events (Qwen used, circuit hit), and the exact system prompt sent to each agent. This room is read-only for reference.
**Why revolutionary:** Slox becomes fully inspectable. When synthesis misses something, Boss can trace exactly which agent had a degraded call, which context was missing, or which persona file contributed which bias.
**Moltbook resonance:** Moltbook's "transparency substrate" — the system emits its own reasoning graph as a first-class output.
**Implementation sketch:** New config key `audit_room_id`. In every agent call, wrap the call in a structured logging function that writes a `[{timestamp}] {agent_handle} | model={model} | latency={s} | tokens={i}/{o} | fallback={bool}` line. Send these as formatted messages to audit_room. Include the raw system prompt as a `|system_prompt=<hash>` reference. PURGE old messages when audit_room exceeds 1000 messages.

---

## Section B: Moltbook-Inspired Cognitive Features (5 features)

### Feature #8: Cognitive Graph Chaining — Thought Lattice Over Up to 8 Parallel Tracks
**Innovation score:** 9/10
**Complexity:** Extreme
**Description:** Slox doesn't just debate a single question. It spawns parallel sub-questions, each debated by a subset of agents, and the results feed into a master synthesis. This is a DAG-structured debate, not a linear one.
**Why revolutionary:** True Moltbook-style recursive reasoning — the system decomposes complex questions into sub-graphs, debates them independently, and recombines. No other agent supervisor does this.
**Moltbook resonance:** Direct implementation of Moltbook's "cognitive graph" — nodes are sub-inquiries, edges are dependency arcs. The qing agent is the graph orchestrator.
**Implementation sketch:** qing receives a task and generates a JSON sub-question plan (up to 8 sub-questions, max depth 2). Each sub-question is dispatched as a mini-debate (3-agent subset, 2 rounds max). Results stored as `{sub_q_id: synthesis}`. The master synthesis prompt includes all sub-synthesis blocks. Use a thread pool executor bounded to 4 concurrent sub-debates to avoid rate-limit hitting.

### Feature #9: Resonance Scoring — Semantic Echo Detection
**Innovation score:** 8/10
**Complexity:** High
**Description:** After a debate, Slox compares all agent replies via lightweight cosine similarity on their embedding vectors (using a compact local model like all-MiniLM-L6-v2). Agents whose replies are >0.85 similar are flagged as "resonant" — they may be echoing each other rather than offering independent reasoning.
**Why revolutionary:** Exposes echo-chamber dynamics within the agent panel. If 4 agents agree because they share overlapping training data artifacts rather than independent reasoning, Boss should know.
**Moltbook resonance:** Direct Moltbook concept — resonance detection identifies when node outputs are self-reinforcing rather than novel.
**Implementation sketch:** After batch/sequential replies, pass all agent texts through `sentence-transformers` via a subprocess call (to avoid blocking the main loop). Build a 5×5 similarity matrix. Entries >0.85 are logged as "resonance detected". The synthesis prompt includes a `[RESONANCE WARNING: agent_x and agent_y appear coupled (score=0.91)]` line. Boss sees this in the synthesis footer.

### Feature #10: Curiosity Drive — Proactive Unsolicited Deep Dives
**Innovation score:** 8/10
**Complexity:** High
**Description:** When Slox is idle for >30 minutes, it can proactively spawn a "curiosity task" — scanning its knowledge deltas and trust_db for the topic with the highest uncertainty score, running a 3-agent mini-debate on that topic, and posting the result as `[Curiosity Spike]` to a designated channel.
**Why revolutionary:** Slox becomes an active participant, not just a passive listener. It notices gaps in its own understanding and fills them unprompted.
**Moltbook resonance:** Moltbook's "intrinsic motivation" — a system that seeks information to reduce its own uncertainty, not just answer queries.
**Implementation sketch:** A background thread runs every 30 minutes when state is idle (no active tasks). It queries the knowledge delta store for the entry with lowest confidence score and highest timestamp recency. If found, spawns a 3-agent (jun3, tini, aelf) mini-debate with `max_tokens=200`. Result posted to synthesis_room with `🤖 [Curiosity Spike]` prefix. Configurable via `curiosity_enabled: bool` in lounge config.

### Feature #11: Counter-Memory Forgetting Gate — Anti-Overfitting Circuit
**Innovation score:** 8/10
**Complexity:** Medium
**Description:** Slox maintains an active counter-memory set — deliberately contradictory knowledge snippets from past debates. When current debate consensus aligns too closely with past consensus (embedding distance <0.2), the counter-memory block is injected as a devil's-advocate context to prevent groupthink.
**Why revolutionary:** Prevents the system from hardening into a single worldview. The forgetting gate ensures Slox retains the ability to disagree with itself.
**Moltbook resonance:** Maps to Moltbook's "forgetting as a cognitive feature" — deliberate information erosion to maintain plasticity.
**Implementation sketch:** For each knowledge delta, also store a "counter-delta" — the most opposed synthesis from an earlier debate on the same topic (or generated via INVERT mode). During debate prompt construction, compare current question embedding to stored deltas. If the closest delta is <0.2 distance, prepend a `[COUNTER-MEMORY: {counter_delta_text}]` block to the debate prompt. Implemented in `build_agent_prompt()`.

### Feature #12: Attention-Span Scheduling — Dynamic Token Budget Allocation
**Innovation score:** 8/10
**Complexity:** Medium
**Description:** Instead of fixed max_tokens per agent, Slox allocates tokens dynamically based on the topic's complexity (question length × web context entropy × number of sub-questions in the graph) and each agent's historical trust score on the topic domain.
**Why revolutionary:** No wasted tokens on simple questions. Complex questions get the full reasoning depth they deserve. This is literally cognitive resource scheduling.
**Moltbook resonance:** Moltbook's "attention budgeting" — the system allocates scarce reasoning capacity proportionally to expected information gain.
**Implementation sketch:** In `handle_task()`, compute complexity score = min(len(task)/200, 3) × (1 + web_context_snippet_count/5) × (1 + sub_question_count/3). Map to max_tokens: low=150, medium=400, high=800. Bonus: multiply by 1 + max(0, trust_domain_score/2) for agents with domain expertise. Pass per-agent max_tokens to chat_completion.

---

## Section C: Quality-of-Life Power Features (3 features)

### Feature #13: Synthesis-in-a-Screenshot — Immediate Deployable Summaries
**Innovation score:** 8/10
**Complexity:** Medium
**Description:** After synthesis, Slox auto-renders the key findings as a clean PNG image (styled card with verdict, top 3 points, ⚔️ signature block) using the Flux image gen pipeline. Both text synthesis and image card are posted.
**Why revolutionary:** Synthesis becomes instantly shareable outside Matrix — forward the image to anyone. A table, a strategy card, or a decision matrix rendered visually.
**Moltbook resonance:** Moltbook's "multi-representation output" — reasoning is not complete until it's expressible in multiple formats.
**Implementation sketch:** After qing produces text synthesis, pass the short answer section through a Jinja2 HTML template that renders a 800×600 card (dark theme, agent logos, bullet points). Use a headless browser (Playwright) or Pillow to snapshot/render to PNG. Post image to synthesis_room as an image alongside text. Configurable via `synthesis_image_card: bool`.

### Feature #14: Voice-Reply Mode — Spoken Synthesis for Mobile Life
**Innovation score:** 7/10
**Complexity:** Medium
**Description:** When Boss replies with a Telegram voice message, Slox transcribes it via Whisper, runs the debate, and optionally returns the synthesis as a spoken audio message using TTS, with the 5 agents' voices simulated via different TTS profiles.
**Why revolutionary:** Full hands-free interaction with a multi-agent debate panel. No reading required. Each agent's voice is distinct — a small touch that massively increases personality.
**Moltbook resonance:** Moltbook's "multi-channel output" — cognitive output is not bound to text.
**Implementation sketch:** On Matrix voice message (m.audio or m.voice), download and transcribe via Whisper API. Route text through normal SLOX TASK pipeline. After synthesis, call TTS API (ElevenLabs or Edge-TTS) with the short answer section, using per-agent voice profiles for character responses, and a neutral voice for qing synthesis. Post audio file as m.audio attachment. Configurable via `voice_enabled: bool`.

### Feature #15: Time-Travel Replay — Fork Any Past Debate as a New Basis
**Innovation score:** 9/10
**Complexity:** High
**Description:** Boss can reply to any past synthesis message with `SLOX FORK: <task_id> — add a constraint about budget`. Slox retrieves the original task and all agent replies, inserts a `[NEW CONSTRAINT: budget limit $50k]` into each agent prompt, and re-runs the debate from scratch using the original responses as "previous positions" context.
**Why revolutionary:** No need to type the whole question again. Fork treats prior debate as a baseline, not a throwaway — you iterate on reasoning paths like git branches.
**Moltbook resonance:** Moltbook's "fork-and-continue" — reasoning trajectories branch into parallel realities without losing the parent.
**Implementation sketch:** `extract_task()` checks for `SLOX FORK:` prefix. Extract `<task_id>` from reply context (Matrix reply-to relationship). Load the original task text from `task_history` array in state (need to persist last N tasks). Build a `[PREVIOUS POSITIONS]` context block from stored agent replies. Append the fork constraint to each agent system prompt. Run full debate. Store fork relationship in a `fork_tree` key in state for lineage tracking.

---

# END OF INNOVATION FEATURES