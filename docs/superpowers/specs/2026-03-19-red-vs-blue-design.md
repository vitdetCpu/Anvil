# Red vs Blue — Live App Hardening

## Overview

A live demo where two MiniMax M2.5 AI agents battle over a vulnerable Flask app. Red Agent crafts HTTP attacks, Blue Agent patches the code. A real-time dashboard shows the fight.

## Architecture

**Approach**: Monorepo, single process. The dashboard server, orchestrator, and SSE broadcaster all run in one Python process on port 5001. The target Flask app runs as a managed subprocess on port 5000.

```
useforminimaxhack/
├── target_app/
│   └── app.py              # Vulnerable Flask app (SQLite, raw SQL)
├── orchestrator/
│   ├── orchestrator.py     # Main battle loop
│   ├── agents.py           # MiniMax M2.5 API wrapper (Red + Blue prompts)
│   ├── server_manager.py   # Start/stop/restart Flask subprocess
│   └── events.py           # In-process SSE event queue (thread-safe Queue)
├── dashboard/
│   ├── index.html          # Single-page dashboard (vanilla JS + SSE)
│   └── style.css           # Dark theme, JetBrains Mono + IBM Plex Sans
├── dashboard_server.py     # Serves dashboard + /events SSE + /start POST + /state GET
├── requirements.txt        # flask, requests, openai (for MiniMax compat endpoint)
├── run.sh                  # One command: starts dashboard server + target app
└── .env                    # MINIMAX_API_KEY
```

## MiniMax M2.5 API Configuration

- **Base URL**: `https://api.minimax.io/v1` (OpenAI-compatible endpoint, per official docs)
- **Model ID**: `MiniMax-M2` (per official docs)
- **Auth**: Bearer token via `Authorization: Bearer $MINIMAX_API_KEY` header
- **Client**: Use the `openai` Python package with `base_url` override
- **JSON mode**: Request `response_format={"type": "json_object"}` in API calls to enforce structured output
- **Fallback**: If JSON parsing fails, retry once. If it fails again, skip the round and log the error.
- **Max tokens**: Red agent: 1024. Blue agent: 4096 (must return full file).

## Target App (Flask)

Deliberately vulnerable Flask app with SQLite (raw SQL, no ORM).

### Endpoints

| Endpoint | Method | Purpose | Intentional Vulnerabilities |
|---|---|---|---|
| `/signup` | POST | Create user account | SQL injection in username, no input sanitization, weak password acceptance |
| `/login` | POST | Authenticate user | SQL injection, no rate limiting |
| `/profile/<id>` | GET | View user profile | IDOR (any user can view any profile), stored XSS in username |
| `/health` | GET | Health check | Clean — used to verify server is up |

### Database

- **Engine**: SQLite, file-based at `target_app/data.db`
- **Schema**:
  ```sql
  CREATE TABLE users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      username TEXT NOT NULL,
      password TEXT NOT NULL,
      email TEXT
  );
  ```
- **Lifecycle**: Database is seeded with 2-3 dummy users on each server restart. This ensures Red always has data to work with (e.g., for IDOR attacks). The DB file is recreated on every restart — no persistence across rounds.
- **Seed data**: `admin/admin123`, `alice/password`, `bob/letmein` — intentionally weak credentials.

The app starts maximally vulnerable. Each round, Blue patches one vulnerability class. By round 5, the app should be reasonably hardened.

## AI Agents

Both agents use MiniMax M2.5. Both know they're competing against each other.

### Red Agent (Attacker)

- **Receives**: Current `app.py` source + round history
- **System prompt**: "You are RED, an offensive security AI competing against BLUE, a defensive AI. BLUE has been patching this app after each of your attacks. Find a vulnerability BLUE hasn't fixed yet. Be creative — your opponent is good."
- **Output**: JSON `{curl_command, vulnerability_type, explanation, expected_impact}`
- Orchestrator parses the curl command and executes it via Python `requests` (not shell — see Sandboxing)

### Blue Agent (Defender)

- **Receives**: Current `app.py` source + the attack that just succeeded + server response + round history
- **System prompt**: "You are BLUE, a defensive security AI competing against RED, an offensive AI. RED just attacked the app and you need to patch the vuln. Be thorough — RED will try to bypass your fix next round."
- **Output**: JSON `{file_content (full patched app.py), explanation, vuln_fixed}`
- Blue returns the **full file** (not a diff). Orchestrator computes the diff for display.

