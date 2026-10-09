import json
import os
import tempfile
import unittest

from herdr_agent_board.adapters import ClaudeAdapter, CodexAdapter, TranscriptReader


def tool_use(use_id, name, args, ts="2026-01-01T00:00:00Z"):
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {"content": [{"type": "tool_use", "id": use_id, "name": name, "input": args}]},
    }


def tool_result(use_id, content="", result=None, is_error=False):
    obj = {
        "type": "user",
        "timestamp": "2026-01-01T00:00:01Z",
        "message": {"content": [{"type": "tool_result", "tool_use_id": use_id, "content": content, "is_error": is_error}]},
    }
    if result is not None:
        obj["toolUseResult"] = result
    return obj


def feed(adapter, *objs):
    for obj in objs:
        adapter.feed(obj)
    return adapter.state


class ClaudeTasksTest(unittest.TestCase):
    def test_create_update_delete(self):
        state = feed(
            ClaudeAdapter(),
            tool_use("u1", "TaskCreate", {"subject": "Write parser", "activeForm": "Writing parser"}),
            tool_result("u1", "Task #1 created successfully: Write parser", {"task": {"id": "1"}}),
            tool_use("u2", "TaskCreate", {"subject": "Write tests"}),
            tool_result("u2", "Task #2 created successfully: Write tests"),
            tool_use("u3", "TaskUpdate", {"taskId": "1", "status": "in_progress"}),
            tool_use("u4", "TaskUpdate", {"taskId": "2", "status": "deleted"}),
        )
        self.assertEqual(list(state.tasks), ["1"])
        self.assertEqual(state.tasks["1"].status, "in_progress")
        self.assertEqual(state.tasks["1"].active_form, "Writing parser")

    def test_todowrite_replaces_list(self):
        todos = [{"content": "a", "status": "completed"}, {"content": "b", "status": "pending"}]
        state = feed(ClaudeAdapter(), tool_use("u1", "TodoWrite", {"todos": todos}))
        self.assertEqual([t.title for t in state.tasks.values()], ["a", "b"])
        state = feed(ClaudeAdapter(), tool_use("u1", "TodoWrite", {"todos": todos}), tool_use("u2", "TodoWrite", {"todos": todos[:1]}))
        self.assertEqual(len(state.tasks), 1)

    def test_sidechain_lines_are_ignored(self):
        line = tool_use("u1", "TodoWrite", {"todos": [{"content": "x", "status": "pending"}]})
        line["isSidechain"] = True
        self.assertEqual(feed(ClaudeAdapter(), line).tasks, {})


class ClaudeSubagentTest(unittest.TestCase):
    def test_sync_subagent_completes_on_result(self):
        state = feed(
            ClaudeAdapter(),
            tool_use("a1", "Agent", {"description": "Find callers", "subagent_type": "Explore"}),
            tool_result("a1", "done"),
        )
        self.assertEqual(state.subagents["a1"].status, "completed")

    def test_background_subagent_waits_for_notification(self):
        adapter = ClaudeAdapter()
        state = feed(
            adapter,
            tool_use("a1", "Agent", {"description": "Find callers"}),
            tool_result("a1", "launched", {"status": "async_launched"}),
        )
        self.assertEqual(state.subagents["a1"].status, "running")
        feed(
            adapter,
            {
                "type": "queue-operation",
                "content": "<task-notification>\n<task-id>x</task-id>\n<tool-use-id>a1</tool-use-id>\n"
                "<status>completed</status>\n</task-notification>",
            },
        )
        self.assertEqual(state.subagents["a1"].status, "completed")


