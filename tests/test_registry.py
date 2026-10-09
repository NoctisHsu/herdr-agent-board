import os
import tempfile
import unittest
from unittest import mock

from herdr_agent_board import registry
from herdr_agent_board.model import Agent


class HookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.tmp.name, "HERDR_PANE_ID": "w1:p1"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_records_started_agent(self):
        child = Agent(pane="w2:p1", kind="claude", status="idle", session="s2")
        payload = {"tool_name": "Bash", "session_id": "s1", "tool_input": {"command": "herdr agent start worker --pane $P"}}
        with mock.patch.object(registry.herdr, "get_agent", return_value=child) as get:
            self.assertEqual(registry.record_from_hook(payload), 1)
        get.assert_called_once_with("worker")
        rec = registry.load()[0]
        self.assertEqual((rec["parent_session"], rec["parent_pane"], rec["pane"], rec["session"]), ("s1", "w1:p1", "w2:p1", "s2"))

    def test_ignores_other_commands(self):
        payload = {"tool_name": "Bash", "tool_input": {"command": "grep 'herdr agent start worker' x"}}
        with mock.patch.object(registry.herdr, "get_agent") as get:
            self.assertEqual(registry.record_from_hook(payload), 0)
        get.assert_not_called()
        self.assertEqual(registry.load(), [])


if __name__ == "__main__":
    unittest.main()
