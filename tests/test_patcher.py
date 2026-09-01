"""Unit tests for Patcher and SecretScrubber."""

import pytest
from src.engine.patcher import Patcher, SecretScrubber
from src.parser.ast_mapper import AstScope
from src.parser.log_parser import FailureReport


def test_secret_scrubber_redacts_tokens():
    raw_prompt = (
        "Error in CI runner using token ghp_123456789012345678901234567890123456 "
        "and key AIzaSyA1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6Q and Authorization: Bearer eyJhbGciOi.eyJzdWIiOi.signature"
    )
    scrubbed = SecretScrubber.scrub(raw_prompt)

    assert "ghp_" not in scrubbed
    assert "AIzaSy" not in scrubbed
    assert "eyJ" not in scrubbed
    assert "[REDACTED_SECRET]" in scrubbed


def test_patcher_diff_normalization():
    patcher = Patcher()
    # Without leading --- a/
    raw_diff = "@@ -1,1 +1,1 @@\n-old\n+new"
    norm = patcher._normalize_diff(raw_diff, "src/foo.py")

    assert norm.startswith("--- a/src/foo.py\n+++ b/src/foo.py")


def test_heuristic_fallback_generation():
    patcher = Patcher(api_key=None)  # Forces fallback
    failure = FailureReport(
        failing_file="src/calculator.py",
        failing_line=2,
        exception_type="ZeroDivisionError",
        exception_message="division by zero",
    )
    scope = AstScope(
        file_path="src/calculator.py",
        target_line=2,
        scope_type="function",
        scope_name="divide",
        start_line=1,
        end_line=2,
        full_source="def divide(x, y):\n    return x / y\n",
    )

    candidate = patcher.generate_patch(failure, scope)
    assert candidate.file_path == "src/calculator.py"
    assert "--- a/src/calculator.py" in candidate.unified_diff
    assert "if y != 0 else 0" in candidate.unified_diff
