"""Unit tests for Patcher and SecretScrubber."""

import pytest
from src.engine.patcher import Patcher, SecretScrubber
from src.parser.ast_mapper import AstScope
from src.parser.log_parser import FailureReport


def test_secret_scrubber_redacts_tokens():
    mock_gh_token = "ghp_MOCK_TEST_TOKEN_REDACTED_FOR_SCANNER"
    mock_api_key = "AIzaSy_MOCK_TEST_KEY_REDACTED_FOR_SCANNER"
    raw_prompt = (
        f"Error in CI runner using token {mock_gh_token} "
        f"and key {mock_api_key} and Authorization: Bearer eyJhbGciOi.eyJzdWIiOi.signature"
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
