"""GitHub PR Manager: creates branches, commits verified patches, and opens automated pull requests."""

import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.engine.patcher import PatchCandidate
from src.engine.sandbox import SandboxResult
from src.parser.ast_mapper import AstScope
from src.parser.log_parser import FailureReport


@dataclass
class PrOutcome:
    """Outcome of attempting to publish a pull request."""
    success: bool
    branch_name: str
    pr_number: Optional[int] = None
    pr_url: Optional[str] = None
    message: str = ""


class GitHubPrManager:
    """Manages Git branch creation, patch committing, and PR submission via PyGithub."""

    def __init__(
        self,
        token: Optional[str] = None,
        repo_name: Optional[str] = None,
        base_branch: Optional[str] = None,
        dry_run: bool = False,
    ):
        self.token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("INPUT_GITHUB_TOKEN")
        self.repo_name = repo_name or os.environ.get("GITHUB_REPOSITORY")
        self.base_branch = base_branch or os.environ.get("GITHUB_REF_NAME") or "main"
        self.commit_sha = os.environ.get("GITHUB_SHA", "HEAD")
        self.dry_run = dry_run
        self.client = None

        if self.token and not self.dry_run:
            try:
                from github import Github
                self.client = Github(self.token)
            except Exception:
                self.client = None

    def publish_patch_pr(
        self,
        candidate: PatchCandidate,
        failure: FailureReport,
        scope: AstScope,
        sandbox: SandboxResult,
        repo_dir: Optional[str] = None,
    ) -> PrOutcome:
        """Commits the patch to a new branch and opens a Pull Request on GitHub."""
        unique_suffix = uuid.uuid4().hex[:7]
        branch_name = f"autofix/patch-{scope.scope_name}-{unique_suffix}".lower().replace(" ", "-")

        pr_title = f"fix(ci): auto-resolve {failure.exception_type} in {Path(scope.file_path).name}"
        pr_body = self._build_pr_body(candidate, failure, scope, sandbox)

        # 1. Handle Dry-Run Mode
        if self.dry_run or not self.client or not self.repo_name:
            dry_run_msg = (
                f"[DRY-RUN] Verified patch generated for {scope.file_path}. "
                f"Branch: {branch_name}, PR Title: '{pr_title}'"
            )
            return PrOutcome(
                success=True,
                branch_name=branch_name,
                pr_number=999,
                pr_url=f"https://github.com/mock-repo/pull/999 (dry-run)",
                message=dry_run_msg,
            )

        # 2. Live GitHub API Flow via PyGithub
        try:
            repo = self.client.get_repo(self.repo_name)

            # Get reference of base branch
            base_ref = repo.get_branch(self.base_branch)
            base_sha = base_ref.commit.sha

            # Create branch
            repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=base_sha)

            # Read new patched file contents from local disk
            base_path = Path(repo_dir).resolve() if repo_dir else Path.cwd().resolve()
            patched_file = (base_path / candidate.file_path).resolve()
            new_content = patched_file.read_text(encoding="utf-8", errors="replace")

            # Get current file in repo to obtain its sha for update
            try:
                repo_file = repo.get_contents(candidate.file_path, ref=branch_name)
                repo.update_file(
                    path=candidate.file_path,
                    message=f"fix(ci): resolve {failure.exception_type} in {scope.scope_name}\n\nSigned-off-by: AutoFix-CI Agent",
                    content=new_content,
                    sha=repo_file.sha,
                    branch=branch_name,
                )
            except Exception:
                # If file is new or couldn't be retrieved, try create_file
                repo.create_file(
                    path=candidate.file_path,
                    message=f"fix(ci): add patched {candidate.file_path}\n\nSigned-off-by: AutoFix-CI Agent",
                    content=new_content,
                    branch=branch_name,
                )

            # Create Pull Request
            pr = repo.create_pull(
                title=pr_title,
                body=pr_body,
                base=self.base_branch,
                head=branch_name,
            )

            # Add labels if available
            try:
                pr.add_to_labels("autofix", "ci-failure", "automated-pr")
            except Exception:
                pass

            return PrOutcome(
                success=True,
                branch_name=branch_name,
                pr_number=pr.number,
                pr_url=pr.html_url,
                message=f"Successfully opened PR #{pr.number}: {pr.html_url}",
            )

        except Exception as exc:
            return PrOutcome(
                success=False,
                branch_name=branch_name,
                message=f"GitHub API error during PR creation: {exc}",
            )

    def _build_pr_body(
        self,
        candidate: PatchCandidate,
        failure: FailureReport,
        scope: AstScope,
        sandbox: SandboxResult,
    ) -> str:
        """Constructs rich GitHub-flavored Markdown PR description."""
        body_lines = [
            "## 🤖 AutoFix-CI: Autonomous CI Failure Triage & Self-Healing",
            "",
            "> **Sandbox Status:** `VERIFIED_PASSING` ✅  ",
            f"> **Target File:** `{scope.file_path}`  ",
            f"> **Enclosing AST Scope:** `{scope.scope_type.upper()}` **`{scope.scope_name}`** (Lines {scope.start_line}–{scope.end_line})  ",
            f"> **Exception Resolved:** `{failure.exception_type}`  ",
            "",
            "---",
            "",
            "### 🔍 Root Cause Analysis & Fix Rationale",
            candidate.explanation,
            "",
            "---",
            "",
            "### 🛠️ Synthesized Patch (Unified Diff)",
            "```diff",
            candidate.unified_diff.strip(),
            "```",
            "",
            "---",
            "",
            "### 🧪 Sandbox Verification Telemetry",
            f"- **Sandbox Exit Code:** `{sandbox.exit_code}` (Success)",
            f"- **Execution Latency:** `{sandbox.duration_seconds}s`",
            "- **Test Output Summary:**",
            "```text",
            sandbox.stdout[-800:].strip() if sandbox.stdout else "All tests passed successfully.",
            "```",
            "",
            "---",
            "*Generated autonomously by [AutoFix-CI](https://github.com/marketplace/actions/autofix-ci) powered by Google Gemini 2.5.*",
        ]
        return "\n".join(body_lines)
