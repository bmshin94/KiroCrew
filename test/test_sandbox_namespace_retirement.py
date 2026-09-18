"""Launcher-owned records retire without granting a historical sweep authority.

The subprocess runs the generated parent and both real pipe handshakes. Only
privileged namespace/map operations and the post-handshake child payload are
substituted; signals target only the generated test parent. No host mounts or
credentials are involved.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from kiro_crew import platform_compat, sandbox

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux launcher lifecycle")


@pytest.fixture
def launcher(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setattr(sandbox, "config_dir", lambda: home)
    monkeypatch.setattr(sandbox.Path, "home", lambda: home)
    monkeypatch.setattr(sandbox, "_ssh_supports_accept_new", lambda: False)
    monkeypatch.setattr(sandbox, "_private_memory_view_setup", lambda *args: "")
    return home


def _run_launcher(home, *, fault="", code=0, private=False):
    source = sandbox._build_launcher_script(private_memory=private)
    # Keep the generated parent and child handshake verbatim. Kernel mounting
    # is covered by the sandbox suite, not by these publication lifecycle tests.
    child_start = source.index("        # Private mount propagation", source.index("def main():"))
    footer = source.index('if __name__ == "__main__":')
    source = (
        source[:child_start]
        + '        if FAULT == "blocked-wait":\n'
        + "            os.close(hold_w)\n"
        + "            os.read(hold_r, 1)\n"
        + "        sys.exit(CHILD_CODE)\n\n"
        + source[footer:]
    )
    harness = textwrap.dedent("""
        import builtins
        import io
        from pathlib import Path

        original_open = builtins.open
        original_write = os.write
        original_wait = os.waitpid
        original_replace = os.replace
        original_dump = json.dump
        original_mkstemp = tempfile.mkstemp
        original_unlink = os.unlink
        original_read = os.read
        hold_r, hold_w = os.pipe()
        import signal
        def read(fd, size):
            if FAULT == "blocked-read" and os.getpid() == int(os.environ["KIROCREW_HOST_PID"]):
                print("blocked", flush=True)
            return original_read(fd, size)
        os.read = read
        def interrupt(stage):
            if FAULT in {"sigterm-" + stage, "sigint-" + stage}:
                signum = signal.SIGINT if FAULT.startswith("sigint-") else signal.SIGTERM
                signal.raise_signal(signum)
                signal.raise_signal(signum)
        def mkstemp(*args, **kwargs):
            result = original_mkstemp(*args, **kwargs)
            interrupt("staging")
            return result
        tempfile.mkstemp = mkstemp
        def unlink(*args, **kwargs):
            interrupt("cleanup")
            return original_unlink(*args, **kwargs)
        os.unlink = unlink
        if FAULT.startswith("target-"):
            pids = Path(HOME) / "member-memory-bindings" / "pids"
            pids.mkdir(parents=True, exist_ok=True)
            target = pids / (str(os.getpid()) + ".namespace.json")
            marker = Path(HOME) / "foreign"
            marker.write_text("foreign")
            if FAULT == "target-symlink":
                target.symlink_to(marker)
            elif FAULT == "target-directory":
                target.mkdir()
            elif FAULT == "target-hardlink":
                os.link(marker, target)
        class Kernel:
            calls = 0
            def unshare(self, flags):
                self.calls += 1
                if FAULT == "blocked-read":
                    os.close(hold_w)
                    os.read(hold_r, 1)
                    sys.exit(0)
                return 1 if FAULT == "setup" and self.calls == 2 else 0
        _libc = Kernel()
        def map_open(path, *args, **kwargs):
            if str(path).startswith("/proc/") and str(path).endswith(
                ("/setgroups", "/uid_map", "/gid_map")
            ):
                if FAULT == "maps":
                    raise PermissionError("synthetic map failure")
                return io.StringIO()
            return original_open(path, *args, **kwargs)
        builtins.open = map_open
        def dump(row, handle, *args, **kwargs):
            if FAULT == "serialization":
                handle.write("partial")
                raise OSError("synthetic serialization failure")
            return original_dump(row, handle, *args, **kwargs)
        json.dump = dump
        def replace(src, dst, *args, **kwargs):
            if FAULT == "publication":
                raise PermissionError("synthetic publication failure")
            interrupt("publication")
            result = original_replace(src, dst, *args, **kwargs)
            interrupt("published")
            return result
        os.replace = replace
        def write(fd, data):
            if FAULT == "brokenpipe" and data == b"n":
                # Only fail the parent's release, not the child's readiness.
                if os.getpid() == int(os.environ["KIROCREW_HOST_PID"]):
                    raise BrokenPipeError("synthetic release failure")
            return original_write(fd, data)
        os.write = write
        def wait(pid, options):
            record = Path(HOME) / "member-memory-bindings" / "pids" / (
                str(os.getpid()) + ".namespace.json"
            )
            row = json.loads(record.read_text())
            # Age is not retirement authority, even for a long-running V1.
            os.utime(record, (1, 1))
            print(json.dumps(row), flush=True)
            interrupt("wait")
            result = original_wait(pid, options)
            assert record.is_file(), "retired before wait returned"
            if FAULT == "replacement":
                other = record.with_suffix(".new")
                other.write_text("replacement")
                original_replace(other, record)
            if FAULT == "wait":
                raise OSError("synthetic wait failure after reaping")
            return result
        os.waitpid = wait
        """)
    source = source.replace(
        'if __name__ == "__main__":',
        f"FAULT = {fault!r}\nCHILD_CODE = {code!r}\nHOME = {str(home)!r}\n"
        + harness
        + '\nif __name__ == "__main__":',
    )
    script = home.parent / "launcher.py"
    script.write_text(source, encoding="utf-8")
    if fault.startswith("blocked-"):
        import select
        import signal

        with subprocess.Popen(
            [sys.executable, "-I", "-S", str(script), "synthetic-child"],
            cwd=home,
            env={"HOME": str(home), "TMPDIR": str(home.parent), "KIROCREW_HOME": str(home)},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        ) as process:
            try:
                assert select.select([process.stdout], [], [], 5)[0], "no wait handshake"
                process.send_signal(signal.SIGTERM)
                stdout, stderr = process.communicate(timeout=5)
                return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)
    return subprocess.run(
        [sys.executable, "-I", "-S", str(script), "synthetic-child"],
        cwd=home,
        env={"HOME": str(home), "TMPDIR": str(home.parent), "KIROCREW_HOME": str(home)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )


@pytest.mark.parametrize("private", [False, True])
@pytest.mark.parametrize(
    "fault,code",
    [
        ("", 0),
        ("", 7),
        ("maps", 0),
        ("setup", 0),
        ("brokenpipe", 0),
        ("serialization", 0),
        ("publication", 0),
        ("wait", 0),
    ],
)
def test_generated_launcher_retires_owned_names(launcher, private, fault, code):
    pids = launcher / "member-memory-bindings" / "pids"
    pids.mkdir(parents=True, mode=0o700)
    foreign = {"123.namespace.json": "legacy", "tmpforeign.tmp": "inflight", "123.json": "binding"}
    for name, content in foreign.items():
        (pids / name).write_text(content)
    result = _run_launcher(launcher, fault=fault, code=code, private=private)
    assert result.returncode == (1 if fault else code), result.stderr
    assert {p.name: p.read_text() for p in pids.iterdir()} == foreign
    if not fault or fault == "wait":
        row = json.loads(result.stdout)
        assert row["private_memory"] is private
        assert row["process_start"]
        assert len(row["namespaces"]) == 2


def test_generated_launcher_preserves_replacement(launcher):
    result = _run_launcher(launcher, fault="replacement")
    assert result.returncode == 0, result.stderr
    pids = launcher / "member-memory-bindings" / "pids"
    assert [p.read_text() for p in pids.iterdir()] == ["replacement"]


def _helpers():
    tree = ast.parse(sandbox._build_launcher_script())
    names = {"_namespace_record_directory", "_retire_namespace_record"}
    selected: list[ast.stmt] = [
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names
    ]
    assert len(selected) == 2
    namespace = {
        "os": os,
        "stat": __import__("stat"),
        "sys": sys,
        "REAL_UID": platform_compat.local_user_id(),
    }
    exec(
        compile(ast.Module(body=selected, type_ignores=[]), "<launcher-helpers>", "exec"), namespace
    )
    return SimpleNamespace(**namespace)


@pytest.mark.parametrize("component", ["member-memory-bindings", "pids"])
def test_linked_protected_directory_is_refused(launcher, component):
    foreign = launcher.parent / "foreign"
    foreign.mkdir()
    marker = foreign / "keep"
    marker.write_text("foreign")
    parent = launcher
    if component == "pids":
        parent = launcher / "member-memory-bindings"
        parent.mkdir(mode=0o700)
    (parent / component).symlink_to(foreign, target_is_directory=True)
    with pytest.raises(OSError):
        _helpers()._namespace_record_directory()
    assert list(foreign.iterdir()) == [marker]
    assert marker.read_text() == "foreign"


@pytest.mark.parametrize(
    "mutation", ["symlink", "directory", "replacement", "hardlink", "unknown", "foreign"]
)
def test_retirement_preserves_unowned_or_unknown_entry(launcher, monkeypatch, mutation):
    helper = _helpers()
    directory = helper._namespace_record_directory()
    pids = launcher / "member-memory-bindings" / "pids"
    target = pids / "owned.namespace.json"
    target.write_text("owned")
    owned = os.open(target, os.O_RDONLY)
    real_stat = os.stat
    try:
        if mutation in {"symlink", "directory", "replacement"}:
            target.unlink()
            if mutation == "symlink":
                target.symlink_to(launcher / "absent")
            elif mutation == "directory":
                target.mkdir()
            else:
                target.write_text("new publication")
        elif mutation == "hardlink":
            os.link(target, pids / "another-name")
        elif mutation in {"unknown", "foreign"}:

            def inspect(path, *args, **kwargs):
                if path == target.name:
                    if mutation == "unknown":
                        raise PermissionError("synthetic probe failure")
                    info = real_stat(path, *args, **kwargs)
                    return SimpleNamespace(st_mode=info.st_mode, st_uid=-1)
                return real_stat(path, *args, **kwargs)

            monkeypatch.setattr(os, "stat", inspect)
        helper._retire_namespace_record(directory, target.name, owned)
        assert os.path.lexists(target)
    finally:
        os.close(owned)
        os.close(directory)


def test_retirement_stays_in_pinned_directory(launcher):
    helper = _helpers()
    directory = helper._namespace_record_directory()
    pids = launcher / "member-memory-bindings" / "pids"
    target = pids / "owned.namespace.json"
    target.write_text("owned")
    owned = os.open(target, os.O_RDONLY)
    moved = pids.with_name("moved")
    foreign = launcher.parent / "foreign"
    foreign.mkdir()
    (foreign / target.name).write_text("foreign")
    try:
        pids.rename(moved)
        pids.symlink_to(foreign, target_is_directory=True)
        helper._retire_namespace_record(directory, target.name, owned)
        assert not (moved / target.name).exists()
        assert (foreign / target.name).read_text() == "foreign"
    finally:
        os.close(owned)
        os.close(directory)


@pytest.mark.parametrize("kind", ["symlink", "directory", "hardlink"])
def test_generated_launcher_refuses_unsafe_target(launcher, kind):
    result = _run_launcher(launcher, fault=f"target-{kind}")
    assert result.returncode == 1
    assert "unsafe namespace record target" in result.stderr
    assert (launcher / "foreign").read_text() == "foreign"
    targets = list((launcher / "member-memory-bindings" / "pids").iterdir())
    assert len(targets) == 1
    target = targets[0]
    if kind == "symlink":
        assert target.is_symlink()
    elif kind == "directory":
        assert target.is_dir()
    else:
        assert target.stat().st_nlink == 2


@pytest.mark.parametrize("component", ["home", "member-memory-bindings", "pids"])
def test_writable_protected_directory_is_refused(launcher, component):
    pids = launcher / "member-memory-bindings" / "pids"
    pids.mkdir(parents=True, mode=0o700)
    path = {"home": launcher, "member-memory-bindings": pids.parent, "pids": pids}[component]
    platform_compat.chmod_safe(path, 0o777)
    try:
        with pytest.raises(PermissionError, match="unsafe namespace record directory"):
            _helpers()._namespace_record_directory()
    finally:
        platform_compat.chmod_safe(path, 0o700)


def test_maintenance_preserves_old_live_v1_and_unknown_backlog(launcher, monkeypatch):
    from kiro_crew import member_memory_auth as auth

    pids = launcher / "member-memory-bindings" / "pids"
    pids.mkdir(parents=True, mode=0o700)
    pid = os.getpid()
    start = platform_compat.get_process_start_id(pid)
    assert start
    namespaces = []
    for kind in ("user", "mnt"):
        info = Path(f"/proc/{pid}/ns/{kind}").stat()
        namespaces.append([info.st_dev, info.st_ino])
    record = pids / f"{pid}.namespace.json"
    record.write_text(
        json.dumps({"process_start": start, "namespaces": namespaces, "private_memory": False})
    )
    os.utime(record, (1, 1))
    auth.publish_member_session_pid(pid, "dashboard:global", home=launcher, memory_store="")
    for index in range(1000):
        (pids / f"tmp-unknown-{index}.tmp").write_text("unknown publisher")
    (pids / "1.namespace.json").write_text("stale or unknown")
    before = {p.name: p.read_bytes() for p in pids.iterdir()}
    for name in (
        "_cleanup_stale_sandbox_mount_sources",
        "_cleanup_legacy_mount_source_residue",
        "_cleanup_retired_acp_snapshot_dir",
    ):
        monkeypatch.setattr(sandbox, name, lambda *args: 0)
    assert (
        sandbox.cleanup_stale_sandbox_profiles(
            data_home=launcher, legacy_dir=str(launcher / "absent-legacy")
        )
        == 0
    )
    assert {p.name: p.read_bytes() for p in pids.iterdir()} == before
    assert auth._matches_published_sandbox_namespace(pid, pid, start, launcher)


@pytest.mark.parametrize("stage", ["staging", "publication", "published", "wait", "cleanup"])
@pytest.mark.parametrize("code", [0, 7])
@pytest.mark.parametrize("signal_name,signal_code", [("sigterm", 143), ("sigint", 130)])
def test_generated_launcher_signal_retires_owned_names(
    launcher, stage, code, signal_name, signal_code
):
    result = _run_launcher(launcher, fault=f"{signal_name}-{stage}", code=code)
    pids = launcher / "member-memory-bindings" / "pids"
    assert list(pids.iterdir()) == []
    assert result.returncode == (code if stage == "cleanup" else signal_code), result.stderr


@pytest.mark.parametrize("stage", ["blocked-read", "blocked-wait"])
def test_generated_launcher_interrupts_blocking_wait(launcher, stage):
    result = _run_launcher(launcher, fault=stage)
    assert result.returncode == 143, result.stderr
    pids = launcher / "member-memory-bindings" / "pids"
    assert not pids.exists() or list(pids.iterdir()) == []
