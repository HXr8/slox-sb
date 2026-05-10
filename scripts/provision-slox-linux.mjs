import { execFileSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const config = JSON.parse(fs.readFileSync(path.join(root, "config", "two_room_lounge.json"), "utf8"));
const localDir = path.join(root, "local");
const roomsPath = path.join(localDir, "slox_rooms.json");
const credsPath = path.join(localDir, "slox_credentials.csv");
const reportPath = path.join(localDir, "slox_verify_report.json");
const baseUrl = process.env.SLOX_HOMESERVER || "http://127.0.0.1:8008";
const masterPassword = process.env.MATRIX_MASTER_PASSWORD || "changeme123";

fs.mkdirSync(localDir, { recursive: true });

function shQuote(value) {
  return `'${String(value).replace(/'/g, `'\\''`)}'`;
}

function dockerExec(args) {
  return execFileSync("podman", ["exec", "slox-synapse", ...args], {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  });
}

async function request(method, requestPath, body, token, ok = [200]) {
  const headers = { "content-type": "application/json" };
  if (token) headers.authorization = `Bearer ${token}`;
  const response = await fetch(`${baseUrl}${requestPath}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  let payload = {};
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { body: text };
    }
  }
  if (!ok.includes(response.status)) {
    const err = new Error(`${method} ${requestPath} failed: ${response.status} ${JSON.stringify(payload)}`);
    err.status = response.status;
    err.payload = payload;
    throw err;
  }
  return payload;
}

function readCsv(file) {
  if (!fs.existsSync(file)) return new Map();
  const lines = fs.readFileSync(file, "utf8").trim().split(/\r?\n/);
  if (lines.length < 2) return new Map();
  const headers = lines[0].split(",");
  const rows = new Map();
  for (const line of lines.slice(1)) {
    const cells = line.match(/("([^"]|"")*"|[^,]*)/g).filter((part, i) => i % 2 === 0).slice(0, headers.length);
    const row = Object.fromEntries(headers.map((h, i) => [h, (cells[i] || "").replace(/^"|"$/g, "").replace(/""/g, '"')]));
    rows.set(row.handle, row);
  }
  return rows;
}

function csvCell(value) {
  const s = String(value ?? "");
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function writeCsv(file, rows) {
  const headers = ["handle", "matrix_user_id", "password", "admin", "role_name", "room_access"];
  const body = [headers.join(","), ...rows.map((row) => headers.map((h) => csvCell(row[h])).join(","))].join("\n");
  fs.writeFileSync(file, body + "\n", { mode: 0o600 });
}

function passwordFor(handle, existing) {
  if (handle === "master") return masterPassword;
  const prior = existing.get(handle)?.password;
  return prior || crypto.randomBytes(24).toString("base64url");
}

function register(handle, password, admin) {
  dockerExec([
    "register_new_matrix_user",
    "-c",
    "/data/homeserver.yaml",
    "http://localhost:8008",
    "-u",
    handle,
    `--password=${password}`,
    admin ? "--admin" : "--no-admin",
    "--exists-ok",
  ]);
}

async function waitForHomeserver() {
  for (let attempt = 1; attempt <= 90; attempt += 1) {
    try {
      await request("GET", "/_matrix/client/versions", undefined, undefined, [200]);
      return;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 2000));
    }
  }
  throw new Error(`Homeserver did not become ready at ${baseUrl}`);
}

async function login(user, password, device) {
  return request("POST", "/_matrix/client/v3/login", {
    type: "m.login.password",
    identifier: { type: "m.id.user", user },
    password,
    initial_device_display_name: device,
  });
}

async function adminLoginAs(adminToken, userId) {
  const encoded = encodeURIComponent(userId);
  return request("POST", `/_synapse/admin/v1/users/${encoded}/login`, {
    valid_until_ms: Date.now() + 10 * 60 * 1000,
  }, adminToken);
}

async function setDisplayName(adminToken, userId, displayname) {
  await request("PUT", `/_synapse/admin/v2/users/${encodeURIComponent(userId)}`, { displayname }, adminToken, [200]);
}

async function putState(token, roomId, type, content, stateKey = "") {
  return request(
    "PUT",
    `/_matrix/client/v3/rooms/${encodeURIComponent(roomId)}/state/${encodeURIComponent(type)}/${encodeURIComponent(stateKey)}`,
    content,
    token,
  );
}

async function putOptionalState(token, roomId, type, content, stateKey = "") {
  try {
    await putState(token, roomId, type, content, stateKey);
    return "updated";
  } catch (err) {
    console.warn(`[WARN] skipped optional state ${type}: ${err.message}`);
    return `skipped_${err.status || "error"}`;
  }
}

async function getState(token, roomId, type, stateKey = "") {
  try {
    return await request(
      "GET",
      `/_matrix/client/v3/rooms/${encodeURIComponent(roomId)}/state/${encodeURIComponent(type)}/${encodeURIComponent(stateKey)}`,
      undefined,
      token,
    );
  } catch (err) {
    if (err.status === 404) return {};
    throw err;
  }
}

