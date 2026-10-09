import unittest
from datetime import datetime, timezone

from herdr_agent_board.board import link, subtree
from herdr_agent_board.herdr import to_agent
from herdr_agent_board.model import Agent, Dispatch, Prompt, SessionState, Task
from herdr_agent_board.render import board_lines, text_width, to_text, truncate


def agent(pane, session, name=None, **state):
    return Agent(pane=pane, kind="claude", status="idle", title=pane, name=name, session=session, state=SessionState(**state))


class LinkTest(unittest.TestCase):
    def test_links_by_session_pane_and_renamed_name(self):
        lead = agent(
            "w1:p1",
            "s1",
            dispatches=[
                Dispatch("a", session="s2"),
                Dispatch("b", pane="w3:p1"),
                Dispatch("c"),
            ],
            renames=[("c", "c-final")],
            prompts=[Prompt("c", "first"), Prompt("c-final", "second"), Prompt("b", "hi")],
        )
        a = agent("w2:p1", "s2", "a")
        b = agent("w3:p1", "s3", "b-renamed")
        c = agent("w4:p1", "s4", "c-final")
        loner = agent("w5:p1", "s5")
        roots = link([a, b, c, lead, loner], [])
        self.assertEqual([r.pane for r in roots], ["w1:p1", "w5:p1"])
        self.assertEqual([x.pane for x in lead.children], ["w2:p1", "w3:p1", "w4:p1"])
        self.assertEqual(c.last_prompt.text, "second")
        self.assertEqual(b.last_prompt.text, "hi")

    def test_registry_records_link_by_parent_session(self):
        lead = agent("w1:p1", "s1")
        child = agent("w2:p1", "s2", "worker")
        records = [{"parent_session": "s1", "name": "worker", "pane": "w2:p1", "session": "s2"}]
        roots = link([lead, child], records)
        self.assertEqual([r.pane for r in roots], ["w1:p1"])
        self.assertIs(subtree(roots, "w2:p1"), child)

    def test_cycles_do_not_hide_agents(self):
        a = agent("w1:p1", "s1", "a", dispatches=[Dispatch("b", session="s2")])
        b = agent("w2:p1", "s2", "b", dispatches=[Dispatch("a", session="s1")])
        roots = link([a, b], [])
        seen = []

        def walk(x):
            seen.append(x.pane)
            for c in x.children:
                walk(c)

        for r in roots:
            walk(r)
        self.assertEqual(sorted(seen), ["w1:p1", "w2:p1"])


class RenderTest(unittest.TestCase):
    def test_truncate_counts_wide_characters(self):
        line = [("", "任務清單 viewer")]
        cut = truncate(line, 6)
        self.assertEqual(to_text(cut, False), "任務…")
        self.assertLessEqual(text_width(to_text(cut, False)), 6)
        self.assertEqual(truncate(line, 40), line)

    def test_lists_latest_completed_tasks(self):
        # Completed in reverse creation order: t4 first, t0 last.
        tasks = {str(i): Task(str(i), "t%d" % i, "completed", done_seq=5 - i) for i in range(5)}
        tasks["9"] = Task("9", "current", "in_progress", "Doing current")
        a = agent("w1:p1", "s1", tasks=tasks)
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        text = [to_text(l, False) for l in board_lines([a], now, 3)]
        self.assertIn("  ✓ 2 earlier done", text)
        self.assertIn("  ✓ t0", text)
        self.assertNotIn("  ✓ t4", text)
        self.assertIn("  ◐ Doing current", text)
        text = [to_text(l, False) for l in board_lines([a], now, 0)]
        self.assertIn("  ✓ 5 done", text)
        text = [to_text(l, False) for l in board_lines([a], now, -1)]
        self.assertIn("  ✓ t4", text)
        self.assertFalse(any("earlier" in t for t in text))

    def test_request_shown_once_when_it_is_the_parent_prompt(self):
        child = agent("w2:p1", "s2", "worker", last_request=Prompt("", "Run the tests now"))
        child.last_prompt = Prompt("worker", "Run the tests now")
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        text = [to_text(l, False) for l in board_lines([child], now)]
        self.assertEqual(sum("Run the tests now" in t for t in text), 1)
        self.assertTrue(any(t.strip().startswith("»") for t in text))

    def test_title_glyph_is_stripped(self):
        raw = {"pane_id": "w1:p1", "agent": "claude", "terminal_title_stripped": "◑ Build board", "agent_session": {"value": "s"}}
        self.assertEqual(to_agent(raw).title, "Build board")


if __name__ == "__main__":
    unittest.main()
