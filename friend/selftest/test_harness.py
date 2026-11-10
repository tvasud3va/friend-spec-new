#!/usr/bin/env python3
"""Self-tests for the harness, without any agent.
Run: python3 harness/selftest/test_harness.py [-v] [-k pattern]
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HARNESS = Path(__file__).resolve().parents[1]


class Hook(unittest.TestCase):
    """The policy hook in isolation (no agent)."""

    def decide(self, tool: str, inp: dict, root: Path) -> int:
        cmd = [sys.executable, str(HARNESS / "policy_hook.py"), "--root", str(root), "--label", "t", "--events", str(root / "ev.jsonl"),
               "--write", "clients/py", "--read", "spec", "--exec", "python3 run.py --target py", "--deny", "friend", "--deny", "tests"]
        return subprocess.run(cmd, input=json.dumps({"tool_name": tool, "tool_input": inp}), capture_output=True, text=True).returncode

    def test_decisions(self):
        root = Path(tempfile.mkdtemp())
        cases = [
            ("Write", {"file_path": "clients/py/a.py"}, 0), ("Write", {"file_path": "tests/x.py"}, 2),
            ("Write", {"file_path": str(root / "clients/py/../../tests/x.py")}, 2),
            ("Read", {"file_path": "spec/a.md"}, 0), ("Read", {"file_path": "tests/x.py"}, 2), ("Grep", {"pattern": "x"}, 2),
            ("Glob", {"pattern": "spec/**/*.md"}, 0), ("Glob", {"pattern": "tests/**"}, 2), ("Glob", {"pattern": "**/*"}, 2),
            ("Bash", {"command": "grep -n x clients/py/friend/client.py"}, 0),       # nested name is not the denied top-level `friend`
            ("Bash", {"command": "friend generate"}, 2),
            ("Bash", {"command": "cat tests/x.py"}, 2), ("Bash", {"command": "python3 run.py --target py --only a"}, 0),
            ("Bash", {"command": "python3 run.py --target py; cat tests/x"}, 2), ("Bash", {"command": "cat ../secret"}, 2),
            ("Bash", {"command": "cat /etc/passwd"}, 2), ("WebFetch", {"url": "http://x"}, 2),
            ("Bash", {"command": "python3 -c print(1)"}, 2), ("Bash", {"command": "node -e 1"}, 2),   # arbitrary code
            ("Bash", {"command": "/usr/bin/python3.12 x.py"}, 2), ("Bash", {"command": "sh -c ls"}, 2),
            ("Bash", {"command": "find clients -exec cat {} +"}, 2),
        ]
        slug = re.sub(r"[^A-Za-z0-9]", "-", str(root.resolve()))
        cases += [("Read", {"file_path": f"/private/tmp/claude-501/{slug}/s1/tasks/b1.output"}, 0),   # own background output
                  ("Read", {"file_path": "/private/tmp/claude-501/-Users-someone-else/s1/tasks/b1.output"}, 2)]
        for tool, inp, want in cases:
            self.assertEqual(self.decide(tool, inp, root), want, f"{tool} {inp}")
        (root / "ev.jsonl").unlink(missing_ok=True)
        self.assertEqual(self.decide("Read", {"file_path": "tests/x"}, root), 2)
        self.assertIn("policy.denied", (root / "ev.jsonl").read_text())

    def test_fails_closed_on_garbage(self):
        root = Path(tempfile.mkdtemp())
        r = subprocess.run([sys.executable, str(HARNESS / "policy_hook.py"), "--root", str(root), "--label", "t", "--events", str(root / "e")],
                           input="not json", capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
