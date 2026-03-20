# Red vs Blue — Live App Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a live demo where two MiniMax AI agents battle over a vulnerable Flask app — Red attacks, Blue patches, a real-time dashboard shows the fight.

**Architecture:** Single Python process (dashboard server + orchestrator) on port 5001 manages a Flask target app subprocess on port 5000. Orchestrator runs in a background thread, pushes events to SSE subscribers via a thread-safe queue. Dashboard is plain HTML/JS consuming SSE.

**Tech Stack:** Python 3.10+, Flask, SQLite, OpenAI SDK (MiniMax-compatible), vanilla HTML/CSS/JS, SSE.

**Spec:** `docs/superpowers/specs/2026-03-19-red-vs-blue-design.md`

**MiniMax API (from official docs):**
- Base URL: `https://api.minimax.io/v1`
- Model: `MiniMax-M2`
- Auth: Bearer token via `MINIMAX_API_KEY` env var passed as `api_key` param
- Client: `openai` Python package with `base_url` override
- Structured output: `response_format={"type": "json_schema", "json_schema": {...}}`

---

## Task 1: Project Scaffolding

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `target_app/__init__.py` (empty)
- Create: `orchestrator/__init__.py` (empty)

- [ ] **Step 1: Create requirements.txt**

```
flask==3.1.1
requests==2.32.3
openai==1.82.0
python-dotenv==1.1.0
```

- [ ] **Step 2: Create .env.example**

```
MINIMAX_API_KEY=your_key_here
```

- [ ] **Step 3: Create .gitignore**

```
.env
__pycache__/
*.pyc
target_app/data.db
.superpowers/
```

- [ ] **Step 4: Create empty __init__.py files**

Create `target_app/__init__.py` and `orchestrator/__init__.py` (both empty).

- [ ] **Step 5: Install dependencies**

Run: `pip install -r requirements.txt`

- [ ] **Step 6: Commit**

```bash
git add requirements.txt .env.example .gitignore target_app/__init__.py orchestrator/__init__.py
git commit -m "feat: project scaffolding with dependencies"
```

---

## Task 2: Target App (Vulnerable Flask Server)

**Files:**
- Create: `target_app/app.py`

This is the deliberately vulnerable Flask app. Raw SQL, no input sanitization, no auth checks. It must be a standalone runnable Flask app (`python target_app/app.py` → server on port 5000).

- [ ] **Step 1: Write target_app/app.py**

```python
import sqlite3
import os
from flask import Flask, request, jsonify, g

app = Flask(__name__)
DB_PATH = os.path.join(os.path.dirname(__file__), "data.db")


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Recreate DB with seed data on every start."""
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    db = sqlite3.connect(DB_PATH)
    db.execute(
        "CREATE TABLE users ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "username TEXT NOT NULL,"
        "password TEXT NOT NULL,"
        "email TEXT)"
    )
    seed_users = [
        ("admin", "admin123", "admin@example.com"),
        ("alice", "password", "alice@example.com"),
        ("bob", "letmein", "bob@example.com"),
    ]
    db.executemany(
        "INSERT INTO users (username, password, email) VALUES (?, ?, ?)",
        seed_users,
    )
    db.commit()
    db.close()


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/signup", methods=["POST"])
def signup():
    data = request.get_json(force=True)
    username = data.get("username", "")
    password = data.get("password", "")
    email = data.get("email", "")
    db = get_db()
    # VULNERABLE: raw SQL, no sanitization, no password requirements
    db.execute(
        f"INSERT INTO users (username, password, email) VALUES ('{username}', '{password}', '{email}')"
    )
    db.commit()
    return jsonify({"message": f"User {username} created"}), 201


@app.route("/login", methods=["POST"])
def login():
    data = request.get_json(force=True)
    username = data.get("username", "")
    password = data.get("password", "")
    db = get_db()
    # VULNERABLE: raw SQL injection
    query = f"SELECT * FROM users WHERE username='{username}' AND password='{password}'"
    user = db.execute(query).fetchone()
    if user:
        return jsonify({"message": "Login successful", "user": dict(user)})
    return jsonify({"message": "Invalid credentials"}), 401


@app.route("/profile/<int:user_id>")
def profile(user_id):
    db = get_db()
    # VULNERABLE: no auth check (IDOR), XSS in username
    user = db.execute(f"SELECT * FROM users WHERE id={user_id}").fetchone()
    if user:
        # VULNERABLE: returns raw username (stored XSS)
        return jsonify({
            "username": user["username"],
            "email": user["email"],
            "id": user["id"]
        })
    return jsonify({"error": "User not found"}), 404


if __name__ == "__main__":
    init_db()
    app.run(port=5000, debug=False)
```

