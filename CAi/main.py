"""CAi — entry point.

Reads configuration from CAi/config.py (which loads CAi/.env), builds
an A1pro agent, and launches the Web UI. Provider auto-detection
happens inside A1pro based on LLM_MODEL; override it by setting
LLM_SOURCE in the .env file.
"""

from CAi.CAi_agent import A1pro
from CAi.config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
    LLM_SOURCE,
    LLM_TEMPERATURE,
)

agent = A1pro(
    llm=LLM_MODEL,
    source=LLM_SOURCE,       # None → auto-detect from model name
    base_url=LLM_BASE_URL,
    api_key=LLM_API_KEY,
    temperature=LLM_TEMPERATURE,
)

# Launch the Web UI (FastAPI + static frontend). See CAi/web_ui/launch.py.
agent.launch_web_ui(port=7001)
