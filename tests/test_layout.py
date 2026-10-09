import os
import subprocess
import tempfile
import unittest
from unittest import mock

from herdr_agent_board import config, layout
from herdr_agent_board.model import Agent


class ConfigTest(unittest.TestCase):
    def test_parse_and_defaults(self):
        parsed = config.parse('auto_open = false  # off\nmin_width = 120\ngit_command = "tig"\n[ignored]\n')
        self.assertEqual(parsed, {"auto_open": False, "min_width": 120, "git_command": "tig"})
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "config.toml"), "w") as fh:
                fh.write("min_width = 120\ndone_shown = -1\ngit_pane = 3\n")
            with mock.patch.dict(os.environ, {"HERDR_PLUGIN_CONFIG_DIR": tmp}):
                settings = config.load()
        self.assertEqual(settings["min_width"], 120)
        self.assertEqual(settings["done_shown"], -1)
        # Wrong type falls back to the default.
        self.assertIs(settings["git_pane"], True)


class HasBoardTest(unittest.TestCase):
    def test_only_counts_board_in_same_tab(self):
        panes = [{"tab_id": "w1:t2", "label": "Agent board"}, {"tab_id": "w1:t1", "label": None}]
        self.assertFalse(layout.has_board(panes, "w1:t1"))
        self.assertTrue(layout.has_board(panes, "w1:t2"))


class RepoResolverTest(unittest.TestCase):
    def test_start_directory_then_last_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, "repo")
            plain = os.path.join(tmp, "plain")
            os.makedirs(os.path.join(repo, "src"))
            os.makedirs(plain)
            subprocess.run(["git", "init", "-q", repo], check=True)
            repo = os.path.realpath(repo)

            in_repo = Agent(pane="w1:p1", kind="claude", status="idle", cwd=os.path.join(repo, "src"))
            resolver = layout.RepoResolver("w1:p1", get_agent=lambda _: in_repo)
            self.assertEqual(os.path.realpath(resolver.resolve()[0]), repo)

            outside = Agent(pane="w1:p1", kind="claude", status="idle", cwd=plain)
            resolver = layout.RepoResolver("w1:p1", get_agent=lambda _: outside)
            with mock.patch.object(resolver, "_last_path", return_value=os.path.join(repo, "src", "a.py")):
                self.assertEqual(os.path.realpath(resolver.resolve()[0]), repo)
            with mock.patch.object(resolver, "_last_path", return_value=None):
                found, why = resolver.resolve()
            self.assertIsNone(found)
            self.assertIn(plain, why)

    def test_waits_for_agent(self):
        resolver = layout.RepoResolver("w1:p9", get_agent=lambda _: None)
        self.assertEqual(resolver.resolve()[0], None)


if __name__ == "__main__":
    unittest.main()
