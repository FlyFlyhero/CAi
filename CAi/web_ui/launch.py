"""
Launch the CAi Web UI (FastAPI backend + static frontend).

Usage:
    python -m CAi.web_ui.launch
    # or from CAi directory:
    python web_ui/launch.py
"""

import os
import sys
from pathlib import Path

import uvicorn


def launch(agent, host="0.0.0.0", port=7000):
    """
    Launch the Web UI with the given agent.

    Args:
        agent: A1pro agent instance
        host: Server host
        port: Server port
    """
    from CAi.web_ui.backend.app import app, set_agent

    # Set agent
    set_agent(agent)

    # Mount frontend static files
    frontend_dir = Path(__file__).parent / "frontend"
    if frontend_dir.exists():
        from fastapi.staticfiles import StaticFiles
        from fastapi.responses import FileResponse

        @app.get("/")
        async def serve_index():
            return FileResponse(str(frontend_dir / "index.html"))

        app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")

        # Catch-all for SPA routing
        @app.get("/{path:path}")
        async def serve_spa(path: str):
            file_path = frontend_dir / path
            if file_path.exists() and file_path.is_file():
                return FileResponse(str(file_path))
            return FileResponse(str(frontend_dir / "index.html"))

    print(f"\n{'='*50}")
    print(f"🚀 CAi Web UI")
    print(f"{'='*50}")
    print(f"   Frontend: http://localhost:{port}")
    print(f"   API Docs: http://localhost:{port}/docs")
    print(f"{'='*50}\n")

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    # Quick launch for development
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    from CAi.CAi_agent.agent import A1pro
    from CAi.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

    agent = A1pro(
        llm=LLM_MODEL,
        source="Custom",
        base_url=LLM_BASE_URL,
        api_key=LLM_API_KEY,
        use_tool_retriever=False,
        expected_data_lake_files=[],
        auto_load_tools=True,
        auto_load_skills=False,
    )

    launch(agent)
