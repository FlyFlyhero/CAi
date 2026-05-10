"""CAi — 入口文件"""

from CAi.CAi_agent import A1pro
from CAi.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

agent = A1pro(
    llm=LLM_MODEL,
    source="Custom",
    base_url=LLM_BASE_URL,
    api_key=LLM_API_KEY,
)

# 启动 Web UI
agent.launch_web_ui(port=7001)