- [ ] **Step 2: Test manually**

Run: `python target_app/app.py`
Then in another terminal:
```bash
curl http://localhost:5000/health
# Expected: {"status":"ok"}
curl -X POST http://localhost:5000/login -d '{"username":"admin","password":"admin123"}' -H "Content-Type: application/json"
# Expected: {"message":"Login successful","user":{...}}
```
Kill the server after verifying.

- [ ] **Step 3: Commit**

```bash
git add target_app/app.py
git commit -m "feat: vulnerable Flask target app with SQLi, XSS, IDOR"
```

---

## Task 3: Events Module (SSE Broadcasting)

**Files:**
- Create: `orchestrator/events.py`

Thread-safe event broadcaster. The orchestrator pushes events, SSE endpoint subscribers consume them. Each subscriber gets its own queue.

- [ ] **Step 1: Write orchestrator/events.py**

```python
import json
import queue
import threading


class EventBroadcaster:
    """Thread-safe SSE event broadcaster with fan-out to multiple subscribers."""

    def __init__(self):
        self._subscribers = []
        self._lock = threading.Lock()
        self._history = []  # All events for /state recovery

    def subscribe(self):
        """Create a new subscriber queue. Returns the queue."""
        q = queue.Queue()
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q):
        """Remove a subscriber queue."""
        with self._lock:
            self._subscribers = [s for s in self._subscribers if s is not q]

    def emit(self, event_type, data):
        """Push an event to all subscribers."""
        event = {"type": event_type, **data}
        with self._lock:
            self._history.append(event)
            for q in self._subscribers:
                q.put(event)

    def get_history(self):
        """Return all events (for /state endpoint)."""
        with self._lock:
            return list(self._history)

    def reset(self):
        """Clear history for a new battle."""
        with self._lock:
            self._history.clear()
```

- [ ] **Step 2: Commit**

```bash
git add orchestrator/events.py
git commit -m "feat: thread-safe SSE event broadcaster"
```

---

## Task 4: Server Manager (Flask Subprocess Control)

**Files:**
- Create: `orchestrator/server_manager.py`

Starts/stops/restarts the Flask target app as a subprocess. Polls `/health` to confirm it's up.

- [ ] **Step 1: Write orchestrator/server_manager.py**

```python
import subprocess
import sys
import time
import requests


class ServerManager:
    """Manages the target Flask app as a subprocess."""

    def __init__(self, app_path="target_app/app.py", port=5000):
        self.app_path = app_path
        self.port = port
        self.process = None

    def start(self):
        """Start the Flask app subprocess."""
        self.process = subprocess.Popen(
            [sys.executable, self.app_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if not self._wait_for_health():
            raise RuntimeError("Target app failed to start")

    def stop(self):
        """Stop the Flask app subprocess."""
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            self.process = None

    def restart(self):
        """Stop then start."""
        self.stop()
        self.start()

    def _wait_for_health(self, max_attempts=10, interval=0.5):
        """Poll /health until 200 or timeout."""
        url = f"http://localhost:{self.port}/health"
        for _ in range(max_attempts):
            try:
                r = requests.get(url, timeout=2)
                if r.status_code == 200:
                    return True
            except requests.ConnectionError:
                pass
            time.sleep(interval)
        return False

    @property
    def is_running(self):
        return self.process is not None and self.process.poll() is None
```

