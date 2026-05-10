# 后端工具开发指南

> 以 `scscore` 为例，完整介绍如何为 CAi 编写并接入一个新的后端工具。

## 整体架构：沙盒执行

Agent 调用工具的完整链路：

```text
Agent wrapper  (CAi/toolkit/functions/*.py)
    │  client.run_tool(<tool>, payload, action=...)
    │  POST /run/{tool}/{action}     body: params dict
    ▼
工具服务  (CAi/toolkit/server/app.py  — FastAPI)
    │  JobManager 创建沙盒目录、写入 params.json
    │  后台任务：job_manager.run_job(job_id, tool, action)
    ▼
JobManager  (CAi/toolkit/server/job_manager.py)
    │  conda run -n <env> python <script.py>
    │  cwd  = workspace/jobs/<job_id>/     ← 隔离的沙盒目录
    │  stdin = params.json 的 JSON 字符串
    ▼
工具脚本  (你的 run.py)
    │  从 params.json 读取参数
    │  执行计算
    │  将结果写入 result.json
    ▼
JobManager 轮询 result.json / error.json
    ▼
client.run_tool 把解析后的结果返回给 wrapper
    ▼
Agent 拿到 wrapper 的返回值
```

**关键设计：每个 Job 都有独立的沙盒目录。**

```text
CAi/toolkit/server/workspace/jobs/
└── <uuid>/
    ├── params.json     ← 输入参数（JobManager 写入）
    ├── result.json     ← 计算结果（run.py 写入）
    ├── error.json      ← 错误信息（run.py 或 JobManager 写入）
    ├── stdout.log      ← 捕获的 stdout
    └── stderr.log      ← 捕获的 stderr
```

脚本的 cwd 就是这个 uuid 目录，所以直接 `open("params.json")` 和
`open("result.json", "w")` 即可，**不需要绝对路径**。

---

## 文件结构：每个工具三个必需文件

```text
CAi/toolkit/server/tools/
└── <your_tool_name>/           ← 工具目录，名字即工具 ID
    ├── config.json             ← 必需：声明 env、GPU、action 映射
    ├── run.py                  ← 主脚本（单 action 工具）
    └── <依赖代码或模型>/
```

---

## 第一步：编写 `config.json`

### 最简单的情况（单 action，不需要 GPU）

```json
{
  "name": "scscore",
  "conda_env": "scscore",
  "gpu": false
}
```

- `name`：工具名，与目录名保持一致。
- `conda_env`：运行脚本所用的 conda 环境名。
- `gpu`：是否向 GPU 队列申请显卡（`false` = CPU 工具）。

当 `actions` 字段缺省时，框架自动使用 `{"default": "run.py"}`，即
`POST /run/scscore/default` 会执行 `run.py`。

### 需要 GPU 的情况

```json
{
  "name": "mytool",
  "conda_env": "mytool_env",
  "gpu": true
}
```

`gpu_manager` 会从可用 GPU 列表中取一张卡，通过 `CUDA_VISIBLE_DEVICES`
注入环境变量，任务结束后自动归还。

### 多 action 的情况（一个工具、多个脚本）

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

这样 wrapper 可以分别调用 `POST /run/reinvent4/sample` 和
`POST /run/reinvent4/score`。

---

## 第二步：编写 `run.py`（以 scscore 为完整示例）

`run.py` 只需遵守一个约定：**从 `params.json` 读参数，把结果写到 `result.json`**。

```python
import json


def main():
    # 1. 读取参数。cwd 已经是沙盒目录。
    params = json.load(open("params.json"))
    smiles_list = params.get("smiles_list", [])
    model_type  = params.get("model_type", "1024bool")

    # 2. 执行计算。
    result = calculate_scscore(smiles_list, model_type)

    # 3. 写入结果。必须可 JSON 序列化。
    with open("result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
```

### 推荐的 `result.json` 格式

toolkit 的 HTTP 客户端（`CAi/toolkit/client.py`）会解析 `result.json`，
推荐使用下面的结构：

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

| 字段 | 说明 |
|---|---|
| `success` | 布尔值。`false` 时客户端会把结果视为失败。 |
| `summary` | 汇总统计 —— wrapper 通常优先读这里。 |
| `results` | 完整的逐条结果。 |
| `errors` | 部分失败的错误列表；全部成功时为 `null`。 |

