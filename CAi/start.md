# Backend Tool Development Guide

> Using `scscore` as an example, this guide explains how to add a new backend tool for CAi.

## Overall Architecture: Sandbox Execution Mechanism

The full tool invocation chain is as follows:

```text
Agent (template_tools.py)
    │  POST /run/{tool}/{action}  params: {...}
    ▼
FastAPI (app.py)
    │  Create job directory and write params.json
    │  Background task: job_manager.run_job(job_id, tool, action)
    ▼
JobManager (job_manager.py)
    │  conda run -n <env> python <script.py>
    │  cwd = workspace/jobs/<job_id>/         ← isolated sandbox directory
    │  stdin = content of params.json (JSON string)
    ▼
Tool script (run.py)
    │  Read parameters from params.json
    │  Execute computation logic
    │  Write results to result.json
    ▼
JobManager polls result.json / error.json
    ▼
Agent receives the result and returns it to the LLM
```

**Key design: every job has its own isolated sandbox directory**

```text
toolkit/server/workspace/jobs/
└── <uuid>/
    ├── params.json     ← input parameters (written by JobManager)
    ├── result.json     ← computation results (written by run.py)
    ├── error.json      ← error information (written by run.py or JobManager)
    ├── stdout.log      ← standard output (captured by JobManager)
    └── stderr.log      ← standard error (captured by JobManager)
```

The script working directory (`cwd`) is this UUID directory, so you can directly use `open("params.json")` and `open("result.json", "w")`. **No absolute path is needed.**

---

## File Structure: The Three Required Files for a New Tool

```text
toolkit/server/tools/
└── <your_tool_name>/           ← tool directory, and also the tool ID
    ├── config.json             ← required: declares environment, GPU requirement, action mapping
    ├── run.py                  ← main script (for a single-action tool)
    └── <dependencies_or_models>/
```

---

## Step 1: Write `config.json`

### Simplest case: single action, no GPU required

```json
{
  "name": "scscore",
  "conda_env": "scscore",
  "gpu": false
}
```

- `name`: tool name, should match the directory name
- `conda_env`: the conda environment used to run the script
- `gpu`: whether the tool should request a GPU (`false` = CPU-only tool)

If the `actions` field is omitted, the framework automatically uses `{"default": "run.py"}`. That means `POST /run/scscore/default` will execute `run.py`.

### GPU-required case

```json
{
  "name": "mytool",
  "conda_env": "mytool_env",
  "gpu": true
}
```

`gpu_manager` will allocate an available GPU, inject it through `CUDA_VISIBLE_DEVICES`, and automatically release it after the task finishes.

### Multi-action case (one tool, multiple scripts)

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

Then the Agent can call `POST /run/reinvent4/sample` and `POST /run/reinvent4/score` separately.

---

## Step 2: Write `run.py` (Full Example with scscore)

`run.py` only needs to follow one rule: **read inputs from `params.json` and write outputs to `result.json`.**

```python
import sys
import json
from pathlib import Path

def main():
    # ✅ 1. Read parameters (fixed pattern)
    # The script cwd is already the job sandbox directory
    params = json.load(open("params.json"))

    smiles_list = params.get("smiles_list", [])
    model_type  = params.get("model_type", "1024bool")

    # ✅ 2. Execute computation logic
    # ... your core code ...
    result = calculate_scscore(smiles_list, model_type)

    # ✅ 3. Write results (fixed pattern)
    # result must be a JSON-serializable dict
    with open("result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
```

### Recommended `result.json` format

The Agent-side parser (`template_tools.py`) will parse `result.json`, so it is recommended to follow this structure:

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
| `success` | Boolean. If `false`, the Agent treats the tool as failed. |
| `summary` | Statistical summary. The Agent reads this first to avoid transferring too much data. |
| `results` | Full per-item results. |
| `errors` | Partial failure list. Use `null` if all items succeed. |

### Error handling: wrap `main()` with try/except

```python
def main():
    try:
        params = json.load(open("params.json"))
        # ... normal logic ...
        result = {"success": True, "summary": {...}, "results": [...]} 
    except Exception as e:
        # Any exception should still be written into result.json
        result = {"success": False, "error": str(e)}

    with open("result.json", "w") as f:
        json.dump(result, f)

if __name__ == "__main__":
    main()
```

> **Note**: do not rely on `print()` for returning results. stdout will be captured into `stdout.log` by JobManager and will not be read by the Agent. For debugging logs, use `print(..., file=sys.stderr)` so they go to stderr.

---

## Step 3: Register the Agent Tool Function in `template_tools.py`

After the backend script is ready, you still need to add a Python wrapper function in `toolkit/functions/` so the Agent can invoke it.

```python
def calculate_scscore(smiles: str = None, smiles_list: list = None, model_type: str = "1024bool") -> str:
    """
    [Tool description - the LLM reads this to decide when to call the tool]
    Estimate the synthetic accessibility of molecules using the SCScore model.
    ...
    """
    # 1. Validate arguments
    if smiles:
        smiles_list = [smiles]
    if not smiles_list:
        return json.dumps({"success": False, "error": "smiles or smiles_list must be provided"})

    # 2. Call backend
    payload = {"smiles_list": smiles_list, "model_type": model_type}
    result = _call_worker_api("scscore", payload)   # tool name must match tools/ directory name
    #                                    ↑ for multi-action tools: _call_worker_api("reinvent4", payload, action="score")

    # 3. Return formatted string to the Agent
    return json.dumps(result, ensure_ascii=False)
```

`_call_worker_api` automatically polls the job status until `result.json` appears. The default timeout is 5 minutes, and it can be adjusted with `timeout_mins`.

---

## Complete Checklist for Adding a New Tool

```text
□ 1. Create the following files under tools/<your_tool>/:
      - config.json  (set name / conda_env / gpu)
      - run.py       (read params.json → compute → write result.json)

□ 2. Ensure dependencies are installed in the corresponding conda environment
      conda activate <env>
      pip install ...

□ 3. Add the Agent wrapper function in template_tools.py
      - the function name will be recognized by the Agent
      - the docstring is the main signal for the Agent to understand how to use the tool

□ 4. Restart the backend service and confirm the tool is loaded
      curl http://localhost:8001/tools

□ 5. Manually test once with curl
      curl -X POST http://localhost:8001/run/scscore/default \
           -H "Content-Type: application/json" \
           -d '{"smiles_list": ["c1ccccc1"]}'
      # returns {"job_id": "..."}

      curl http://localhost:8001/job/<job_id>
      # returns {"status": "finished", "data": {...}}
```
