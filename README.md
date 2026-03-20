# Red vs Blue — Live App Hardening

Two AI agents battle over a vulnerable Flask app in real-time. **Red Agent** finds and exploits security vulnerabilities. **Blue Agent** patches them. Watch it happen live in a dark-themed dashboard with streaming AI reasoning.

![Battle Dashboard](https://img.shields.io/badge/status-battle--ready-brightgreen)

## How It Works

1. Paste any Flask app (or use the default vulnerable one)
2. Hit **START BATTLE** — 5 rounds begin
3. Each round:
   - **Red** analyzes the code, crafts a `curl` attack
   - Attack executes against the live server
   - **Judge** (Claude Haiku) determines if the attack succeeded
   - **Blue** patches the vulnerability and restarts the server
4. End screen shows round-by-round breakdown, remaining risks, and security recommendations

## Tech Stack

- **AI**: Claude Sonnet 4 (Red/Blue agents with extended thinking), Claude Haiku (judge + summary)
- **Backend**: Flask, Server-Sent Events (SSE), subprocess management
- **Frontend**: Vanilla JS, dark OLED theme, real-time streaming
- **Security**: Curl command sandboxing via `shlex` + Python `requests`

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Set your API key
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env

# Run
bash run.sh
```

Dashboard opens at **http://localhost:5001**

## Project Structure

```
├── dashboard_server.py      # Flask server (SSE, static files, API)
├── dashboard/
│   ├── index.html           # Single-page dashboard UI
│   └── style.css            # Dark OLED theme
├── orchestrator/
│   ├── orchestrator.py      # Battle loop (5 rounds)
│   ├── agents.py            # Claude API calls (streaming + thinking)
│   ├── events.py            # SSE fan-out broadcaster
│   ├── server_manager.py    # Target app subprocess manager
│   └── curl_parser.py       # Safe curl command parser
├── target_app/
│   └── app.py               # Deliberately vulnerable Flask app
├── run.sh                   # Startup script
├── Procfile                 # Railway deployment
└── requirements.txt
```

## Scoring

- **Red** gets +1 for each successful exploit
- **Blue** gets +1 for each defended attack
- Final percentage = attacks defended / total rounds
- \>50% defended = **Blue Wins**, ≤50% = **Red Wins**

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Claude API key |
| `PORT` | No | Dashboard port (default: 5001) |

## Deployment

Supports Railway, Render, Fly.io, or any platform that runs long-lived Python processes. Includes a `Procfile` for easy deployment.

## Credits

Built with ❤️ and ☕️ at Minimax GTC Minihack 2026.