- [ ] **Step 2: Commit**

```bash
git add orchestrator/server_manager.py
git commit -m "feat: Flask subprocess manager with health check polling"
```

---

## Task 5: Curl Parser (Safe Command Extraction)

**Files:**
- Create: `orchestrator/curl_parser.py`

Parses a curl command string from Red Agent into method/url/headers/body. Never executes as shell. Only allows `localhost:5000`.

- [ ] **Step 1: Write orchestrator/curl_parser.py**

```python
import shlex
from urllib.parse import urlparse


class CurlParseError(Exception):
    pass


def parse_curl(curl_string):
    """Parse a curl command string into requests-compatible kwargs.

    Returns dict with keys: method, url, headers, data, cookies.
    Raises CurlParseError if unparseable or targets wrong host.
    """
    curl_string = curl_string.strip()
    if curl_string.startswith("curl "):
        curl_string = curl_string[5:]

    try:
        tokens = shlex.split(curl_string)
    except ValueError as e:
        raise CurlParseError(f"Failed to parse curl command: {e}")

    method = "GET"
    url = None
    headers = {}
    data = None
    cookies = {}

    i = 0
    while i < len(tokens):
        token = tokens[i]

        if token in ("-X", "--request") and i + 1 < len(tokens):
            method = tokens[i + 1].upper()
            i += 2
        elif token in ("-H", "--header") and i + 1 < len(tokens):
            header = tokens[i + 1]
            if ":" in header:
                key, val = header.split(":", 1)
                headers[key.strip()] = val.strip()
            i += 2
        elif token in ("-d", "--data", "--data-raw") and i + 1 < len(tokens):
            data = tokens[i + 1]
            if method == "GET":
                method = "POST"
            i += 2
        elif token in ("-b", "--cookie") and i + 1 < len(tokens):
            cookie_str = tokens[i + 1]
            for pair in cookie_str.split(";"):
                if "=" in pair:
                    k, v = pair.strip().split("=", 1)
                    cookies[k.strip()] = v.strip()
            i += 2
        elif not token.startswith("-"):
            if url is None:
                url = token
            i += 1
        else:
            # Unknown flag — skip
            i += 1

    if not url:
        raise CurlParseError("No URL found in curl command")

    # Validate URL targets localhost:5000 only
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    if hostname not in ("localhost", "127.0.0.1") or port != 5000:
        raise CurlParseError(
            f"URL must target localhost:5000, got {hostname}:{port}"
        )

    return {
        "method": method,
        "url": url,
        "headers": headers,
        "data": data,
        "cookies": cookies,
    }
```

- [ ] **Step 2: Commit**

```bash
git add orchestrator/curl_parser.py
git commit -m "feat: safe curl command parser with localhost:5000 validation"
```

---

## Task 6: Agents Module (MiniMax API Wrapper)

**Files:**
- Create: `orchestrator/agents.py`

Wraps MiniMax API calls for Red and Blue agents. Uses the `openai` Python SDK with base_url override.

- [ ] **Step 1: Write orchestrator/agents.py**

