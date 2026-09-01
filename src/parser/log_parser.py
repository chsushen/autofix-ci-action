"""Log parser module for extracting failing files, line numbers, and tracebacks from CI logs."""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class StackFrame:
    file_path: str
    line_number: int
    function_name: Optional[str] = None
    code_snippet: Optional[str] = None


@dataclass
class FailureReport:
    failing_file: str
    failing_line: int
    exception_type: str
    exception_message: str
    stack_frames: List[StackFrame] = field(default_factory=list)
    raw_traceback: str = ""
    target_function: Optional[str] = None


class LogParser:
    """Extracts diagnostic failure data and stack trace frames from raw CI test logs."""

    # Matches: File "/path/to/file.py", line 42, in my_func
    TRACEBACK_FILE_RE = re.compile(
        r'File\s+["\'](?P<file>[^"\']+\.py)["\'],\s+line\s+(?P<line>\d+)(?:,\s+in\s+(?P<func>[^\n]+))?',
        re.MULTILINE,
    )

    # Matches pytest style: tests/test_foo.py:25: in test_method
    PYTEST_FRAME_RE = re.compile(
        r'^(?P<file>[a-zA-Z0-9_\-./]+\.py):(?P<line>\d+):\s+(?:in\s+(?P<func>[^\n]+))?',
        re.MULTILINE,
    )

    # Matches pytest failure header: tests/test_foo.py:25: AssertionError
    PYTEST_SUMMARY_LINE_RE = re.compile(
        r'^(?P<file>[a-zA-Z0-9_\-./]+\.py):(?P<line>\d+):\s+(?P<exc>[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception)?)(?::\s*(?P<msg>.*))?$',
        re.MULTILINE,
    )

    # Matches standard exception tail: ZeroDivisionError: division by zero or E   ZeroDivisionError: division by zero
    EXCEPTION_LINE_RE = re.compile(
        r'^(?:E\s+)?(?P<exc>[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Failure)?)(?::\s*(?P<msg>.*))?$',
        re.MULTILINE,
    )

    def __init__(self, repo_dir: Optional[str] = None):
        self.repo_dir = Path(repo_dir).resolve() if repo_dir else Path.cwd().resolve()

    def parse(self, raw_log: str) -> List[FailureReport]:
        """Parses raw CI log output and extracts distinct failure reports."""
        if not raw_log or not raw_log.strip():
            return []

        reports: List[FailureReport] = []

        # Split into blocks separated by pytest failure delimiters or standard traceback indicators
        blocks = self._split_failure_blocks(raw_log)
        for block in blocks:
            report = self._parse_single_block(block)
            if report:
                reports.append(report)

        # Fallback if no multi-block failure matched but traceback exists
        if not reports:
            fallback = self._parse_single_block(raw_log)
            if fallback:
                reports.append(fallback)

        # Deduplicate reports by (failing_file, failing_line)
        deduped: List[FailureReport] = []
        seen = set()
        for r in reports:
            key = (r.failing_file, r.failing_line, r.exception_type)
            if key not in seen:
                seen.add(key)
                deduped.append(r)

        return deduped

    def _split_failure_blocks(self, raw_log: str) -> List[str]:
        """Splits raw log output into per-test failure blocks."""
        # Check for pytest FAILURES block delimiter: "___ test_name ___" or "FAILED tests/..."
        if "=== FAILURES ===" in raw_log or "___ test" in raw_log:
            lines = raw_log.splitlines()
            blocks = []
            current = []
            in_failures = False

            for line in lines:
                if "=== FAILURES ===" in line:
                    in_failures = True
                    continue
                if in_failures and re.match(r"^_{3,}\s+.+\s+_{3,}$", line):
                    if current:
                        blocks.append("\n".join(current))
                        current = []
                if in_failures and line.startswith("=== short test summary"):
                    if current:
                        blocks.append("\n".join(current))
                        current = []
                    in_failures = False
                if in_failures:
                    current.append(line)

            if current:
                blocks.append("\n".join(current))
            if blocks:
                return blocks

        # Check for Traceback (most recent call last):
        if "Traceback (most recent call last):" in raw_log:
            chunks = raw_log.split("Traceback (most recent call last):")
            return [("Traceback (most recent call last):" + chunk) for chunk in chunks[1:]]

        return [raw_log]

    def _parse_single_block(self, block: str) -> Optional[FailureReport]:
        """Parses a single failure block into a structured FailureReport."""
        frames: List[StackFrame] = []

        # 1. Search for standard File "...", line X, in Y
        for match in self.TRACEBACK_FILE_RE.finditer(block):
            file_path = match.group("file")
            line_num = int(match.group("line"))
            func_name = match.group("func").strip() if match.group("func") else None
            frames.append(StackFrame(file_path=file_path, line_number=line_num, function_name=func_name))

        # 2. Search for pytest path:line: in func
        for match in self.PYTEST_FRAME_RE.finditer(block):
            file_path = match.group("file")
            line_num = int(match.group("line"))
            func_name = match.group("func").strip() if match.group("func") else None
            # Only append if not duplicate of immediately preceding frame
            if not frames or (frames[-1].file_path != file_path or frames[-1].line_number != line_num):
                frames.append(StackFrame(file_path=file_path, line_number=line_num, function_name=func_name))

        # 3. Detect Exception Type & Message
        exc_type = "AssertionError"
        exc_msg = ""

        # Check for pytest specific summary line first
        summary_match = self.PYTEST_SUMMARY_LINE_RE.search(block)
        if summary_match:
            exc_type = summary_match.group("exc")
            exc_msg = summary_match.group("msg") or ""

        # Scan from end for exception line (e.g. `E   ZeroDivisionError: division by zero`)
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        for line in reversed(lines):
            # Check pytest "E   ..." line
            if line.startswith("E "):
                trimmed = line[2:].strip()
                if ":" in trimmed:
                    parts = trimmed.split(":", 1)
                    cand_exc = parts[0].strip()
                    if cand_exc.endswith(("Error", "Exception", "Failure", "Interrupt")):
                        exc_type = cand_exc
                        exc_msg = parts[1].strip()
                        break
                elif any(trimmed.startswith(k) for k in ("assert ", "AssertionError")):
                    exc_type = "AssertionError"
                    exc_msg = trimmed
                    break
            else:
                m = self.EXCEPTION_LINE_RE.match(line)
                if m and m.group("exc"):
                    cand = m.group("exc")
                    if cand.endswith(("Error", "Exception", "Failure", "Interrupt", "Warning")):
                        exc_type = cand
                        exc_msg = (m.group("msg") or "").strip()
                        break

        if not frames:
            # Look for summary line alone if no frame was found
            if summary_match:
                f_path = summary_match.group("file")
                l_num = int(summary_match.group("line"))
                frames.append(StackFrame(file_path=f_path, line_number=l_num))
            else:
                return None

        # Prioritize project application source frames over 3rd-party/venv frames
        target_frame = self._select_focal_frame(frames)

        normalized_file = self._normalize_path(target_frame.file_path)

        return FailureReport(
            failing_file=normalized_file,
            failing_line=target_frame.line_number,
            exception_type=exc_type,
            exception_message=exc_msg,
            stack_frames=frames,
            raw_traceback=block.strip(),
            target_function=target_frame.function_name,
        )

    def _select_focal_frame(self, frames: List[StackFrame]) -> StackFrame:
        """Selects the most actionable frame within user repository code (skipping virtualenvs/libs)."""
        # Filter out site-packages, stdlib, and pytest internals
        project_frames = []
        for f in frames:
            f_norm = f.file_path.replace("\\", "/")
            if "site-packages" in f_norm or "/lib/python" in f_norm or "/pytest" in f_norm:
                continue
            project_frames.append(f)

        if not project_frames:
            # Return last frame if all were filtered
            return frames[-1]

        # Prefer non-test files first if the error originated in application logic
        source_frames = [f for f in project_frames if not Path(f.file_path).name.startswith("test_")]
        if source_frames:
            return source_frames[-1]

        return project_frames[-1]

    def _normalize_path(self, raw_path: str) -> str:
        """Normalizes file path relative to repo_dir when possible."""
        p = Path(raw_path)
        try:
            if p.is_absolute() and self.repo_dir:
                try:
                    return str(p.relative_to(self.repo_dir))
                except ValueError:
                    # Check if relative path can be resolved from cwd or subpath
                    pass
        except Exception:
            pass
        return raw_path
