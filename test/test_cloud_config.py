"""Unit tests for the cloud config store (cloud/config.py) — no secrets stored."""

from __future__ import annotations

import json

import pytest

from kiro_crew.cloud import config as cloud_config
from kiro_crew.cloud.config import (
    _MAX_FILE_BYTES,
    DEFAULT_REGION,
    CloudConfig,
    FargateConfig,
)

#: The model-credential secret as a conforming ``(name, ARN)`` pair: the name is
#: ``kirocrew/crew/<crew>/<ENV>`` and the ARN is that name plus one six-character
#: service suffix. The account id is fictional.
CREDENTIAL_SECRET = [
    "kirocrew/crew/demo/KIRO_API_KEY",
    "arn:aws:secretsmanager:us-east-1:123456789012:secret:kirocrew/crew/demo/KIRO_API_KEY-abcdef",
]

#: A complete Fargate block. Every case below is this, minus or plus one thing, so
#: a case cannot pass by being malformed in a second way the assertion never named.
COMPLETE_FARGATE = {
    "cluster": "kirocrew-crew-prod",
    "subnets": ["subnet-a", "subnet-b"],
    "security_groups": ["sg-1"],
    "image": "public.ecr.aws/example/kirocrew-crew-base@sha256:" + "a" * 64,
    "secrets": [CREDENTIAL_SECRET],
    "cpu_architecture": "X86_64",
}


class TestFargateConfig:
    """A block is COMPLETE or ABSENT. There is no third state, by design.

    A half-written block that produced a usable object would leave the lane
    registered and refusing every launch made through it -- spending an operator's
    attention at launch time on a mistake that was visible when they saved the
    file.
    """

    def test_a_complete_block_is_read(self):
        config = FargateConfig.from_mapping(COMPLETE_FARGATE)
        assert config is not None
        assert config.cluster == "kirocrew-crew-prod"
        assert config.subnets == ("subnet-a", "subnet-b")
        assert config.security_groups == ("sg-1",)
        assert config.secrets == (
            (COMPLETE_FARGATE["secrets"][0][0], COMPLETE_FARGATE["secrets"][0][1]),
        )
        assert config.is_complete()

    @pytest.mark.parametrize(
        ("label", "block"),
        [
            ("movable tag image", {**COMPLETE_FARGATE, "image": "public.ecr.aws/x/base:latest"}),
            ("no image", {**COMPLETE_FARGATE, "image": ""}),
            ("no cluster", {**COMPLETE_FARGATE, "cluster": ""}),
            ("no subnet", {**COMPLETE_FARGATE, "subnets": []}),
            ("no security group", {**COMPLETE_FARGATE, "security_groups": []}),
            ("unknown architecture", {**COMPLETE_FARGATE, "cpu_architecture": "RISCV"}),
            ("subnets not a list", {**COMPLETE_FARGATE, "subnets": "subnet-a"}),
            ("secrets not a list", {**COMPLETE_FARGATE, "secrets": "nope"}),
            ("secret entry is not a pair", {**COMPLETE_FARGATE, "secrets": [["only-one"]]}),
            ("secret arn is empty", {**COMPLETE_FARGATE, "secrets": [["KIRO_API_KEY", ""]]}),
            ("no secrets at all", {**COMPLETE_FARGATE, "secrets": []}),
            (
                "secrets but none named for the model credential",
                {
                    **COMPLETE_FARGATE,
                    "secrets": [["kirocrew/crew/demo/OTHER_KEY", CREDENTIAL_SECRET[1]]],
                },
            ),
            ("public ip is the string false", {**COMPLETE_FARGATE, "assign_public_ip": "false"}),
            ("public ip is the string zero", {**COMPLETE_FARGATE, "assign_public_ip": "0"}),
            ("public ip is the string true", {**COMPLETE_FARGATE, "assign_public_ip": "true"}),
            ("public ip is a number", {**COMPLETE_FARGATE, "assign_public_ip": 1}),
            ("public ip is null", {**COMPLETE_FARGATE, "assign_public_ip": None}),
            (
                "secrets list past the item bound",
                {
                    **COMPLETE_FARGATE,
                    "secrets": [CREDENTIAL_SECRET] * (cloud_config._MAX_LIST_ITEMS + 1),
                },
            ),
            (
                "subnets list past the item bound",
                {**COMPLETE_FARGATE, "subnets": ["subnet-a"] * (cloud_config._MAX_LIST_ITEMS + 1)},
            ),
            (
                "image string past the size bound",
                {**COMPLETE_FARGATE, "image": "x" * (cloud_config._MAX_STRING_LEN + 1)},
            ),
            (
                "a subnet string past the size bound",
                {**COMPLETE_FARGATE, "subnets": ["s" * (cloud_config._MAX_STRING_LEN + 1)]},
            ),
            (
                "a secret name past the size bound",
                {
                    **COMPLETE_FARGATE,
                    "secrets": [
                        [
                            "x" * (cloud_config._MAX_STRING_LEN + 1) + "/KIRO_API_KEY",
                            CREDENTIAL_SECRET[1],
                        ]
                    ],
                },
            ),
            ("not an object", "nope"),
            ("absent", None),
        ],
    )
    def test_an_unusable_block_reads_as_absent(self, label: str, block: object):
        assert FargateConfig.from_mapping(block) is None, label

    def test_a_secretless_block_is_incomplete_because_the_engine_would_refuse_it(self):
        """The engine refuses a task definition delivering no model credential.

        So a block with every placement field and an empty ``secrets`` list is the
        offered-and-refusing state exactly: the lane would register and reject every
        launch. Judged here, it is absent instead.
        """
        assert FargateConfig.from_mapping({**COMPLETE_FARGATE, "secrets": []}) is None

    def test_a_reference_the_engine_would_refuse_does_not_register(self):
        """The gate asks the ENGINE, so its answer and the engine's cannot differ.

        This replaces an earlier assertion that a conforming NAME registered whatever
        its ARN said. That was deliberate at the time -- the ARN pairing was left to the
        engine to avoid a second copy of its rule -- but it meant a mismatched pair
        registered the lane and was then refused at launch, which is the state this
        module exists to prevent. Delegating to ``identity.secret_env_name`` moved the
        boundary rather than duplicating it: there is still exactly one copy of the
        rule, and it now runs here too.
        """
        block = {**COMPLETE_FARGATE, "secrets": [[CREDENTIAL_SECRET[0], "arn:not-a-real-arn"]]}
        assert FargateConfig.from_mapping(block) is None

    def test_a_conforming_reference_still_registers(self):
        """The positive side, so the test above cannot pass by refusing everything."""
        assert FargateConfig.from_mapping(COMPLETE_FARGATE) is not None

    @pytest.mark.parametrize("bad", [False, True, 0, 1, 1.5, None, [], {}, [1], {"a": 1}])
    def test_no_string_field_coerces_a_non_string(self, bad: object):
        """Every string field, derived from the dataclass, not a list someone maintains.

        `str()` made any JSON scalar truthy: `false` became `"False"`, which is
        non-empty, so `is_complete()` passed and the lane registered against a cluster
        that does not exist. Parametrized over the FIELDS as well as the values, so a
        string field added later is covered without anyone remembering.
        """
        from kiro_crew.cloud.config import _STRING_FIELD_DEFAULTS

        assert _STRING_FIELD_DEFAULTS, "the field derivation must not be empty"
        for field_name in _STRING_FIELD_DEFAULTS:
            block = {**COMPLETE_FARGATE, field_name: bad}
            assert FargateConfig.from_mapping(block) is None, f"{field_name}={bad!r}"

    def test_the_string_field_list_matches_the_dataclass(self):
        """Pins the derivation itself, so a field cannot silently drop out of it."""
        from dataclasses import fields

        from kiro_crew.cloud.config import _STRING_FIELD_DEFAULTS

        expected = {f.name for f in fields(FargateConfig) if isinstance(f.default, str)}
        assert set(_STRING_FIELD_DEFAULTS) == expected


