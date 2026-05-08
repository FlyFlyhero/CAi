
![CAiCopilot](assets/CAiCopilot.png)

# CAi 分子设计智能助手 CAi Molecule Design Copilot

面向分子生成、性质评估与候选优选的智能 Agent 平台。

[English](./README.md) | [简体中文](./README_zh.md)

## 项目概述

CAi 分子设计智能助手是一个集智能分子设计、基于骨架的分子生成、从头药物发现与多维度性能评估于一体的 Agent 平台。该系统面向药物发现研究人员与计算化学工作者设计，支持通过简单命令快速部署并运行复杂的分子设计工作流，降低环境配置、模型集成与评估流程构建带来的使用门槛。

平台将分子生成、性质评估与候选筛选整合到统一工作流中，使整个实验流程更加易用、可复现且具备较强的科学严谨性，从而帮助研究者节省时间与计算资源，并提升早期药物设计实验的整体效率。

## 为什么选择 CAi？

- **一键式部署**：支持端到端分子设计工作流的快速部署与执行  
- **Web 交互界面**：面向化学研究者的友好交互方式  
- **生成—评估—筛选一体化**：打通完整分子设计流程  
- **灵活工具选择**：既支持单工具独立调用，也支持流程化组合使用  

## 快速开始

### 1. 配置环境变量

在 `CAi/` 目录下创建 `.env` 文件：

```bash
# CAi/.env
LLM_API_KEY=your_api_key_here
LLM_BASE_URL=your_llm_base_url_here
LLM_MODEL=claude-sonnet-4-5-20250929

TOOL_SERVER_HOST=0.0.0.0
TOOL_SERVER_PORT=8001
```

### 2. 安装基础依赖

```bash
conda create -n CAi python==3.11
conda activate CAi
pip install -e .
```

### 3. 安装工具运行环境

每个工具运行在独立的 Conda 环境中，可根据需求按需安装：

```bash
cd CAi/additional_tools/server

# 安装全部工具环境（耗时较长）
bash install_all.sh

# 仅安装部分工具
bash install_all.sh vina scscore toxicity
```

### 4. 启动工具后端服务

