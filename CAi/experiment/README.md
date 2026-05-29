# CAi Experiment Runner

批量运行 Agent 在数据集上的评估实验。支持顺序执行和多进程并行，自动隔离 REPL kernel，结果按时间戳分目录存放。

## 快速开始

```bash
# 1. 准备数据集（JSON 格式，详见下方「数据格式」）
echo '[{"id":"q1","prompt":"计算阿司匹林的分子量"}]' > my_dataset.json

# 2. 运行实验
python run_experiment_test.py --dataset my_dataset.json --name mol_weight_test

# 3. 查看结果
cat experiments/20260528_143022_mol_weight_test/summary.txt
```

## 数据格式

支持 **JSON**、**JSONL**、**CSV** 三种格式。

### JSON（推荐）

一个 JSON 数组，每个对象代表一条任务：

```json
[
  {
    "id": "task1",
    "prompt": "计算阿司匹林 (CC(=O)Oc1ccccc1C(=O)O) 的分子量",
    "category": "property_calculation",
    "difficulty": "easy"
  },
  {
    "id": "task2",
    "prompt": "用 DrugEx 生成 5 个类药分子",
    "category": "molecule_generation",
    "expected_output": "5 valid SMILES"
  }
]
```

- `id`：任务唯一标识（可选，不填则自动生成 `None`）
- `prompt`：**必填**，Agent 收到的指令
- `expected_output`：预期输出（可选，用于 scorer 对比）
- **其他任意字段**：自动收集到 `metadata` 中，可在结果 CSV 里看到

### JSONL

每行一个 JSON 对象：

```jsonl
{"id":"t1","prompt":"计算苯的 LogP","source":"bench_v1"}
{"id":"t2","prompt":"预测青霉素的毒性","source":"bench_v1"}
```

### CSV

列名对应字段名，空白值视为 `None`：

```csv
id,prompt,category,difficulty
t1,计算水的分子量,basic,easy
t2,用 DeepChem MolGAN 生成 10 个分子,generation,medium
```

> CSV 中的 JSON 值会自动解析（如 `history` 列写 `'[{"role":"user"}]'` 可正常读取）。

### 自定义字段映射

如果数据文件的字段名不是 `prompt` / `id`，用 `--prompt-field` 和 `--id-field` 指定：

```bash
python run_experiment_test.py \
  --dataset data.csv \
  --prompt-field question \
  --id-field qid
```

## 命令行参数

### 基础参数

| 参数 | 说明 | 默认值 |
|---|---|---|
| `--dataset` | 数据集文件路径 | `experiment_test_tasks.json` |
| `--name` | 实验名称（用于目录命名） | 数据集文件名 |
| `--output-dir` | 结果根目录 | `experiments/` |

### Agent 配置

| 参数 | 说明 | 默认值 |
|---|---|---|
| `--model` | LLM 模型名 | `.env` 中的 `LLM_MODEL` |
| `--source` | LLM 来源（Custom/Anthropic/OpenAI 等） | `.env` 中的 `LLM_SOURCE` |
| `--base-url` | LLM API 地址 | `.env` 中的 `LLM_BASE_URL` |
| `--no-tools` | 禁用工具加载 | 默认加载 |
| `--no-skills` | 禁用技能加载 | 默认加载 |
| `--utilities` | 启用 utility 加载 | 默认禁用 |

### 执行参数

| 参数 | 说明 | 默认值 |
|---|---|---|
| `--workers` | 并行 worker 数量 | `1`（顺序） |
| `--timeout` | 单条任务超时（秒） | `300` |
| `--prompt-field` | prompt 字段名 | `prompt` |
| `--id-field` | id 字段名 | `id` |

## 输出目录结构

每次运行在 `experiments/` 下创建带时间戳的目录：

```
experiments/
├── 20260528_143022_smoke_test/
│   ├── config.json        # 实验参数快照（模型、数据集、worker数等）
│   ├── results.json       # 完整报告（summary + 每条结果的详细内容）
│   ├── results.csv        # 扁平表格（方便 Excel/pandas 分析）
│   └── summary.txt        # 可读文本摘要
└── 20260529_090000_bench_v2/
    ├── config.json
    ├── results.json
    ├── results.csv
    └── summary.txt
```

### `config.json` 示例

