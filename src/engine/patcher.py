"""Gemini GenAI client module for synthesizing structured, verified code patches with zero-egress scrubbing."""

import json
import os
import re
from typing import Optional
from pydantic import BaseModel, Field

from src.parser.ast_mapper import AstScope
from src.parser.log_parser import FailureReport


class PatchCandidate(BaseModel):
    """Structured representation of a generated code patch."""
    explanation: str = Field(description="Clear explanation of root cause and fix logic.")
    file_path: str = Field(description="Relative path of the file being patched.")
    unified_diff: str = Field(description="Standard Git unified diff (with --- a/ and +++ b/ headers).")


class SecretScrubber:
    """Zero-egress privacy filter to scrub credentials and tokens before LLM dispatch."""

    PATTERNS = [
        # GitHub Personal Access Tokens
        re.compile(r"ghp_[a-zA-Z0-9]{36,}", re.IGNORECASE),
        re.compile(r"github_pat_[a-zA-Z0-9_]{82,}", re.IGNORECASE),
        # Google API Keys
        re.compile(r"AIzaSy[a-zA-Z0-9_\-]{33}", re.IGNORECASE),
        # Generic Secret/Private Keys (OpenAI, Slack, Stripe, etc)
        re.compile(r"(?:sk-|xoxb-|xoxp-|rk_live_)[a-zA-Z0-9]{20,}", re.IGNORECASE),
        # JWT Bearer tokens
        re.compile(r"eyJ[a-zA-Z0-9_\-]+\.eyJ[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_\-]+", re.IGNORECASE),
        # Authorization Headers
        re.compile(r"(?:Authorization:\s*)?Bearer\s+[a-zA-Z0-9_\-.~+/=]+", re.IGNORECASE),
        # Password / secret assignments in logs
        re.compile(r'(?:password|secret|token|api_key)\s*[:=]\s*["\'][^"\']+["\']', re.IGNORECASE),
    ]

    @classmethod
    def scrub(cls, text: str) -> str:
        """Sanitizes text by replacing all matched credential tokens with redacting placeholders."""
        if not text:
            return ""
        sanitized = text
        for pattern in cls.PATTERNS:
            sanitized = pattern.sub("[REDACTED_SECRET]", sanitized)
        return sanitized


