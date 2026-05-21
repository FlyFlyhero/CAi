"""CAi CLI entry point — REPL loop and launch function."""

from __future__ import annotations

from typing import Any

from CAi.cli.theme import console
from CAi.cli.display import (
    print_banner,
    print_session_info,
    print_resumed_info,
)
from CAi.cli.input import create_prompt_session
from CAi.cli.session import load_conversation, new_conversation, append_turn
from CAi.cli.streaming import stream_response, StreamingInterrupted
from CAi.cli.commands import dispatch, CommandResult


def _flush_usage(agent: Any) -> None:
    """Flush REPL utility usage back to the registry."""
    try:
        registry = getattr(agent, "utility_registry", None)
        if registry is None:
            return
        from CAi.CAi_agent.execution.repl import flush_utility_usage
        usage = flush_utility_usage()
        if usage:
            registry.apply_usage(usage)
    except Exception:
        pass


def repl_loop(agent: Any, store: Any, workspace_dir: str, conv_id: str | None = None) -> None:
    """Main REPL loop."""
    from CAi.web_ui.backend.chat_service import clean_stored_answer

    session = create_prompt_session()

    # --- Session init ---
    if conv_id:
        loaded = load_conversation(store, conv_id)
        if loaded is None:
            console.print(f"[cai.highlight]  Session {conv_id} not found. Starting fresh.[/cai.highlight]")
            conv_id, model_history, display_messages = new_conversation(store)
        else:
            model_history, display_messages = loaded
    else:
        conv_id, model_history, display_messages = new_conversation(store)

    # Mutable state dict — passed into command handlers so they can mutate it
    state: dict = {
        "conv_id": conv_id,
        "model_history": model_history,
        "display_messages": display_messages,
        "last_raw_log": [],
        "last_user_input": None,
        "override_input": None,
    }

    model_name = getattr(agent.llm, "model_name", "unknown")
    print_session_info(state["conv_id"], model_name, workspace_dir)

    if model_history:
        print_resumed_info(len(model_history) // 2)

    # --- REPL ---
    while True:
        # Check for override (e.g. :retry, :ml)
        if state["override_input"] is not None:
            user_input = state["override_input"]
            state["override_input"] = None
        else:
            try:
                # Separator line before user input
                console.print("[cai.dim]  ──────────────────────────────────────────[/cai.dim]")
                user_input = session.prompt("› ")
            except KeyboardInterrupt:
                console.print("\n[cai.dim]  interrupted — :quit to exit[/cai.dim]")
                continue
            except EOFError:
                console.print("\n[cai.dim]  goodbye.[/cai.dim]")
                break

        if not user_input or not user_input.strip():
            continue

        stripped = user_input.strip()

        # --- Command dispatch ---
        result = dispatch(
            stripped,
            agent=agent,
            store=store,
            state=state,
            session=session,
        )

        if result == CommandResult.BREAK:
            break
        if result == CommandResult.CONTINUE:
            continue
        # PASSTHROUGH — treat as a message to the agent
        # (state["override_input"] may have been set by :ml or :retry)
        if state["override_input"] is not None:
            user_input = state["override_input"]
            state["override_input"] = None
            stripped = user_input.strip()

        # --- Stream response ---
        prompt = f"{user_input}\n\n[工作目录]: {workspace_dir}"
        state["last_user_input"] = user_input
        state["last_raw_log"] = []

        try:
            stream_result = stream_response(agent, prompt, state["model_history"])
            state["last_raw_log"] = stream_result.raw_log

        except StreamingInterrupted as exc:
            state["last_raw_log"] = exc.raw_log
            if exc.partial:
                console.print("[cai.warn]  ⚠ interrupted[/cai.warn]")
                clean = clean_stored_answer(exc.partial) or exc.partial
                append_turn(
                    state["model_history"],
                    state["display_messages"],
                    user_input,
                    exc.partial,
                    clean,
                    interrupted=True,
                    interrupted_at_char=len(exc.partial),
                )
                store.save_messages(state["conv_id"], state["display_messages"])
            else:
                console.print("[cai.warn]  ⚠ interrupted before output[/cai.warn]")
            continue

        # --- Persist turn ---
        clean = clean_stored_answer(stream_result.full_message) or stream_result.full_message
        append_turn(
            state["model_history"],
            state["display_messages"],
            user_input,
            stream_result.full_message,
            clean,
        )
        store.save_messages(state["conv_id"], state["display_messages"])
        _flush_usage(agent)


def launch_cli(agent: Any, resume_conv_id: str | None = None) -> None:
    """Bootstrap the CLI: set up workspace, store, and enter the REPL."""
    from CAi.CAi_agent.execution.repl import set_workspace_dir
    from CAi.web_ui.backend.conversation_store import ConversationStore
    from CAi.config import WORKSPACE_DIR

    workspace_dir = str((WORKSPACE_DIR / "agent_workspace").resolve())
    conversations_dir = str((WORKSPACE_DIR / "agent_workspace" / "_conversations").resolve())

    set_workspace_dir(workspace_dir)
    store = ConversationStore(conversations_dir)

    print_banner()
    repl_loop(agent, store, workspace_dir, conv_id=resume_conv_id)
