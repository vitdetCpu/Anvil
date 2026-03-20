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