```python
import json
import os
from openai import OpenAI


def _get_client():
    api_key = os.environ.get("MINIMAX_API_KEY")
    if not api_key:
        raise RuntimeError("MINIMAX_API_KEY not set")
    return OpenAI(
        api_key=api_key,
        base_url="https://api.minimax.io/v1",
    )


def _call_minimax(messages, max_tokens=1024):
    """Call MiniMax API with 30s timeout. Retry once on JSON parse failure or rate limit."""
    import time as _time
    client = _get_client()
    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model="MiniMax-M2",
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.7,
                timeout=30,
            )
        except Exception as e:
            error_str = str(e).lower()
            if "rate" in error_str or "429" in error_str:
                if attempt == 0:
                    _time.sleep(5)
                    continue
            raise
        content = response.choices[0].message.content.strip()
        # Strip markdown code fences if present
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            if attempt == 0:
                messages.append({"role": "assistant", "content": content})
                messages.append(
                    {"role": "user", "content": "That was not valid JSON. Please return ONLY a valid JSON object, no other text."}
                )
    raise ValueError(f"MiniMax returned invalid JSON after retry: {content[:200]}")


def _build_history_context(history):
    """Build a condensed history string for agent context."""
    lines = []
    for entry in history:
        r = entry["round"]
        atk = entry.get("red_attack", {})
        success = entry.get("attack_success", False)
        patch = entry.get("blue_patch", {})
        lines.append(
            f"Round {r}: RED used {atk.get('vulnerability_type', '?')} "
            f"({'SUCCESS' if success else 'BLOCKED'}). "
            f"BLUE patched: {patch.get('vuln_fixed', 'N/A')}."
        )
    return "\n".join(lines) if lines else "No previous rounds."


RED_SYSTEM = """You are RED, an offensive security AI in a live competition against BLUE, a defensive AI.

Your job: find vulnerabilities in a Flask web app and craft curl commands to exploit them.
BLUE patches the app after each of your attacks. You must find NEW vulnerabilities that BLUE hasn't fixed yet.
Be creative — your opponent is skilled.

You MUST respond with ONLY a JSON object in this exact format:
{
  "curl_command": "curl -X POST http://localhost:5000/login -H 'Content-Type: application/json' -d '{...}'",
  "vulnerability_type": "SQL Injection",
  "explanation": "Brief explanation of the attack",
  "expected_impact": "What the attack should achieve"
}

Rules:
- Target ONLY http://localhost:5000
- Use real curl syntax with -X, -H, -d, -b flags
- Do NOT repeat attacks that were already blocked
- Try different vulnerability classes each round"""

BLUE_SYSTEM = """You are BLUE, a defensive security AI in a live competition against RED, an offensive AI.

Your job: patch vulnerabilities in a Flask web app after RED attacks it.
RED will try to bypass your fixes next round, so be thorough.

You MUST respond with ONLY a JSON object in this exact format:
{
  "file_content": "the COMPLETE patched app.py file content as a string",
  "explanation": "What vulnerability you fixed and how",
  "vuln_fixed": "Short label like 'SQL injection in login'"
}

Rules:
- Return the COMPLETE app.py file — not a diff, not a snippet
- Keep all existing functionality working
- Only fix the vulnerability that was just exploited
- Do NOT add unnecessary features or imports
- The app must remain runnable with: python target_app/app.py"""

RED_JUDGE_SYSTEM = """You are judging whether a security attack succeeded.
Given the HTTP response from the server, determine if the attack achieved its intended impact.

Respond with ONLY a JSON object:
{
  "success": true or false,
  "evidence": "Brief explanation of why the attack succeeded or failed"
}"""


def call_red_agent(app_source, history):
    """Ask Red to craft an attack. Returns dict with curl_command, vulnerability_type, etc."""
    history_ctx = _build_history_context(history)
    messages = [
        {"role": "system", "content": RED_SYSTEM},
        {
            "role": "user",
            "content": (
                f"## Current app.py source:\n```python\n{app_source}\n```\n\n"
                f"## Battle history:\n{history_ctx}\n\n"
                f"Craft your next attack."
            ),
        },
    ]
    return _call_minimax(messages, max_tokens=1024)


def call_blue_agent(app_source, attack, attack_response, history):
    """Ask Blue to patch the app. Returns dict with file_content, explanation, vuln_fixed."""
    history_ctx = _build_history_context(history)
    messages = [
        {"role": "system", "content": BLUE_SYSTEM},
        {
            "role": "user",
            "content": (
                f"## Current app.py source:\n```python\n{app_source}\n```\n\n"
                f"## RED's attack this round:\n"
                f"Type: {attack.get('vulnerability_type', '?')}\n"
                f"Command: {attack.get('curl_command', '?')}\n"
                f"Explanation: {attack.get('explanation', '?')}\n\n"
                f"## Server response to the attack:\n"
                f"Status: {attack_response.get('status_code', '?')}\n"
                f"Body: {attack_response.get('body', '?')[:500]}\n\n"
                f"## Battle history:\n{history_ctx}\n\n"
                f"Patch the vulnerability."
            ),
        },
    ]
    return _call_minimax(messages, max_tokens=4096)


def call_red_judge(attack, response_body, status_code):
    """Ask Red to judge if its attack succeeded. Returns dict with success, evidence."""
    messages = [
        {"role": "system", "content": RED_JUDGE_SYSTEM},
        {
            "role": "user",
            "content": (
                f"Attack type: {attack.get('vulnerability_type', '?')}\n"
                f"Expected impact: {attack.get('expected_impact', '?')}\n"
                f"Server status code: {status_code}\n"
                f"Server response body: {response_body[:1000]}\n\n"
                f"Did the attack succeed?"
            ),
        },
    ]
    return _call_minimax(messages, max_tokens=256)
```

