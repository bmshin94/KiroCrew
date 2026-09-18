"""The live-target pointer's spawn refusal has to reach a HUMAN, not just the log.

``sandbox._materialize_live_target_mask_target`` is fail-closed on every Linux spawn: a
symlink, a non-regular file, or a second hard link on ``live_target.json`` means a bind
mask over that name would leave another writable path to the bytes the gateway
``execve``s into, so the launcher refuses rather than run an agent with the hole open.
That refusal is right. Its VISIBILITY was not:

* the operator's only notice was a ``logger.warning`` in the gateway log plus agents that
  stopped starting -- and the shapes that trigger it are ordinary operation for something
  else on the host (``cp -al``, rsnapshot, a dotfile manager keeping the pointer as a
  link), so nobody did anything wrong and nobody has a reason to read that log;
* on the CLI the refusal is not in the ``AcpError`` hierarchy, so ``_stream_and_print``'s
  handler never saw it and it escaped as a stack trace that reads as a Kiro Crew crash;
* nothing named the condition BEFORE a spawn tried and failed.

So this file pins three surfaces and the one property that keeps them honest: they all
say the SAME sentence, because they all read it from the same formatter. A paraphrase in
any one of them is a second diagnosis of one file, and the drift guard here fails on it.
"""

from __future__ import annotations

import inspect
import os
import sys

import pytest

from kiro_crew import cli_chat, cli_doctor, sandbox

_POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="POSIX link semantics")
_LINUX_ONLY = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="the refusal is on the Linux launcher path"
)


@pytest.fixture()
def crew_home(tmp_path, monkeypatch):
    """An isolated crew data home, so no test here reads the developer's own pointer."""
    home = tmp_path / ".kiro" / "crew"
    home.mkdir(parents=True)
    monkeypatch.setattr(sandbox, "config_dir", lambda: home)
    monkeypatch.setattr(sandbox.Path, "home", staticmethod(lambda: tmp_path))
    return home


def _pointer(crew_home):
    return crew_home / sandbox._LIVE_TARGET_LEAF


class TestTheProbeClassifiesWhatTheLauncherWouldRefuse:
    """``live_target_pointer_unfitness`` is the pre-spawn read: same verdict, no spawn.

    It must agree with the launcher in BOTH directions. A false alarm sends an operator
    to remove a file that was fine; a miss leaves the condition invisible, which is the
    whole defect.
    """

    def test_a_healthy_pointer_reports_nothing(self, crew_home) -> None:
        _pointer(crew_home).write_text('{"checkout": "/real"}\n', encoding="utf-8")

        assert sandbox.live_target_pointer_unfitness() is None

    def test_an_absent_pointer_reports_nothing(self, crew_home) -> None:
        """Absent is FIT: the materialiser publishes the absent-equivalent stub for it,
        which is the reason that function exists. Reporting absence would fire on every
        fresh install."""
        assert not _pointer(crew_home).exists()

        assert sandbox.live_target_pointer_unfitness() is None

    def test_an_absent_data_home_reports_nothing(self, tmp_path, monkeypatch) -> None:
        """A host with no install is not a host with a broken pointer."""
        monkeypatch.setattr(sandbox, "config_dir", lambda: tmp_path / "nope")

        assert sandbox.live_target_pointer_unfitness() is None

    @_POSIX_ONLY
    def test_a_second_hard_link_is_reported(self, crew_home) -> None:
        """The snapshot-tool case from the issue: ``cp -al`` / rsnapshot raise link counts
        as normal operation, so this is the shape an operator meets without touching
        anything."""
        pointer = _pointer(crew_home)
        pointer.write_text('{"checkout": "/real"}\n', encoding="utf-8")
        os.link(pointer, crew_home / "snapshot-alias")

        unfit = sandbox.live_target_pointer_unfitness()

        assert unfit is not None
        assert unfit.kind == sandbox.LIVE_TARGET_UNFIT_MULTILINK
        assert unfit.path == str(pointer)
        # The remedy names the command, not just the condition: the operator does not
        # know which OTHER path shares the inode, so "remove the extra link" alone names
        # no file to remove.
        assert "-samefile" in unfit.detail
        assert str(pointer) in unfit.detail

    @_POSIX_ONLY
    def test_a_symlink_is_reported(self, crew_home, tmp_path) -> None:
        elsewhere = tmp_path / "elsewhere.json"
        elsewhere.write_text("{}\n", encoding="utf-8")
        _pointer(crew_home).symlink_to(elsewhere)

        unfit = sandbox.live_target_pointer_unfitness()

        assert unfit is not None
        assert unfit.kind == sandbox.LIVE_TARGET_UNFIT_SYMLINK
        assert str(elsewhere) in unfit.detail

    @_POSIX_ONLY
    def test_a_dangling_symlink_is_reported_too(self, crew_home, tmp_path) -> None:
        """A link with no referent is the same hole with the target missing, so it must
        not read as healthy just because ``exists()`` is False."""
        _pointer(crew_home).symlink_to(tmp_path / "gone.json")

        unfit = sandbox.live_target_pointer_unfitness()

        assert unfit is not None
        assert unfit.kind == sandbox.LIVE_TARGET_UNFIT_SYMLINK

    @_POSIX_ONLY
    def test_a_special_file_is_reported(self, crew_home) -> None:
        os.mkfifo(_pointer(crew_home))

        unfit = sandbox.live_target_pointer_unfitness()

        assert unfit is not None
        assert unfit.kind == sandbox.LIVE_TARGET_UNFIT_IRREGULAR
        assert "non-regular file" in unfit.detail