class ClaudeHerdrTest(unittest.TestCase):
    def test_start_reads_session_from_reply(self):
        reply = json.dumps(
            {
                "id": "cli:agent:start",
                "result": {"agent": {"agent_session": {"kind": "id", "value": "child-1"}, "pane_id": "w1:p2"}},
            }
        )
        state = feed(
            ClaudeAdapter(),
            tool_use("b1", "Bash", {"command": "herdr agent start worker --kind claude --pane w1:p2"}),
            tool_result("b1", result={"stdout": reply}),
        )
        self.assertEqual(len(state.dispatches), 1)
        d = state.dispatches[0]
        self.assertEqual((d.name, d.pane, d.session), ("worker", "w1:p2", "child-1"))

    def test_start_with_quoted_name_and_variable_pane(self):
        state = feed(
            ClaudeAdapter(),
            tool_use("b1", "Bash", {"command": 'P=$(herdr pane split --current); herdr agent start "worker" --pane $P'}),
            tool_result("b1", result={"stdout": '"agent_status":"idle"'}),
        )
        d = state.dispatches[0]
        self.assertEqual((d.name, d.pane, d.session), ("worker", None, None))

    def test_quoted_mentions_are_not_dispatches(self):
        state = feed(
            ClaudeAdapter(),
            tool_use("b1", "Bash", {"command": "grep -l 'herdr agent start worker' *.jsonl; herdr agent list"}),
            tool_result("b1", result={"stdout": '{"id":"cli:agent:list","agent_session":{"value":"other"}}'}),
        )
        self.assertEqual(state.dispatches, [])

    def test_unrelated_replies_do_not_leak_sessions(self):
        state = feed(
            ClaudeAdapter(),
            tool_use("b1", "Bash", {"command": "herdr agent start worker --pane $P | grep -o status; herdr agent list"}),
            tool_result("b1", result={"stdout": '{"id":"cli:agent:list","agent_session":{"value":"other"},"pane_id":"w9:p9"}'}),
        )
        d = state.dispatches[0]
        self.assertEqual((d.pane, d.session), (None, None))

    def test_prompt_and_rename(self):
        state = feed(
            ClaudeAdapter(),
            tool_use("b1", "Bash", {"command": "herdr agent prompt worker 'Run the tests' --wait"}),
            tool_use("b2", "Bash", {"command": "herdr agent rename worker worker-2 >/dev/null"}),
            tool_use("b3", "Bash", {"command": "herdr agent prompt $a 'broadcast'"}),
        )
        self.assertEqual([(p.target, p.text) for p in state.prompts], [("worker", "Run the tests")])
        self.assertEqual(state.renames, [("worker", "worker-2")])


class ClaudeRequestTest(unittest.TestCase):
    def user(self, content, **extra):
        obj = {"type": "user", "timestamp": "2026-01-01T00:00:00Z", "message": {"content": content}}
        obj.update(extra)
        return obj

    def test_keeps_last_typed_request(self):
        state = feed(
            ClaudeAdapter(),
            self.user("Write the parser"),
            tool_result("u1", "ok"),
            self.user([{"type": "text", "text": "Now add tests"}]),
        )
        self.assertEqual(state.last_request.text, "Now add tests")

    def test_ignores_markup_meta_and_interrupts(self):
        state = feed(
            ClaudeAdapter(),
            self.user("Ship it"),
            self.user("<bash-input>ls</bash-input>"),
            self.user("<task-notification>x</task-notification>"),
            self.user("# Skill body", isMeta=True),
            self.user("[Request interrupted by user]"),
        )
        self.assertEqual(state.last_request.text, "Ship it")

    def test_slash_command_becomes_command_text(self):
        state = feed(
            ClaudeAdapter(),
            self.user("<command-message>x</command-message>\n<command-name>/review</command-name>\n<command-args>42</command-args>"),
        )
        self.assertEqual(state.last_request.text, "/review 42")


class CodexTest(unittest.TestCase):
    def test_user_message_is_request(self):
        msg = {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Fix the build"}]}}
        ctx = {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "<environment_context>x</environment_context>"}]}}
        state = feed(CodexAdapter(), msg, ctx)
        self.assertEqual(state.last_request.text, "Fix the build")

    def test_update_plan_replaces_list(self):
        plan = {"plan": [{"step": "Read diff", "status": "completed"}, {"step": "Report", "status": "in_progress"}]}
        line = {"type": "response_item", "payload": {"type": "function_call", "name": "update_plan", "arguments": json.dumps(plan)}}
        state = feed(CodexAdapter(), line)
        self.assertEqual([(t.title, t.status) for t in state.tasks.values()], [("Read diff", "completed"), ("Report", "in_progress")])


class ReaderTest(unittest.TestCase):
    def test_reads_only_appended_complete_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.jsonl")
            first = json.dumps(tool_use("u1", "TodoWrite", {"todos": [{"content": "a", "status": "pending"}]}))
            second = json.dumps(tool_use("u2", "TodoWrite", {"todos": [{"content": "b", "status": "pending"}]}))
            with open(path, "w") as fh:
                fh.write(first + "\n" + second[:10])
            reader = TranscriptReader(path, ClaudeAdapter())
            self.assertTrue(reader.poll())
            self.assertEqual([t.title for t in reader.state.tasks.values()], ["a"])
            self.assertFalse(reader.poll())
            with open(path, "a") as fh:
                fh.write(second[10:] + "\n")
            reader.poll()
            self.assertEqual([t.title for t in reader.state.tasks.values()], ["b"])


if __name__ == "__main__":
    unittest.main()
