import unittest

from herdr_agent_board.scroll import WHEEL_STEP, apply, parse_input, viewport


class ParseInputTest(unittest.TestCase):
    def test_sgr_wheel_events_scroll(self):
        self.assertEqual(parse_input("\x1b[<64;10;5M", 20), [-WHEEL_STEP])
        self.assertEqual(parse_input("\x1b[<65;10;5M\x1b[<65;10;5M", 20), [WHEEL_STEP, WHEEL_STEP])
        # Ctrl+wheel still scrolls; button releases and clicks do nothing.
        self.assertEqual(parse_input("\x1b[<81;1;1M", 20), [WHEEL_STEP])
        self.assertEqual(parse_input("\x1b[<0;3;4M\x1b[<0;3;4m", 20), [])

    def test_keys(self):
        self.assertEqual(parse_input("\x1b[A\x1b[B\x1bOA", 20), [-1, 1, -1])
        self.assertEqual(parse_input("\x1b[5~\x1b[6~", 20), [-20, 20])
        self.assertEqual(parse_input("jkgG d", 20), [1, -1, "top", "bottom", 20, 10])
        self.assertEqual(parse_input("x\x1b[C", 20), [])


class ViewportTest(unittest.TestCase):
    def test_fits_without_note(self):
        lines = ["a", "b"]
        self.assertEqual(viewport(lines, 5, 10), (lines, 0, ""))

    def test_clamps_and_reports_position(self):
        lines = [str(i) for i in range(30)]
        shown, offset, note = viewport(lines, apply(0, ["bottom"]), 10)
        self.assertEqual((shown[0], shown[-1], offset), ("20", "29", 20))
        self.assertIn("21-30 of 30", note)
        shown, offset, _ = viewport(lines, apply(5, [-WHEEL_STEP, -WHEEL_STEP]), 10)
        self.assertEqual((shown[0], offset), ("0", 0))


if __name__ == "__main__":
    unittest.main()
