# Anvil

**Defend what matters.**

Two AI agents battle over a vulnerable Flask app in real-time. **Attacker** finds and exploits security vulnerabilities. **Defender** patches them. Watch it happen live in a dark-themed dashboard with streaming AI reasoning.

![Battle Dashboard](https://img.shields.io/badge/status-battle--ready-brightgreen)

## How It Works

1. Hit **START BATTLE** — 3 rounds begin
2. Each round:
   - **Attacker** analyzes the current source code, crafts a `curl` attack
   - Attack executes against the live target server
   - **Judge** determines if the attack succeeded (runs concurrently with Defender)
   - **Defender** patches the vulnerability and the server restarts automatically
   - If Defender's patch is invalid or crashes the server, it rolls back to the previous version
3. End screen shows final scores, remaining risks, and security recommendations
4. Hit **RESTART** at any point to reset and run a fresh battle

Bonus: paste a prompt injection attempt into the code input and see what happens.

## Architecture

```
Browser (vanilla JS + SSE)
    |
    | SSE stream + REST
    v
Dashboard Server (Flask, :5001)
    |-- BattleOrchestrator (background thread)
    |       |-- Attacker  -> finds & exploits vulns
    |       |-- Judge     -> scores attack success (concurrent with Defender)
    |       |-- Defender  -> patches the code
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
Attacker analyzes source -> crafts curl attack
                                 |
                         attack executed via requests
                                 |
                     +-----------+-----------+
                     |                       |
               Judge scores           Defender patches
               (concurrent)           (concurrent)
                     |                       |
                     +-----------+-----------+
                                 |
                       patch validated (py_compile)
                                 |
                       target app restarted
                       (rollback on failure)
```

## Tech Stack

- **AI**: MiniMax M2.7-highspeed via Anthropic SDK (Attacker/Defender with extended thinking, Judge for fast scoring)
- **Backend**: Python, Flask, Server-Sent Events (SSE), subprocess management
- **Frontend**: Vanilla HTML/CSS/JS, dark OLED theme, real-time streaming
- **Security**: Curl commands parsed (never shelled out), URL whitelisted to localhost:5050, prompt injection detection on code input

## Quick Start

```bash
# Clone and enter the project
git clone https://github.com/vitdetCpu/Anvil.git && cd Anvil

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
| `/reset` | POST | Stop and reset battle |
| `/state` | GET | Full state snapshot (for reconnection) |
| `/source` | GET | Current target app source code |

## Scoring

- **Attacker** gets +1 for each successful exploit
- **Defender** gets +1 for each defended attack
- Final percentage = attacks defended / total rounds
- \>50% defended = **Defender Wins**, <=50% = **Attacker Wins**

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `MINIMAX_API_KEY` | Yes | MiniMax API key |
| `PORT` | No | Dashboard port (default: 5001) |

## Deployment

Supports Railway, Render, Fly.io, or any platform that runs long-lived Python processes. Includes a `Procfile` for easy deployment.

## Credits

Built with ❤️ and ☕️ at Minimax GTC Minihack 2026.
