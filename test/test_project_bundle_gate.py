"""Project Git sandbox failures remain explicit at the store and HTTP boundaries."""

from unittest.mock import Mock

import pytest
import yaml
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_state
from test_project_review import _PROJECT_ID, _app, _bundle, _git, _registry

from kiro_crew import project_git, sandbox
from kiro_crew.dashboard import handlers_project
from kiro_crew.project_git import GitProjectStore, ProjectSandboxUnavailableError


@pytest.fixture
def denied_sandbox(monkeypatch):
    monkeypatch.setattr(sandbox, "userns_available", lambda: False)
    monkeypatch.setattr(sandbox, "unavailable_reason", lambda: "unshare: EPERM")
    spawn = Mock(side_effect=AssertionError("Git must not spawn without its sandbox"))
    monkeypatch.setattr(project_git, "sandboxed_spawn_argv", spawn)
    monkeypatch.setattr(project_git, "run_limited", spawn)
    return spawn


@pytest.mark.parametrize("helper_only", [False, True])
def test_git_refuses_before_spawning(tmp_path, denied_sandbox, helper_only):
    with pytest.raises(ProjectSandboxUnavailableError) as caught:
        if helper_only:
            GitProjectStore._credential_helper_env()
        else:
            GitProjectStore._run_git(tmp_path, "status")
    assert caught.value.code == "project_sandbox_unavailable"
    assert "user namespaces are disabled" in str(caught.value)
    assert str(caught.value).endswith("unshare: EPERM")
    denied_sandbox.assert_not_called()


def test_sandbox_failure_without_probe_detail(tmp_path, monkeypatch, denied_sandbox):
    monkeypatch.setattr(sandbox, "unavailable_reason", lambda: "")
    with pytest.raises(ProjectSandboxUnavailableError) as caught:
        GitProjectStore._run_git(tmp_path, "status")
    assert str(caught.value).endswith("(user namespaces are disabled)")
    denied_sandbox.assert_not_called()


def test_spawn_sandbox_failure_keeps_its_error_code(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "userns_available", lambda: True)
    monkeypatch.setattr(sandbox, "unavailable_reason", lambda: "unshare: EPERM")
    monkeypatch.setattr(GitProjectStore, "_credential_helper_env", lambda: {})
    spawn = Mock(
        side_effect=sandbox.SandboxUnavailableError(
            "sandbox denied", "no_backend", "unshare: EPERM"
        )
    )
    monkeypatch.setattr(project_git, "sandboxed_spawn_argv", spawn)
    with pytest.raises(ProjectSandboxUnavailableError) as caught:
        GitProjectStore._run_git(tmp_path, "status")
    assert caught.value.code == "project_sandbox_unavailable"
    assert str(caught.value).endswith("unshare: EPERM")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["url-add", "local-add", "sync", "review"])
async def test_sandbox_denied_operations_return_503(tmp_path, denied_sandbox, operation):
    bundle = _bundle(tmp_path)
    registry = _registry(tmp_path)
    if operation in {"local-add", "review"}:
        manifest = yaml.safe_load((bundle / "project.yaml").read_text())
        manifest["sources"] = [
            {"type": "repo", "url": "https://example.com/source.git", "role": "primary"}
        ]
        (bundle / "project.yaml").write_text(yaml.safe_dump(manifest))
    if operation == "sync":
        bundle = _bundle(registry.projects_dir / "managed" / _PROJECT_ID)
        _git(bundle, "init", "-b", "main")
        registry.add_managed(bundle, remote="https://example.com/bundle.git", default_branch="main")
    elif operation == "review":
        registry.add_local(bundle)
    state = _make_state(tmp_path / "sessions")
    app = _app(state, registry)
    app.router.add_post("/api/project-bundles/{id}/sync", handlers_project.api_project_sync)
    async with TestClient(TestServer(app)) as client:
        if operation in {"url-add", "local-add"}:
            source = "https://example.com/bundle.git" if operation == "url-add" else str(bundle)
            response = await client.post("/api/project-bundles/add", json={"source": source})
        elif operation == "sync":
            response = await client.post(f"/api/project-bundles/{_PROJECT_ID}/sync")
        else:
            response = await client.post(
                f"/api/project-bundles/{_PROJECT_ID}/review", json={"digest": "shown"}
            )
        assert response.status == 503
        assert await response.json() == {
            "code": "project_sandbox_unavailable",
            "error": (
                "Project git operations run in the Kiro Crew sandbox, which this host "
                "does not allow (user namespaces are disabled): unshare: EPERM"
            ),
        }
    denied_sandbox.assert_not_called()