class TestTheProbeAndTheRefusalSayOneThing:
    """The drift guard, and the reason the formatters exist.

    Three surfaces now report this condition (doctor, the chat card, the CLI). If any of
    them carried its own wording, an operator who saw the doctor line and later hit the
    refusal would read two descriptions of one file and have to work out they are the
    same problem. So the assertion is not "both mention hard links" -- it is that the
    launcher's exception text CONTAINS the probe's sentence verbatim.
    """

    @_POSIX_ONLY
    def test_the_multilink_refusal_carries_the_probe_sentence_verbatim(self, crew_home) -> None:
        pointer = _pointer(crew_home)
        pointer.write_text('{"checkout": "/real"}\n', encoding="utf-8")
        os.link(pointer, crew_home / "snapshot-alias")
        unfit = sandbox.live_target_pointer_unfitness()
        assert unfit is not None

        with pytest.raises(sandbox.SandboxCeilingUnsealable) as err:
            sandbox._materialize_live_target_mask_target()

        assert unfit.detail in str(err.value)

    @_POSIX_ONLY
    def test_the_symlink_refusal_carries_the_probe_sentence_verbatim(
        self, crew_home, tmp_path
    ) -> None:
        elsewhere = tmp_path / "elsewhere.json"
        elsewhere.write_text("{}\n", encoding="utf-8")
        _pointer(crew_home).symlink_to(elsewhere)
        unfit = sandbox.live_target_pointer_unfitness()
        assert unfit is not None

        with pytest.raises(sandbox.SandboxCeilingUnsealable) as err:
            sandbox._materialize_live_target_mask_target()

        assert unfit.detail in str(err.value)

    @_POSIX_ONLY
    def test_the_irregular_refusal_carries_the_probe_sentence_verbatim(self, crew_home) -> None:
        os.mkfifo(_pointer(crew_home))
        unfit = sandbox.live_target_pointer_unfitness()
        assert unfit is not None

        with pytest.raises(sandbox.SandboxCeilingUnsealable) as err:
            sandbox._materialize_live_target_mask_target()

        assert unfit.detail in str(err.value)

    @_POSIX_ONLY
    def test_a_dangling_link_also_refuses_through_the_pointers_own_check(
        self, crew_home, tmp_path
    ) -> None:
        """The pointer's own symlink check replaced two generic helpers, so it must refuse
        the SUPERSET they refused between them -- a dangling link included."""
        _pointer(crew_home).symlink_to(tmp_path / "gone.json")
        unfit = sandbox.live_target_pointer_unfitness()
        assert unfit is not None

        with pytest.raises(sandbox.SandboxCeilingUnsealable) as err:
            sandbox._materialize_live_target_mask_target()

        assert unfit.detail in str(err.value)


