"""GitProjectStore add/sync against a real local Git bundle, thin manifest."""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import pytest

from kiro_crew.project_git import GitProjectStore, ProjectGitError
from kiro_crew.project_registry import ProjectRegistry


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "PATH": "/usr/bin:/bin",
            "HOME": str(cwd),
        },
    )


def _make_bundle_repo(root: Path) -> Path:
    repo = root / "bundle-remote"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    (repo / "project.yaml").write_text(
        "apiVersion: crew.kiro/v1\n"
        "kind: Project\n"
        f"id: {uuid.uuid4()}\n"
        "name: e2e\n"
        "sources:\n"
        "  - type: repo\n"
        "    url: https://example.com/primary\n"
        "    role: primary\n",
        encoding="utf-8",
    )
    _git(repo, "add", "project.yaml")
    _git(repo, "commit", "-m", "init")
    return repo


@pytest.mark.skipif(
    subprocess.run(["which", "git"], capture_output=True).returncode != 0,
    reason="git not installed",
)
def test_add_clones_and_registers_managed_bundle(tmp_path: Path) -> None:
    remote = _make_bundle_repo(tmp_path)
    registry = ProjectRegistry(
        projects_dir=tmp_path / "projects",
        registry_dir=tmp_path / "projects-registry",
    )
    store = GitProjectStore(registry)
    try:
        project = store.add(f"file://{remote}")
    except ProjectGitError as exc:
        if "sandbox" in str(exc).lower():
            pytest.skip(f"sandbox unavailable in this environment: {exc}")
        raise

    managed = [r for r in project.registrations if r.origin == "managed_git"]
    assert managed, "add did not create a managed registration"
    reg = managed[-1]
    # Coordinates are pinned from the fresh clone, into the fenced registry.
    assert reg.remote == f"file://{remote}"
    assert reg.default_branch == "main"
    assert reg.path.exists()
    assert (reg.path / "project.yaml").exists()
    # The managed clone lives under the visible projects/ leaf, keyed by id.
    assert (tmp_path / "projects" / "managed") in reg.path.parents
