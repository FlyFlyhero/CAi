"""Command dispatcher — maps :command strings to handler functions."""

from __future__ import annotations

from typing import Any, Callable

from CAi.cli.theme import console


# ---------------------------------------------------------------------------
# Command result sentinel
# ---------------------------------------------------------------------------

class CommandResult:
    """Returned by command handlers to signal control flow."""
    CONTINUE = "continue"   # go back to prompt
    BREAK = "break"         # exit the REPL loop
    PASSTHROUGH = "pass"    # not a command, treat as user message


# ---------------------------------------------------------------------------
# Individual command handlers
# ---------------------------------------------------------------------------

def cmd_quit(**_: Any) -> str:
    console.print("[cai.dim]  goodbye.[/cai.dim]")
    return CommandResult.BREAK


def cmd_help(**_: Any) -> str:
    from CAi.cli.display import display_help
    display_help()
    return CommandResult.CONTINUE


def cmd_convs(store: Any, **_: Any) -> str:
    from CAi.cli.display import display_conversations
    display_conversations(store.list_conversations())
    return CommandResult.CONTINUE


def cmd_load(stripped: str, store: Any, state: dict, **_: Any) -> str:
    target_id = stripped[len(":load "):].strip()
    from CAi.cli.session import load_conversation
    loaded = load_conversation(store, target_id)
    if loaded is None:
        console.print(f"[cai.highlight]  Session {target_id} not found.[/cai.highlight]")
    else:
        state["conv_id"] = target_id
        state["model_history"], state["display_messages"] = loaded
        turns = len(state["model_history"]) // 2
        console.print(
            f"[cai.accent]  ✔ switched to {target_id[:12]}[/cai.accent] "
            f"[cai.dim]({turns} turns)[/cai.dim]"
        )
    return CommandResult.CONTINUE


def cmd_new(store: Any, state: dict, **_: Any) -> str:
    from CAi.cli.session import new_conversation
    conv_id, mh, dm = new_conversation(store)
    state["conv_id"] = conv_id
    state["model_history"] = mh
    state["display_messages"] = dm
    console.print(f"[cai.accent]  ✔ new session[/cai.accent] [cai.primary]{conv_id[:12]}[/cai.primary]")
    return CommandResult.CONTINUE


def cmd_retry(state: dict, **_: Any) -> str:
    if state.get("last_user_input"):
        state["override_input"] = state["last_user_input"]
        console.print("[cai.secondary]  ↺ retrying…[/cai.secondary]")
        return CommandResult.PASSTHROUGH
    console.print("[cai.warn]  nothing to retry[/cai.warn]")
    return CommandResult.CONTINUE


def cmd_rename(stripped: str, store: Any, state: dict, **_: Any) -> str:
    new_title = stripped[len(":rename "):].strip()
    if new_title:
        store.update_title(state["conv_id"], new_title)
        console.print(f"[cai.accent]  ✔ renamed → {new_title}[/cai.accent]")
    return CommandResult.CONTINUE


def cmd_reset_kernel(**_: Any) -> str:
    try:
        from CAi.CAi_agent.execution.repl import reset_namespace
        reset_namespace()
        console.print("[cai.accent]  ✔ kernel reset.[/cai.accent]")
    except Exception as e:
        console.print(f"[cai.highlight]  ✗ kernel reset failed: {e}[/cai.highlight]")
    return CommandResult.CONTINUE


def cmd_clear(**_: Any) -> str:
    console.clear()
    return CommandResult.CONTINUE


def cmd_forget(state: dict, **_: Any) -> str:
    state["model_history"].clear()
    state["display_messages"].clear()
    console.print("[cai.dim]  memory cleared.[/cai.dim]")
    return CommandResult.CONTINUE


def cmd_delete(store: Any, state: dict, **_: Any) -> str:
    store.delete_conversation(state["conv_id"])
    from CAi.cli.session import new_conversation
    conv_id, mh, dm = new_conversation(store)
    state["conv_id"] = conv_id
    state["model_history"] = mh
    state["display_messages"] = dm
    console.print(f"[cai.dim]  deleted. new session:[/cai.dim] [cai.primary]{conv_id[:12]}[/cai.primary]")
    return CommandResult.CONTINUE


def cmd_last_obs(state: dict, **_: Any) -> str:
    from CAi.cli.display import display_full_observation
    display_full_observation(state.get("last_raw_log", []))
    return CommandResult.CONTINUE


def cmd_history(stripped: str, state: dict, **_: Any) -> str:
    from CAi.cli.display import display_history
    parts = stripped.split()
    n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 3
    display_history(state["display_messages"], n)
    return CommandResult.CONTINUE


def cmd_tools(agent: Any, **_: Any) -> str:
    from CAi.cli.display import display_tools
    tools = agent.list_tools() if hasattr(agent, "list_tools") else []
    display_tools(tools)
    return CommandResult.CONTINUE


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def dispatch(stripped: str, *, agent: Any, store: Any, state: dict, session: Any) -> str:
    """
    Match stripped input against known commands and execute the handler.

    Returns a CommandResult constant:
      - CONTINUE  → go back to prompt
      - BREAK     → exit REPL
      - PASSTHROUGH → not a command, caller should treat as a message
    """
    # Multiline mode is handled before dispatch
    if stripped == ":ml":
        from CAi.cli.input import read_multiline
        text = read_multiline(session)
        if text is None:
            return CommandResult.CONTINUE
        state["override_input"] = text
        return CommandResult.PASSTHROUGH

    kwargs = dict(stripped=stripped, agent=agent, store=store, state=state, session=session)

    if stripped in (":quit", ":exit", ":q"):
        return cmd_quit(**kwargs)
    if stripped in (":help", ":h", ":?"):
        return cmd_help(**kwargs)
    if stripped == ":convs":
        return cmd_convs(**kwargs)
    if stripped.startswith(":load "):
        return cmd_load(**kwargs)
    if stripped == ":new":
        return cmd_new(**kwargs)
    if stripped == ":retry":
        return cmd_retry(**kwargs)
    if stripped.startswith(":rename "):
        return cmd_rename(**kwargs)
    if stripped == ":reset-kernel":
        return cmd_reset_kernel(**kwargs)
    if stripped == ":clear":
        return cmd_clear(**kwargs)
    if stripped == ":forget":
        return cmd_forget(**kwargs)
    if stripped == ":delete":
        return cmd_delete(**kwargs)
    if stripped == ":last_obs":
        return cmd_last_obs(**kwargs)
    if stripped.startswith(":history"):
        return cmd_history(**kwargs)
    if stripped == ":tools":
        return cmd_tools(**kwargs)

    return CommandResult.PASSTHROUGH