- [ ] **Step 2: Commit**

```bash
git add orchestrator/agents.py
git commit -m "feat: MiniMax API wrapper for Red/Blue agents with JSON retry"
```

---

## Task 7: Orchestrator (Main Battle Loop)

**Files:**
- Create: `orchestrator/orchestrator.py`

The core battle loop. Runs in a background thread. Coordinates Red → attack → judge → Blue → patch → restart for 5 rounds.

- [ ] **Step 1: Write orchestrator/orchestrator.py**

```python
import difflib
import os
import py_compile
import tempfile
import threading
import time
import requests

from orchestrator.agents import call_red_agent, call_blue_agent, call_red_judge
from orchestrator.curl_parser import parse_curl, CurlParseError
from orchestrator.events import EventBroadcaster
from orchestrator.server_manager import ServerManager

APP_PATH = os.path.join(os.path.dirname(__file__), "..", "target_app", "app.py")
TOTAL_ROUNDS = 5


class BattleOrchestrator:
    def __init__(self, broadcaster: EventBroadcaster, server_manager: ServerManager):
        self.broadcaster = broadcaster
        self.server = server_manager
        self.history = []
        self.scores = {"red_successes": 0, "blue_patches": 0, "hardened_pct": 0, "rounds_played": 0}
        self.status = "idle"
        self.current_round = 0
        self._previous_app_source = None
        self._thread = None

    def start(self):
        """Start the battle in a background thread."""
        if self.status == "running":
            return False
        self.status = "running"
        self.broadcaster.reset()
        self.history.clear()
        self.scores = {"red_successes": 0, "blue_patches": 0, "hardened_pct": 0, "rounds_played": 0}
        self._thread = threading.Thread(target=self._run_battle, daemon=True)
        self._thread.start()
        return True

    def get_state(self):
        """Return current state for /state endpoint."""
        return {
            "status": self.status,
            "current_round": self.current_round,
            "total_rounds": TOTAL_ROUNDS,
            "rounds": self.history,
            "scores": self.scores,
        }

    def _read_app_source(self):
        app_path = os.path.abspath(APP_PATH)
        with open(app_path, "r") as f:
            return f.read()

    def _write_app_source(self, source):
        app_path = os.path.abspath(APP_PATH)
        with open(app_path, "w") as f:
            f.write(source)

    def _validate_python(self, source):
        """Check if source is valid Python. Returns True/False."""
        tmp = tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w")
        try:
            tmp.write(source)
            tmp.close()
            py_compile.compile(tmp.name, doraise=True)
            return True
        except py_compile.PyCompileError:
            return False
        finally:
            os.unlink(tmp.name)

    def _compute_diff(self, old_source, new_source):
        """Compute a unified diff string."""
        old_lines = old_source.splitlines(keepends=True)
        new_lines = new_source.splitlines(keepends=True)
        diff = difflib.unified_diff(old_lines, new_lines, fromfile="app.py (before)", tofile="app.py (after)")
        return "".join(diff)

    def _execute_attack(self, curl_command):
        """Parse and execute a curl command safely. Returns dict with status_code, body."""
        try:
            parsed = parse_curl(curl_command)
        except CurlParseError as e:
            return {"status_code": 0, "body": f"PARSE ERROR: {e}"}

        try:
            r = requests.request(
                method=parsed["method"],
                url=parsed["url"],
                headers=parsed["headers"],
                data=parsed["data"],
                cookies=parsed["cookies"],
                timeout=5,
            )
            return {"status_code": r.status_code, "body": r.text[:2000]}
        except requests.RequestException as e:
            return {"status_code": 0, "body": f"REQUEST ERROR: {e}"}

    def _run_battle(self):
        try:
            self.server.start()
            time.sleep(1)

            for round_num in range(1, TOTAL_ROUNDS + 1):
                self.current_round = round_num
                self._run_round(round_num)
                if self.status == "error":
                    return

            summary = (
                f"Red found {self.scores['red_successes']} vulnerabilities. "
                f"Blue patched {self.scores['blue_patches']}. "
                f"App is {self.scores['hardened_pct']}% hardened."
            )
            self.broadcaster.emit("battle_complete", {
                "summary": summary,
                "final_scores": self.scores,
            })
            self.status = "complete"

        except Exception as e:
            self.broadcaster.emit("error", {"message": str(e), "round": self.current_round})
            self.status = "error"
        finally:
            self.server.stop()

    def _run_round(self, round_num):
        emit = self.broadcaster.emit
        emit("round_start", {"round": round_num, "total": TOTAL_ROUNDS})

        app_source = self._read_app_source()
        self._previous_app_source = app_source

        # --- RED AGENT ---
        emit("red_thinking", {"message": f"Round {round_num}: Analyzing code for vulnerabilities..."})
        try:
            red_result = call_red_agent(app_source, self.history)
        except Exception as e:
            emit("error", {"message": f"Red agent failed: {e}", "round": round_num})
            return

        curl_cmd = red_result.get("curl_command", "")
        emit("red_attack", {
            "curl_cmd": curl_cmd,
            "vuln_type": red_result.get("vulnerability_type", "Unknown"),
            "explanation": red_result.get("explanation", ""),
        })

        # Execute attack
        attack_response = self._execute_attack(curl_cmd)

        # Check if target app crashed during attack
        if not self.server.is_running:
            emit("error", {"message": "Target app crashed during attack, restarting", "round": round_num})
            try:
                self.server.start()
            except RuntimeError:
                pass

        # Judge success
        try:
            judge_result = call_red_judge(red_result, attack_response["body"], attack_response["status_code"])
            attack_success = judge_result.get("success", False)
        except Exception:
            attack_success = attack_response["status_code"] == 200

        emit("attack_result", {
            "status_code": attack_response["status_code"],
            "response_body": attack_response["body"][:500],
            "success": attack_success,
        })

        if attack_success:
            self.scores["red_successes"] += 1

        # --- BLUE AGENT ---
        emit("blue_thinking", {"message": f"Round {round_num}: Diagnosing vulnerability..."})
        try:
            blue_result = call_blue_agent(app_source, red_result, attack_response, self.history)
        except Exception as e:
            emit("error", {"message": f"Blue agent failed: {e}", "round": round_num})
            self._record_round(round_num, red_result, attack_response, attack_success, {})
            return

        new_source = blue_result.get("file_content", "")
        patch_valid = bool(new_source) and self._validate_python(new_source)
        diff = self._compute_diff(app_source, new_source) if patch_valid else "INVALID PYTHON - patch rejected"

        emit("blue_patch", {
            "diff": diff,
            "explanation": blue_result.get("explanation", ""),
            "vuln_fixed": blue_result.get("vuln_fixed", ""),
        })

        if patch_valid:
            self._write_app_source(new_source)
            emit("server_restart", {"status": "restarting"})
            try:
                self.server.restart()
                emit("server_ready", {"status": "up"})
                self.scores["blue_patches"] += 1
            except RuntimeError:
                emit("error", {"message": "Server failed to restart, rolling back", "round": round_num})
                self._write_app_source(self._previous_app_source)
                try:
                    self.server.restart()
                    emit("server_ready", {"status": "up (rolled back)"})
                except RuntimeError:
                    emit("battle_complete", {
                        "summary": "Battle halted: server unrecoverable after rollback.",
                        "final_scores": self.scores,
                    })
                    self.status = "error"
                    return

        self.scores["rounds_played"] = round_num
        self.scores["hardened_pct"] = int(
            (self.scores["blue_patches"] / round_num) * 100
        )

        emit("round_complete", {"round": round_num, "scores": dict(self.scores)})
        self._record_round(round_num, red_result, attack_response, attack_success, blue_result)

    def _record_round(self, round_num, red_result, attack_response, attack_success, blue_result):
        self.history.append({
            "round": round_num,
            "red_attack": {
                "curl_command": red_result.get("curl_command", ""),
                "vulnerability_type": red_result.get("vulnerability_type", ""),
                "explanation": red_result.get("explanation", ""),
            },
            "attack_response": {
                "status_code": attack_response.get("status_code", 0),
                "body": attack_response.get("body", "")[:500],
            },
            "attack_success": attack_success,
            "blue_patch": {
                "explanation": blue_result.get("explanation", ""),
                "vuln_fixed": blue_result.get("vuln_fixed", ""),
            },
        })
```

