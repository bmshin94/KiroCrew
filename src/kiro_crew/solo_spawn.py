"""The solo-spawn gate: one sub-agent for one task has to say why.

``spawn_run`` / ``spawn_sub_agents`` carry a textual gate in their descriptions
("2+ independent tasks, bulk data, or a different agent/model -- otherwise do
it yourself"). Text is advice, and the base prompt's "delegate to a sub-agent"
kept winning against it: a single investigation, a single fix, a single review
each spawned one sub-agent for no parallelism gain. This module is the
MECHANICAL half of that gate, and it is a handshake rather than a wall:

1. A call that spawns exactly ONE task, names no ``solo_reason``, and asks for
   no model / agent / crew other than the caller's own is REFUSED with a
   question -- can the caller do this itself? -- and nothing is spawned.
2. The caller either does the work in its own session, or calls again with a
   ``solo_reason`` from :data:`SOLO_SPAWN_REASONS`, or names a genuinely
   different model / agent / crew. Only then does the spawn proceed.

The reasons are a closed vocabulary on purpose. A free-text "why" or a bare
``confirm=true`` is rubber-stamped by the second call; a menu that does NOT
contain "to preserve context" or "investigation" makes the caller pick a
reason it can defend, and the reason travels into the spawn line the user
reads and into the audit log.

Two halves, two hosts:

* :func:`solo_spawn_refusal` runs in the MCP tool process, which is the only
  place the task COUNT is known (the gateway sees one POST per task). It knows
  whether a model / agent / crew was NAMED, not whether it differs from the
  caller's own, so it lets a named one through.
* :func:`solo_spawn_difference` runs in the gateway, which knows the
  parent session's resolved agent template, its member selection and (for a
  dashboard slot) its model, and catches the call that named the caller's OWN
  agent, model or crew to slip past the tool-side check. It answers with the
  GROUND on which the named value differs, so the gateway audits every
  outcome -- refused, let through on a reason, let through on a difference.
  It fails OPEN when a parent fact is unknown: an un-comparable value never
  refuses a spawn that the tool side let through.

Programmatic clients (the SDK, apps posting to ``/api/spawn`` directly) are
not gated: they do not send the ``solo`` marker, and the gate is about a
model's decision, not an app's.
"""

from __future__ import annotations

from typing import Any

from kiro_crew.model_registry import canonical_key

# The closed vocabulary. ``""`` is "not given", matching the EFFORT_VALUES
# convention so the validator accepts an absent/empty field.
SOLO_SPAWN_REASONS = frozenset({"", "bulk_data", "fresh_context"})

# What each reason licenses, in the words the tool description uses. Read by
# the schema builder so the parameter description and this module agree.
SOLO_SPAWN_REASON_GLOSS: dict[str, str] = {
    "bulk_data": (
        "the step would flood YOUR context with bulk output (a huge log, a "
        "wide search, many large files) and only the distilled result is needed"
    ),
    "fresh_context": (
        "the RESULT would be wrong if the run saw this session's context (a blind "
        "review, a clean-slate repro) -- not for investigation, research or "
        "saving context"
    ),
}

# Error code the gateway returns when the roster check refuses a solo spawn.
SOLO_SPAWN_REFUSED_CODE = "solo_spawn_unjustified"

# Sentinels a dashboard slot stores for "no model chosen"; neither is a model.
_UNKNOWN_MODEL = frozenset({"", "auto"})


def solo_spawn_question(*, tool: str = "spawn_run") -> str:
    """The refusal text: the question first, then the two ways forward.

    Shared by the tool-side refusal and the gateway's roster refusal so the
    caller reads ONE wording wherever the gate fired. Prefixed ``Error:`` for
    the SEL / caller convention that a spawn result which started nothing opens
    with that word.
    """
    param = "solo_reason"
    if tool == "spawn_sub_agents":
        differs = "an agent_or_mode that differs from your own"
    else:
        differs = "a model, agent or crew that differs from your own"
    return (
        "Error: solo spawn refused -- one task, no solo_reason, and nothing "
        "that differs from this session. Can you do this task yourself, here, "
        "now? If yes, do it: one sub-agent is a round-trip with no parallelism "
        f"gain. If not, call again with {param}='bulk_data' ("
        f"{SOLO_SPAWN_REASON_GLOSS['bulk_data']}) or {param}='fresh_context' ("
        f"{SOLO_SPAWN_REASON_GLOSS['fresh_context']}), or name {differs}. "
        "Nothing was spawned."
    )