class TestDoctorNamesItBeforeTheNextSpawn:
    """``kirocrew doctor`` is where an operator looks when agents stop starting."""

    @_LINUX_ONLY
    def test_a_healthy_pointer_prints_nothing(self, crew_home, capsys) -> None:
        """Silent on a healthy host, like the installer-residue section: a line every run
        for the normal state is noise, and noise is what stops people reading doctor."""
        _pointer(crew_home).write_text('{"checkout": "/real"}\n', encoding="utf-8")
        issues: list[str] = []

        cli_doctor._doctor_live_target_pointer(issues)

        assert capsys.readouterr().out == ""
        assert issues == []

    @_LINUX_ONLY
    @_POSIX_ONLY
    def test_a_multi_linked_pointer_is_reported_with_its_remedy(self, crew_home, capsys) -> None:
        pointer = _pointer(crew_home)
        pointer.write_text('{"checkout": "/real"}\n', encoding="utf-8")
        os.link(pointer, crew_home / "snapshot-alias")
        issues: list[str] = []

        cli_doctor._doctor_live_target_pointer(issues)

        out = capsys.readouterr().out
        assert "Live Target Pointer" in out
        assert str(pointer) in out
        # The remedy has to survive the wrapper, or the operator reads the condition and
        # not the fix. Matched on the distinctive token rather than the whole sentence,
        # which _print_wrapped may break across lines.
        assert "-samefile" in out
        assert issues == ["live-target pointer"]

    @_LINUX_ONLY
    @_POSIX_ONLY
    def test_a_symlinked_pointer_is_reported(self, crew_home, tmp_path, capsys) -> None:
        elsewhere = tmp_path / "elsewhere.json"
        elsewhere.write_text("{}\n", encoding="utf-8")
        _pointer(crew_home).symlink_to(elsewhere)
        issues: list[str] = []

        cli_doctor._doctor_live_target_pointer(issues)

        out = capsys.readouterr().out
        assert "SYMLINK" in out
        assert issues == ["live-target pointer"]

    @_LINUX_ONLY
    @_POSIX_ONLY
    def test_the_find_command_is_printed_unbroken(self, crew_home, capsys) -> None:
        """A remedy the operator cannot copy is not a remedy.

        Doctor wraps details at 80 columns, and ``textwrap``'s defaults split long words
        AND break after an embedded hyphen -- so a real data-home path came out cut in the
        middle of a directory name, and again right after a hyphen inside a component,
        turning the ``find <dir> -samefile <file>`` line into fragments that run as
        nothing. Found by rendering the section rather than by reading it, which is why it
        is pinned.
        """
        pointer = _pointer(crew_home)
        pointer.write_text('{"checkout": "/real"}\n', encoding="utf-8")
        os.link(pointer, crew_home / "snapshot-alias")

        cli_doctor._doctor_live_target_pointer([])

        out = capsys.readouterr().out
        # Every whitespace-separated token of the remedy must survive on ONE line.
        printed_tokens = set(out.split())
        for token in sandbox._live_target_multilink_detail(str(pointer), 2).split():
            assert token in printed_tokens, f"the wrap split {token!r} across lines"

    @_LINUX_ONLY
    def test_a_broken_probe_is_reported_as_unknown_not_as_a_verdict(
        self, monkeypatch, capsys
    ) -> None:
        """Doctor must not turn its own failure into a claim about the host: an
        unreadable data home is "could not check", never "spawns will be refused"."""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(
            sandbox,
            "live_target_pointer_unfitness",
            lambda: (_ for _ in ()).throw(OSError("probe exploded")),
        )
        issues: list[str] = []

        cli_doctor._doctor_live_target_pointer(issues)

        out = capsys.readouterr().out
        assert "could not check" in out
        assert "REFUSED" not in out
        assert issues == []

    def test_a_platform_that_cannot_hit_the_refusal_stays_silent(self, monkeypatch, capsys) -> None:
        """The refusal is on the namespace launcher's path. A Seatbelt profile denies by
        path rule and needs no mount target, so naming it on macOS would promise a spawn
        outage that does not happen."""
        monkeypatch.setattr(sys, "platform", "darwin")
        called: list[None] = []
        monkeypatch.setattr(
            sandbox, "live_target_pointer_unfitness", lambda: called.append(None) or None
        )
        issues: list[str] = []

        cli_doctor._doctor_live_target_pointer(issues)

        assert capsys.readouterr().out == ""
        assert called == [], "the probe must not even run where the refusal cannot happen"
        assert issues == []

    def test_doctor_runs_the_check_right_after_the_sandbox_section(self) -> None:
        """Wired into the run, not merely defined. Pinned by ORDER too: an operator who
        just read the sandbox verdict is the one who needs to know a spawn will be
        refused for a reason that verdict cannot express."""
        source = inspect.getsource(cli_doctor._doctor)
        assert "_doctor_live_target_pointer(issues)" in source
        assert source.index("_doctor_sandbox(issues)") < source.index(
            "_doctor_live_target_pointer(issues)"
        )


