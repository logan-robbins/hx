import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import greet  # noqa: E402


class TestGreet(unittest.TestCase):
    def test_greets_by_name(self):
        self.assertEqual(greet.greet("World"), "Hello, World!")

    def test_cli_prints_one_line(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(greet.main(["World"]), 0)
        self.assertEqual(out.getvalue(), "Hello, World!\n")


if __name__ == "__main__":
    unittest.main()