```json
{
  "experiment_name": "smoke_test",
  "timestamp": "2026-05-28T14:30:22",
  "dataset": {
    "file": "experiment_test_tasks.json",
    "size": 4,
    "prompt_field": "prompt",
    "id_field": "id"
  },
  "agent": {
    "model": "qwen3.6-max-preview",
    "source": "Custom",
    "base_url": "http://35.220.164.252:3888/v1/",
    "auto_load_tools": true,
    "auto_load_skills": true,
    "auto_load_utilities": false
  },
  "execution": {
    "workers": 1,
    "per_item_timeout_seconds": 300
  },
  "result": {
    "total": 4,
    "total_wall_time_seconds": 151.8
  }
}
```

### `results.csv` 列说明

```
item_id, prompt, final_response, status, error_message,
wall_time_seconds, code_executions, match_score,
meta_<任意 metadata 字段>...
```

- `status`：`success` / `error` / `timeout`
- `code_executions`：该任务执行了多少次代码块
- `match_score`：自定义 scorer 的评分（见下方「自定义 Scorer」）
- `meta_*`：数据集中非标准字段自动变成 metadata 列

## 并行执行

`--workers` 控制并发方式：

| 值 | 模式 | 说明 |
|---|---|---|
| `1` | 顺序 | 当前进程内逐个执行，适合调试 |
| `>1` | 多进程 | `multiprocessing.Pool`（spawn 模式），每个子进程独立 REPL kernel |

```bash
# 4 个 worker 并行跑 20 条任务
python run_experiment_test.py --dataset bench_20.json --workers 4 --name parallel_run
```

> 注意：多进程模式下每个 worker 会独立启动 Jupyter kernel，内存占用约为 `workers × 单 worker`。

## Python API

除了在命令行调用，也可以直接在 Python 脚本中使用：

```python
from CAi.experiment import run_experiment, load_dataset, DatasetItem

# 方式 1：从文件加载
dataset = load_dataset("benchmark.csv", prompt_field="question", id_field="id")

# 方式 2：手动构建
dataset = [
    DatasetItem(id="q1", prompt="计算阿司匹林的分子量"),
    DatasetItem(id="q2", prompt="用 DrugEx 生成 5 个类药分子"),
]

report = run_experiment(
    dataset,
    max_workers=4,
    agent_args={
        "llm": "qwen3.6-max-preview",
        "source": "Custom",
        "base_url": "http://35.220.164.252:3888/v1/",
        "api_key": "sk-xxx",
        "auto_load_tools": True,
        "auto_load_skills": True,
        "auto_load_utilities": False,
    },
    per_item_timeout_seconds=300,
    on_progress=lambda done, total, r: print(f"[{done}/{total}] {r.item_id}: {r.status}"),
)

print(f"{report.successes}/{report.total} success, avg {report.avg_wall_time:.1f}s")
```

### 自定义 Scorer

传入 `scorer` 回调函数，为每条结果计算匹配分数（存入 `match_score` 字段）：

```python
def exact_match(result):
    if result.expected_output and result.final_response:
        return 1.0 if result.expected_output.lower() in result.final_response.lower() else 0.0
    return None

report = run_experiment(dataset, scorer=exact_match, ...)
```

## 结果分析

拿到 `results.json` 后，可以用 pandas 快速分析：

```python
import json
import pandas as pd

with open("experiments/20260528_143022_smoke_test/results.json") as f:
    data = json.load(f)

# 摘要统计
print(f"成功率: {data['summary']['successes']}/{data['summary']['total']}")
print(f"平均耗时: {data['summary']['avg_wall_time']:.1f}s")

# 转为 DataFrame 分析
df = pd.DataFrame(data["results"])
print(df.groupby("status").size())
print(df[["item_id", "wall_time_seconds", "code_executions"]].describe())
```

或直接读 CSV：

```python
df = pd.read_csv("experiments/20260528_143022_smoke_test/results.csv")
print(df.head())
```

## 注意事项

1. **LLM 配置**：默认读取 `CAi/.env` 文件，也可用 `--model` / `--base-url` 等覆盖
2. **Tool Server**：需要 Tool Server 已启动（`TOOL_SERVER_HOST` 和 `TOOL_SERVER_PORT` 在 `.env` 配置）
3. **内存**：每个 worker 独立启动 Jupyter kernel，`--workers 4` 大约需要额外 ~2GB 内存
4. **超时**：`--timeout` 是单条任务的硬超时（通过 SIGALRM 实现），不是总超时
5. **并发安全**：使用 `spawn` 上下文创建子进程，确保每个 worker 有独立的 REPL kernel 和 builtins，不会互相干扰
