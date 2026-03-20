#!/usr/bin/env bash
set -e

if [ ! -f .env ]; then
  echo "ERROR: .env file not found. Copy .env.example to .env and add your MINIMAX_API_KEY."
  exit 1
fi

source .env

if [ -z "$MINIMAX_API_KEY" ]; then
  echo "ERROR: MINIMAX_API_KEY is empty in .env"
  exit 1
fi

if lsof -i :5050 -sTCP:LISTEN -t >/dev/null 2>&1; then
  echo "ERROR: Port 5050 is already in use"
  exit 1
fi

if lsof -i :5001 -sTCP:LISTEN -t >/dev/null 2>&1; then
  echo "ERROR: Port 5001 is already in use"
  exit 1
fi

pip install -q -r requirements.txt

echo ""
echo "  ╔═══════════════════════════════════════╗"
echo "  ║   RED vs BLUE — Live App Hardening    ║"
echo "  ║                                       ║"
echo "  ║   Dashboard: http://localhost:5001     ║"
echo "  ║   Target:    http://localhost:5050     ║"
echo "  ╚═══════════════════════════════════════╝"
echo ""

if command -v open &> /dev/null; then
  open http://localhost:5001
fi

python dashboard_server.py
