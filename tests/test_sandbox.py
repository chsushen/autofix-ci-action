"""Unit tests for the Sandbox Runner module."""

import tempfile
from pathlib import Path
import pytest

from src.engine.sandbox import SandboxRunner


def test_apply_and_revert_patch():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo_path = Path(tmp_dir)
        target_file = repo_path / "math_ops.py"
        original_code = "def divide(a, b):\n    return a / b\n"
        target_file.write_text(original_code, encoding="utf-8")

        diff = """--- a/math_ops.py
+++ b/math_ops.py
@@ -1,2 +1,2 @@
 def divide(a, b):
-    return a / b
+    return a / b if b != 0 else 0
"""
        sandbox = SandboxRunner(repo_dir=str(repo_path))

        # Apply patch
        ok, msg = sandbox.apply_patch(diff, "math_ops.py")
        assert ok is True
        patched_content = target_file.read_text(encoding="utf-8")
        assert "if b != 0 else 0" in patched_content

        # Revert patch
        revert_ok = sandbox.revert_patch("math_ops.py")
        assert revert_ok is True
        restored_content = target_file.read_text(encoding="utf-8")
        assert restored_content == original_code


def test_sandbox_run_successful_command():
    sandbox = SandboxRunner()
    result = sandbox.run_tests('python -c "print(\'OK\')"')

    assert result.success is True
    assert result.exit_code == 0
    assert "OK" in result.stdout


def test_sandbox_run_failing_command():
    sandbox = SandboxRunner()
    result = sandbox.run_tests('python -c "import sys; sys.exit(42)"')

    assert result.success is False
    assert result.exit_code == 42


def test_sandbox_timeout():
    sandbox = SandboxRunner(default_timeout=1)
    # Command sleeps for 5 seconds which exceeds 1 second timeout
    result = sandbox.run_tests("python -c \"import time; time.sleep(5)\"", timeout=1)

    assert result.success is False
    assert result.exit_code == 124
    assert "timed out" in result.stderr.lower()
