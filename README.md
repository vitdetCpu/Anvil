# Red vs Blue — Live App Hardening

Two AI agents battle over a vulnerable Flask app in real-time. **Red Agent** finds and exploits security vulnerabilities. **Blue Agent** patches them. Watch it happen live in a dark-themed dashboard with streaming AI reasoning.

![Battle Dashboard](https://img.shields.io/badge/status-battle--ready-brightgreen)

## How It Works

1. Hit **START BATTLE** — 3 rounds begin
2. Each round:
   - **Red** analyzes the current source code, crafts a `curl` attack
   - Attack executes against the live target server
   - **Judge** determines if the attack succeeded (runs concurrently with Blue)
   - **Blue** patches the vulnerability and the server restarts automatically
   - If Blue's patch is invalid or crashes the server, it rolls back to the previous version
3. End screen shows final scores, remaining risks, and security recommendations

## Architecture

```
Browser (vanilla JS + SSE)
    |
    | SSE stream + REST
    v
Dashboard Server (Flask, :5001)
    |-- BattleOrchestrator (background thread)
    |       |-- Red Agent  -> finds & exploits vulns
    |       |-- Judge      -> scores attack success (concurrent with Blue)
    |       |-- Blue Agent -> patches the code
    |-- EventBroadcaster (thread-safe queue fan-out)
    |-- ServerManager (subprocess control)
            |
            v
Target Flask App (subprocess, :5050)
    |-- Deliberately vulnerable (SQL injection, IDOR)
    |-- SQLite database, reset on each restart
```

### Round Flow

```
Red analyzes source -> crafts curl attack
                            |
                    attack executed via requests
                            |
                +-----------+-----------+
                |                       |
          Judge scores             Blue patches
          (concurrent)             (concurrent)
                |                       |
                +-----------+-----------+
                            |
                  patch validated (py_compile)
                            |
                  target app restarted
                  (rollback on failure)
```

## Tech Stack

- **AI**: MiniMax M2.7-highspeed via Anthropic SDK (Red/Blue with extended thinking, Judge for fast scoring)
- **Backend**: Python, Flask, Server-Sent Events (SSE), subprocess management
- **Frontend**: Vanilla HTML/CSS/JS, dark OLED theme, real-time streaming
- **Security**: Curl commands parsed (never shelled out), URL whitelisted to localhost:5050, executed via Python `requests`

## Quick Start

```bash
# Clone and enter the project
git clone <repo-url> && cd Anvil

# Set your API key
cp .env.example .env
# Edit .env and add your MINIMAX_API_KEY

# Run (installs deps, checks ports, opens browser)
bash run.sh
```

Dashboard opens at **http://localhost:5001**

## Project Structure

```
├── dashboard_server.py      # Flask server (SSE, static files, REST API)
├── dashboard/
│   ├── index.html           # Single-page dashboard UI
│   └── style.css            # Dark OLED theme
├── orchestrator/
│   ├── orchestrator.py      # Battle loop (3 rounds, scoring, rollback)
│   ├── agents.py            # MiniMax API calls (streaming + extended thinking)
│   ├── events.py            # Thread-safe SSE fan-out broadcaster
│   ├── server_manager.py    # Target app subprocess manager
│   └── curl_parser.py       # Safe curl command parser
├── target_app/
│   └── app.py               # Deliberately vulnerable Flask app
├── run.sh                   # Startup script (validates env, checks ports)
├── Procfile                 # Railway/Render/Fly.io deployment
└── requirements.txt         # flask, requests, anthropic, python-dotenv
```

## API Endpoints

| Route | Method | Description |
|-------|--------|-------------|
| `/` | GET | Dashboard SPA |
| `/events` | GET | SSE event stream |
| `/start` | POST | Begin a new battle |
| `/state` | GET | Full state snapshot (for reconnection) |
| `/source` | GET | Current target app source code |

## Scoring

- **Red** gets +1 for each successful exploit
- **Blue** gets +1 for each defended attack
- Final percentage = attacks defended / total rounds
- \>50% defended = **Blue Wins**, <=50% = **Red Wins**

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `MINIMAX_API_KEY` | Yes | MiniMax API key |
| `PORT` | No | Dashboard port (default: 5001) |

## Deployment

Supports Railway, Render, Fly.io, or any platform that runs long-lived Python processes. Includes a `Procfile` for easy deployment.

## Credits

Built with ❤️ and ☕️ at Minimax GTC Minihack 2026.