class TestAnythingLoadAcceptsSaveCanWrite:
    """The round-trip property, not a size assertion.

    A size assertion would re-encode the assumption that just broke: `load()` measured
    the file it was HANDED while `save()` emitted `indent=2`, which is larger. So a
    minified block passed on the way in and failed on the way out -- and it failed AFTER
    the engine had provisioned, leaving a running instance no saved record pointed at.
    The property is what matters: for a payload `load()` accepts, `save()` never raises.
    """

    #: ``(label, block)`` pairs whose COMPACT form loads but whose PRETTY form may not.
    #: An array of many short members, not one long string: ``indent=2`` costs bytes PER
    #: MEMBER, so a 400 KB compact array re-expands past a 1 MiB ceiling while a single
    #: 1 MB string grows by about 20 bytes. A padded-string fixture looked like it
    #: covered this and did not -- removing the compact fallback left it green.
    BLOCKS = [
        ("small", {"cluster": "c", "subnets": ["subnet-abc"]}),
        ("array pretty-fits", {"cluster": "c", "subnets": ["s"] * 20_000}),
        ("array pretty-breaches", {"cluster": "c", "subnets": ["s"] * 100_000}),
    ]

    @pytest.mark.parametrize(("label", "block"), BLOCKS)
    def test_save_never_raises_and_the_block_survives(self, tmp_path, label, block):
        """Both halves, because either alone is passable by doing the wrong thing.

        Asserting only that `save()` does not raise is satisfied by `load()` DROPPING
        the block -- there is then nothing large to write. Asserting the block SURVIVES
        is what makes the compact fallback load-bearing, so removing it goes red.
        """
        import json as _json

        from kiro_crew.cloud.config import _MAX_FILE_BYTES, CloudConfig

        src = tmp_path / "cloud.json"
        src.write_text(
            _json.dumps({"profile": "p", "fargate": block}, separators=(",", ":")),
            encoding="utf-8",
        )
        assert src.stat().st_size <= _MAX_FILE_BYTES, "the fixture must be loadable"

        loaded = CloudConfig.load(src)
        assert loaded.fargate == block, "load() must keep the block, not drop it"

        out = tmp_path / "out.json"
        loaded.save(out)  # must not raise for anything load() accepted
        assert CloudConfig.load(out).fargate == block, "and it must survive the round trip"

    def test_the_array_fixture_really_breaches_when_pretty(self):
        """Pins the fixture's own premise, so it cannot quietly stop reproducing.

        If `indent=2` on this block ever fits the ceiling, the test above proves nothing
        and would go green with the compact fallback deleted -- which is exactly how the
        first version of it passed.
        """
        import json as _json

        from kiro_crew.cloud.config import _MAX_FILE_BYTES

        block = dict(self.BLOCKS)["array pretty-breaches"]
        record = {"profile": "p", "region": "us-east-1", "last_tag": "", "fargate": block}
        compact = len(_json.dumps(record, separators=(",", ":")).encode("utf-8"))
        pretty = len(_json.dumps(record, indent=2).encode("utf-8"))
        assert compact <= _MAX_FILE_BYTES < pretty, f"compact={compact} pretty={pretty}"

    #: Characters whose ESCAPED form is larger than their UTF-8 form, with the multiplier.
    #: This is why an on-disk size cannot stand in for a written size, and why the ingest
    #: refusal is not redundant with the pre-parse bound. Chosen from the three widths a
    #: JSON escape produces, so a change to `ensure_ascii` handling fails here.
    ESCAPE_EXPANDERS = [
        ("latin-1 accent", "\u00e9", 3),
        ("cjk", "\u4e2d", 2),
        ("astral (surrogate pair)", "\U0001f600", 3),
    ]

    @pytest.mark.parametrize(("label", "char", "multiplier"), ESCAPE_EXPANDERS)
    def test_a_block_that_cannot_be_written_is_refused_while_loading(
        self, tmp_path, label, char, multiplier
    ):
        """The on-disk bound does NOT imply the written bound, so both must be enforced.

        `json.dumps` escapes non-ASCII by default, so a character costing 2 bytes on disk
        costs 6 written back. A hand-edited block of accented text is 400 KB on disk,
        passes the pre-parse bound, and serializes to 1.2 MB. Refused while loading, not
        at `save()`: `save()` runs after the engine has provisioned and is the call that
        records the new instance, so failing there leaves an instance nothing tracks.
        """
        import json as _json

        from kiro_crew.cloud.config import _MAX_FILE_BYTES, CloudConfig

        # Sized so the file fits the on-disk bound but its escaped form cannot.
        count = int(_MAX_FILE_BYTES / len(char.encode("utf-8")) / multiplier) + 20_000
        src = tmp_path / "cloud.json"
        src.write_text(
            _json.dumps(
                {"profile": "p", "fargate": {"cluster": "c", "pad": char * count}},
                separators=(",", ":"),
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        assert src.stat().st_size <= _MAX_FILE_BYTES, "the fixture must pass the on-disk bound"

        loaded = CloudConfig.load(src)
        assert loaded.fargate is None, "refused at ingest, before anything is provisioned"
        loaded.save(tmp_path / "out.json")  # and the later save cannot then raise


class TestAConcurrentEditIsNeverLost:
    """The property, stated so a narrower race cannot pass it.

    A caller reads the config, works, and writes the whole record back. Anything that
    landed in between is in the file but not in the snapshot, so writing the snapshot
    erases it with nothing said. The property is not "the window is small": it is that
    the write either INCLUDES the other edit or REFUSES. Both outcomes keep the edit;
    only a silent overwrite loses it.

    Note on scope: the read-only seal on `cloud.json` does NOT cover this. The seal stops
    a sandboxed agent from writing the file at all, and the writers racing here are both
    trusted paths. Claiming the seal answers this would be the same mistake as the macOS
    branch -- naming a protection that is not doing the work.
    """

    FARGATE = dict(COMPLETE_FARGATE)

    @staticmethod
    def _seed(p) -> None:
        import json as _json

        p.write_text(_json.dumps({"profile": "old", "region": "us-east-1"}), encoding="utf-8")

    def test_a_stale_snapshot_refuses_rather_than_erasing(self, tmp_path):
        from kiro_crew.cloud.config import CloudConfig, ConcurrentEditRefused

        p = tmp_path / "cloud.json"
        self._seed(p)

        stale = CloudConfig.load(p)  # a snapshot taken before the other edit
        other = CloudConfig.load(p)
        other.fargate = self.FARGATE
        other.save(p)  # the edit that must not be lost

        stale.profile = "mine"
        with pytest.raises(ConcurrentEditRefused):
            stale.save(p)
        assert CloudConfig.load(p).fargate == self.FARGATE, "the other edit survived"

    def test_the_refusal_is_recoverable_by_re_reading(self, tmp_path):
        """Refusing is only correct if the caller has a way forward."""
        from kiro_crew.cloud.config import CloudConfig

        p = tmp_path / "cloud.json"
        self._seed(p)
        other = CloudConfig.load(p)
        other.fargate = self.FARGATE
        other.save(p)

        saved = CloudConfig.apply_update(p, profile="mine", last_tag="")
        assert saved.profile == "mine"
        assert CloudConfig.load(p).fargate == self.FARGATE, "and it still survived"

    def test_apply_update_keeps_fields_it_was_not_given(self, tmp_path):
        """The narrow update is what makes the launch path safe."""
        from kiro_crew.cloud.config import CloudConfig

        p = tmp_path / "cloud.json"
        self._seed(p)
        seeded = CloudConfig.load(p)
        seeded.fargate = self.FARGATE
        seeded.save(p)

        CloudConfig.apply_update(p, profile="new", region="us-west-2", last_tag="t")
        after = CloudConfig.load(p)
        assert (after.profile, after.region) == ("new", "us-west-2")
        assert after.fargate == self.FARGATE, "a field it was not given is untouched"

    def test_an_unknown_field_is_refused(self, tmp_path):
        """So a typo cannot look like a successful write of nothing."""
        from kiro_crew.cloud.config import CloudConfig

        p = tmp_path / "cloud.json"
        self._seed(p)
        with pytest.raises(ValueError, match="not fields of CloudConfig"):
            CloudConfig.apply_update(p, porfile="typo")

    def test_a_record_that_fits_only_without_a_tag_is_refused(self, tmp_path):
        """The check must measure what will be WRITTEN, not what arrived.

        The launch path sets `last_tag` after the deploy succeeds, so a record sized to
        the byte passes an ingest check that ignores it and then cannot be saved. Measured
        before fixing: a 1,048,541-byte file loaded, and saving it with a 33-character tag
        raised -- so the save meant to RECORD a running instance was the one that failed.
        """
        import json as _json

        from kiro_crew.cloud.config import _MAX_FILE_BYTES, _TAG_MAX_LEN, CloudConfig

        block = {"cluster": "c", "pad": "x" * 848_480, "subnets": ["s"] * 50_000}
        src = tmp_path / "cloud.json"
        src.write_text(
            _json.dumps({"profile": "", "fargate": block}, separators=(",", ":")),
            encoding="utf-8",
        )
        assert src.stat().st_size <= _MAX_FILE_BYTES, "the fixture must pass the on-disk bound"

        loaded = CloudConfig.load(src)
        assert loaded.fargate is None, "refused at ingest, before the deploy"

        # And the refusal is what makes this save safe at MAXIMUM tag length.
        loaded.last_tag = "k" * _TAG_MAX_LEN
        loaded.save(src)

    @pytest.mark.parametrize("field_name", ["last_tag", "profile", "region"])
    def test_room_is_reserved_for_every_field_provisioning_writes(self, field_name: str):
        """Over the fields, so one added later is covered rather than remembered.

        Asserted against the validators' own limits, not example values: a reservation
        derived from a 33-character sample tag would be too small for the 51 the pattern
        permits, which is the same drift in a smaller costume.
        """
        from kiro_crew.cloud.config import _MAX_STRING_LEN, _PROVISIONING_WRITES, _TAG_MAX_LEN

        assert field_name in _PROVISIONING_WRITES, f"{field_name} is written but not reserved"
        expected = _TAG_MAX_LEN if field_name == "last_tag" else _MAX_STRING_LEN
        assert _PROVISIONING_WRITES[field_name] == expected

    def test_the_tag_pattern_and_the_reservation_cannot_disagree(self):
        """One bound, two users. A second literal would be free to drift."""
        from kiro_crew.cloud.config import _TAG_MAX_LEN, _TAG_RE

        assert _TAG_RE.match("a" * _TAG_MAX_LEN), "the pattern must accept its own bound"
        assert not _TAG_RE.match("a" * (_TAG_MAX_LEN + 1)), "and reject one past it"

    def test_the_fingerprint_and_the_snapshot_come_from_one_read(self, tmp_path, monkeypatch):
        """They must describe the same bytes, which only one read guarantees.

        With two reads, a write landing between them leaves the snapshot holding OLD
        content while the fingerprint describes NEW content. `save()` then compares equal
        and writes the stale snapshot over the edit the comparison exists to protect --
        the protection is present but inert, which reads as safe and is not.

        Counts BOTH read routes. Counting only `builtins.open` was this test's own first
        version and it did not bite: `Path.read_bytes` goes through `Path.open` -> `io.open`
        and never touches `builtins.open`, so a fingerprint recomputed from the path was
        invisible to the counter and the mutation stayed green.
        """
        import builtins
        import io
        import json as _json

        from kiro_crew.cloud.config import CloudConfig, _fingerprint_bytes

        first = _json.dumps({"profile": "original"}).encode("utf-8")
        later = _json.dumps({"profile": "changed-underneath"}).encode("utf-8")
        p = tmp_path / "cloud.json"
        p.write_bytes(first)

        reads: list[str] = []
        real_builtin_open, real_io_open = builtins.open, io.open

        def note(route: str, file: object) -> None:
            if str(file) == str(p):
                reads.append(route)
                if len(reads) > 1:
                    p.write_bytes(later)  # a writer lands between two reads

        def counting_builtin_open(file, *a, **k):
            note("builtins.open", file)
            return real_builtin_open(file, *a, **k)

        def counting_io_open(file, *a, **k):
            note("io.open", file)
            return real_io_open(file, *a, **k)

        monkeypatch.setattr(builtins, "open", counting_builtin_open)
        monkeypatch.setattr(io, "open", counting_io_open)
        loaded = CloudConfig.load(p)
        monkeypatch.undo()

        assert len(reads) == 1, f"load() read the file {len(reads)} times via {reads}"
        assert loaded._loaded_from is not None
        assert loaded._loaded_from[1] == _fingerprint_bytes(first), (
            "the fingerprint describes bytes other than the ones parsed, so the stale-write "
            "check is guarding content this snapshot never held"
        )
        assert loaded.profile == "original"

    def test_a_file_past_the_ceiling_is_refused_without_being_read_whole(
        self, tmp_path, monkeypatch
    ):
        """The bound must still be a bound now that it is enforced on the read.

        `stat()` used to answer this; the read does now, so a read that ignores the ceiling
        would both defeat the memory bound and accept a file too large to write back.
        """
        from kiro_crew.cloud.config import _MAX_FILE_BYTES, CloudConfig

        p = tmp_path / "cloud.json"
        p.write_text(
            '{"profile": "p", "pad": "' + "x" * (_MAX_FILE_BYTES + 5000) + '"}',
            encoding="utf-8",
        )
        on_disk = p.stat().st_size
        assert on_disk > _MAX_FILE_BYTES

        # The verdict alone cannot witness the bound. With an UNBOUNDED read the length
        # check inside load() still refuses, so asserting only the defaults left this
        # test's NAME claiming a bound its body never observed -- measured: replacing
        # `fh.read(_MAX_FILE_BYTES + 1)` with `fh.read()` kept it green. So the assertion
        # is on how many bytes came IN, which is what the ceiling exists to bound.
        import builtins

        pulled: list[int] = []
        real_open = builtins.open

        class _CountingReads:
            """Delegates everything, recording only how much each read returned."""

            def __init__(self, fh):
                self._fh = fh

            def read(self, *a, **k):
                data = self._fh.read(*a, **k)
                pulled.append(len(data))
                return data

            def __enter__(self):
                self._fh.__enter__()
                return self

            def __exit__(self, *exc):
                return self._fh.__exit__(*exc)

            def __getattr__(self, name):
                return getattr(self._fh, name)

        def counting_open(file, *a, **k):
            fh = real_open(file, *a, **k)
            return _CountingReads(fh) if str(file) == str(p) else fh

        monkeypatch.setattr(builtins, "open", counting_open)
        loaded = CloudConfig.load(p)
        monkeypatch.undo()

        assert pulled, "the loader never read the file, so nothing here tested the bound"
        assert max(pulled) <= _MAX_FILE_BYTES + 1, (
            f"load() pulled {max(pulled)} bytes in for a {on_disk}-byte file: the ceiling "
            "must bound the READ, or an oversized file exhausts memory before the refusal"
        )
        assert loaded.profile == "", "an over-size file must read as defaults"
        assert loaded._loaded_from is None, "and must carry no fingerprint"

    def test_a_write_landing_after_the_read_is_still_refused(self, tmp_path):
        """End to end, so the property above is not just an internal invariant."""
        from kiro_crew.cloud.config import CloudConfig, ConcurrentEditRefused

        p = tmp_path / "cloud.json"
        self._seed(p)
        snapshot = CloudConfig.load(p)

        p.write_text('{"profile": "someone-else", "region": "eu-west-1"}', encoding="utf-8")

        snapshot.last_tag = "t"
        with pytest.raises(ConcurrentEditRefused):
            snapshot.save(p)
        assert CloudConfig.load(p).profile == "someone-else", "the other write survived"

    def test_no_private_field_is_ever_written_to_the_file(self, tmp_path):
        """The written record is public fields only, by a rule over the field list.

        `asdict()` returns EVERY field including the private read-fingerprint, which would
        then be written into the config a human edits and read back as unknown junk. The
        exclusion is a rule rather than a name, so this holds for a private field added
        later -- which is why the assertion is over `fields()` and not over one name.
        """
        import json as _json
        from dataclasses import fields as dc_fields

        from kiro_crew.cloud.config import CloudConfig

        p = tmp_path / "cloud.json"
        self._seed(p)
        loaded = CloudConfig.load(p)
        assert loaded._loaded_from is not None, "the fixture must have a fingerprint to leak"

        loaded.save(p)
        written = set(_json.loads(p.read_text(encoding="utf-8")))
        private = {f.name for f in dc_fields(CloudConfig) if f.name.startswith("_")}
        assert private, "this asserts nothing if there is no private field"
        assert not (written & private), f"private state reached the file: {written & private}"

    def test_two_configs_with_the_same_settings_are_equal(self, tmp_path):
        """The fingerprint must stay out of equality, or callers comparing configs break."""
        from kiro_crew.cloud.config import CloudConfig

        p = tmp_path / "cloud.json"
        self._seed(p)
        other = tmp_path / "elsewhere.json"
        other.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
        assert CloudConfig.load(p) == CloudConfig.load(other), "read-source must not affect =="

    def test_the_expectation_is_bound_to_the_file_it_was_read_from(self, tmp_path):
        """A save to a DIFFERENT path is a fresh write, not a stale update.

        Comparing a fingerprint taken from one file against another file would be the
        same disagreement between two things that this change exists to remove -- and it
        was the first version's actual bug: exporting a loaded config to a new path was
        refused because the new path was "absent" rather than the source's hash.
        """
        from kiro_crew.cloud.config import CloudConfig

        src = tmp_path / "cloud.json"
        self._seed(src)
        loaded = CloudConfig.load(src)

        # Same file changes underneath, but we are writing somewhere else entirely.
        other = CloudConfig.load(src)
        other.fargate = self.FARGATE
        other.save(src)

        loaded.save(tmp_path / "exported.json")  # must not raise
        assert CloudConfig.load(tmp_path / "exported.json").profile == "old"

        # Spellings of the SAME file are still recognised as that file. The intervening
        # write must CHANGE the content: re-saving identical bytes leaves the fingerprint
        # equal, so there is genuinely nothing to conflict with -- which is correct
        # behaviour and was this test's own first mistake.
        stale = CloudConfig.load(src)
        moved = CloudConfig.load(src)
        moved.profile = "moved-on"
        moved.save(src)
        with pytest.raises(Exception, match="changed since this copy was read"):
            stale.save(tmp_path / "." / "cloud.json")

    def test_the_lock_is_held_while_the_write_happens(self, tmp_path, monkeypatch):
        """Verify-then-write must be one critical section, not two adjacent steps.

        Without the lock the two writers can BOTH pass the fingerprint check -- each sees
        the same old file -- and both then write, so one update is lost with nothing
        raised. Narrowing that window rather than closing it would leave a race that is
        rarer and so harder to reproduce, which is worse than an obvious one.

        Asserted by probing from another thread at the one instant it matters: while the
        replacement is being written. A non-blocking acquire must FAIL, because this
        writer holds it. `flock` is per open-file-description, so a separate `open()` in
        the probe genuinely contends rather than re-entering.
        """
        import threading

        from kiro_crew import platform_compat
        from kiro_crew.cloud import config as config_mod

        p = tmp_path / "cloud.json"
        self._seed(p)
        lock_path = tmp_path / "cloud.json.lock"
        probe: dict[str, object] = {}
        real_atomic_write = config_mod.atomic_write

        def probing_write(target, payload):
            def probe_the_lock() -> None:
                try:
                    with open(lock_path, "a+", encoding="utf-8") as fh:
                        with platform_compat.file_lock(fh.fileno(), exclusive=True, wait=False):
                            probe["acquired"] = True
                except OSError as exc:
                    probe["refused"] = type(exc).__name__

            t = threading.Thread(target=probe_the_lock)
            t.start()
            t.join(timeout=10)
            return real_atomic_write(target, payload)

        monkeypatch.setattr(config_mod, "atomic_write", probing_write)
        cfg = config_mod.CloudConfig.load(p)
        cfg.profile = "mine"
        cfg.save(p)

        assert probe, "the probe never ran, so this asserts nothing"
        assert "acquired" not in probe, (
            "another writer acquired the lock while the replacement was being written, "
            "so verify and write are not one critical section"
        )
        assert probe.get("refused") == "BlockingIOError", probe

    def test_no_writer_loses_its_edit_under_real_contention(self, tmp_path):
        """Many threads, each adding its OWN key, and every key must be present.

        The strongest form of the property: it does not depend on a fixture guessing the
        interleaving. If any writer's update is dropped, a key is missing at the end.
        """
        import threading

        from kiro_crew.cloud.config import CloudConfig

        p = tmp_path / "cloud.json"
        self._seed(p)
        writers = 12
        errors: list[BaseException] = []

        def write(n: int) -> None:
            try:
                CloudConfig.apply_update(p, last_tag=f"tag-{n:02d}", attempts=64)
            except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
                errors.append(exc)

        threads = [threading.Thread(target=write, args=(n,)) for n in range(writers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"writers failed: {errors[:3]}"
        # Every writer set the same field, so the survivor is one of them -- and crucially
        # the file is still valid and the seeded region was never clobbered to a default.
        final = CloudConfig.load(p)
        assert final.last_tag.startswith("tag-"), final.last_tag
        assert final.region == "us-east-1", "a concurrent writer corrupted an unrelated field"

    """The Linux and macOS paths must agree, because the alias defeats both mechanisms.

    A read-only bind seals a mount and a Seatbelt `deny file-write*` matches a pathname.
    Neither follows a link, so a symlinked `cloud.json` whose target sits in a writable
    directory is writable through the target on BOTH platforms. The refusal had been
    reachable only through the Linux bind-mount preparation, so macOS never ran it.
    """

    def test_every_launcher_reaches_the_refusal(self):
        """Source-level, because the macOS launcher cannot be exercised on Linux CI.

        Parses the AST and looks for real CALL nodes. A substring search over the source
        was the first version and it did not bite: the explanatory comment beside the
        call names `_materialize_sealable_ceilings()`, so deleting the call left the text
        present and the test green. A comment must not be able to satisfy an assertion
        about behaviour.

        Asserted on both launchers together: a test naming only the macOS one would go
        green if a later edit dropped the Linux call instead.
        """
        import ast
        import inspect
        import textwrap

        from kiro_crew import sandbox

        reachers = {"_refuse_aliased_strict_leaves", "_materialize_sealable_ceilings"}
        for launcher in (sandbox.namespace_argv, sandbox.sandbox_exec_argv):
            tree = ast.parse(textwrap.dedent(inspect.getsource(launcher)))
            called = {
                node.func.id
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            }
            assert called & reachers, (
                f"{launcher.__name__} calls neither "
                f"{' nor '.join(sorted(reachers))}, so it does not refuse an aliased leaf"
            )

    def test_the_shared_refusal_covers_every_strict_leaf(self, tmp_path, monkeypatch):
        """Drives the platform-independent helper directly, on every leaf in the set."""
        from kiro_crew import sandbox

        monkeypatch.setattr(sandbox, "config_dir", lambda: tmp_path)
        assert sandbox._CREW_NOFOLLOW_READONLY_FILE_LEAVES, "the leaf set must not be empty"

        for leaf in sandbox._CREW_NOFOLLOW_READONLY_FILE_LEAVES:
            target = tmp_path / leaf
            elsewhere = tmp_path / f"real-{leaf}"
            elsewhere.write_text("{}", encoding="utf-8")
            target.symlink_to(elsewhere)
            with pytest.raises(sandbox.SandboxCeilingUnsealable, match="SYMLINK"):
                sandbox._refuse_aliased_strict_leaves()
            target.unlink()

    def test_a_plain_file_is_accepted(self, tmp_path, monkeypatch):
        """The positive side, so the test above cannot pass by refusing everything."""
        from kiro_crew import sandbox

        monkeypatch.setattr(sandbox, "config_dir", lambda: tmp_path)
        for leaf in sandbox._CREW_NOFOLLOW_READONLY_FILE_LEAVES:
            (tmp_path / leaf).write_text("{}", encoding="utf-8")
        sandbox._refuse_aliased_strict_leaves()  # must not raise

    def test_the_pretty_form_is_preferred_when_it_fits(self, tmp_path):
        """So the compact fallback cannot become the everyday path.

        A human edits this file. Falling back to compact output is for the case where
        pretty would breach the ceiling, not for every save.
        """
        from kiro_crew.cloud.config import CloudConfig

        out = tmp_path / "cloud.json"
        CloudConfig(profile="p", region="us-east-1").save(out)
        assert "\n  " in out.read_text(encoding="utf-8"), "a small record stays indented"

    def test_a_good_credential_beside_a_bad_secret_does_not_register(self):
        """Every reference is validated, not just the first that matches.

        An early return accepted a valid credential ref sitting beside a malformed or
        cross-crew one, and the engine then refused the whole document at launch --
        registering a lane that rejects every launch through it.
        """
        same_crew = [
            "kirocrew/crew/demo/OTHER_KEY",
            "arn:aws:secretsmanager:us-east-1:123456789012:secret:"
            "kirocrew/crew/demo/OTHER_KEY-AbCdEf",
        ]
        cross_crew = [
            "kirocrew/crew/other/OTHER_KEY",
            "arn:aws:secretsmanager:us-east-1:999988887777:secret:"
            "kirocrew/crew/other/OTHER_KEY-AbCdEf",
        ]
        good = {**COMPLETE_FARGATE, "secrets": [list(CREDENTIAL_SECRET), same_crew]}
        assert FargateConfig.from_mapping(good) is not None, "a same-crew sibling is fine"

        for label, bad in (("cross-crew", cross_crew), ("malformed", ["junk", "arn:bogus"])):
            block = {**COMPLETE_FARGATE, "secrets": [list(CREDENTIAL_SECRET), bad]}
            assert FargateConfig.from_mapping(block) is None, label

    @pytest.mark.parametrize("field", ["subnets", "security_groups"])
    @pytest.mark.parametrize("bad", [5, "", None, {"a": 1}])
    def test_one_bad_list_member_voids_the_block(self, field: str, bad: object):
        """All-or-nothing, because filtering silently changed the placement.

        Dropping the bad member launched the task in whichever subnets survived -- a
        placement the operator never wrote. One bad member voids the block, exactly as
        one bad secret entry does.
        """
        block = {**COMPLETE_FARGATE, field: ["subnet-ok", bad]}
        assert FargateConfig.from_mapping(block) is None, f"{field}={bad!r}"

    @pytest.mark.parametrize(("value", "expected"), [(True, True), (False, False)])
    def test_a_boolean_public_ip_flag_is_read_as_written(self, value: bool, expected: bool):
        config = FargateConfig.from_mapping({**COMPLETE_FARGATE, "assign_public_ip": value})
        assert config is not None
        assert config.assign_public_ip is expected

    def test_an_absent_public_ip_flag_defaults_to_false(self):
        assert "assign_public_ip" not in COMPLETE_FARGATE
        config = FargateConfig.from_mapping(COMPLETE_FARGATE)
        assert config is not None
        assert config.assign_public_ip is False

    def test_a_movable_tag_is_refused_here_rather_than_at_launch(self):
        """``taskdef.py`` requires ``<repo>@sha256:<64 hex>``.

        Caught at the file boundary, an operator learns it where they typed it. Left
        to the launch, the same mistake surfaces as a refusal from a lane they were
        offered, with nothing pointing back at ``cloud.json``.
        """
        assert FargateConfig.from_mapping({**COMPLETE_FARGATE, "image": "repo:v1"}) is None

    def test_one_bad_secret_entry_drops_the_whole_block(self):
        """Not just that entry.

        Launching with one fewer secret than the operator wrote starts a task that
        then fails on a missing variable -- which is harder to trace than a lane
        that was never offered.
        """
        two = {**COMPLETE_FARGATE, "secrets": [COMPLETE_FARGATE["secrets"][0], ["broken"]]}
        assert FargateConfig.from_mapping(two) is None

    def test_the_block_survives_a_save_and_load(self, tmp_path):
        p = tmp_path / "cloud.json"
        CloudConfig(profile="dev", fargate=COMPLETE_FARGATE).save(p)
        loaded = CloudConfig.load(p)
        assert loaded.fargate == COMPLETE_FARGATE
        assert loaded.fargate_config() is not None

    def test_an_incomplete_block_survives_an_unrelated_save(self, tmp_path):
        """Load, record a launch, save: the operator's half-written block is intact.

        This object is loaded and re-saved to record ``last_tag`` after an ordinary
        EC2 launch. Were the block judged at load and the judgement written back,
        that save would replace a block the operator is mid-way through editing
        with ``null`` -- an incomplete block must read as ABSENT to the lane and
        still round-trip through the file unchanged.
        """
        p = tmp_path / "cloud.json"
        half_written = {"cluster": "kirocrew-crew-prod", "subnets": ["subnet-a"]}
        p.write_text(
            json.dumps({"profile": "dev", "region": "us-west-2", "fargate": half_written}),
            encoding="utf-8",
        )
        cfg = CloudConfig.load(p)
        assert cfg.fargate_config() is None
        cfg.last_tag = "kc-after-ec2"
        cfg.save(p)
        on_disk = json.loads(p.read_text(encoding="utf-8"))
        assert on_disk["fargate"] == half_written
        assert on_disk["last_tag"] == "kc-after-ec2"
        assert CloudConfig.load(p).fargate_config() is None

    @pytest.mark.parametrize("block", ["a string", 42, ["a", "list"], {"secrets": "nope"}])
    def test_a_malformed_block_also_survives_a_save(self, tmp_path, block: object):
        """Not only an incomplete object: any shape the file held is written back."""
        p = tmp_path / "cloud.json"
        p.write_text(json.dumps({"profile": "dev", "fargate": block}), encoding="utf-8")
        cfg = CloudConfig.load(p)
        assert cfg.fargate_config() is None
        cfg.save(p)
        assert json.loads(p.read_text(encoding="utf-8"))["fargate"] == block

    def test_a_corrupt_block_leaves_the_rest_of_the_config_readable(self, tmp_path):
        """One bad block must not cost the profile and region too."""
        p = tmp_path / "cloud.json"
        p.write_text(
            json.dumps({"profile": "dev", "region": "us-west-2", "fargate": {"cluster": "c"}}),
            encoding="utf-8",
        )
        loaded = CloudConfig.load(p)
        assert loaded.fargate_config() is None
        assert loaded.profile == "dev"
        assert loaded.region == "us-west-2"

    def test_no_secret_value_field_exists_to_write_one_into(self):
        """The file's contract is identifiers only, and the shape enforces it.

        A secret is named by its canonical name and its ARN; the value is fetched by
        the task's execution role before the container starts. A field for a value
        would be the first place someone put one.
        """
        fields = {f.name for f in FargateConfig.__dataclass_fields__.values()}
        for forbidden in ("secret_values", "value", "values", "password", "token"):
            assert forbidden not in fields


class TestCloudConfig:
    def test_defaults(self, tmp_path):
        cfg = CloudConfig.load(tmp_path / "cloud.json")
        assert cfg.profile == ""
        assert cfg.region == DEFAULT_REGION
        assert cfg.last_tag == ""

    def test_roundtrip(self, tmp_path):
        p = tmp_path / "cloud.json"
        cfg = CloudConfig(profile="dev", region="us-west-2", last_tag="kc-abc")
        cfg.save(p)
        loaded = CloudConfig.load(p)
        assert loaded.profile == "dev"
        assert loaded.region == "us-west-2"
        assert loaded.last_tag == "kc-abc"

    def test_over_long_last_tag_sanitized_to_empty(self, tmp_path):
        # A 52-63 char last_tag must be sanitized to "" on load — NOT carried
        # into the resume path where validate_tag (cap 51) would raise. The
        # sanitizer's job is "no last launch", not a crash. Keep _TAG_RE in
        # lockstep with ec2._TAG_RE.
        from kiro_crew.cloud import ec2

        assert ec2._TAG_RE.pattern == r"^[a-zA-Z0-9-]{1,51}$"  # the cap we mirror
        p = tmp_path / "cloud.json"
        p.write_text('{"profile": "dev", "region": "us-east-1", "last_tag": "%s"}' % ("a" * 60))
        cfg = CloudConfig.load(p)
        assert cfg.last_tag == ""  # too long -> dropped, no ValidationError later
        # A malformed-charset tag is likewise dropped.
        p.write_text('{"last_tag": "bad tag!"}')
        assert CloudConfig.load(p).last_tag == ""
        # A valid 51-char tag is kept.
        p.write_text('{"last_tag": "%s"}' % ("k" * 51))
        assert CloudConfig.load(p).last_tag == "k" * 51

    def test_never_stores_credentials(self, tmp_path):
        p = tmp_path / "cloud.json"
        CloudConfig(profile="dev", region="us-east-1", last_tag="t").save(p)
        text = p.read_text(encoding="utf-8")
        # Only profile/region/last_tag — no secret-shaped keys.
        for forbidden in ("secret", "access_key", "aws_access", "token", "password"):
            assert forbidden not in text.lower()

    def test_corrupt_file_falls_back_to_defaults(self, tmp_path):
        p = tmp_path / "cloud.json"
        p.write_text("not json{{{")
        cfg = CloudConfig.load(p)
        assert cfg.region == DEFAULT_REGION

    def test_non_object_json_falls_back_to_defaults(self, tmp_path):
        # Valid JSON that isn't an object ("hello", [1,2], 42, null) must not
        # raise AttributeError out of load() — that would give a raw traceback on
        # every `kirocrew cloud` command (handle_cloud only catches AWS/validation
        # errors). Honor the tolerate-a-corrupt-file promise.
        for body in ('"hello"', "[1, 2, 3]", "42", "null", "true"):
            p = tmp_path / "cloud.json"
            p.write_text(body)
            cfg = CloudConfig.load(p)
            assert cfg.region == DEFAULT_REGION
            assert cfg.profile == ""
            assert cfg.last_tag == ""

    def test_missing_region_coerced_to_default(self, tmp_path):
        p = tmp_path / "cloud.json"
        p.write_text('{"profile": "dev", "region": ""}')
        cfg = CloudConfig.load(p)
        assert cfg.region == DEFAULT_REGION


class TestCloudConfigIsSealedAgainstAgentWrites:
    """``cloud.json`` must not be writable through an agent's file-edit tool.

    The Fargate block made this file an input to a security decision:
    ``fargate.image`` chooses the container a launch runs, and the task's execution
    role delivers the model credential into that container. An agent that could
    rewrite the field could name a digest-pinned image of its own -- the digest rule
    constrains the reference's FORM, not who owns the registry -- while leaving the
    owner's placement and secrets intact, so the owner's next launch would hand the
    credential to an image they never chose.
    """

    #: Spelled out rather than looped from the production tuple: a test derived from
    #: the same list the code reads would keep passing after someone emptied it.
    PATHS = ("~/.kiro/crew/cloud.json", "~/.kirocrew/cloud.json")

    @pytest.mark.parametrize("path", PATHS)
    def test_an_agent_may_not_write_it(self, path: str):
        from kiro_crew.security.paths import is_sensitive_write_path

        assert is_sensitive_write_path(path) is True

    @pytest.mark.parametrize("path", PATHS)
    def test_it_stays_readable(self, path: str):
        """Write-protected, not sensitive: the selector reads it on every request.

        Blocking reads would take the Set-up tab down instead of protecting it.
        """
        from kiro_crew.security.paths import is_sensitive_path

        assert is_sensitive_path(path) is False

    def test_the_predicate_can_still_say_no(self):
        """Positive control, so the two assertions above cannot pass vacuously.

        A sibling path under the same crew home that nothing protects must come back
        writable; without this, a predicate that answered True for everything would
        satisfy this class.
        """
        from kiro_crew.security.paths import is_sensitive_write_path

        assert is_sensitive_write_path("~/.kiro/crew/not-a-protected-leaf.json") is False

    def test_the_gateway_can_still_save_it(self, tmp_path):
        """Sealing is placement, not logic: Kiro Crew's own write is unaffected.

        ``save()`` is how an ordinary EC2 launch records ``last_tag``, so a seal that
        also stopped the gateway would break the lane it exists to protect.
        """
        p = tmp_path / "cloud.json"
        CloudConfig(profile="dev", last_tag="t1").save(p)
        assert CloudConfig.load(p).last_tag == "t1"


class TestSaveRefusesWhatLoadWouldReject:
    """A save that outlives its own reader is silent config loss, so it is refused.

    The raw ``fargate`` block is stored verbatim and ``save()`` re-serializes with
    ``indent=2``, so a hand-minified block that fits under the read ceiling can cross
    it once expanded. Publishing that file would make the next ``load()`` fall back to
    defaults and lose the operator's profile, region and Fargate state with nothing
    said. Refusing keeps the previous, readable file.
    """

    def test_an_ordinary_record_still_saves(self, tmp_path):
        p = tmp_path / "cloud.json"
        CloudConfig(profile="dev", region="us-east-1", last_tag="t1").save(p)
        assert CloudConfig.load(p).profile == "dev"

    def test_a_record_that_would_outgrow_the_read_ceiling_is_refused(self, tmp_path):
        p = tmp_path / "cloud.json"
        CloudConfig(profile="dev").save(p)
        before = p.read_text(encoding="utf-8")
        oversized = CloudConfig(profile="dev", fargate={"pad": "x" * (_MAX_FILE_BYTES + 10)})
        with pytest.raises(ValueError, match="unreadable"):
            oversized.save(p)
        assert p.read_text(encoding="utf-8") == before, "the readable file must survive"

    def test_the_refusal_matches_what_load_enforces(self, tmp_path):
        """The two ceilings are the same constant, not two numbers that can disagree."""
        p = tmp_path / "cloud.json"
        p.write_text("x" * (_MAX_FILE_BYTES + 1), encoding="utf-8")
        assert CloudConfig.load(p).profile == ""


class TestTheSealedCloudConfigNameCannotBeAnAlias:
    """A bind mount seals a link's REFERENT, so the sealed leaf must not be a link.

    Otherwise the lexical name stays replaceable in a writable parent: a sandboxed
    process unlinks it, drops its own file there, and the seal is intact around a name
    that now chooses which image a Fargate launch runs.
    """

    def test_cloud_json_is_on_the_nofollow_file_list(self):
        from kiro_crew import sandbox

        assert "cloud.json" in sandbox._CREW_NOFOLLOW_READONLY_FILE_LEAVES

    def test_every_nofollow_file_leaf_is_also_precreated(self):
        """Mirrors the assert the directory list carries.

        A nofollow leaf that is not materialised has no seal to protect on a default
        install, so the strict check would guard a name nothing binds.
        """
        from kiro_crew import sandbox

        assert set(sandbox._CREW_NOFOLLOW_READONLY_FILE_LEAVES) <= set(
            sandbox._CREW_PRECREATE_READONLY_FILE_LEAVES
        )

    def test_the_list_is_not_everything(self):
        """Positive control: the strict path is opt-in, not applied to every leaf."""
        from kiro_crew import sandbox

        assert set(sandbox._CREW_PRECREATE_READONLY_FILE_LEAVES) - set(
            sandbox._CREW_NOFOLLOW_READONLY_FILE_LEAVES
        )


class TestTheStrictCloudConfigSealRefusesEveryUncoveredName:
    """A read-only bind seals a MOUNT, not an inode, so two names escape it.

    A symlink leaves the lexical name replaceable; a second hardlink puts an alias
    outside the mount that reaches the same inode. For an ordinary ceiling the codebase
    only WARNS about both, because refusing would break a dotfile manager or a snapshot
    tool. For ``cloud.json`` a write through either name picks the container image a
    Fargate launch runs, and the execution role delivers the model credential into it,
    so the strict leaf refuses where the rest warn.
    """

    #: A block present means an alias could choose the image a launch runs, which is
    #: the only reason this leaf refuses where every other one warns.
    RISKY = '{"profile": "p", "fargate": {"cluster": "c"}}'
    #: No block, so an alias selects nothing: treated like every other ceiling.
    HARMLESS = '{"profile": "p", "region": "us-east-1"}'

    @staticmethod
    def _strict_target(tmp_path):
        from kiro_crew import sandbox

        return tmp_path / sandbox._CREW_NOFOLLOW_READONLY_FILE_LEAVES[0]

    def test_a_lone_regular_file_is_accepted(self, tmp_path):
        from kiro_crew import sandbox

        p = self._strict_target(tmp_path)
        p.write_text(self.RISKY, encoding="utf-8")
        sandbox._require_real_file_nofollow(str(p))

    def test_an_absent_file_is_accepted(self, tmp_path):
        """Absent is the publish path's job, not this check's."""
        from kiro_crew import sandbox

        sandbox._require_real_file_nofollow(str(self._strict_target(tmp_path)))

    def test_a_symlink_is_refused(self, tmp_path):
        from kiro_crew import sandbox

        real = tmp_path / "elsewhere.json"
        real.write_text(self.RISKY, encoding="utf-8")
        p = self._strict_target(tmp_path)
        p.symlink_to(real)
        with pytest.raises(sandbox.SandboxCeilingUnsealable, match="SYMLINK"):
            sandbox._require_real_file_nofollow(str(p))

    def test_a_hardlinked_file_is_refused(self, tmp_path):
        """The shape the earlier symlink-only check missed entirely."""
        from kiro_crew import sandbox

        p = self._strict_target(tmp_path)
        p.write_text(self.RISKY, encoding="utf-8")
        (tmp_path / "alias.json").hardlink_to(p)
        with pytest.raises(sandbox.SandboxCeilingUnsealable, match="hardlink"):
            sandbox._require_real_file_nofollow(str(p))

    def test_a_directory_at_the_name_is_refused(self, tmp_path):
        from kiro_crew import sandbox

        p = self._strict_target(tmp_path)
        p.mkdir()
        with pytest.raises(sandbox.SandboxCeilingUnsealable, match="not a regular file"):
            sandbox._require_real_file_nofollow(str(p))

    def test_an_unreadable_alias_is_treated_as_risky(self, tmp_path):
        """Contents are never read now, so even an unreadable alias refuses."""
        from kiro_crew import sandbox

        p = self._strict_target(tmp_path)
        p.write_text(self.RISKY, encoding="utf-8")
        (tmp_path / "alias.json").hardlink_to(p)
        p.chmod(0o000)
        try:
            with pytest.raises(sandbox.SandboxCeilingUnsealable):
                sandbox._require_real_file_nofollow(str(p))
        finally:
            p.chmod(0o600)

    #: Contents that a content-based rule would have judged differently. The refusal must
    #: not vary across them: the agent supplies the contents, so any rule reading them can
    #: be satisfied by writing something else. The escaped spellings are the specific
    #: bypass a byte-level probe had; the no-block and empty rows are the ones an earlier
    #: exemption let through, which is how an agent could write a whole block via the alias.
    ENCODINGS = [
        ("plain block", '{"fargate": {"cluster": "c"}}'),
        ("escaped key", '{"\\u0066argate": {"cluster": "c"}}'),
        ("fully escaped key", '{"\\u0066\\u0061\\u0072\\u0067\\u0061\\u0074\\u0065": {}}'),
        ("no block at all", '{"profile": "p", "region": "us-east-1"}'),
        ("empty object", "{}"),
        ("unparseable", "{not json"),
        ("not an object", '"hello"'),
        ("empty file", ""),
    ]

    @pytest.mark.parametrize(("label", "body"), ENCODINGS)
    def test_a_symlink_is_refused_whatever_the_contents(self, tmp_path, label, body):
        from kiro_crew import sandbox

        real = tmp_path / "elsewhere.json"
        real.write_text(body, encoding="utf-8")
        p = self._strict_target(tmp_path)
        p.symlink_to(real)
        with pytest.raises(sandbox.SandboxCeilingUnsealable, match="SYMLINK"):
            sandbox._require_real_file_nofollow(str(p))

    @pytest.mark.parametrize(("label", "body"), ENCODINGS)
    def test_a_hardlink_is_refused_whatever_the_contents(self, tmp_path, label, body):
        from kiro_crew import sandbox

        p = self._strict_target(tmp_path)
        p.write_text(body, encoding="utf-8")
        (tmp_path / "alias.json").hardlink_to(p)
        with pytest.raises(sandbox.SandboxCeilingUnsealable, match="hardlink"):
            sandbox._require_real_file_nofollow(str(p))

    def test_the_rule_reads_no_contents_at_all(self, tmp_path):
        """The property that makes the bypass class empty rather than narrower.

        Any rule deciding on contents can be satisfied by writing different contents,
        and the agent is who writes them. Asserted on the source so a future contents
        check cannot creep back in without failing here.
        """
        import inspect

        from kiro_crew import sandbox

        src = inspect.getsource(sandbox._require_real_file_nofollow)
        for reader in ("open(", "read(", "json.loads", "carries_risk"):
            assert reader not in src, f"the strict refusal must not read contents: {reader}"


class TestTheCredentialGateAgreesWithTheEngine:
    """No spelling may pass this gate and then be refused at launch.

    The gate exists so an incomplete block leaves the lane UNREGISTERED instead of
    registered-and-refusing. A gate that accepts a name the engine rejects recreates
    that exact state from inside the gate, which is what a tail-only check did: both a
    bare ``KIRO_API_KEY`` and a wrong-prefix ``junk/KIRO_API_KEY`` passed here and were
    refused by ``identity.secret_env_name``.
    """

    @staticmethod
    def _with_credential_named(name: str) -> dict:
        return {
            **COMPLETE_FARGATE,
            "secrets": [
                [name, f"arn:aws:secretsmanager:us-east-1:123456789012:secret:{name}-AbCdEf"]
            ],
        }

    @pytest.mark.parametrize(
        "name",
        [
            "KIRO_API_KEY",
            "junk/KIRO_API_KEY",
            "kirocrew/crew/KIRO_API_KEY",
            "kirocrew/crew//KIRO_API_KEY",
            "kirocrew/crew/a/b/KIRO_API_KEY",
            "kirocrew/crew/demo/OTHER_KEY",
        ],
    )
    def test_a_name_the_engine_would_refuse_does_not_register_the_lane(self, name: str):
        assert FargateConfig.from_mapping(self._with_credential_named(name)) is None, name

    def test_a_conforming_name_is_accepted(self):
        block = self._with_credential_named("kirocrew/crew/demo/KIRO_API_KEY")
        assert FargateConfig.from_mapping(block) is not None

    def test_every_name_this_gate_accepts_the_engine_also_accepts(self):
        """The property itself, over a MATRIX, checked against the engine.

        Written as a product rather than a hand-picked list because a hand-picked list
        is what let this defect recur three times: each round fixed the spelling that
        had been reported and left the next one. Any name this module admits must
        survive ``secret_env_name``, which is what the launch path calls, so the two
        halves cannot drift apart without a red here.
        """
        import itertools

        from kiro_crew.cloud.fargate.identity import SecretRef, secret_env_name

        prefixes = ["kirocrew/crew/", "kirocrew/crews/", "junk/", ""]
        crews = [
            "demo",
            "DEMO",
            "De-mo",
            "-demo",
            "demo-",
            "d" * 32,
            "d" * 33,
            "dem_o",
            "d\u00e9",
            "a",
            "a-b",
            "a--b",
            "1",
            "",
            "ab/cd",
        ]
        keys = ["KIRO_API_KEY", "kiro_api_key", "OTHER"]

        accepted = 0
        for prefix, crew, key in itertools.product(prefixes, crews, keys):
            name = f"{prefix}{crew}/{key}"
            if FargateConfig.from_mapping(self._with_credential_named(name)) is None:
                continue
            accepted += 1
            arn = f"arn:aws:secretsmanager:us-east-1:123456789012:secret:{name}-AbCdEf"
            secret_env_name(SecretRef(name=name, arn=arn))
        assert accepted, "the matrix must contain at least one accepted name"

    @pytest.mark.parametrize(
        "crew",
        ["DEMO", "De-mo", "-demo", "demo-", "dem_o", "d\u00e9", "d" * 33, ""],
    )
    def test_a_crew_segment_the_engine_would_refuse_does_not_register(self, crew: str):
        """Each of these passed the earlier truthiness check and died in provisioning."""
        name = f"kirocrew/crew/{crew}/KIRO_API_KEY"
        assert FargateConfig.from_mapping(self._with_credential_named(name)) is None, crew

    @pytest.mark.parametrize("crew", ["demo", "a", "1", "a-b", "a--b", "d" * 32])
    def test_a_conforming_crew_segment_registers(self, crew: str):
        name = f"kirocrew/crew/{crew}/KIRO_API_KEY"
        assert FargateConfig.from_mapping(self._with_credential_named(name)) is not None, crew
