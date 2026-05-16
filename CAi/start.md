# Backend Tool Development Guide

> Using `scscore` as an example, this guide walks through adding a new
> backend tool to CAi end-to-end.

## Overall architecture — sandbox execution

The full invocation chain is:

```text
Agent wrapper  (CAi/toolkit/functions/*.py)
    │  client.run_tool(<tool>, payload, action=...)
    │  POST /run/{tool}/{action}     body: params dict
    ▼
Tool server   (CAi/toolkit/server/app.py  — FastAPI)
    │  JobManager creates a sandbox dir, writes params.json
    │  background task: job_manager.run_job(job_id, tool, action)
    ▼
JobManager    (CAi/toolkit/server/job_manager.py)
    │  conda run -n <env> python <script.py>
    │  cwd  = workspace/jobs/<job_id>/     ← isolated sandbox
    │  stdin = params.json content (JSON string)
    ▼
Tool script   (run.py in your tool's directory)
    │  read parameters from params.json
    │  execute computation
    │  write results to result.json
    ▼
JobManager polls result.json / error.json
    ▼
client.run_tool returns the parsed result to the wrapper
    ▼
Agent sees the wrapper's return value
```

**Key design: every job has its own isolated sandbox directory.**

```text
CAi/toolkit/server/workspace/jobs/
└── <uuid>/
    ├── params.json     ← input parameters (written by JobManager)
    ├── result.json     ← computation results (written by run.py)
    ├── error.json      ← error info (written by run.py or JobManager)
    ├── stdout.log      ← captured stdout
    └── stderr.log      ← captured stderr
```

The script's cwd is this UUID directory, so `open("params.json")` and
`open("result.json", "w")` work directly. **No absolute paths needed.**

---

## File layout — three files per tool

```text
CAi/toolkit/server/tools/
└── <your_tool_name>/           ← tool directory; name is the tool ID
    ├── config.json             ← required: env, GPU, action mapping
    ├── run.py                  ← main script (single-action tools)
    └── <dependencies_or_models>/
```

---

## Step 1 — `config.json`

### Simplest case (single action, CPU only)

```json
{
  "name": "scscore",
  "conda_env": "scscore",
  "gpu": false
}
```

- `name`: tool name — must match the directory name.
- `conda_env`: conda environment used to run the script.
- `gpu`: whether to request a GPU from the queue (`false` = CPU only).

When `actions` is omitted the framework uses `{"default": "run.py"}`,
so `POST /run/scscore/default` runs `run.py`.

### GPU-required case

```json
{
  "name": "mytool",
  "conda_env": "mytool_env",
  "gpu": true
}
```

`gpu_manager` allocates a free GPU, injects `CUDA_VISIBLE_DEVICES`, and
releases the GPU when the job finishes.

### Multi-action case (one tool, several scripts)

```json
{
  "name": "reinvent4",
  "conda_env": "reinvent4",
  "gpu": true,
  "actions": {
    "sample": "sample.py",
    "score":  "score.py"
  }
}
```

Then the wrapper can call `POST /run/reinvent4/sample` or
`POST /run/reinvent4/score` independently.

---

## Step 2 — `run.py` (full example using scscore)

`run.py` follows one rule: **read from `params.json`, write to `result.json`**.

```python
import json


def main():
    # 1. Read parameters. cwd is already the sandbox dir.
    params = json.load(open("params.json"))
    smiles_list = params.get("smiles_list", [])
    model_type  = params.get("model_type", "1024bool")

    # 2. Run the computation.
    result = calculate_scscore(smiles_list, model_type)

    # 3. Write the result. Must be JSON-serialisable.
    with open("result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
```

### Recommended `result.json` format

The toolkit's HTTP client (`CAi/toolkit/client.py`) parses `result.json`
and expects this shape:

```json
{
  "success": true,
  "summary": {
    "total": 2,
    "successful": 2,
    "failed": 0,
    "avg_scscore": 1.85
  },
  "results": [...],
  "errors": null
}
```

| Field | Description |
|---|---|
| `success` | Boolean. If `false`, the client treats the tool as failed. |
| `summary` | Aggregated statistics — the wrapper usually reads this first. |
| `results` | Full per-item results. |
| `errors` | Partial-failure list, or `null` when everything succeeded. |

