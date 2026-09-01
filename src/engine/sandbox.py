"""Isolated test runner and sandbox verification engine for validating candidate patches."""

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple


@dataclass
class SandboxResult:
    """Outcome of running test suite within the sandbox."""
    success: bool
    exit_code: int
    stdout: str
    stderr: str
    error_summary: str = ""
    duration_seconds: float = 0.0


class SandboxRunner:
    """Executes candidate patches and tests in a sandboxed, rollback-capable environment."""

    def __init__(self, repo_dir: Optional[str] = None, default_timeout: int = 30):
        self.repo_dir = Path(repo_dir).resolve() if repo_dir else Path.cwd().resolve()
        self.default_timeout = default_timeout
        self._backups = {}

    def apply_patch(self, unified_diff: str, file_path: str) -> Tuple[bool, str]:
        """Applies unified diff patch to target file with automatic snapshot backup."""
        target_file = (self.repo_dir / file_path).resolve()

        # 1. Snapshot original file content for atomic rollback
        if target_file.exists() and file_path not in self._backups:
            self._backups[file_path] = target_file.read_text(encoding="utf-8", errors="replace")

        # 2. Try applying via `git apply`
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".patch") as tmp:
            tmp.write(unified_diff)
            tmp_path = tmp.name

        try:
            # First check if git apply can apply it
            check_res = subprocess.run(
                ["git", "apply", "--check", "--whitespace=nowarn", tmp_path],
                cwd=str(self.repo_dir),
                capture_output=True,
                text=True,
                timeout=10,
            )
            if check_res.returncode == 0:
                apply_res = subprocess.run(
                    ["git", "apply", "--whitespace=nowarn", tmp_path],
                    cwd=str(self.repo_dir),
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if apply_res.returncode == 0:
                    return True, "Patch applied successfully via git apply."

            # 3. Fallback: In-memory unified diff application
            success, msg = self._apply_diff_manually(target_file, unified_diff)
            if success:
                return True, msg

            return False, f"Git apply failed: {check_res.stderr.strip()}"

        except Exception as exc:
            # Fallback to manual diff application
            success, msg = self._apply_diff_manually(target_file, unified_diff)
            if success:
                return True, msg
            return False, f"Patch apply exception: {exc}"
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def revert_patch(self, file_path: str) -> bool:
        """Restores file to its pre-patch state from snapshot backup."""
        target_file = (self.repo_dir / file_path).resolve()
        if file_path in self._backups:
            target_file.write_text(self._backups[file_path], encoding="utf-8")
            del self._backups[file_path]
            return True

        # Fallback to git checkout
        try:
            subprocess.run(
                ["git", "checkout", "--", str(file_path)],
                cwd=str(self.repo_dir),
                capture_output=True,
                timeout=10,
            )
            return True
        except Exception:
            return False

    def run_tests(
        self,
        test_command: str = "pytest",
        timeout: Optional[int] = None,
    ) -> SandboxResult:
        """Runs the verification test command inside the sandbox."""
        timeout_val = timeout or self.default_timeout

        import time
        start_time = time.time()

        try:
            res = subprocess.run(
                test_command,
                shell=True,
                cwd=str(self.repo_dir),
                capture_output=True,
                text=True,
                timeout=timeout_val,
            )
            elapsed = time.time() - start_time
            success = (res.returncode == 0)
            combined_output = (res.stdout + "\n" + res.stderr).strip()

            error_summary = ""
            if not success:
                error_summary = self._extract_error_summary(combined_output)

            return SandboxResult(
                success=success,
                exit_code=res.returncode,
                stdout=res.stdout,
                stderr=res.stderr,
                error_summary=error_summary,
                duration_seconds=round(elapsed, 3),
            )

        except subprocess.TimeoutExpired as exc:
            elapsed = time.time() - start_time
            return SandboxResult(
                success=False,
                exit_code=124,
                stdout=exc.stdout or "" if isinstance(exc.stdout, str) else "",
                stderr=f"Execution timed out after {timeout_val} seconds.",
                error_summary=f"TimeoutExpired: test command exceeded {timeout_val}s limit.",
                duration_seconds=round(elapsed, 3),
            )
        except Exception as exc:
            elapsed = time.time() - start_time
            return SandboxResult(
                success=False,
                exit_code=-1,
                stdout="",
                stderr=str(exc),
                error_summary=f"Sandbox execution error: {exc}",
                duration_seconds=round(elapsed, 3),
            )

    def _extract_error_summary(self, output: str) -> str:
        """Extracts concise error summary from failed test execution output."""
        lines = output.splitlines()
        for line in reversed(lines):
            line_str = line.strip()
            if any(k in line_str for k in ("FAILED", "Error:", "Exception:", "=== FAILURES ===")):
                return line_str
        return lines[-1] if lines else "Test run failed with non-zero exit code."

    def _apply_diff_manually(self, target_file: Path, diff_text: str) -> Tuple[bool, str]:
        """Manual hunk applier for unified diffs when git binary is not available."""
        if not target_file.exists():
            return False, f"Target file does not exist: {target_file}"

        original_lines = target_file.read_text(encoding="utf-8", errors="replace").splitlines()
        diff_lines = diff_text.splitlines()

        # Extract hunk additions and removals
        new_lines = []
        orig_idx = 0

        # Scan for hunk headers: @@ -start,len +start,len @@
        hunk_found = False
        i = 0
        while i < len(diff_lines):
            line = diff_lines[i]
            if line.startswith("@@"):
                hunk_found = True
                # Parse hunk header
                import re
                match = re.search(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
                if match:
                    old_start = int(match.group(1)) - 1
                    # Append lines up to old_start
                    while orig_idx < old_start and orig_idx < len(original_lines):
                        new_lines.append(original_lines[orig_idx])
                        orig_idx += 1

                i += 1
                while i < len(diff_lines) and not diff_lines[i].startswith("@@"):
                    h_line = diff_lines[i]
                    if h_line.startswith("+") and not h_line.startswith("+++"):
                        new_lines.append(h_line[1:])
                    elif h_line.startswith("-") and not h_line.startswith("---"):
                        orig_idx += 1  # Skip original line
                    elif h_line.startswith(" "):
                        if orig_idx < len(original_lines):
                            new_lines.append(original_lines[orig_idx])
                            orig_idx += 1
                    i += 1
                continue
            i += 1

        if not hunk_found:
            return False, "No valid unified diff hunks (@@ ... @@) found."

        # Append remaining lines
        while orig_idx < len(original_lines):
            new_lines.append(original_lines[orig_idx])
            orig_idx += 1

        target_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        return True, "Patch applied manually via fallback hunk parser."