在启动服务前，请先从我们的 [Google Drive](https://drive.google.com/drive/folders/1tjYJrMcVJnMopzbTyrf9KskvAxg2Xfin?usp=sharing) 下载工具源码，并将其放置并解压到 `CAi/additional_tools/server/tools/` 目录下。

```bash
# 在 CAi/ 目录下运行
python additional_tools/server/app.py

# 服务启动后监听 http://0.0.0.0:8001
# 可用接口如下：
# GET  /tools               列出所有已加载工具
# POST /run/{tool}/{action} 提交工具任务
# GET  /job/{job_id}        查询任务状态
```

### 5. 启动 Agent 交互界面

```bash
# 在 CAi_copilot/ 目录下运行
python CAi/main.py
```

如需更换大模型后端，请修改 `.env` 文件中的相关配置。

## 示例 Prompt

### 基于骨架的类似物生成

```text
给定青霉素母核骨架
CC1(C)S[C@@H]2(NC(=O)*)C(=O)N2[C@H]1C(=O)O，
使用 DrugEx3、Reinvent 4、LibINVENT 和 RNN-based Constrained Scaffold Generation 生成 10 个基于骨架的小分子类似物，并按照 SC score 进行排序。
```

### 基于靶点的从头分子设计

```text
以 BamA 作为目标蛋白，使用 7NRE.pdbqt 作为目标结构文件，结合位点中心坐标为 [33.489, 8.39, 4.238]，调用 RXNFlow 和 Reinvent 4 生成候选小分子，并按照 Vina score 进行排序。
```

### 分子性质评估

```text
对生成的小分子计算 toxicity 和 MIC，用于评估候选分子的安全性与活性，并筛除表现较差的分子。
```

## 项目结构

```text
CAi_copilot/
├── CAi/
│   ├── config.py                        # 全局配置
│   ├── .env                             # 本地环境变量
│   ├── main.py                          # Agent 启动入口
│   ├── additional_tools/
│   │   ├── __init__.py
│   │   ├── template_tools.py            # Agent 可调用的工具函数
│   │   └── server/
│   │       ├── app.py                   # 工具执行后端（FastAPI）
│   │       ├── job_manager.py           # 任务沙盒管理
│   │       ├── install_all.sh           # 一键安装所有工具环境
│   │       └── tools/                   # 各工具目录（config.json + run.py）
│   └── CAi_agent/
│       ├── agent.py                     # A1pro Agent
│       ├── ui.py                        # Gradio 界面
│       └── skills/                      # Agent 技能描述文件
└── base_CAi/                            # 基础 Agent 框架
```

## 工具工作流

工具调用链路如下：

```text
Agent 启动（template_tools.py）
    │  POST /run/{tool}/{action}
    ▼
工具后端运行 - FastAPI (app.py) → JobManager
    │  conda run -n <env> python run.py
    │  cwd = workspace/jobs/<uuid>/
    ▼
工具执行（run.py）→ result.json
    ▼
Agent 接收结果并继续后续流程
```

## 工具说明
| 功能类型     | 工具                                        | 函数                      | 详细说明                                                                                             |
| -------- | ----------------------------------------- | --------------------------------------- | -------------------------------------------------------------------------------------------------- |
| 骨架约束分子生成 | RNN-based Constrained Scaffold Generation | `generate_scaffold_analogs` | 输入骨架结构，修改部分可以包括R-groups或者linking，在保留核心骨架的前提下生成结构类似的小分子。适用于先导化合物扩展、母核优化和定向类似物探索。 |
| 骨架约束分子生成 | LibINVENT                                 | `generate_libinvent_decorations`            | 以骨架为中心生成可修饰的分子库，尤其适合围绕固定母核开展系统化 R-group 扩展和可控分子设计，并支持反应类型约束以提升分子合成可行性。|
| 骨架约束分子生成 | Reinvent 4                                | `generate_molecules_reinvent4_libinvent`    | 更进一步，在多目标打分函数引导下，基于骨架生成具备反应约束的化学分子库。|
| 从头分子设计   | RXNFlow                                   | `run_rxnflow_design()`                  | 不依赖固定骨架，基于目标蛋白、靶点信息或指定化学空间进行从头设计的小分子生成，适用于靶点导向药物设计与全新候选分子发现。                                   |
| 从头分子设计   | Reinvent 4                                | `generate_molecules_reinvent4_denovo` | 在多目标打分函数引导下，实现多目标驱动的分子生成。 |            
| 从头分子生成   | Reinvent 4                                | `generate_molecules_reinvent4_mol2mol` |接收完整分子输入并以该分子为条件，在多目标优化驱动下生成结构相似的候选分子，实现局部优化。       |
| 逆向合成评估   | SC Score                                  | `calculate_scscore`                  | 对生成分子的结构可合成性进行评估，衡量其与已知合成模式的一致性和潜在可行性，可用于候选分子的初步可合成性筛选。                                      |
| 亲和力性能评估  | Vina Score                                | `perform_molecular_docking_vina`                | 需要蛋白质和小分子文件作为输入，计算 docking score，用于预测分子与目标蛋白之间的结合亲和力，支持靶点导向候选分子的筛选与排序。|                       
| ADMET性能评估  | Toxicity Prediction                       | `predict_molecule_toxicity`                    | 使用Toxcast肝细胞毒性数据对模型微调后，用于预测分子的肝细胞毒性反应风险，为早期药物筛选提供安全性参考，并且支持子结构的Shapley value 可视化分析，展示不同子结构对毒性的贡献程度。 |                                      
| 抑菌浓度性能评估  | MIC Prediction                            | `predict_antibacterial_pmic`                         | 基于化学性质预测模型使用ChEMBL中的所有包括MIC数据的分子训练，并预测分子的最低抑菌浓度（MIC），并辅助抗菌药物设计任务中的分子排序与筛选。                                 |

---

## 工具扩展

如需添加自定义工具，可按照以下流程扩展。每个工具通常包含三个步骤：

### 1. 创建工具目录 `<your_tool>/`

```text
additional_tools/server/tools/<your_tool>/
├── config.json    # Conda 环境配置与运行设置
└── run.py         # 读取 params.json 并输出 result.json
```

`config.json` 模板示例：

```json
{
  "name": "mytool",
  "conda_env": "mytool_env",
  "gpu": false
}
```

### 2. 编写 `run.py`

```python
import json

def main():
    params = json.load(open("params.json"))
    # 在这里实现你需要的功能逻辑
    result = {"success": True, "summary": {...}, "results": [...]}
    with open("result.json", "w") as f:
        json.dump(result, f)

if __name__ == "__main__":
    main()
```

### 3. 在 `template_tools.py` 中注册 Agent 工具函数

```python
def my_tool(smiles: str) -> str:
    """
    工具描述
    （请在此处详细描述该工具的调用时机与功能）
    """
    payload = {"smiles": smiles}
    result = _call_worker_api("mytool", payload)
    return json.dumps(result, ensure_ascii=False)
```

更详细的开发说明请参考 [CAi/start.md](CAi/start.md)。

## 贡献

CAi Molecule Design Copilot 构建了一套统一的分子生成、性质评估与候选优选的 Agent 化工作流。通过将基于骨架的分子设计、从头分子生成、可合成性评估、毒性预测、抗菌活性预测以及基于 docking 的亲和力估计整合到同一系统中，平台有效降低了传统分子设计流程中工具链分散、流程割裂和使用复杂的问题，使高级分子设计流程能够更便捷地服务于药物发现研究人员。

当前，该平台已经可用于基于骨架的类似物生成、面向靶点的从头分子设计以及多目标候选分子筛选。借助 Web 交互界面与自然语言指令方式，CAi 使研究者能够以更高效率、更强可复现性和更灵活的方式开展分子设计实验。

## 引用

如果你在研究工作或相关流程中使用了本项目，请按以下方式引用：

```bibtex
@misc{cai_molecule_design_copilot_2026,
  author       = {Datalab},
  title        = {CAi Molecule Design Copilot},
  year         = {2026},
  month        = apr,
  publisher    = {GitHub},
  note         = {An agentic platform for molecular generation, evaluation, and candidate selection}
}
```