- [ ] **Step 2: Commit**

```bash
git add orchestrator/orchestrator.py
git commit -m "feat: battle orchestrator with 5-round loop, scoring, rollback"
```

---

## Task 8: Dashboard Server (Flask + SSE)

**Files:**
- Create: `dashboard_server.py`

Flask app on port 5001 that serves the dashboard, exposes SSE endpoint, and manages the orchestrator.

- [ ] **Step 1: Write dashboard_server.py**

```python
import atexit
import json
import os
import signal
import sys

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, send_from_directory

from orchestrator.events import EventBroadcaster
from orchestrator.orchestrator import BattleOrchestrator
from orchestrator.server_manager import ServerManager

load_dotenv()

if not os.environ.get("MINIMAX_API_KEY"):
    print("ERROR: MINIMAX_API_KEY not set. Copy .env.example to .env and add your key.")
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


@app.route("/start", methods=["POST"])
def start_battle():
    if orchestrator.status == "running":
        return jsonify({"error": "Battle already running"}), 409
    orchestrator.start()
    return jsonify({"message": "Battle started"})


@app.route("/state")
def state():
    return jsonify(orchestrator.get_state())


def cleanup():
    server_manager.stop()

atexit.register(cleanup)
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))


if __name__ == "__main__":
    app.run(port=5001, debug=False, threaded=True)
```