def solo_spawn_refusal(
    task_count: int,
    solo_reason: str,
    *,
    model: str = "",
    agent: str = "",
    crew: str = "",
    tool: str = "spawn_run",
) -> str | None:
    """Tool-side gate: the refusal text, or ``None`` when the spawn may proceed.

    Refuses exactly the call that is one task, gives no reason, and names no
    model / agent / crew at all. A NAMED one passes here because this process
    cannot tell it from the caller's own; :func:`solo_spawn_difference` on
    the gateway can, and does.
    """
    if task_count != 1:
        return None
    if solo_reason:
        return None
    if _named_model(model) or agent or crew:
        return None
    return solo_spawn_question(tool=tool)


def solo_spawn_note(
    solo_reason: str,
    *,
    model: str = "",
    agent: str = "",
    crew: str = "",
) -> str:
    """The one line a solo spawn adds to the tool result, so the transcript
    shows on what grounds a lone sub-agent was let through."""
    if solo_reason:
        return f"Solo spawn -- reason: {solo_reason}."
    model = _named_model(model)
    named = [f"{k}={v!r}" for k, v in (("crew", crew), ("agent", agent), ("model", model)) if v]
    return "Solo spawn -- let through because it names " + ", ".join(named) + "."


def _named_model(model: str) -> str:
    """*model* as a NAMED model, or ``""``: the ``"auto"`` sentinel (and empty)
    mean "no model chosen" and must not count as naming one, or a lone task
    could pass the gate with ``model="auto"`` and no justification at all."""
    return "" if not isinstance(model, str) or model.strip().lower() in _UNKNOWN_MODEL else model


def _canonical_model(name: str) -> str:
    """Registry canonical for *name*, else the lower-cased name itself."""
    return canonical_key(name) or name.strip().lower()


def parent_slot_model(state: Any, parent_session: str) -> str:
    """The model a dashboard parent slot is pinned to, or ``""`` when unknown.

    Match the slot's effective session key, including channel-linked slots.
    An absent slot, an empty model and the ``"auto"`` sentinel are "unknown".
    """
    slots = getattr(state, "_slots", None) or {}
    try:
        # circular import: chat_utils imports dashboard.state, which reaches
        # validation.py, which imports this module.
        from kiro_crew.dashboard.chat_utils import effective_session_key

        slot = next(
            (slot for slot in slots.values() if effective_session_key(slot) == parent_session),
            None,
        )
    except Exception:  # noqa: BLE001 - identity check is best-effort
        return ""
    if slot is None:
        return ""
    model = getattr(slot, "model", "") or ""
    return "" if not isinstance(model, str) or model in _UNKNOWN_MODEL else model


def solo_spawn_difference(
    state: Any,
    parent_session: str,
    *,
    agent: str = "",
    model: str = "",
    crew: str = "",
) -> str:
    """Gateway roster check: on what ground is the requested agent / model /
    crew NOT the parent's own?

    Returns the ground -- ``"crew"``, ``"agent"`` or ``"model"``, suffixed with
    ``" (parent unknown)"`` when the parent fact could not be compared and the
    check fails OPEN -- so the gateway can audit WHY a lone spawn was let
    through. Returns ``""`` only when every value the caller named is the
    parent's own, which is the case the tool-side check cannot see and the
    reason this half exists.

    ``agent`` is a provider template id and is compared against the parent's
    RESOLVED template (``sessions.get_agent``), never against its member
    alias: a member session ``coder`` running on ``kirocrew-worker`` that names
    ``kirocrew-worker`` has named its own agent. The member alias is what
    ``crew`` compares against.
    """
    kind, own = "", ""
    own_agent = ""
    model = _named_model(model)
    if parent_session:
        try:
            sel = state.sessions.get_agent_selection(parent_session)
            if isinstance(sel, tuple) and len(sel) == 2:
                kind, own = str(sel[0]), str(sel[1] or "")
        except Exception:  # noqa: BLE001 - unknown parent, compare nothing
            kind, own = "", ""
        try:
            got = state.sessions.get_agent(parent_session)
            own_agent = got if isinstance(got, str) else ""
        except Exception:  # noqa: BLE001 - unknown parent, compare nothing
            own_agent = ""
    if crew:
        # A crew is another member's memory silo -- unless it is the parent's
        # own: a private member is REQUIRED to name itself to delegate, and a
        # plain template session already runs on the ``default`` crew's store.
        own_crew = (kind == "member" and own and crew == own) or (
            kind == "template" and crew == "default"
        )
        if not own_crew:
            return "crew" if kind else "crew (parent unknown)"
    if agent:
        if not own_agent:
            return "agent (parent unknown)"
        if agent != own_agent:
            return "agent"
    if model:
        parent_model = parent_slot_model(state, parent_session)
        if not parent_model:
            return "model (parent unknown)"
        if _canonical_model(model) != _canonical_model(parent_model):
            return "model"
    return ""
