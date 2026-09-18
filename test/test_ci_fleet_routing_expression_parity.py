"""
The CodeBuild fleet routing decision -- same-repo run gets a per-run
`codebuild-kirocrew-gha-linux-<run_id>-<run_attempt>` label, a fork run (or a
run in any repository other than kirodotdev/KiroCrew) stays on
`ubuntu-latest` -- is hand-copied as one literal `runs-on:` expression into
every job that has no resolver job of its own to read an output from. A
`needs:` edge cannot cross a workflow file, and several of the copies (Fast
Gate's twelve gates) are FORBIDDEN a `needs:` edge even within their own file
(see test_fast_gate_barrier.py), so this repo has ~50 independent copies of
the same trust-boundary decision instead of one.

That is a real hole: a future fix to the fork rule applied to one copy and
missed the rest reintroduces exactly the vulnerability the fix closed, and
nothing short of reading every workflow file by hand would catch the miss.
This test is the single point that would catch it -- every occurrence of the
expression's literal text, across every workflow file, must be byte-identical
to the one canonical copy defined below, or this test fails and names which
file drifted.

`ci.yml`'s `changes` job resolver, and the jobs in other files that read its
`needs.changes.outputs.linux_runner`/`linux_runner_large` output rather than
carrying their own copy, are a DIFFERENT (and stricter -- see that job's own
`IS_FORK` env var, which computes the identical boolean) implementation of
the same decision and are named as exceptions below, not exempted from
scrutiny.

Five jobs are PERMANENT exceptions: they fit the migration's other criteria
but must stay on `ubuntu-latest` for a reason specific to each, recorded
both at the job's own `runs-on:` line and in `_PERMANENT_EXCEPTIONS` below.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOWS_DIR = _REPO_ROOT / ".github" / "workflows"

# The one canonical copy. Every other occurrence of this literal `runs-on:`
# value, anywhere under .github/workflows/, must match this exactly.
_CANONICAL_ROUTING_EXPR = (
    "${{ (github.repository != 'kirodotdev/KiroCrew' || "
    "(github.event_name == 'pull_request' && "
    "github.event.pull_request.head.repo.full_name != github.repository)) "
    "&& 'ubuntu-latest' || format('codebuild-kirocrew-gha-linux-{0}-{1}', "
    "github.run_id, github.run_attempt) }}"
)

# Permanent exceptions: jobs that must stay on `ubuntu-latest` even though
# their workflow otherwise fits this PR's migration scope, each for a
# reason recorded at the job's own `runs-on:` line so a future reviewer
# reads it before "fixing" the job back onto the fleet.
#
# - security-scope-review.yml's `generate`: its write fence hardcodes a
#   GitHub-hosted-runner path (`Write(//home/runner/work/_actions/**)`) that
#   the CodeBuild routing would silently bypass.
# - issue-summary.yml's `summarize`, issue-triage.yml's `triage`,
#   ai-review-human-override.yml's `record`, and
#   disposition-deferral-check.yml's `validate-deferral`: each fires on
#   `issues`/`issue_comment`, an event any GitHub user can trigger
#   regardless of push access. The fork-vs-same-repo boolean this PR's
#   routing expression checks has no PR head repository to compare against
#   on these events, so it always evaluates "same-repo" and requests the
#   fleet -- which the AWS-side webhook's actor allowlist then rejects for a
#   non-collaborator triggering user, leaving the job queued rather than
#   running on ubuntu-latest. Caught by GPT 5.6 on issue-summary.yml/
#   issue-triage.yml; the other two share the identical trigger class and
#   were reverted proactively rather than waiting for the same finding
#   twice more.
_PERMANENT_EXCEPTIONS = {
    ("security-scope-review.yml", "generate"),
    ("issue-summary.yml", "summarize"),
    ("issue-triage.yml", "triage"),
    ("ai-review-human-override.yml", "record"),
    ("disposition-deferral-check.yml", "validate-deferral"),
}

# The complete set of (file, job) pairs expected to carry the canonical
# routing expression -- everything this PR's migration actually routed.
# Listed explicitly, not derived, so a job silently added to or removed
# from this set (rather than merely having its runs-on: text corrupted,
# which the byte-match test above catches) fails a test too.
_EXPECTED_ROUTED_JOBS = {
    ("fast-gate.yml", "vendor-manifest"),
    ("fast-gate.yml", "brand-lint"),
    ("fast-gate.yml", "comment-history-lint"),
    ("fast-gate.yml", "focus-cue-lint"),
    ("fast-gate.yml", "feature-map-lint"),
    ("fast-gate.yml", "changelog-history"),
    ("fast-gate.yml", "builtin-skill-scope"),
    ("fast-gate.yml", "loop-bound-locks"),
    ("fast-gate.yml", "testpaths-coverage"),
    ("fast-gate.yml", "harness-parity"),
    ("fast-gate.yml", "memory-store-seam"),
    ("fast-gate.yml", "docs-lint"),
    ("build.yml", "build-wheel"),
    ("build.yml", "desktop-matrix"),
    ("main-ratchet-audit.yml", "ratchet-gates"),
    ("main-ratchet-audit.yml", "frontend-ceiling"),
    ("main-ratchet-audit.yml", "report"),
    ("release.yml", "version"),
    ("release.yml", "resolve-promotion"),
    ("release.yml", "stable-gate"),
    ("release.yml", "github-release"),
    ("release.yml", "record-promotion"),
    ("build-wheel.yml", "build-wheel"),
    ("pages.yml", "build"),
    ("pages.yml", "deploy"),
    ("connections-l0.yml", "probe"),
    ("cross-platform.yml", "cross-platform"),
    ("nightly.yml", "version"),
    ("memory-benchmark.yml", "accept"),
    ("fix-loop-analysis.yml", "metrics"),
    ("fix-loop-analysis.yml", "analyze"),
    ("dependency-review.yml", "license-gate"),
    ("dependency-vulnerability.yml", "audit-production-dependencies"),
    ("pr-scope.yml", "pr-scope"),
    ("pr-merge-conflict-label.yml", "label"),
    ("deferred-findings-audit.yml", "audit"),
    ("add-contributor.yml", "add"),
    ("ship-report.yml", "report"),
    ("screenshot-evidence.yml", "screenshot-evidence"),
    ("macos-on-demand.yml", "decide"),
    ("first-principles-review.yml", "first-principles-review"),
    ("ux-review.yml", "ux-review"),
    ("design-review.yml", "design-review"),
    ("security-scope-review.yml", "validate"),
    ("security-scope-review.yml", "publish"),
    ("ci.yml", "changes"),
    ("ci.yml", "await-fast-gate"),
}


def _all_workflow_files() -> list[Path]:
    return sorted(_WORKFLOWS_DIR.glob("*.yml"))


def test_every_copy_of_the_routing_expression_matches_the_canonical_one() -> None:
    drifted: list[str] = []
    found_any = False
    for path in _all_workflow_files():
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if "runs-on:" not in line:
                continue
            # Anchor on the CodeBuild label format call, not on the fork
            # boolean itself -- the boolean is exactly the text a drift could
            # corrupt, so matching on it would make a corrupted copy invisible
            # to its own detector. Every `runs-on:` line that builds this
            # fleet's per-run label is a candidate for the canonical-text
            # comparison below, regardless of what its boolean half says.
            if "codebuild-kirocrew-gha-linux-{0}-{1}" not in line:
                continue
            found_any = True
            value = line.split("runs-on:", 1)[1].strip()
            if value != _CANONICAL_ROUTING_EXPR:
                drifted.append(f"{path.name}:{lineno}: {value}")
    assert found_any, (
        "no occurrence of the routing expression was found at all -- this "
        "test's own matcher broke, not that the expression is gone"
    )
    assert not drifted, (
        "one or more copies of the fork-vs-fleet routing expression have "
        "drifted from the canonical text -- a rule change applied to one "
        "copy and missed here:\n" + "\n".join(drifted)
    )


def test_every_job_using_the_routing_expression_is_accounted_for() -> None:
    """Companion to the byte-match test above: every job whose `runs-on:` is
    the canonical expression must be exactly `_EXPECTED_ROUTED_JOBS`, and
    every permanent exception's job must actually exist with `runs-on:
    ubuntu-latest` (not merely be a name in a set nothing reads back). A job
    silently added to or dropped from the routed set -- migrated further, or
    quietly reverted -- fails here, not just a job whose `runs-on:` text was
    corrupted in place (which is what the byte-match test catches).
    """
    routed: set[tuple[str, str]] = set()
    exception_runs_on: dict[tuple[str, str], object] = {}
    for path in _all_workflow_files():
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        jobs = workflow.get("jobs") or {}
        for job_id, spec in jobs.items():
            key = (path.name, job_id)
            runs_on = spec.get("runs-on")
            if runs_on == _CANONICAL_ROUTING_EXPR:
                routed.add(key)
            if key in _PERMANENT_EXCEPTIONS:
                exception_runs_on[key] = runs_on

    missing_from_workflows = _EXPECTED_ROUTED_JOBS - routed
    added_since_expected = routed - _EXPECTED_ROUTED_JOBS
    assert not missing_from_workflows, (
        "a job this PR migrated no longer carries the canonical routing "
        f"expression -- silently reverted or renamed: {missing_from_workflows}"
    )
    assert not added_since_expected, (
        "a job not in the expected routed set now carries the canonical "
        f"routing expression -- update _EXPECTED_ROUTED_JOBS if this is "
        f"intentional, or investigate if it is not: {added_since_expected}"
    )

    missing_exceptions = _PERMANENT_EXCEPTIONS - set(exception_runs_on)
    assert not missing_exceptions, (
        f"a permanent exception no longer exists as a job at all -- "
        f"renamed or removed: {missing_exceptions}"
    )
    wrong_runner = {
        key: value
        for key, value in exception_runs_on.items()
        if value != "ubuntu-latest"
    }
    assert not wrong_runner, (
        f"a permanent exception's actual runs-on: is not 'ubuntu-latest' -- "
        f"it was routed to the fleet despite the exception, or its runner "
        f"changed to something else: {wrong_runner}"
    )
