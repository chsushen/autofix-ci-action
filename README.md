# ⚡ AutoFix-CI: Autonomous CI Failure Triage & Self-Healing Action

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python Version](https://img.shields.io/badge/Python-3.10%2B-brightgreen.svg)](https://python.org)
[![GitHub Action](https://img.shields.io/badge/GitHub%20Marketplace-AutoFix--CI-blue?logo=githubactions)](https://github.com/marketplace/actions/autofix-ci)
[![Tests](https://img.shields.io/badge/Tests-17%20Passed-success)](tests/)

**AutoFix-CI** is a production-ready, publishable GitHub Action and developer CLI engine that autonomously diagnoses CI test failures, statically scopes offending code using Python's Abstract Syntax Tree (AST), synthesizes minimal verified patches via Google GenAI (Gemini 2.5), verifies candidate fixes inside an isolated sandbox, and opens an automated Pull Request with root-cause analysis and telemetry.

---

## 🏗️ System Architecture

```
                                  +-----------------------+
                                  | CI Step Test Failure  |
                                  +-----------+-----------+
                                              |
                                              v
                              +-------------------------------+
                              | Log Parser (Regex & Traces)   |
                              +---------------+---------------+
                                              |
                        Failing file path & line number
                                              v
                              +-------------------------------+
                              | AST Static Scope Mapper       |
                              | - Enclosing Func/Class Nodes  |
                              | - Context Window & Imports    |
                              +---------------+---------------+
                                              |
                                              v
                              +-------------------------------+
                              | Zero-Egress Secret Scrubber   |
                              | (Redacts Tokens & API Keys)   |
                              +---------------+---------------+
                                              |
                                              v
                              +-------------------------------+
                              | Google GenAI (Gemini 2.5)     |
                              | Structured JSON Diff Patch    |
                              +---------------+---------------+
                                              |
                                              v
                              +-------------------------------+
                              | Sandbox Runner (Subprocess)   |
                              | - Apply Patch                 |
                              | - Verify via `pytest`         |
                              | - Rollback on Error           |
                              +---------------+---------------+
                                     /                 \
                           [Passes] /                   \ [Fails: Retry up to Max]
                                   v                     v
                        +--------------------+    +--------------------+
                        | PyGithub PR Client |    | Multi-Turn Healing |
                        | Branch & Open PR   |    | Re-Prompt LLM      |
                        +--------------------+    +--------------------+
```

---

## 🚀 Quickstart: GitHub Actions Workflow

Add this workflow to your repository at `.github/workflows/autofix.yml`:

```yaml
name: "Continuous Integration & AutoFix"

on:
  push:
    branches: [main, master]
  pull_request:
    branches: [main, master]

permissions:
  contents: write
  pull-requests: write

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout Code
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: "pip"

      - name: Install Dependencies
        run: |
          pip install --upgrade pip
          pip install -r requirements.txt || pip install pytest

      - name: Run Test Suite
        id: test_run
        run: pytest
        continue-on-error: true

      - name: AutoFix-CI Autonomous Self-Healing
        if: steps.test_run.outcome == 'failure'
        uses: chsushen/autofix-ci@v1
        with:
          github_token: ${{ secrets.GITHUB_TOKEN }}
          gemini_api_key: ${{ secrets.GEMINI_API_KEY }}
          test_command: "pytest"
          max_retries: "2"
```

---

## ⚙️ Inputs & Outputs

### Action Inputs (`action.yml`)

| Input | Required | Default | Description |
| :--- | :---: | :---: | :--- |
| `github_token` | **Yes** | N/A | GitHub Personal Access Token or `${{ secrets.GITHUB_TOKEN }}` with `contents: write` and `pull-requests: write`. |
| `gemini_api_key` | **Yes** | N/A | Google Gemini API key used for code patch synthesis. |
| `test_command` | No | `pytest` | Test execution command used in sandbox verification. |
| `max_retries` | No | `2` | Number of feedback iterations to attempt if a candidate patch fails. |
| `log_path` | No | `""` | Path to existing CI failure log file. If omitted, `test_command` is run directly. |
| `repo_dir` | No | `.` | Target repository directory relative to workspace root. |
| `dry_run` | No | `false` | When `true`, verifies patches locally without opening remote branches or PRs. |

### Action Outputs

| Output | Description |
| :--- | :--- |
| `patch_status` | Final resolution state: `PASSED`, `FAILED`, or `NO_ERROR_FOUND`. |
| `pr_url` | Full URL of the newly created Pull Request. |
| `diff` | Synthesized Git unified diff applied to repository. |

---

## 💻 Local CLI Execution

AutoFix-CI can be run locally or integrated into custom pre-commit hooks:

```bash
# 1. Install via pip
pip install -e .

# 2. Run AutoFix-CI in dry-run mode
autofix-ci \
  --repo-dir . \
  --test-command "pytest tests/" \
  --gemini-api-key "$GEMINI_API_KEY" \
  --dry-run
```

---

## 🔒 Security & Zero-Egress Policy

AutoFix-CI features an automated **Zero-Egress Secret Scrubber** that strips sensitive data prior to prompt dispatch:
- **GitHub PATs** (`ghp_*`, `github_pat_*`)
- **Google API Keys** (`AIzaSy*`)
- **JWT Bearer Tokens** (`eyJ*`)
- **Authorization Headers** (`Bearer *`)
- **Passwords & Secrets** in test assertions and environment dumps

All detected credentials are replaced with `[REDACTED_SECRET]` before network transmission.

---

## 🧪 Automated Verification Suite

Run the unit test suite covering log parsing, AST scope extraction, secret redaction, and sandbox rollback:

```bash
PYTHONPATH=. pytest tests/ -v
```

### Passing Test Suites:
- `test_ast_mapper.py`: Top-level functions, class methods, async workers, module scopes, and syntax fallbacks.
- `test_log_parser.py`: Pytest logs, assertion errors, and multi-frame call stacks.
- `test_patcher.py`: Secret scrubber validation, diff formatting, and heuristic fallbacks.
- `test_sandbox.py`: Atomic diff patching, automatic snapshot rollback, and execution timeouts.

---

## 📄 License

Apache License 2.0. Authored by Chunduri Sushen.
