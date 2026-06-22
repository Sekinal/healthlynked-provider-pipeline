"""Adversarial review of the highest-risk logic via codex-cli.

codex acts as an independent second model that red-teams the exact code paths that
can corrupt the directory: the confidence formula, the decision gates, and the
safe auto-update rules. Run before committing changes to those files.

    uv run python -m review.codex_review
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TARGETS = ["pipeline/confidence.py", "pipeline/decide.py", "pipeline/config.py"]

PROMPT = """You are an adversarial reviewer for a healthcare provider-directory
update pipeline. Review ONLY the auto-update safety logic in the files below.

Find concrete cases where the system could:
1. AUTO-UPDATE a field that should have gone to human review (false confidence).
2. Mis-score a genuine source conflict so it does NOT deflate confidence.
3. Let a high-risk field (npi, provider_name, active_status) be auto-applied.
4. Treat the stale existing record as if it were corroborating evidence.

For each issue: cite the file/line, explain the failure case concretely, and rate
severity (high/med/low). Be skeptical and specific. If the logic is sound, say so.
Do not modify files."""


def main() -> int:
    files = [f for f in TARGETS if Path(f).exists()]
    prompt = PROMPT + "\n\nFiles to review:\n" + "\n".join(f"- {f}" for f in files)
    print(f"Running codex adversarial review on: {', '.join(files)}\n")
    try:
        proc = subprocess.run(
            ["codex", "exec", "--skip-git-repo-check", prompt],
            text=True, capture_output=True, timeout=600,
        )
    except FileNotFoundError:
        print("codex-cli not found on PATH; skipping.", file=sys.stderr)
        return 0
    except subprocess.TimeoutExpired:
        print("codex review timed out.", file=sys.stderr)
        return 0
    print(proc.stdout)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
