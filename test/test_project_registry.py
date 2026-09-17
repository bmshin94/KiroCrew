"""Thin Project registry and read-only session attachment (no Git required)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kiro_crew.project_manifest import create_project_manifest
from kiro_crew.project_registry import ProjectRegistry, ProjectRegistryError
from kiro_crew.project_sessions import (
    ProjectSessionError,
    resolve_project_attachment,
)


def _registry(tmp_path: Path) -> ProjectRegistry:
    return ProjectRegistry(
        projects_dir=tmp_path / "projects",
        registry_dir=tmp_path / "projects-registry",
    )


def _local_bundle(tmp_path: Path, name: str) -> Path:
    bundle = tmp_path / name
    create_project_manifest(bundle, name=name)
    return bundle


def test_add_local_get_list_resolve_unregister(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    bundle = _local_bundle(tmp_path, "alpha")
    project = registry.add_local(bundle)

    assert registry.get(project.id).name == "alpha"
    assert [p.id for p in registry.list_projects()] == [project.id]
    assert registry.resolve("alpha").id == project.id
    assert registry.resolve(project.id).id == project.id

    registry.unregister(project.id)
    with pytest.raises(ProjectRegistryError):
        registry.get(project.id)


def test_registry_storage_lives_under_the_fenced_dir(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.add_local(_local_bundle(tmp_path, "beta"))
    # registry.json is written under the fenced registry_dir, NOT the visible
    # projects_dir where materialized checkouts live.
    assert registry.registry_path.exists()
    assert registry.registry_path.parent == (tmp_path / "projects-registry")
    assert not (tmp_path / "projects" / "registry.json").exists()


def test_resolve_attachment_for_self_workspace_builds_a_brief(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    project = registry.add_local(_local_bundle(tmp_path, "gamma"))

    attachment = resolve_project_attachment(project.id, registry=registry)
    assert attachment.project_id == project.id
    assert attachment.name == "gamma"
    # A source-less local bundle is its own workspace ("self").
    assert attachment.workspace_dir == (tmp_path / "gamma").resolve()
    assert attachment.repositories == ()
    assert "Project: gamma" in attachment.brief


def test_resolve_attachment_unknown_project_is_project_not_found(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    with pytest.raises(ProjectSessionError) as exc:
        resolve_project_attachment("11111111-1111-4111-8111-111111111111", registry=registry)
    assert exc.value.code == "project_not_found"