class TestTheRefusalReachesTheChatCard:
    """The dashboard surface for an agent spawn, pinned at the two places it can break.

    The refusal escapes ``_run_chat``'s ACP handler chain (it is a ``RuntimeError``, not
    an ``AcpError``) and lands in its terminal ``except Exception`` handler, which appends
    ``str(exc)`` as the error row. Two things have to hold for the remedy to arrive, and
    each is a separate way the card could go quiet:

    * the handler must keep using the exception's own text -- a generic "agent failed to
      start" there is exactly the substitution the issue asked about;
    * the refusal must not classify as TRANSIENT, or the retry ladder absorbs it into
      "Connection lost -- retrying..." and the operator never sees a reason at all.
    """

    def test_the_terminal_handler_appends_the_exceptions_own_text(self) -> None:
        from kiro_crew.dashboard import chat_runner

        source = inspect.getsource(chat_runner._run_chat)
        terminal = source[source.rindex("except Exception as exc:") :]
        assert "_err_text, _ = redact_exfiltration_urls(str(exc))" in terminal
        assert 'slot.append("error", _err_text' in terminal

    def test_the_refusal_survives_the_cards_redaction_unchanged(self, crew_home) -> None:
        """The card runs the text through the exfiltration-URL and credential scrubbers
        before appending it. A remedy containing a path and a ``find`` invocation must
        come out the other side intact -- a scrubbed ``find`` line is a remedy the
        operator cannot run."""
        from kiro_crew.security import redact_credentials, redact_exfiltration_urls

        detail = sandbox._live_target_multilink_detail(str(_pointer(crew_home)), 2)

        scrubbed, _ = redact_exfiltration_urls(detail)
        scrubbed, _ = redact_credentials(scrubbed)

        assert scrubbed == detail

    def test_the_refusal_is_not_retryable(self) -> None:
        """A transient verdict would put this in the "Connection lost -- retrying..."
        ladder: the card would show no reason, and the retries would fail identically
        because nothing about the host changed between them."""
        from kiro_crew.llm_helpers import acp_error_is_transient

        refusal = sandbox.SandboxCeilingUnsealable(
            sandbox._live_target_multilink_detail("/home/u/.kiro/crew/live_target.json", 2)
        )

        assert acp_error_is_transient(refusal) is False


class TestTheRefusalReachesTheCli:
    """``kirocrew chat`` printed a stack trace for a host with one extra file link."""

    def test_the_refusal_prints_the_remedy_and_exits_nonzero(self, monkeypatch, capsys) -> None:
        detail = sandbox._live_target_multilink_detail("/home/u/.kiro/crew/live_target.json", 2)

        def _boom(coro, *_args, **_kwargs):
            # Close it rather than drop it: an abandoned coroutine emits a
            # "never awaited" RuntimeWarning that would make this test dirty the run.
            coro.close()
            raise sandbox.SandboxCeilingUnsealable(detail)

        monkeypatch.setattr(cli_chat.asyncio, "run", _boom)

        with pytest.raises(SystemExit) as exit_info:
            cli_chat._run_chat("hello", None)

        assert exit_info.value.code == 1
        err = capsys.readouterr().err
        assert "-samefile" in err
        assert "kirocrew doctor" in err

    def test_a_sigint_still_exits_cleanly(self, monkeypatch, capsys) -> None:
        """Guard the guard: the new handler must not have displaced the Ctrl-C path."""

        def _interrupt(coro, *_args, **_kwargs):
            coro.close()
            raise KeyboardInterrupt

        monkeypatch.setattr(cli_chat.asyncio, "run", _interrupt)

        cli_chat._run_chat("hello", None)

        assert "Bye!" in capsys.readouterr().out
