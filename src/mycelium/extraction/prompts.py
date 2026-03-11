"""Prompt builders for extraction workflows."""

from __future__ import annotations

from mycelium.domain.predicates import get_canonical_predicates


def daily_notes_system_prompt() -> str:
    """Prompt focused on cross-agent valuable facts from daily notes."""
    canonical = ", ".join(sorted(get_canonical_predicates()))
    return (
        "Extract only cross-agent useful facts from daily notes. "
        "Return ONLY valid JSON with shape: "
        '{"facts":[{"subject":"...","predicate":"...","object":"...",'
        '"context":"optional","tags":["optional"],'
        '"source_type":"optional"}]}. '
        "Use canonical predicates when possible. "
        f"Allowed canonical predicates: {canonical}. "
        "Prioritize state changes, decisions, discoveries, and human corrections. "
        "Skip internal mechanics, credentials, build logs, and volatile raw metrics."
    )


def daily_notes_user_prompt(
    *,
    file_path: str,
    workspace_key: str,
    source_agent_id: str,
    correction_authority: bool,
    content: str,
) -> str:
    return (
        f"Workspace: {workspace_key}\n"
        f"File: {file_path}\n"
        f"Default source agent id: {source_agent_id}\n"
        f"Correction authority workspace: {correction_authority}\n"
        "Extract facts that other agents should know.\n"
        "If a line is a clear human correction/approval/rejection, set source_type to "
        '"human_correction". Otherwise use "agent_extraction".\n'
        "Daily note content:\n"
        f"{content}"
    )