### 错误处理：用 try/except 包裹 main()

```python
def main():
    try:
        params = json.load(open("params.json"))
        # ... 正常逻辑 ...
        result = {"success": True, "summary": {...}, "results": [...]}
    except Exception as e:
        # 依然写 result.json，让客户端报出干净的错误。
        result = {"success": False, "error": str(e)}

    with open("result.json", "w") as f:
        json.dump(result, f)


if __name__ == "__main__":
    main()
```

> **注意**：不要用 `print()` 输出结果。stdout 会被捕获到 `stdout.log`，
> Agent 根本读不到。调试信息用 `print(..., file=sys.stderr)` 写到 stderr。

---

## 第三步：注册 Agent wrapper

后端脚本写好之后，需要加一个 Python wrapper 函数让 Agent 能调用。
Wrapper 位于：

- `CAi/toolkit/functions/generation.py` —— 分子生成类工具
- `CAi/toolkit/functions/evaluation.py` —— 分子评估类工具

每个 wrapper 都用 `CAi.toolkit.client.run_tool`：

```python
# CAi/toolkit/functions/evaluation.py (scscore 示例)

import json

from ..client import run_tool


def calculate_scscore(
    smiles: str | None = None,
    smiles_list: list[str] | None = None,
    model_type: str = "1024bool",
) -> str:
    """
    [工具描述 —— 大模型会读这里决定何时调用]
    Estimate synthetic accessibility via the SCScore model.
    ...
    """
    # 1. 参数校验
    if smiles:
        smiles_list = [smiles]
    if not smiles_list:
        return json.dumps(
            {"success": False, "error": "smiles or smiles_list must be provided"}
        )

    # 2. 调用后端（工具名必须与 tools/ 目录名一致）
    payload = {"smiles_list": smiles_list, "model_type": model_type}
    result = run_tool("scscore", payload)
    # 多 action 工具，传 action=...:
    #   run_tool("reinvent4", payload, action="score", timeout_mins=15)

    # 3. 给 Agent 返回一个 JSON 字符串
    return json.dumps(result, ensure_ascii=False)
```

`run_tool` 内部负责提交任务、轮询（指数退避）、超时（默认 5 分钟，可用
`timeout_mins` 覆盖）、以及错误归一化。

### 把 wrapper 暴露给 Agent

需要把函数名添加到两份 `__all__` 列表：

1. `CAi/toolkit/functions/__init__.py`
2. `CAi/toolkit/__init__.py`

这两份导出决定了 Agent 的 `ModuleScanner` 能否扫到你。少一个，工具对
`A1pro` 就是不可见的。

---

## 完整 Checklist

```text
□ 1. 在 CAi/toolkit/server/tools/<your_tool>/ 下创建文件：
      - config.json    （填写 name / conda_env / gpu；多 action 加 actions）
      - run.py         （读 params.json → 计算 → 写 result.json）

□ 2. 在对应 conda 环境中安装依赖
      conda activate <env>
      pip install ...

□ 3. 在下面任一处添加 wrapper 函数：
      - CAi/toolkit/functions/generation.py
      - CAi/toolkit/functions/evaluation.py
      然后在两份 __all__ 里导出：
      - CAi/toolkit/functions/__init__.py
      - CAi/toolkit/__init__.py

□ 4. 重启工具服务
      python -m CAi.toolkit.server.app
      # 启动 banner 会打印已加载的工具 —— 确认你的工具在列表中。

□ 5. 通过 /health 快速自检
      curl http://localhost:8001/health
      # → {"status": "ok", "tools": [..., "your_tool_name", ...], ...}

□ 6. 用 curl 做一次手动冒烟测试
      curl -X POST http://localhost:8001/run/scscore/default \
           -H "Content-Type: application/json" \
           -d '{"smiles_list": ["c1ccccc1"]}'
      # → {"job_id": "<uuid>"}

      curl http://localhost:8001/job/<uuid>
      # → {"status": "finished", "data": {"success": true, ...}}

□ 7. 走 Python 客户端再测一次（与 Agent 走的是同一路径）
      python -c "from CAi.toolkit.client import run_tool; \
                 print(run_tool('scscore', {'smiles_list':['c1ccccc1']}))"

□ 8. 如果 Agent 正在运行，重启 Agent 或热更新工具：
      agent.reload_tools()
```