### Attack Success Detection

Integrated into the round sequence. After executing the curl command, the orchestrator makes a follow-up call to Red: "Here is the server response. Did your attack succeed? Return `{success: true/false, evidence: string}`". The `success` field in `attack_result` is populated from this follow-up, not from step 6 directly.

### Curl Command Sandboxing

Red's `curl_command` is **never executed as a shell command**. The orchestrator:
1. Parses the curl string to extract: method, URL, headers, body
2. Validates the URL targets `localhost:5000` only (rejects any other host/port)
3. Executes via Python `requests` library with the extracted parameters
4. 5-second timeout on the request

**Supported curl flags**: `-X` (method), `-H` (header), `-d` / `--data` (body), `-b` (cookie). All other flags are silently ignored. If the curl string is unparseable (no URL found, malformed syntax), the attack is skipped with an `error` event and Red gets 0 points for the round.

This prevents shell injection and ensures Red can only attack the target app.

### Agent Memory / Round History

Each round entry in the history list contains:

```json
{
  "round": 1,
  "red_attack": {"curl_command": "...", "vulnerability_type": "SQLi", "explanation": "..."},
  "attack_response": {"status_code": 200, "body": "..."},
  "attack_success": true,
  "blue_patch": {"explanation": "...", "vuln_fixed": "SQLi in login"},
  "app_source_before": "# first 50 lines or summary...",
  "app_source_after": "# first 50 lines or summary..."
}
```

**Context window management**: To avoid exceeding MiniMax's context limit, the history includes only the `red_attack`, `attack_success`, and `blue_patch.explanation` fields for rounds older than 2. The full `app_source` is only sent for the current round. The current `app.py` source is always sent in full.

## Orchestrator

Python script running the battle loop. Always runs exactly **5 rounds**. The round sequence runs regardless of whether Red's attack succeeds — if an attack is blocked, Blue still gets a turn (it can reinforce defenses or fix a different vuln).

### Round Sequence

```
1.  Emit: round_start {round, total: 5}
2.  Read current app.py source
3.  Emit: red_thinking {message: "Analyzing code for vulnerabilities..."}
4.  Call MiniMax Red Agent → get attack JSON
5.  Emit: red_attack {curl_cmd, vuln_type, explanation}
6.  Execute attack via Python requests (5s timeout, localhost:5000 only)
7.  Call MiniMax Red Agent follow-up → get {success, evidence}
8.  Emit: attack_result {status_code, response_body, success}
9.  Emit: blue_thinking {message: "Diagnosing vulnerability..."}
10. Call MiniMax Blue Agent → get patched file + explanation
11. Validate patched file: compile-check with py_compile.compile()
    - If invalid: keep old app.py, emit blue_patch with error note, skip restart
12. Emit: blue_patch {diff, explanation, vuln_fixed}
13. Write new app.py (only if validation passed)
14. Emit: server_restart {status: "restarting"}
15. Kill + restart Flask subprocess
16. Poll /health every 500ms, max 10 attempts (5s total)
    - If health check fails: emit error event, rollback to previous app.py, restart
    - Only one previous version is kept. If rollback also fails health check, halt battle with battle_complete containing error state.
17. Emit: server_ready {status: "up"}
18. Emit: round_complete {round, scores}

After round 5:
19. Emit: battle_complete {summary, final_scores}
    - Summary is a template string: "Red found X vulnerabilities. Blue patched Y. App is Z% hardened."
```

### Scoring

- **Red attacks**: Count of rounds where `attack_success == true`
- **Blue patches**: Count of rounds where Blue's patch was valid and applied
- **Hardened %**: `blue_patches_applied / total_rounds_completed * 100` (simple ratio — not tied to a predefined vuln count since Red may find unexpected vulns)
- **`final_scores`**: `{red_successes: int, blue_patches: int, hardened_pct: int, rounds_played: 5}`

### Safety Constraints

- Curl/requests timeout: 5 seconds
- MiniMax API timeout: 30 seconds
- Hard cap: 5 rounds, no early termination
- Curl sandboxing: parsed and executed via `requests`, localhost:5000 only

### Error Handling

