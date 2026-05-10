![CAiCopilot](assets/CAiCopilot.png)

# CAi 分子设计智能助手

面向分子生成、性质评估与候选优选的智能 Agent 平台。

[English](./README.md) | [简体中文](./README_zh.md)

## 项目概述

CAi 是一个面向药物发现工作流的 AI Agent 平台，将轻量级 LangGraph 执行引擎与分子生成、对接、毒性预测、可合成性评估等领域工具相结合。

**核心设计原则：**
- 混合交互模式 — Agent 可以直接回答问题、执行代码，或在同一回复中两者兼顾
- 精简系统提示词（约 1,700 tokens）— 只包含你实际使用的工具
- Skills（SOP）— 预验证的工作流按需加载，不占用每次对话的上下文
- 清晰的双层架构 — `BaseAgent` 负责执行，`A1pro` 添加领域工具

## 架构

```
BaseAgent  （核心：LangGraph + LLM + REPL）
    └── A1pro  （领域：工具 + 技能 + 系统提示词）
              └── Web UI  （FastAPI + 静态前端）
```

详细架构说明见 [docs/architecture.md](docs/architecture.md)。

## 快速开始

### 1. 配置环境变量

在 `CAi/` 目录下创建 `.env` 文件：

```bash
LLM_MODEL=claude-sonnet-4-5-20250929
LLM_BASE_URL=http://your-endpoint/v1/
LLM_API_KEY=your_key_here

TOOL_SERVER_HOST=0.0.0.0
TOOL_SERVER_PORT=8001
```

### 2. 安装基础依赖

```bash
conda create -n CAi python=3.11
conda activate CAi
pip install -e .
```

### 3. 安装工具运行环境

每个工具运行在独立的 Conda 环境中：

```bash
cd CAi/toolkit/server

# 安装全部工具环境
bash install_all.sh

# 仅安装部分工具
bash install_all.sh vina scscore toxicity
```

启动前请先从 [Google Drive](https://drive.google.com/drive/folders/1tjYJrMcVJnMopzbTyrf9KskvAxg2Xfin?usp=sharing) 下载工具源码，解压到 `CAi/toolkit/server/tools/`。

### 4. 启动工具后端

```bash
# 在仓库根目录运行
python -m CAi.toolkit.server.app
# 监听 http://0.0.0.0:8001，访问 /health 可自检
```

### 5. 启动 Agent

```bash
python CAi/main.py
# Web UI 地址：http://localhost:7001
```

## 交互模式

Agent 支持三种回复模式：

| 模式 | 示例 |
|---|---|
| 直接回答 | "什么是 LogP？" → 直接输出文字解释 |
| 代码执行 | "计算阿司匹林的 SCScore" → 执行代码并展示结果 |
| 混合模式 | "分析这个分子" → 先说明计划，再执行分析 |

不再强制每次回复都包含 `<solution>` 标签。

## 示例 Prompt

**基于骨架的类似物生成**
```
给定青霉素母核骨架 CC1CSC2(NC(=O)*)C(=O)N2[C@H]1C(=O)O，
使用 LibINVENT 和 RNN-based Constrained Scaffold Generation 分别生成 10 个类似物，
并按 SC score 排序。
```

**基于靶点的从头分子设计**
```
以 HIV-1 protease 为目标蛋白，使用 1HVR.pdb 作为结构文件，
结合位点中心坐标为 [15.2, 23.5, 6.8]。
调用 RxnFlow 和 REINVENT4 生成候选分子，并按 Vina score 排名。
```

**分子性质评估**
```
对上面生成的分子预测毒性和 MIC 值，
总结哪些候选分子最有潜力。
```

## 项目结构

```
CAi_copilot/
├── CAi/
│   ├── config.py                    # 全局配置
│   ├── .env                         # 本地环境变量
│   ├── main.py                      # 启动入口
│   ├── CAi_agent/
│   │   ├── base.py                  # BaseAgent — LangGraph + LLM + REPL
│   │   ├── agent.py                 # A1pro — 编排层
│   │   ├── prompt/                  # PromptBuilder + 各 Section
│   │   ├── tools/                   # ToolRegistry + Scanner + ReplBridge
│   │   └── skills/                  # SOP Markdown 文件
│   ├── toolkit/                     # 面向 Agent 的药物发现工具集
│   │   ├── client.py                # 工具服务端 HTTP 客户端
│   │   ├── skill_helpers.py         # get_skill_content / list_available_skills
│   │   ├── functions/
│   │   │   ├── generation.py        # 6 个分子生成工具
│   │   │   └── evaluation.py        # 4 个分子评估工具
│   │   └── server/                  # 工具执行后端（FastAPI）
│   └── web_ui/
│       ├── backend/
│       │   ├── app.py               # FastAPI 聊天 + 文件接口
│       │   ├── conversation_store.py
│       │   └── pdf_export.py        # 对话 → Markdown → PDF
│       └── frontend/                # 静态 HTML/JS/CSS
├── base_CAi/                        # LLM 工厂 + 代码执行工具
└── docs/
    └── architecture.md              # 详细架构文档
```

## 工具调用链路

```
Agent（CAi/toolkit/functions/*.py）
    │  POST /run/{tool}/{action}
    ▼
工具服务（CAi/toolkit/server/app.py）→ JobManager
    │  conda run -n <env> python run.py
    │  cwd = workspace/jobs/<uuid>/
    ▼
工具执行（run.py）→ result.json
    ▼
Agent 接收结果
```

## 可用工具

| 功能类型 | 工具 | 函数 |
|---|---|---|
| 骨架约束生成 | RNN-based | `generate_scaffold_analogs` |
| 骨架约束生成 | LibINVENT | `generate_libinvent_decorations` |
| 骨架约束生成 | REINVENT4 LibInvent | `generate_molecules_reinvent4_libinvent` |
| 从头分子设计 | RXNFlow | `generate_molecules_for_pocket` |
| 从头分子设计 | REINVENT4 de novo | `generate_molecules_reinvent4_denovo` |
| 类似物生成 | REINVENT4 Mol2Mol | `generate_molecules_reinvent4_mol2mol` |
| 可合成性评估 | SC Score | `calculate_scscore` |
| 分子对接 | AutoDock Vina | `perform_molecular_docking_vina` |
| 毒性预测 | HepG2 预测 | `predict_molecule_toxicity` |
| 抗菌活性 | MIC 预测 | `predict_antibacterial_pmic` |

## 扩展 CAi

### 添加工具

1. 在 `CAi/toolkit/functions/generation.py` 或 `evaluation.py` 中添加函数
2. 在 `CAi/toolkit/__init__.py` 和 `functions/__init__.py` 中导出
3. 重启 Agent 或调用 `agent.reload_tools()`

```python
def my_tool(smiles: str) -> str:
    """一句话描述，会显示在 Agent 的工具目录中。"""
    ...
```

### 添加技能（SOP）

在 `CAi/CAi_agent/skills/` 下创建 Markdown 文件，文件名即为技能 ID。
格式说明见 [docs/architecture.md](docs/architecture.md#adding-skills)。

完整开发指南见 [CAi/start.md](CAi/start.md)。

## 引用

```bibtex
@misc{cai_molecule_design_copilot_2026,
  author    = {Datalab},
  title     = {CAi Molecule Design Copilot},
  year      = {2026},
  month     = {May},
  publisher = {GitHub},
  note      = {An agentic platform for molecular generation, evaluation, and candidate selection}
}
```