### Error handling — wrap `main()` in try/except

```python
def main():
    try:
        params = json.load(open("params.json"))
        # ... normal logic ...
        result = {"success": True, "summary": {...}, "results": [...]}
    except Exception as e:
        # Still write result.json so the client surfaces a clean error.
        result = {"success": False, "error": str(e)}

    with open("result.json", "w") as f:
        json.dump(result, f)


if __name__ == "__main__":
    main()
```

> **Note**: don't rely on `print()` for output. stdout is captured into
> `stdout.log` and never reaches the agent. For debugging, write to
> stderr with `print(..., file=sys.stderr)`.

---

## Step 3 — Register the agent wrapper

After the backend script is ready, add a Python wrapper function so the
agent can call the tool. Wrappers live in:

- `CAi/toolkit/functions/generation.py` — for molecule-generation tools
- `CAi/toolkit/functions/evaluation.py` — for molecule-evaluation tools

Each wrapper uses `run_tool` from `CAi.toolkit.client`:

```python
# in CAi/toolkit/functions/evaluation.py (for scscore)

import json

from ..client import run_tool


def calculate_scscore(
    smiles: str | None = None,
    smiles_list: list[str] | None = None,
    model_type: str = "1024bool",
) -> str:
    """
    [Tool description — the LLM reads this to decide when to call the tool]
    Estimate synthetic accessibility via the SCScore model.
    ...
    """
    # 1. Argument validation
    if smiles:
        smiles_list = [smiles]
    if not smiles_list:
        return json.dumps(
            {"success": False, "error": "smiles or smiles_list must be provided"}
        )

    # 2. Call the backend (tool name must match the tools/ directory)
    payload = {"smiles_list": smiles_list, "model_type": model_type}
    result = run_tool("scscore", payload)
    # For multi-action tools, pass action=...:
    #   run_tool("reinvent4", payload, action="score", timeout_mins=15)

    # 3. Return a JSON string to the agent
    return json.dumps(result, ensure_ascii=False)
```

`run_tool` handles submission, polling (with exponential backoff),
timeout (default 5 minutes, override with `timeout_mins`), and error
normalisation.

### Expose the wrapper to the agent

Add the function name to two `__all__` lists:

1. `CAi/toolkit/functions/__init__.py`
2. `CAi/toolkit/__init__.py`

This is what makes the agent's `ModuleScanner` pick it up. Without
either export, the tool is invisible to `A1pro`.

---

## Complete checklist

```text
□ 1. Create files under CAi/toolkit/server/tools/<your_tool>/
      - config.json   (name / conda_env / gpu; optionally actions)
      - run.py        (read params.json → compute → write result.json)

□ 2. Install dependencies in the target conda environment
      conda activate <env>
      pip install ...

□ 3. Add the wrapper function to one of:
      - CAi/toolkit/functions/generation.py
      - CAi/toolkit/functions/evaluation.py
      and re-export it from:
      - CAi/toolkit/functions/__init__.py  (__all__)
      - CAi/toolkit/__init__.py            (__all__)

□ 4. Restart the tool server
      python -m CAi.toolkit.server.app
      # Startup banner prints the loaded tools — verify your tool is listed.

□ 5. Quick self-check via /health
      curl http://localhost:8001/health
      # → {"status": "ok", "tools": [..., "your_tool_name", ...], ...}

□ 6. Manual smoke test with curl
      curl -X POST http://localhost:8001/run/scscore/default \
           -H "Content-Type: application/json" \
           -d '{"smiles_list": ["c1ccccc1"]}'
      # → {"job_id": "<uuid>"}

      curl http://localhost:8001/job/<uuid>
      # → {"status": "finished", "data": {"success": true, ...}}

□ 7. Smoke test via the Python client (same path the agent takes)
      python -c "from CAi.toolkit.client import run_tool; \
                 print(run_tool('scscore', {'smiles_list':['c1ccccc1']}))"

□ 8. For a running agent, either restart it or hot-reload tools:
      agent.reload_tools()
```
