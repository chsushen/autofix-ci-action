"""CLI & Action entrypoint for AutoFix-CI."""

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

from src.engine.patcher import Patcher
from src.engine.sandbox import SandboxRunner
from src.github.pr_manager import GitHubPrManager
from src.parser.ast_mapper import AstMapper
from src.parser.log_parser import LogParser


def parse_args():
    """Parses command-line arguments and GitHub Action inputs."""
    parser = argparse.ArgumentParser(
        description="AutoFix-CI: Autonomous CI Failure Triage and Self-Healing Engine"
    )

    parser.add_argument(
        "--github-token",
        type=str,
        default=os.environ.get("INPUT_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN"),
        help="GitHub token for PR creation and branch management.",
    )
    parser.add_argument(
        "--gemini-api-key",
        type=str,
        default=os.environ.get("INPUT_GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY"),
        help="Google Gemini API key for autonomous code patch synthesis.",
    )
    parser.add_argument(
        "--test-command",
        type=str,
        default=os.environ.get("INPUT_TEST_COMMAND", "pytest"),
        help="Test command executed in the sandbox to verify candidate patches.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=int(os.environ.get("INPUT_MAX_RETRIES", "2")),
        help="Maximum patch generation and validation attempts before halting.",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default=os.environ.get("INPUT_LOG_PATH", ""),
        help="Path to raw CI failure log file. If omitted, test_command will be run to capture it.",
    )
    parser.add_argument(
        "--repo-dir",
        type=str,
        default=os.environ.get("INPUT_REPO_DIR", "."),
        help="Target repository working directory.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=os.environ.get("INPUT_DRY_RUN", "false").lower() in ("true", "1", "yes"),
        help="Verify patches locally without pushing to GitHub or opening a PR.",
    )

    return parser.parse_args()


def set_github_output(name: str, value: str):
    """Writes output variables for GitHub Actions runner."""
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path and os.path.exists(output_path):
        with open(output_path, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")


def run_autofix(args) -> int:
    """Core autonomous self-healing execution pipeline."""
    repo_dir = Path(args.repo_dir).resolve()
    print("=" * 70)
    print("🚀 AutoFix-CI Engine Initialized")
    print(f"📁 Repository Directory : {repo_dir}")
    print(f"🧪 Test Command        : {args.test_command}")
    print(f"🔄 Max Retries         : {args.max_retries}")
    print(f"🔒 Dry-Run Mode        : {args.dry_run}")
    print("=" * 70)

    sandbox = SandboxRunner(repo_dir=str(repo_dir))

    # 1. Obtain raw test failure log
    raw_log = ""
    if args.log_file and os.path.exists(args.log_file):
        print(f"📖 Ingesting specified log file: {args.log_file}")
        raw_log = Path(args.log_file).read_text(encoding="utf-8", errors="replace")
    else:
        print(f"⚡ No log file provided. Executing '{args.test_command}' to detect failures...")
        initial_run = sandbox.run_tests(args.test_command)
        if initial_run.success:
            print("✅ All tests already pass! No failures detected.")
            set_github_output("patch_status", "NO_ERROR_FOUND")
            return 0
        raw_log = initial_run.stdout + "\n" + initial_run.stderr
        print(f"❌ Test command failed with exit code {initial_run.exit_code}. Triaging logs...")

    # 2. Parse logs to extract structured FailureReport
    log_parser = LogParser(repo_dir=str(repo_dir))
    failures = log_parser.parse(raw_log)

    if not failures:
        print("⚠️ No actionable stack trace or Python failure frames found in log.")
        set_github_output("patch_status", "NO_ERROR_FOUND")
        return 0

    print(f"🎯 Detected {len(failures)} distinct failure point(s).")
    primary_failure = failures[0]
    print(f"   • Primary Target File : {primary_failure.failing_file}")
    print(f"   • Offending Line      : {primary_failure.failing_line}")
    print(f"   • Exception           : {primary_failure.exception_type}: {primary_failure.exception_message}")

    # 3. Static AST Scope Mapping
    ast_mapper = AstMapper(repo_dir=str(repo_dir))
    scope = ast_mapper.analyze_file(primary_failure.failing_file, primary_failure.failing_line)

    print(f"🔬 Enclosing AST Scope    : {scope.scope_type.upper()} '{scope.scope_name}'")
    print(f"   • Scope Range         : Lines {scope.start_line} - {scope.end_line}")
    if scope.enclosing_class:
        print(f"   • Enclosing Class     : {scope.enclosing_class}")

    # 4. Multi-Turn GenAI Patch Synthesis & Sandbox Verification
    patcher = Patcher(api_key=args.gemini_api_key)
    retry_context = None
    verified_candidate = None
    successful_sandbox = None

    for attempt in range(1, args.max_retries + 2):
        print(f"\n[Turn {attempt}/{args.max_retries + 1}] Synthesizing candidate patch via Gemini...")
        candidate = patcher.generate_patch(primary_failure, scope, retry_context)

        print(f"📝 Rationale: {candidate.explanation}")
        print("--- Unified Diff Candidate ---")
        print(candidate.unified_diff.strip())
        print("------------------------------")

        # Apply candidate patch
        apply_ok, apply_msg = sandbox.apply_patch(candidate.unified_diff, candidate.file_path)
        if not apply_ok:
            print(f"❌ Failed to apply diff: {apply_msg}")
            retry_context = f"Git diff apply failed: {apply_msg}. Please provide a standard hunk diff matching exact original line content."
            continue

        # Run verification inside sandbox
        print(f"🧪 Validating candidate patch with '{args.test_command}'...")
        sb_result = sandbox.run_tests(args.test_command)

        if sb_result.success:
            print(f"✅ Sandbox verification PASSED in {sb_result.duration_seconds}s!")
            verified_candidate = candidate
            successful_sandbox = sb_result
            break
        else:
            print(f"❌ Sandbox verification FAILED (Exit Code: {sb_result.exit_code})")
            print(f"   Error Summary: {sb_result.error_summary}")
            retry_context = (
                f"Candidate patch failed verification with exit code {sb_result.exit_code}.\n"
                f"Output snippet:\n{sb_result.stdout[-600:]}\n{sb_result.stderr[-600:]}"
            )
            # Rollback to pre-patch state for the next turn
            sandbox.revert_patch(candidate.file_path)

    if not verified_candidate or not successful_sandbox:
        print("\n⛔ Maximum retries exhausted. AutoFix-CI could not synthesize a verified passing patch.")
        set_github_output("patch_status", "FAILED")
        return 1

    # 5. Ensure verified patch is applied on disk for git commit
    sandbox.apply_patch(verified_candidate.unified_diff, verified_candidate.file_path)

    # 6. Publish Pull Request via GitHub API
    print("\n📦 Publishing verified patch...")
    pr_manager = GitHubPrManager(token=args.github_token, dry_run=args.dry_run)
    outcome = pr_manager.publish_patch_pr(
        candidate=verified_candidate,
        failure=primary_failure,
        scope=scope,
        sandbox=successful_sandbox,
        repo_dir=str(repo_dir),
    )

    print(f"🎉 Result: {outcome.message}")
    if outcome.pr_url:
        print(f"🔗 Pull Request URL: {outcome.pr_url}")

    set_github_output("patch_status", "PASSED")
    if outcome.pr_url:
        set_github_output("pr_url", outcome.pr_url)
    set_github_output("diff", verified_candidate.unified_diff)

    return 0


def cli():
    """CLI wrapper entry point."""
    args = parse_args()
    code = run_autofix(args)
    sys.exit(code)


if __name__ == "__main__":
    cli()