- [ ] **Step 2: Commit**

```bash
git add dashboard_server.py
git commit -m "feat: dashboard server with SSE, /start, /state endpoints"
```

---

## Task 9: Dashboard UI (HTML + CSS + JS)

**Files:**
- Create: `dashboard/index.html`
- Create: `dashboard/style.css`

The two-column battle view dashboard. Dark OLED theme, JetBrains Mono + IBM Plex Sans, SSE-powered live updates. All dynamic content uses safe DOM methods (textContent, createElement) — no innerHTML with untrusted data.

- [ ] **Step 1: Write dashboard/style.css**

Full CSS file implementing the dark OLED design system with JetBrains Mono + IBM Plex Sans typography, red/blue color scheme, and all component styles (header, scoreboard, timeline, battle grid, log entries, code blocks, diffs, badges, spinners, start button, overlay). Use the exact color values from the spec: `--bg: #020617`, `--panel: #0F172A`, `--border: #1E293B`, `--red: #EF4444`, `--blue: #3B82F6`, `--green: #22C55E`, `--yellow: #EAB308`.

The CSS is large (~200 lines) — write it directly using the color tokens and component patterns established during brainstorming. Reference the mockup at `.superpowers/brainstorm/95191-1773948212/dashboard-layout.html` for the exact class names and structure.