| Error | Response |
|---|---|
| MiniMax API 429 (rate limit) | Wait 5s, retry once. If still 429, skip round, emit error event. |
| MiniMax API 500 / timeout | Skip round, emit error event, continue to next round. |
| Malformed JSON from agent | Retry once with "Please return valid JSON." If still bad, skip round. |
| Blue returns invalid Python | Keep old app.py, log error in blue_patch event, skip restart. |
| Target app crashes during attack | Restart it, emit error event, continue. |
| Health check fails after restart | Rollback to previous app.py, restart, emit error event. |
| `.env` missing or empty | Fail fast at startup with clear error message. |

## Dashboard Server & Events

Single Flask process on port 5001. The orchestrator runs in a background thread within this process.

**Events IPC**: `events.py` exposes a thread-safe `queue.Queue`. The orchestrator thread pushes events to the queue. The SSE endpoint reads from the queue. Multiple SSE clients each get their own queue (fan-out via a list of subscriber queues).

| Endpoint | Method | Purpose |
|---|---|---|
| `/` | GET | Serve dashboard HTML |
| `/events` | GET (SSE) | Stream battle events to browser |
| `/start` | POST | Kick off the battle loop (idempotent — ignored if already running) |
| `/state` | GET | Current battle state for page refresh recovery (see schema below) |

**`/state` response schema:**
```json
{
  "status": "idle | running | complete | error",
  "current_round": 0,
  "total_rounds": 5,
  "rounds": [{"round": 1, "red_attack": {...}, "attack_success": true, "blue_patch": {...}}],
  "scores": {"red_successes": 0, "blue_patches": 0, "hardened_pct": 0}
}
```

**SSE reconnection**: The dashboard JS uses `EventSource` which auto-reconnects. On reconnect, the client calls `/state` to catch up on missed events.

**Double-start protection**: `/start` is idempotent. If a battle is already running, it returns 409 Conflict.

## Dashboard UI

### Layout: Two-Column Battle View

- **Header bar**: "RED vs BLUE" title, round counter pill, current status
- **Scoreboard**: Red attacks count, Blue patches count, Hardened %
- **Round timeline**: Dots alternating red/blue showing progression
- **Left panel (Red)**: Attack log with curl commands, vulnerability types, success/fail badges
- **Right panel (Blue)**: Patch log with code diffs, explanations, applied badges
- **Start Battle button**: Centered, prominent. Sends POST to `/start`. Disabled once battle begins.

### Design System

- **Theme**: Dark OLED (`#020617` background, `#0F172A` panels, `#1E293B` borders)
- **Typography**: JetBrains Mono (headings, code, data) + IBM Plex Sans (body text)
- **Colors**: Red `#EF4444` for attacks, Blue `#3B82F6` for defense, Green `#22C55E` for success, Yellow `#EAB308` for in-progress
- **Tech**: Plain HTML + vanilla JS, SSE for live updates, no build step

### SSE Event Types

| Event Type | Payload | Dashboard Action |
|---|---|---|
| `round_start` | `{round, total}` | Update round counter, timeline |
| `red_thinking` | `{message}` | Show spinner on Red panel |
| `red_attack` | `{curl_cmd, vuln_type, explanation}` | Display attack in Red panel |
| `attack_result` | `{status_code, response_body, success}` | Show SUCCESS/BLOCKED badge |
| `blue_thinking` | `{message}` | Show spinner on Blue panel |
| `blue_patch` | `{diff, explanation, vuln_fixed}` | Display code diff in Blue panel |
| `server_restart` | `{status: "restarting"}` | Flash server status indicator |
| `server_ready` | `{status: "up"}` | Green server status |
| `round_complete` | `{round, scores}` | Update scoreboard |
| `battle_complete` | `{summary, final_scores}` | Show final results overlay |
| `error` | `{message, round}` | Show error toast, continue |

## Startup

`./run.sh`:
1. Checks that `.env` exists and `MINIMAX_API_KEY` is set (fails fast if not)
2. Checks ports 5000 and 5001 are free
3. Installs dependencies from `requirements.txt` if needed
4. Starts `dashboard_server.py` (which also manages the target app subprocess)
5. Opens `http://localhost:5001` in the browser
6. On Ctrl+C: kills both the dashboard server and target app subprocess cleanly
