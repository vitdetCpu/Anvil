import atexit
import json
import os
import signal
import sys

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request, send_from_directory

from orchestrator.events import EventBroadcaster
from orchestrator.orchestrator import BattleOrchestrator
from orchestrator.server_manager import ServerManager

load_dotenv()

if not os.environ.get("MINIMAX_API_KEY"):
    print("ERROR: MINIMAX_API_KEY not set. Add it to your .env file.")
    sys.exit(1)

app = Flask(__name__, static_folder="dashboard")
broadcaster = EventBroadcaster()
server_manager = ServerManager()
orchestrator = BattleOrchestrator(broadcaster, server_manager)


@app.route("/")
def index():
    return send_from_directory("dashboard", "index.html")


@app.route("/style.css")
def style():
    return send_from_directory("dashboard", "style.css")


@app.route("/events")
def events():
    """SSE endpoint."""
    q = broadcaster.subscribe()

    def stream():
        try:
            while True:
                try:
                    event = q.get(timeout=30)
                    yield f"data: {json.dumps(event)}\n\n"
                except Exception:
                    yield ": keepalive\n\n"
        except GeneratorExit:
            broadcaster.unsubscribe(q)

    return Response(stream(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    })


@app.route("/source")
def source():
    """Return current target app source code."""
    return jsonify({"source": orchestrator._read_app_source()})


@app.route("/start", methods=["POST"])
def start_battle():
    if orchestrator.status == "running":
        return jsonify({"error": "Battle already running"}), 409
    data = request.get_json(silent=True) or {}
    custom_code = data.get("code", "").strip()
    if custom_code:
        orchestrator._write_app_source(custom_code)
    orchestrator.start()
    return jsonify({"message": "Battle started"})


@app.route("/reset", methods=["POST"])
def reset_battle():
    orchestrator.reset()
    return jsonify({"message": "Battle reset"})


@app.route("/state")
def state():
    return jsonify(orchestrator.get_state())


def cleanup():
    server_manager.stop()

atexit.register(cleanup)
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