- [ ] **Step 2: Write dashboard/index.html**

Full HTML file with embedded JavaScript. All dynamic content uses **safe DOM methods** (`textContent`, `createElement`, `appendChild`) — no innerHTML with untrusted data.

Structure: header bar, scoreboard (3 items), timeline container, start button, two-column battle grid (red panel left, blue panel right), battle-complete overlay.

JavaScript functions (all use safe DOM manipulation):
- `startBattle()` — POST /start, disable button, call connectSSE()
- `connectSSE()` — EventSource on /events, parse JSON, dispatch to handleEvent()
- `handleEvent(event)` — switch on event.type from parsed JSON data
- `showSpinner(panel, msg)` — create div.spinner with textContent
- `removeSpinner(panel)` — remove by class query
- `addRedEntry(event)` — createElement for round badge, vuln type span, explanation p, code block pre (all via textContent)
- `updateLastRedResult(event)` — append SUCCESS/BLOCKED badge span
- `addBlueEntry(event)` — createElement for patch entry, call formatDiffSafe() for diff display
- `formatDiffSafe(diffStr)` — split by newlines, create span per line with .diff-add/.diff-del class, set textContent per line, return DocumentFragment
- `addErrorEntry(event)` — create error badge with textContent
- `updateScores(scores)` — set textContent on score number elements
- `updateTimeline()` — clear and rebuild dot elements for current round
- `rebuildFromState(state)` — fetch /state on load and reconnect, replay into UI

On page load: fetch /state, if running then connectSSE() and rebuild UI

- [ ] **Step 3: Commit**

```bash
git add dashboard/index.html dashboard/style.css
git commit -m "feat: live battle dashboard with dark theme, SSE, two-column layout"
```

---

## Task 10: Startup Script

**Files:**
- Create: `run.sh`

One-command startup: validates env, starts dashboard server (which manages target app).

- [ ] **Step 1: Write run.sh**

```bash
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

if lsof -i :5000 -sTCP:LISTEN -t >/dev/null 2>&1; then
  echo "ERROR: Port 5000 is already in use"
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
echo "  ║   Target:    http://localhost:5000     ║"
echo "  ╚═══════════════════════════════════════╝"
echo ""

if command -v open &> /dev/null; then
  open http://localhost:5001
fi

python dashboard_server.py
```

- [ ] **Step 2: Make executable**

Run: `chmod +x run.sh`

- [ ] **Step 3: Commit**

```bash
git add run.sh
git commit -m "feat: one-command startup script with validation"
```

---

## Task 11: Integration Smoke Test

Verify the full system works end-to-end.

- [ ] **Step 1: Create .env with real API key**

Copy `.env.example` to `.env` and add a real MiniMax API key.

- [ ] **Step 2: Run the system**

Run: `./run.sh`

- [ ] **Step 3: Verify in browser**

1. Open http://localhost:5001
2. Click "START BATTLE"
3. Watch Red and Blue alternate for 5 rounds
4. Verify: scoreboard updates, timeline progresses, code diffs appear, final overlay shows

- [ ] **Step 4: Fix any issues found**

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "fix: integration fixes from smoke test"
```