class Patcher:
    """Interacts with Google GenAI Gemini API to generate minimal, verified diffs."""

    SYSTEM_INSTRUCTION = (
        "You are AutoFix-CI, an expert Principal Systems & Software Engineer. "
        "Your task is to analyze a continuous integration failure (stack trace + AST code scope) "
        "and generate a minimal, surgically precise code patch in valid Git unified diff format. "
        "CRITICAL RULES:\n"
        "1. Do not refactor unrelated code. Only fix the exact bug causing the test failure.\n"
        "2. Output MUST be valid JSON matching the schema: "
        "{'explanation': str, 'file_path': str, 'unified_diff': str}.\n"
        "3. The 'unified_diff' MUST begin with '--- a/<file_path>\\n+++ b/<file_path>\\n@@ ... @@' "
        "and apply cleanly with `git apply` or patch utility.\n"
        "4. Preserve existing style, indentation, type hints, and comments."
    )

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: str = "gemini-2.5-flash",
    ):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self.model_name = model_name
        self.client = None

        if self.api_key:
            try:
                from google import genai
                self.client = genai.Client(api_key=self.api_key)
            except Exception as exc:
                self.client = None

    def generate_patch(
        self,
        failure: FailureReport,
        scope: AstScope,
        retry_context: Optional[str] = None,
    ) -> PatchCandidate:
        """Generates a structured PatchCandidate using Gemini API with zero-egress scrubbing."""
        prompt = self._build_prompt(failure, scope, retry_context)
        sanitized_prompt = SecretScrubber.scrub(prompt)

        # If no API client or key provided, fallback to deterministic heuristic patch or mock for testing
        if not self.client:
            return self._heuristic_fallback(failure, scope)

        try:
            from google.genai import types

            response = self.client.models.generate_content(
                model=self.model_name,
                contents=sanitized_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=self.SYSTEM_INSTRUCTION,
                    response_mime_type="application/json",
                    temperature=0.2,
                ),
            )

            raw_text = response.text.strip()
            return self._parse_json_response(raw_text, scope.file_path)

        except Exception as exc:
            # Fallback gracefully if API request fails
            return self._heuristic_fallback(failure, scope, str(exc))

    def _build_prompt(
        self,
        failure: FailureReport,
        scope: AstScope,
        retry_context: Optional[str] = None,
    ) -> str:
        """Builds structured prompt payload."""
        prompt_parts = [
            f"### TARGET REPOSITORY FILE: {scope.file_path}",
            f"### FAILING LINE: {failure.failing_line}",
            f"### EXCEPTION: {failure.exception_type}: {failure.exception_message}",
            f"### ENCLOSING AST SCOPE: {scope.scope_type.upper()} '{scope.scope_name}' (Lines {scope.start_line}-{scope.end_line})",
        ]

        if scope.enclosing_class:
            prompt_parts.append(f"### PARENT CLASS: {scope.enclosing_class}")

        if scope.imports:
            prompt_parts.append("### RELEVANT IMPORTS:\n" + "\n".join(scope.imports))

        prompt_parts.append(f"### RELEVANT CODE CONTEXT (with line numbers):\n{scope.code_context}")
        prompt_parts.append(f"### FULL RAW FILE CONTENT:\n```python\n{scope.full_source}\n```")
        prompt_parts.append(f"### CI TRACEBACK SNIPPET:\n{failure.raw_traceback}")

        if retry_context:
            prompt_parts.append(
                f"\n### PREVIOUS PATCH ATTEMPT FAILED IN SANDBOX VALIDATION:\n{retry_context}\n"
                "Please analyze why the previous patch failed and provide an alternative correct patch."
            )

        prompt_parts.append(
            "\nRespond ONLY with valid JSON having the keys: 'explanation', 'file_path', 'unified_diff'."
        )

        return "\n\n".join(prompt_parts)

    def _parse_json_response(self, raw_text: str, default_file: str) -> PatchCandidate:
        """Parses model response into validated PatchCandidate object."""
        # Clean markdown codeblocks if model wrapped JSON in ```json ... ```
        cleaned = raw_text
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            # Fallback regex extraction for json object
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
            else:
                raise ValueError(f"Failed to parse JSON response from LLM: {raw_text[:200]}")

        explanation = data.get("explanation", "Patch synthesized by AutoFix-CI")
        file_path = data.get("file_path", default_file)
        raw_diff = data.get("unified_diff", "")

        # Validate and normalize unified diff headers
        normalized_diff = self._normalize_diff(raw_diff, file_path)

        return PatchCandidate(
            explanation=explanation,
            file_path=file_path,
            unified_diff=normalized_diff,
        )

    def _normalize_diff(self, diff: str, file_path: str) -> str:
        """Ensures diff starts with valid git unified diff header '--- a/... +++ b/...'."""
        diff = diff.strip()
        header_a = f"--- a/{file_path}"
        header_b = f"+++ b/{file_path}"

        if not diff.startswith("---"):
            diff = f"{header_a}\n{header_b}\n" + diff
        elif not ("--- a/" in diff and "+++ b/" in diff):
            # Replace raw filenames with git standard a/ and b/ prefix
            lines = diff.splitlines()
            if len(lines) >= 2 and lines[0].startswith("---") and lines[1].startswith("+++"):
                lines[0] = header_a
                lines[1] = header_b
                diff = "\n".join(lines)

        return diff + "\n"

    def _heuristic_fallback(
        self,
        failure: FailureReport,
        scope: AstScope,
        error_detail: Optional[str] = None,
    ) -> PatchCandidate:
        """Generates a deterministic fallback candidate patch for common arithmetic and type edge cases."""
        explanation = (
            f"AutoFix-CI heuristic fallback for {failure.exception_type} at line {failure.failing_line}. "
            + (f"Note: GenAI API unavailable: {error_detail}" if error_detail else "")
        )

        source_lines = scope.full_source.splitlines()
        target_idx = failure.failing_line - 1

        if 0 <= target_idx < len(source_lines):
            original_line = source_lines[target_idx]
            patched_line = original_line

            # Heuristic 1: Division by zero -> guard divisor
            if "ZeroDivisionError" in failure.exception_type and "/" in original_line:
                if " / " in original_line:
                    # Example: a / b -> a / b if b != 0 else 0
                    parts = original_line.split(" / ", 1)
                    denom = parts[1].strip().split()[0].rstrip(",):;")
                    indent = original_line[:len(original_line) - len(original_line.lstrip())]
                    patched_line = f"{original_line} if {denom} != 0 else 0"

            # Heuristic 2: Off by one / wrong operator
            elif "AssertionError" in failure.exception_type:
                if " - " in original_line:
                    patched_line = original_line.replace(" - ", " + ")
                elif " + " in original_line:
                    patched_line = original_line.replace(" + ", " - ")

            diff = (
                f"--- a/{scope.file_path}\n"
                f"+++ b/{scope.file_path}\n"
                f"@@ -{failure.failing_line},1 +{failure.failing_line},1 @@\n"
                f"-{original_line}\n"
                f"+{patched_line}\n"
            )
            return PatchCandidate(explanation=explanation, file_path=scope.file_path, unified_diff=diff)

        # Generic safe diff
        dummy_diff = (
            f"--- a/{scope.file_path}\n"
            f"+++ b/{scope.file_path}\n"
            f"@@ -1,1 +1,1 @@\n"
            f"# AutoFix-CI: Automated bug fix candidate\n"
        )
        return PatchCandidate(explanation=explanation, file_path=scope.file_path, unified_diff=dummy_diff)
