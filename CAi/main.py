"""CAi — entry point.

Reads configuration from CAi/config.py (which loads CAi/.env), builds
an A1pro agent, and launches the Web UI. Provider auto-detection
happens inside A1pro based on LLM_MODEL; override it by setting
LLM_SOURCE in the .env file.
"""

import socket
import sys

from CAi.CAi_agent import A1pro
from CAi.config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
    LLM_SOURCE,
    LLM_TEMPERATURE,
)

WEB_UI_PORT = 8888


def _startup_summary() -> None:
    """Print effective LLM config before anything else, so if the agent
    fails to initialise the user can see which knobs were read."""
    print("=" * 50)
    print("CAi starting with:")
    print(f"  LLM_MODEL       = {LLM_MODEL!r}")
    print(f"  LLM_SOURCE      = {LLM_SOURCE!r}  (None = auto-detect)")
    print(f"  LLM_BASE_URL    = {LLM_BASE_URL!r}")
    print(f"  LLM_API_KEY     = {'<set>' if LLM_API_KEY else '<EMPTY — requests will 401>'}")
    print(f"  LLM_TEMPERATURE = {LLM_TEMPERATURE}")
    print("=" * 50)


def _check_port_free(port: int) -> None:
    """Fail fast with a clear message if the target port is already in use.

    Uvicorn will also fail, but its default message is easy to miss in a
    busy terminal. This check runs before agent initialisation so users
    don't wait for tool loading only to hit a port conflict afterwards.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
    except OSError:
        print(
            f"\n❌ Port {port} is already in use.\n\n"
            f"Likely a previous CAi run didn't shut down cleanly.\n"
            f"Find and stop the process, then retry:\n"
            f"  PowerShell:\n"
            f"    Get-NetTCPConnection -LocalPort {port} -State Listen |\n"
            f"      ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force }}\n"
            f"  Linux/macOS:\n"
            f"    lsof -ti tcp:{port} | xargs -r kill -9\n",
            file=sys.stderr,
        )
        sys.exit(1)
    finally:
        s.close()


def main() -> None:
    _startup_summary()
    _check_port_free(WEB_UI_PORT)
    try:
        agent = A1pro(
            llm=LLM_MODEL,
            source=LLM_SOURCE,        # None → auto-detect from model name
            base_url=LLM_BASE_URL,
            api_key=LLM_API_KEY,
            temperature=LLM_TEMPERATURE,
        )
    except Exception as e:
        print(f"\n❌ Agent initialisation failed: {type(e).__name__}: {e}", file=sys.stderr)
        print(
            "\nCheck CAi/.env — common causes:\n"
            "  - LLM_MODEL prefix doesn't match a known provider AND LLM_BASE_URL is empty.\n"
            "  - A real provider (claude-* / gpt-* / deepseek-*) was detected but the\n"
            "    matching API key env var (ANTHROPIC_API_KEY / OPENAI_API_KEY /\n"
            "    DEEPSEEK_API_KEY) is missing.\n"
            "  - Force provider by setting LLM_SOURCE=Custom and providing LLM_BASE_URL.",
            file=sys.stderr,
        )
        raise

    # Launch the Web UI (FastAPI + static frontend). See CAi/web_ui/launch.py.
    agent.launch_web_ui(port=WEB_UI_PORT)


if __name__ == "__main__":
    main()
