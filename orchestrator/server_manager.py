import subprocess
import sys
import time
import requests


class ServerManager:
    """Manages the target Flask app as a subprocess."""

    def __init__(self, app_path="target_app/app.py", port=5050):
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