async function createOrResolveRoom(ownerToken, roomKey) {
  const room = config.rooms[roomKey];
  try {
    const created = await request("POST", "/_matrix/client/v3/createRoom", {
      room_alias_name: room.alias,
      name: room.name,
      topic: room.purpose,
      preset: "private_chat",
      visibility: "private",
      invite: [],
      initial_state: [
        { type: "m.room.join_rules", state_key: "", content: { join_rule: "invite" } },
        { type: "m.room.guest_access", state_key: "", content: { guest_access: "forbidden" } },
        { type: "m.room.history_visibility", state_key: "", content: { history_visibility: "shared" } },
      ],
    }, ownerToken);
    return { room_id: created.room_id, status: "created" };
  } catch (err) {
    if (!String(err.message).includes("M_ROOM_IN_USE")) throw err;
    const resolved = await request("GET", `/_matrix/client/v3/directory/room/${encodeURIComponent(room.canonical_alias)}`, undefined, ownerToken);
    return { room_id: resolved.room_id, status: "exists" };
  }
}

function desiredPower(existing, roomKey) {
  const users = { [config.owner_user_id]: 100 };
  if (roomKey === "debate") {
    for (const agent of config.debate_agents) users[agent.matrix_user_id] = 0;
    users[config.synthesis_agent.matrix_user_id] = -1;
  } else {
    users[config.synthesis_agent.matrix_user_id] = 0;
  }
  return {
    ...existing,
    users_default: 0,
    events_default: 0,
    state_default: 100,
    invite: 100,
    kick: 100,
    ban: 100,
    redact: 100,
    notifications: { room: 100 },
    users,
    events: {
      ...(existing.events || {}),
      "m.room.name": 100,
      "m.room.topic": 100,
      "m.room.avatar": 100,
      "m.room.canonical_alias": 100,
      "m.room.power_levels": 100,
      "m.room.history_visibility": 100,
      "m.room.guest_access": 100,
      "m.room.join_rules": 100,
      "m.room.pinned_events": 100,
      "com.ai_lounge.guardrails": 100,
      "com.slox.safety_policy": 100,
      "com.ai_lounge.agent_roster": 100,
      "m.room.message": 0,
    },
  };
}

function guardrails(roomKey) {
  return {
    project: "Slox Two-Room Private AI Agent Lounge",
    room_key: roomKey,
    owner_user_id: config.owner_user_id,
    agent_count: config.agent_count,
    max_active_debate_agents_per_task: config.max_active_debate_agents_per_task,
    no_bridges: true,
    no_public_directory: true,
    no_agent_room_state_changes: true,
    debate_round_limit_per_agent: 3,
    safety_policy: config.safety || {},
    debate_prompt: fs.readFileSync(path.join(root, "config", "two_room_lounge_debate_prompt.txt"), "utf8"),
    synthesis_prompt: fs.readFileSync(path.join(root, "config", "two_room_lounge_synthesis_prompt.txt"), "utf8"),
    task_template: config.task_template,
    debate_reply_template: config.debate_reply_template,
    final_synthesis_template: config.final_synthesis_template,
    enforced_at: Math.floor(Date.now() / 1000),
  };
}

function roster() {
  return {
    synthesis_agent: config.synthesis_agent,
    debate_agents: config.debate_agents,
    agent_count: config.agent_count,
    priority_invite_agents: config.priority_invite_agents,
    max_active_debate_agents_per_task: config.max_active_debate_agents_per_task,
  };
}

function sortedAgentsFor(roomKey) {
  const agents = roomKey === "debate" ? [...config.debate_agents, config.synthesis_agent] : [config.synthesis_agent];
  const priority = new Map(config.priority_invite_agents.map((handle, index) => [handle, index]));
  return agents.sort((a, b) => (priority.get(a.handle) ?? 999) - (priority.get(b.handle) ?? 999));
}

async function forceJoin(adminToken, roomId, userId) {
  await request("POST", `/_synapse/admin/v1/join/${encodeURIComponent(roomId)}`, { user_id: userId }, adminToken, [200]);
}

async function members(token, roomId) {
  const payload = await request("GET", `/_matrix/client/v3/rooms/${encodeURIComponent(roomId)}/members`, undefined, token);
  const out = new Map();
  for (const event of payload.chunk || []) {
    const membership = event.content?.membership;
    if (["join", "invite"].includes(membership)) out.set(event.state_key, membership);
  }
  return out;
}

