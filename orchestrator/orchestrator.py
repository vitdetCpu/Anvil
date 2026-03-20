import difflib
import os
import py_compile
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future
import requests

from orchestrator.agents import call_red_agent, call_blue_agent, call_red_judge, call_summary_agent
from orchestrator.curl_parser import parse_curl, CurlParseError
from orchestrator.events import EventBroadcaster
from orchestrator.server_manager import ServerManager

APP_PATH = os.path.join(os.path.dirname(__file__), "..", "target_app", "app.py")
TOTAL_ROUNDS = 3


class BattleOrchestrator:
    def __init__(self, broadcaster: EventBroadcaster, server_manager: ServerManager):
        self.broadcaster = broadcaster
        self.server = server_manager
        self.history = []
        self.scores = {"red_exploited": 0, "blue_defended": 0, "defended_pct": 0, "rounds_played": 0}
        self.status = "idle"
        self.current_round = 0
        self._previous_app_source = None
        self._original_app_source = None
        self._thread = None

    def start(self):
        """Start the battle in a background thread."""
        if self.status == "running":
            return False
        self.status = "running"
        self.broadcaster.reset()
        self.history.clear()
        self.scores = {"red_exploited": 0, "blue_defended": 0, "defended_pct": 0, "rounds_played": 0}
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
            self.server.stop()  # Clean up any leftover process
            self._original_app_source = self._read_app_source()
            self.server.start()
            time.sleep(1)

            for round_num in range(1, TOTAL_ROUNDS + 1):
                self.current_round = round_num
                self._run_round(round_num)
                if self.status == "error":
                    return

            # Recalculate final scores to catch any missed updates
            total = self.scores["red_exploited"] + self.scores["blue_defended"]
            if total > 0:
                self.scores["defended_pct"] = int((self.scores["blue_defended"] / total) * 100)
            self.scores["rounds_played"] = TOTAL_ROUNDS

            summary = (
                f"Red exploited {self.scores['red_exploited']} vulnerabilities. "
                f"Blue defended {self.scores['blue_defended']}. "
                f"{self.scores['defended_pct']}% of attacks were defended."
            )

            # Generate security recommendations
            recommendations = []
            remaining_risks = []
            try:
                final_source = self._read_app_source()
                summary_result, _ = call_summary_agent(
                    self._original_app_source, final_source, self.history
                )
                recommendations = summary_result.get("recommendations", [])
                remaining_risks = summary_result.get("remaining_risks", [])
            except Exception:
                pass  # Non-critical, proceed without recommendations

            self.broadcaster.emit("battle_complete", {
                "summary": summary,
                "final_scores": self.scores,
                "rounds": self.history,
                "recommendations": recommendations,
                "remaining_risks": remaining_risks,
            })
            self.status = "complete"

        except Exception as e:
            self.broadcaster.emit("error", {"message": str(e), "round": self.current_round})
            self.status = "error"
        finally:
            self.server.stop()

    def _run_round(self, round_num):
        emit = self.broadcaster.emit
        app_source = self._read_app_source()
        emit("round_start", {"round": round_num, "total": TOTAL_ROUNDS, "source": app_source})
        self._previous_app_source = app_source

        # --- RED AGENT ---
        emit("red_thinking", {"message": f"Round {round_num}: Analyzing code for vulnerabilities..."})
        try:
            red_result, red_thinking = call_red_agent(
                app_source, self.history,
                on_thinking=lambda delta: emit("red_thinking_delta", {"delta": delta}),
                on_output_start=lambda: emit("red_generating", {"message": "Generating vulnerability exploit..."}),
            )
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

        # --- JUDGE + BLUE in parallel ---
        emit("blue_thinking", {"message": f"Round {round_num}: Diagnosing vulnerability..."})

        judge_evidence = ""
        attack_success = False
        judge_done = threading.Event()
        judge_data = {}

        def run_judge():
            nonlocal attack_success, judge_evidence
            try:
                judge_result, _ = call_red_judge(red_result, attack_response["body"], attack_response["status_code"])
                attack_success = judge_result.get("success", False)
                judge_evidence = judge_result.get("evidence", "")
                print(f"[JUDGE OK] Round {round_num}: success={attack_success}, evidence={judge_evidence[:80]}", flush=True)
            except Exception as e:
                print(f"[JUDGE ERROR] Round {round_num}: {e}", flush=True)
                attack_success = attack_response["status_code"] == 200
            judge_done.set()

        judge_thread = threading.Thread(target=run_judge, daemon=True)
        judge_thread.start()

        # Blue runs concurrently with judge
        try:
            blue_result, blue_thinking = call_blue_agent(
                app_source, red_result, attack_response, self.history,
                on_thinking=lambda delta: emit("blue_thinking_delta", {"delta": delta}),
                on_output_start=lambda: emit("blue_generating", {"message": "Generating fix..."}),
            )
        except Exception as e:
            emit("error", {"message": f"Blue agent failed: {e}", "round": round_num})
            judge_done.wait()
            if attack_success:
                self.scores["red_exploited"] += 1
            else:
                self.scores["blue_defended"] += 1
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

        # Wait for judge if not done yet, then show verdict
        judge_done.wait()

        if attack_success:
            self.scores["red_exploited"] += 1
        else:
            self.scores["blue_defended"] += 1

        emit("attack_result", {
            "round": round_num,
            "status_code": attack_response["status_code"],
            "response_body": attack_response["body"][:500],
            "success": attack_success,
            "evidence": judge_evidence,
        })

        if patch_valid:
            self._write_app_source(new_source)
            emit("server_restart", {"status": "restarting"})
            try:
                self.server.restart()
                emit("server_ready", {"status": "up"})
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
        self.scores["defended_pct"] = int(
            (self.scores["blue_defended"] / round_num) * 100
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
