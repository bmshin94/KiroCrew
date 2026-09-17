"""Persisted cloud-launcher config — **profile name only, never credentials**.

Stores the AWS *profile name*, region, and the most-recent instance tag under
``~/.kiro/crew/cloud.json``. AWS credentials are never written here — they are
resolved by the ``aws`` CLI's own provider chain from the profile.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from dataclasses import field as dc_field
from dataclasses import fields
from pathlib import Path
from typing import Any, Optional

from kiro_crew import platform_compat
from kiro_crew.atomic_write import atomic_write
from kiro_crew.config.loader import config_dir

_FILENAME = "cloud.json"
DEFAULT_REGION = "us-east-1"

# Cap at 51 (not 63) to match ec2._TAG_RE / validate_tag: a longer last_tag
# would pass THIS sanitizer but then raise ValidationError on resume (the IAM
# role name kirocrew-ec2-<tag> maxes at 64), defeating the "just treat it as no
# last launch" intent. Keep in lockstep with ec2._TAG_RE.
#: Longest ``last_tag`` this module accepts. NAMED because two places need the number --
#: the pattern below and the room reserved for a tag provisioning has not written yet -- and
#: a second literal 51 in either would be free to drift from the other.
_TAG_MAX_LEN = 51
_TAG_RE = re.compile(rf"^[a-zA-Z0-9-]{{1,{_TAG_MAX_LEN}}}$")

#: A digest-pinned image reference, the only form ``cloud/fargate/taskdef.py``
#: accepts. Checked here so a hand-edited ``cloud.json`` naming a movable tag is
#: treated as no Fargate configuration at all, rather than becoming a refusal at
#: the first launch -- which is where the operator has least context for it.


#: The two architectures Fargate runs. Spelled here rather than imported from
#: ``cloud/fargate/taskdef.py`` on purpose: this module is loaded to read a config
#: file and must not pull in the ``cloud`` package's AWS surface to do it. The
#: values are held to the imported set by a test, so the two cannot drift.


#: Upper bounds on how much this reader will retain from one ``fargate`` block. The
#: file is not writable through the agent file-edit tool and is mounted read-only in
#: the sandbox, but a same-UID process outside a sandbox can still write it, so the
#: reader cannot assume the bytes are small. ``load`` catches only ``OSError`` and
#: ``JSONDecodeError``, so an oversized-but-valid-JSON document would otherwise be
#: parsed and every string retained, and the read runs on every request that builds
#: the provisioner list -- an unbounded list or an unbounded string is a gateway
#: memory-exhaustion surface with only manual recovery. A block that exceeds any bound
#: reads as absent, the same as any other malformed block: the ceiling is generous
#: next to any real placement, so a legitimate operator never meets it.
_MAX_LIST_ITEMS = 64
_MAX_STRING_LEN = 2048


#: Ceiling on the whole file, checked BEFORE it is parsed. ``json.loads`` builds its
#: result in memory, so a bound applied to the parsed document is applied too late;
#: the read itself is what must refuse. Generous next to a real ``cloud.json`` of a
#: few hundred bytes, and an over-sized file falls back to defaults exactly as a
#: corrupt one does rather than raising into every cloud command.
_MAX_FILE_BYTES = 1 << 20


@dataclass(frozen=True)
class FargateConfig:
    """Where an operator writes the Fargate lane's placement, image and secrets.

    **Identifiers only, never a secret value.** A crew secret is named by its
    canonical name and its ARN; the value is fetched by the task's execution role
    from Secrets Manager before the container starts, so nothing here is a
    credential and this file's no-secrets contract holds unchanged.

    The engine takes these four fields as a ``FargateLaunchSpec`` and refuses to
    guess any of them -- "an unnamed subnet or security group is the same class of
    error as deleting a task on a guess". This is the place they are written down.
    """

    cluster: str = ""
    subnets: tuple[str, ...] = ()
    security_groups: tuple[str, ...] = ()
    image: str = ""
    #: ``(canonical name, ARN)`` pairs. A pair, not a bare ARN: an ARN alone
    #: cannot say where the secret's NAME ends, because the service appends a
    #: six-character suffix and nothing marks the boundary.
    secrets: tuple[tuple[str, str], ...] = ()
    cpu_architecture: str = "X86_64"
    #: False is the safe direction, and the flag is not the boundary -- a task in
    #: a public subnet with no NAT gateway cannot pull its image without one.
    assign_public_ip: bool = False

    def is_complete(self) -> bool:
        """True when every field the engine requires is present and well-formed.

        INCOMPLETE MEANS ABSENT, and that is the whole design of this method. A
        half-written block must leave the lane unregistered rather than registered
        and refusing: a lane that exists and rejects every launch spends the
        operator's attention at launch time on a mistake that was visible when
        they saved the file.

        The secrets must include one named for the model credential, because the
        engine refuses a task definition that delivers none: a block with every
        placement field and no credential secret is the offered-and-refusing state
        in its most likely form.

        The whole SET is judged, not just one name: the engine's own
        ``secret_destinations`` derives every reference's destination (refusing two
        that collide) and ``sole_binding`` refuses a set naming more than one crew. So
        a valid credential beside a malformed or cross-crew reference leaves the lane
        unregistered rather than registering one whose every launch then fails. None of
        it is a second copy of those rules -- both are called, not reimplemented.
        """
        return bool(
            self.cluster
            and self.subnets
            and self.security_groups
            and _digest_pinned(self.image or "")
            and self.cpu_architecture in _cpu_architectures()
            and _names_model_credential(self.secrets)
        )

    @classmethod
    def from_mapping(cls, data: object) -> Optional["FargateConfig"]:
        """Read one block, or ``None`` for anything that is not usable.

        Every rejection returns ``None`` rather than a partially-populated object,
        so a caller cannot hold a config that looks present and is not. A secret
        entry that is not a two-string pair drops the WHOLE block, not just that
        entry: silently launching with one fewer secret than the operator wrote is
        how a task starts and then fails on a missing variable.

        ``assign_public_ip`` is read the same way: absent means ``False``, and a
        present value that is not a JSON boolean drops the block. The field decides
        network exposure, and coercing it would read the string ``"false"`` as
        true, which is the one direction this field must never be guessed in.
        """
        if not isinstance(data, dict):
            return None
        secrets: list[tuple[str, str]] = []
        raw_secrets = data.get("secrets", [])
        if not isinstance(raw_secrets, list) or len(raw_secrets) > _MAX_LIST_ITEMS:
            return None
        for entry in raw_secrets:
            if not (isinstance(entry, (list, tuple)) and len(entry) == 2):
                return None
            name, arn = entry
            if not (isinstance(name, str) and isinstance(arn, str) and name and arn):
                return None
            if len(name) > _MAX_STRING_LEN or len(arn) > _MAX_STRING_LEN:
                return None
            secrets.append((name, arn))
        assign_public_ip = data.get("assign_public_ip", False)
        if not isinstance(assign_public_ip, bool):
            return None
        # EVERY string-typed field, in one place. `str()` on a raw value made any JSON
        # scalar truthy: `false` became the string "False", which is non-empty, so
        # `is_complete()` passed and the lane registered against a cluster named
        # "False" that does not exist. This is the same defect already fixed on
        # `assign_public_ip`, one field over, so the check is written once over the
        # field list rather than per field -- a string field added later is covered
        # without anyone remembering to add a branch.
        strings: dict[str, str] = {}
        for field_name, default in _STRING_FIELD_DEFAULTS.items():
            value = data.get(field_name, default)
            if not isinstance(value, str) or len(value) > _MAX_STRING_LEN:
                return None
            strings[field_name] = value
        subnets = _bounded_string_tuple(data.get("subnets"))
        security_groups = _bounded_string_tuple(data.get("security_groups"))
        if subnets is None or security_groups is None:
            return None
        candidate = cls(
            subnets=subnets,
            security_groups=security_groups,
            secrets=tuple(secrets),
            assign_public_ip=assign_public_ip,
            **strings,
        )
        return candidate if candidate.is_complete() else None


def _cpu_architectures() -> frozenset[str]:
    """The architectures the ENGINE accepts, read from it rather than copied.

    Imported on use for the same reason as the credential check below: the value must
    be the engine's, so no copy can drift from it, and the import is effectively free
    once ``cloud/__init__.py`` has run.
    """
    from kiro_crew.cloud.fargate.taskdef import CPU_ARCHITECTURES

    return frozenset(CPU_ARCHITECTURES)


def _digest_pinned(image: str) -> bool:
    """Whether the ENGINE would accept this image reference as digest-pinned.

    Delegates to ``taskdef``'s own refusal so a movable tag is judged here by exactly
    the rule that would reject it at launch -- checked at config time so a hand-edited
    ``cloud.json`` naming a tag reads as no Fargate configuration instead of becoming a
    refusal on first use.
    """
    from kiro_crew.cloud.fargate.taskdef import DocumentRefused, _refuse_undigested_image

    try:
        _refuse_undigested_image(image)
    except DocumentRefused:
        return False
    return True


class ConcurrentEditRefused(ValueError):
    """Raised when the file changed between the read and the write it is based on.

    A distinct type, not a bare ``ValueError``, because the caller's correct response is
    specific and recoverable: re-read, re-apply the change, save again. A caller that
    cannot tell this apart from the size refusal has to treat both as fatal.
    """


#: Fields the launch path writes AFTER a deploy succeeds, mapped to the largest value each
#: can hold. The ingest check reserves room for these, because the record it measures is
#: not the record that will be written: provisioning adds a tag, and possibly a longer
#: profile and region, to whatever arrived. Measured, not assumed -- a block sized to the
#: byte loaded fine and then could not be saved with a 33-character tag, leaving a running
#: instance the failed save was supposed to record.
#:
#: Derived from ``_TAG_MAX_LEN`` and ``_MAX_STRING_LEN`` rather than from example values, so
#: the reservation cannot fall behind what the validators actually permit.
_PROVISIONING_WRITES: dict[str, int] = {
    "last_tag": _TAG_MAX_LEN,
    "profile": _MAX_STRING_LEN,
    "region": _MAX_STRING_LEN,
}


def _fits_after_provisioning(cfg: "CloudConfig") -> bool:
    """Whether this record still fits once the launch path has written its fields.

    Serializes the WORST CASE with the real serializer rather than predicting a size. A
    predicted size would be a second implementation of ``save()`` and would drift from it,
    which is the defect this module has now met seven times. Here the check and the write
    are the same function, called with the largest input the write can be handed.
    """
    worst = _public_record(cfg)
    for name, cap in _PROVISIONING_WRITES.items():
        if len(str(worst.get(name, ""))) < cap:
            worst[name] = "x" * cap
    return _serialize_record(worst) is not None


def _public_record(cfg: "CloudConfig") -> dict[str, Any]:
    """The fields that belong in the file: every PUBLIC one, and nothing else.

    Derived from the dataclass and filtered by a rule, not by listing names. `asdict()`
    returns every field including the private read-fingerprint, which would then be
    written into the config the user edits and re-read as unknown junk. Excluding it by
    name would be a second list to keep in step with the first; excluding every
    underscore-prefixed field is one rule that covers fields not written yet.
    """
    return {f.name: getattr(cfg, f.name) for f in fields(cfg) if not f.name.startswith("_")}


def _resolved_key(p: Path) -> str:
    """The identity of a path for comparing one save's target to one load's source.

    ``resolve()`` so the same file reached by two spellings -- a relative path, a symlinked
    parent, a trailing ``.`` -- is recognised as the same file. ``strict=False`` because
    the target legitimately may not exist yet on a first save.
    """
    return str(p.resolve(strict=False))


def _fingerprint_bytes(raw: bytes) -> str:
    """The comparable identity of some bytes ALREADY READ.

    Takes bytes rather than a path so a caller cannot accidentally fingerprint a different
    read than the one it is describing. That was a real defect: ``load()`` parsed one read
    and fingerprinted another, so a write landing between them left the snapshot holding
    old content while its fingerprint described new content -- and ``save()`` then compared
    equal and overwrote the edit the comparison existed to protect.

    Content, not an mtime: two writes inside one filesystem timestamp tick are
    indistinguishable by mtime, and a coarse clock is exactly the case a lost update needs.
    """
    return hashlib.sha256(raw).hexdigest()


def _on_disk_fingerprint(p: Path) -> str:
    """What *p* contains right now, for comparison against a fingerprint taken earlier.

    One read, hashed by the same function :func:`_fingerprint_bytes` gives the loader, so
    the two sides of the comparison cannot hash differently. ``"absent"`` is a real state
    and must be distinguishable from empty, so a file appearing between the read and the
    write is also a conflict.
    """
    try:
        return _fingerprint_bytes(p.read_bytes())
    except FileNotFoundError:
        return "absent"
    except OSError as exc:  # unreadable is not "unchanged"
        return f"unreadable:{exc.errno}"


def _string_field_defaults() -> dict[str, str]:
    """Every ``str``-typed field on :class:`FargateConfig`, with its default.

    Derived from the dataclass rather than listed, so adding a string field extends the
    non-string rejection and its test automatically. A hand-written list is what let
    ``cluster`` keep coercing with ``str()`` after ``assign_public_ip`` was fixed.
    """
    return {
        f.name: f.default
        for f in fields(FargateConfig)
        if f.type in ("str", str) and isinstance(f.default, str)
    }


_STRING_FIELD_DEFAULTS = _string_field_defaults()


def _serialize_record(record: dict[str, Any]) -> str | None:
    """The exact bytes :meth:`CloudConfig.save` writes, or ``None`` if none fit.

    The ONE place a record becomes text, so the ceiling is enforced against the form
    that will actually be written. The bug this exists to make impossible: ``load()``
    measured the file it was HANDED while ``save()`` emitted ``indent=2``, which is
    larger, so a minified block could pass on the way in and fail on the way out. That
    failure arrived after the engine had already provisioned, leaving a RUNNING instance
    with no saved record -- an untracked instance still costing money.

    Pretty output is preferred because a human edits this file. When pretty does not
    fit, compact separators are used rather than refusing: the operator's data is worth
    more than its indentation. ``None`` means not even the compact form fits, and the
    caller refuses -- at ingest, before anything is provisioned.
    """
    pretty = json.dumps(record, indent=2)
    if len(pretty.encode("utf-8")) <= _MAX_FILE_BYTES:
        return pretty
    compact = json.dumps(record, separators=(",", ":"))
    if len(compact.encode("utf-8")) <= _MAX_FILE_BYTES:
        return compact
    return None


def _names_model_credential(secrets: tuple[tuple[str, str], ...]) -> bool:
    """True when a secret IS the crew's model credential, per the ENGINE's own check.

    Calls ``identity.secret_env_name`` instead of re-deriving what it accepts. Three
    rounds of review found the same defect while this was a local approximation: a
    tail-only test admitted a bare ``KIRO_API_KEY`` and a wrong-prefix
    ``junk/KIRO_API_KEY``, then a truthiness test on the crew segment admitted seven
    more spellings. Each one registered the lane so every launch through it refused --
    the offered-and-refusing state this module exists to prevent, reached from inside
    the check written to prevent it. Delegating makes the two agree by CONSTRUCTION, so
    no spelling can pass here and fail there, and no drift pin is needed because there
    is no second copy to drift.

    The import is local to this path and effectively free: ``identity`` imports only the
    standard library, and ``cloud/__init__.py`` has already loaded ~400 modules by the
    time this module exists, so it adds 5 modules and about 4 ms.
    """
    from kiro_crew.cloud.fargate.identity import SecretRef, sole_binding
    from kiro_crew.cloud.fargate.taskdef import MODEL_CREDENTIAL_ENV, secret_destinations_for

    if not secrets:
        return False
    refs = tuple(SecretRef(name=name, arn=arn) for name, arn in secrets)
    try:
        # EVERY reference, not just the first that matches. An early return accepted a
        # good credential ref sitting beside a malformed or cross-crew one, and the
        # engine then refused the whole document at launch -- registering a lane that
        # rejects every launch through it, which is the state this module exists to
        # prevent. Both calls are the ENGINE's own functions, not a second copy:
        # `secret_destinations_for` takes the references precisely so a caller holding
        # only secrets can apply that rule instead of approximating it.
        destinations = secret_destinations_for(refs)
        sole_binding({f"secrets[{i}].valueFrom": ref.arn for i, ref in enumerate(refs)})
    except Exception:  # noqa: BLE001 - any refusal means this set is not usable
        return False
    return MODEL_CREDENTIAL_ENV in destinations


def _bounded_string_tuple(value: object) -> Optional[tuple[str, ...]]:
    """Non-empty strings within the retention bounds, or ``None`` when unusable.

    ``None`` is a positive rejection that voids the whole block, used for the two
    shapes that must not be silently accepted: a list longer than ``_MAX_LIST_ITEMS``
    and a member string longer than ``_MAX_STRING_LEN``. Retaining either without a
    bound is the memory-exhaustion surface ``_MAX_LIST_ITEMS`` exists to close, and
    truncating instead would launch against a placement the operator did not write.

    A non-list still reads as the empty tuple rather than an error, so ``is_complete``
    stays the single place emptiness is judged; an empty required list is what leaves
    the lane unregistered there.
    """
    if not isinstance(value, list):
        return ()
    if len(value) > _MAX_LIST_ITEMS:
        return None
    # ALL-OR-NOTHING. Filtering the bad members out silently launched against a
    # placement the operator did not write: a subnet list with one non-string entry
    # became a shorter list, and the task ran in whichever subnets survived. One bad
    # member voids the block, like one bad secret entry does, so the operator sees an
    # unregistered lane instead of a task in the wrong place.
    for item in value:
        if not isinstance(item, str) or not item or len(item) > _MAX_STRING_LEN:
            return None
    return tuple(value)


@dataclass
class CloudConfig:
    """The launcher's saved state (no secrets)."""

    profile: str = ""
    region: str = DEFAULT_REGION
    last_tag: str = ""
    #: The ``fargate`` block EXACTLY as read from the file, or ``None`` when the
    #: file has none. It is kept raw, not judged, so that ``save()`` writes back
    #: whatever the operator wrote: this object is loaded and re-saved to record
    #: ``last_tag`` after an ordinary EC2 launch, and a field that held only a
    #: judged value would erase a block the operator is half-way through writing.
    #: Whether the block is usable is :meth:`fargate_config`'s question.
    fargate: Any = None

    def fargate_config(self) -> Optional[FargateConfig]:
        """The Fargate block as a typed config, or ``None`` when it is not complete.

        ``None`` is what keeps the lane UNREGISTERED, so an operator who has not
        filled the block in is never offered a lane that would refuse them. This
        judges the raw block on every call rather than once at load, so the file
        round-trips untouched and the seam still sees complete-or-absent.
        """
        return FargateConfig.from_mapping(self.fargate)

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "CloudConfig":
        p = path or (config_dir() / _FILENAME)
        try:
            # ONE read, serving all three purposes: the size bound, the fingerprint the
            # stale-write check compares against, and the parse. Previously the size came
            # from `stat()`, the parse from `read_text()` and the fingerprint from a
            # separate `read_bytes()` -- three observations of a file another writer can
            # change between them. The damaging pair was fingerprint and parse: a write
            # landing in that gap left the snapshot holding OLD content and the
            # fingerprint describing NEW content, so `save()` compared equal and wrote the
            # stale snapshot over the very edit the check exists to protect. Present but
            # inert is worse than absent, because it reads as protected.
            #
            # Reading one byte PAST the ceiling is what makes the bound a bound: it is the
            # smallest read that can tell "at the limit" from "over it" without a second
            # look at the file, and it replaces the `stat()` for the same reason.
            with open(p, "rb") as fh:
                raw = fh.read(_MAX_FILE_BYTES + 1)
        except OSError:
            return cls()
        # Size BEFORE parse. The field ceilings below bound what a block may retain, but
        # json.loads builds the whole document in memory first, so a bound applied to the
        # parsed result never runs on the input that would exhaust it. Treated as a corrupt
        # file rather than an error, because every caller of this already tolerates that
        # and nothing here should raise into a cloud command.
        if len(raw) > _MAX_FILE_BYTES:
            return cls()
        # Both derived from `raw`, so they cannot describe different bytes.
        fingerprint = _fingerprint_bytes(raw)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return cls()
        # A hand-edited cloud.json may parse to valid JSON that is NOT an object
        # (e.g. `"hello"`, `[1,2]`, `42`, `null`); the .get() calls below would
        # then raise AttributeError and escape load() (handle_cloud only catches
        # AWS/validation errors), giving a raw traceback on every cloud command.
        # Honor the docstring's tolerate-a-corrupt-file promise: fall back to
        # defaults on any non-object shape.
        if not isinstance(data, dict):
            return cls()
        # Sanitize last_tag at the boundary: a hand-edited/corrupt cloud.json
        # must not carry a malformed tag into the resume path (downstream
        # validate_tag would raise; an empty tag just means "no last launch").
        last_tag = str(data.get("last_tag", ""))
        if last_tag and not _TAG_RE.match(last_tag):
            last_tag = ""
        record = cls(
            profile=str(data.get("profile", "")),
            region=str(data.get("region", "") or DEFAULT_REGION),
            last_tag=last_tag,
            # Deliberately NOT sanitized here, unlike last_tag: an incomplete
            # block must survive a save, so it is carried as written and judged
            # by fargate_config() at the point of use.
            fargate=data.get("fargate"),
        )
        # Refuse at INGEST what could not be written back, because the size check above
        # does NOT imply this one. `json.dumps` escapes non-ASCII by default, so a
        # character costing 2 bytes on disk costs 6 written back (`e-acute` ->
        # `\\u00e9`), and 4 bytes costs 12 for a surrogate pair. A hand-edited block of
        # 200,000 accented characters is 400 KB on disk, passes the pre-parse bound, and
        # serializes to 1.2 MB. So the on-disk size is not an upper bound on the written
        # size and cannot stand in for it.
        #
        # Refusing HERE and not at `save()` is the whole point: `save()` runs after the
        # engine has provisioned, and it is the call that records the new instance's tag.
        # A failure there leaves a real instance running with nothing tracking it. At
        # ingest nothing has been provisioned, so the same refusal costs nothing.
        #
        # It reserves room for the fields the launch path writes AFTER the deploy, because
        # the record measured here is not the record that gets written. Measured: a block
        # sized to the byte passed this check and then could not be saved with a
        # 33-character tag, so the save that was meant to RECORD a running instance was
        # the one that failed.
        if not _fits_after_provisioning(record):
            return cls()
        # The fingerprint of the bytes THIS snapshot was parsed from -- carried forward
        # from the single read above, never recomputed from the file. A PRIVATE field, so
        # `_public_record` keeps it out of what is written, and `compare=False` keeps it
        # out of equality. `save()` reads it to refuse a write based on a stale read.
        record._loaded_from = (_resolved_key(p), fingerprint)
        return record

    #: ``(resolved path, fingerprint)`` of the file this snapshot was read from, or
    #: ``None`` when it was not read from a usable file. ``save()`` reads it to refuse a
    #: write based on a stale read.
    #:
    #: Private, and every private field is excluded from the written record by
    #: :func:`_public_record` -- by a RULE over the field list, not by naming this one, so
    #: a private field added later cannot leak into the file. ``ClassVar`` looks right
    #: here and is not: the value differs per snapshot, and a ``ClassVar`` cannot be
    #: assigned on an instance. ``compare=False`` keeps it out of ``__eq__``, so two
    #: configs holding the same settings stay equal whatever they were read from.
    #:
    #: The PATH is part of it because the expectation is about one file. ``save(other)``
    #: writes somewhere this snapshot was never read from, so there is nothing to conflict
    #: with, and comparing the two would be exactly the kind of disagreement between two
    #: things that this change exists to remove.
    _loaded_from: tuple[str, str] | None = dc_field(default=None, repr=False, compare=False)

    @classmethod
    def apply_update(
        cls,
        path: Optional[Path] = None,
        *,
        attempts: int = 4,
        **changes: Any,
    ) -> "CloudConfig":
        """Change only the named fields, without erasing anyone else's edit.

        The correct read-modify-write, in ONE place. A caller that holds a snapshot,
        mutates it and saves either erases whatever landed meanwhile or, once ``save()``
        refuses, crashes. Both are wrong, and both were reachable from the launch path:
        ``save()`` there runs immediately after a successful deploy and is the call that
        records the new instance's tag, so an exception at that point leaves a running
        instance untracked -- the same harm as the size crash, differently triggered.

        A caller says which fields IT owns and nothing else is touched, so the launch
        path updating ``profile``/``region``/``last_tag`` cannot disturb a ``fargate``
        block an operator edited during the deploy.

        Retried rather than locked across the caller's work, so a long interactive flow
        never holds a lock. ``attempts`` is bounded because an unbounded retry against a
        writer that never stops would hang; exhausting it re-raises the refusal, which
        the caller can report rather than silently drop.
        """
        unknown = set(changes) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"not fields of {cls.__name__}: {sorted(unknown)}")
        last: ConcurrentEditRefused | None = None
        for _ in range(max(1, attempts)):
            fresh = cls.load(path) if path is not None else cls.load()
            for name, value in changes.items():
                setattr(fresh, name, value)
            try:
                fresh.save(path)
            except ConcurrentEditRefused as exc:
                last = exc  # someone wrote between our read and our write; re-read
                continue
            return fresh
        assert last is not None
        raise last

    def save(self, path: Optional[Path] = None) -> None:
        """Write this record, refusing if the file changed since it was read.

        The refusal is what stops a LOST UPDATE. A caller reads the config, works for a
        while -- the wizard holds its snapshot across an entire interactive flow and a
        deploy -- and writes the whole record back. Any edit that landed in between is in
        the file but not in the snapshot, and writing the snapshot erases it silently.

        Refusing rather than locking the whole read-modify-write: an exclusive lock held
        for the duration of that flow would block every other writer for minutes, and a
        crash mid-flow would leave the lock behind. A conditional write lets the loser
        re-read and re-apply, which is the recoverable direction, and it composes across
        processes because the condition lives on disk rather than in one process's memory.

        The verify and the replace happen under one lock, so the window between deciding
        and writing cannot be interleaved by another writer. Narrowing that window instead
        of closing it would leave a race that is rarer and therefore harder to reproduce.

        A snapshot with no recorded fingerprint -- a fresh instance, or one from a file
        that could not be read as a config -- is written WITHOUT this check. That is a
        deliberate limit, not an oversight: a corrupt or oversized file must stay
        repairable by writing over it, and a first save has nothing to conflict with.
        """
        p = path or (config_dir() / _FILENAME)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = _serialize_record(_public_record(self))
        # Cannot raise for anything `load()` accepted: `load()` enforces the same bound
        # through the same function, so a record that got in can be written back out.
        # This refusal is reachable only for a record built in memory that was never
        # loaded, and it leaves the previous file intact -- the recoverable direction.
        if payload is None:
            raise ValueError(
                f"refusing to write {p}: the record does not fit the "
                f"{_MAX_FILE_BYTES}-byte ceiling load() enforces even when serialized "
                "compactly, so saving it would make this config unreadable"
            )
        lock_path = p.parent / (p.name + ".lock")
        with open(lock_path, "a+", encoding="utf-8") as lock_fh:
            with platform_compat.file_lock(lock_fh.fileno(), exclusive=True):
                expected = self._loaded_from
                # Only when writing BACK to the file this snapshot came from.
                if expected is not None and expected[0] == _resolved_key(p):
                    if _on_disk_fingerprint(p) != expected[1]:
                        raise ConcurrentEditRefused(
                            f"refusing to write {p}: it changed since this copy was read, "
                            "so writing would erase the other edit. Re-read the config, "
                            "re-apply your change, and save again."
                        )
                # Unique temp name per writer: concurrent cloud invocations must not
                # race on a shared .tmp path (see atomic_write's rationale).
                atomic_write(p, payload)
                # The record now MATCHES what is on disk, so a second save from this same
                # instance is not a stale write. Without this a caller that saves twice --
                # the wizard's retry path does -- would refuse its own previous write.
                self._loaded_from = (_resolved_key(p), _on_disk_fingerprint(p))