async function configureRoom(ownerToken, adminToken, roomKey) {
  const room = config.rooms[roomKey];
  const { room_id, status } = await createOrResolveRoom(ownerToken, roomKey);
  await putState(ownerToken, room_id, "m.room.name", { name: room.name });
  await putState(ownerToken, room_id, "m.room.topic", { topic: room.purpose });
  await putState(ownerToken, room_id, "m.room.join_rules", { join_rule: "invite" });
  await putState(ownerToken, room_id, "m.room.guest_access", { guest_access: "forbidden" });
  await putState(ownerToken, room_id, "m.room.history_visibility", { history_visibility: "shared" });
  await putOptionalState(ownerToken, room_id, "com.slox.safety_policy", guardrails(roomKey));
  await putOptionalState(ownerToken, room_id, "com.ai_lounge.agent_roster", roster());
  const existingPower = await getState(ownerToken, room_id, "m.room.power_levels");
  await putState(ownerToken, room_id, "m.room.power_levels", desiredPower(existingPower, roomKey));

  const joined = {};
  for (const agent of sortedAgentsFor(roomKey)) {
    await forceJoin(adminToken, room_id, agent.matrix_user_id);
    joined[agent.handle] = "force_joined";
  }

  const allowed = new Set([config.owner_user_id, ...sortedAgentsFor(roomKey).map((agent) => agent.matrix_user_id)]);
  const current = await members(ownerToken, room_id);
  const extras = [...current.keys()].filter((userId) => !allowed.has(userId));
  for (const userId of extras) {
    await request("POST", `/_matrix/client/v3/rooms/${encodeURIComponent(room_id)}/kick`, {
      user_id: userId,
      reason: "Not in the Slox two-room access list.",
    }, ownerToken, [200]);
  }
  return {
    room_key: roomKey,
    alias: room.canonical_alias,
    room_id,
    status,
    allowed_user_ids: [...allowed].sort(),
    joins: joined,
    removed: extras,
  };
}

async function trySend(token, roomId, body) {
  try {
    await request("PUT", `/_matrix/client/v3/rooms/${encodeURIComponent(roomId)}/send/m.room.message/${Date.now()}`, {
      msgtype: "m.text",
      body,
    }, token, [200]);
    return "sent";
  } catch (err) {
    return `blocked_${err.status || "error"}`;
  }
}

function check(report, name, ok, detail) {
  report.checks.push({ name, ok: Boolean(ok), detail });
  console.log(`[${ok ? "PASS" : "FAIL"}] ${name}: ${detail}`);
}

async function main() {
  await waitForHomeserver();
  const existing = readCsv(credsPath);
  const allAgents = [...config.debate_agents, config.synthesis_agent];
  const rows = [
    {
      handle: "master",
      matrix_user_id: config.owner_user_id,
      password: masterPassword,
      admin: "true",
      role_name: "Human Owner / Sole Admin",
      room_access: "debate,synthesis",
    },
  ];

  register("master", masterPassword, true);
  for (const agent of allAgents) {
    const password = passwordFor(agent.handle, existing);
    register(agent.handle, password, false);
    rows.push({
      handle: agent.handle,
      matrix_user_id: agent.matrix_user_id,
      password,
      admin: "false",
      role_name: agent.role_name,
      room_access: agent.handle === config.synthesis_agent.handle ? "debate-readonly,synthesis-readwrite" : "debate-readwrite",
    });
  }
  writeCsv(credsPath, rows);

  const master = await login("master", masterPassword, "slox-provisioner");
  const adminToken = master.access_token;
  await setDisplayName(adminToken, config.owner_user_id, config.owner_display_name);
  for (const agent of allAgents) {
    await setDisplayName(adminToken, agent.matrix_user_id, agent.display_name);
  }

  const rooms = [
    await configureRoom(adminToken, adminToken, "debate"),
    await configureRoom(adminToken, adminToken, "synthesis"),
  ];
  fs.writeFileSync(roomsPath, JSON.stringify(rooms, null, 2) + "\n");

  const report = { generated_at: Math.floor(Date.now() / 1000), checks: [] };
  check(report, "agent count", allAgents.length === config.agent_count, `${allAgents.length} configured agents`);
  check(report, "room catalog", rooms.length === 2, rooms.map((r) => r.alias).join(", "));
  for (const room of rooms) {
    const power = await getState(adminToken, room.room_id, "m.room.power_levels");
    const current = await members(adminToken, room.room_id);
    check(report, `${room.room_key} owner admin`, power.users?.[config.owner_user_id] === 100, config.owner_user_id);
    check(report, `${room.room_key} invite admin-only`, power.invite === 100, `invite=${power.invite}`);
    check(report, `${room.room_key} state admin-only`, power.state_default === 100, `state_default=${power.state_default}`);
    check(report, `${room.room_key} no extra members`, room.removed.length === 0, `removed=${JSON.stringify(room.removed)}`);
    for (const userId of room.allowed_user_ids) {
      check(report, `${room.room_key} has ${userId}`, current.has(userId), current.get(userId) || "missing");
    }
  }

  const qingToken = (await adminLoginAs(adminToken, config.synthesis_agent.matrix_user_id)).access_token;
  const debateRoom = rooms.find((room) => room.room_key === "debate");
  const sendResult = await trySend(qingToken, debateRoom.room_id, "VERIFY: synthesis agent should be read-only in debate");
  check(report, "synthesis cannot post in debate", sendResult === "blocked_403", sendResult);

  const failures = report.checks.filter((item) => !item.ok);
  report.failure_count = failures.length;
  fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + "\n");
  console.log(`Slox provisioned. Verify failures: ${failures.length}.`);
  if (failures.length) process.exit(1);
}

main().catch((err) => {
  console.error(err.stack || err.message || err);
  process.exit(1);
});
