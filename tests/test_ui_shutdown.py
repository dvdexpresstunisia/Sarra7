import threading
import unittest
from types import SimpleNamespace

import main
from ui import _emit_when_window_alive


class _DeletedSignal:
    def emit(self, *_args):
        raise RuntimeError("wrapped C/C++ object has been deleted")


class UIShutdownTests(unittest.TestCase):
    def test_deleted_window_signal_is_ignored_once(self):
        window = SimpleNamespace(_closed=False, _state_sig=_DeletedSignal())

        _emit_when_window_alive(window, "_state_sig", "LISTENING")
        _emit_when_window_alive(window, "_state_sig", "SLEEPING")

        self.assertTrue(window._closed)

    def test_application_close_stops_reconnect_loop(self):
        jarvis = main.JarvisLive.__new__(main.JarvisLive)
        jarvis._shutdown_requested = threading.Event()
        persisted = []
        jarvis._persist_session_memory = lambda: persisted.append(True)

        jarvis._on_application_close()

        self.assertTrue(jarvis._shutdown_requested.is_set())
        self.assertEqual(persisted, [True])


if __name__ == "__main__":
    unittest.main()
