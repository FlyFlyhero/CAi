![CAiCopilot](assets/CAiCopilot.png)
# CAi Molecule Design Copilot 
An agentic platform for molecular generation, evaluation, and candidate prioritization.
[English](./README.md) | [简体中文](./README_zh.md)
## Overviews
This agent is an integrated platform for intelligent molecular design, scaffold-based generation, de novo drug discovery, and multi-dimensional evaluation. Designed for both drug discovery researchers and computational chemists, it enables you to deploy and run complex molecular design workflows with a single command, removing the typical overhead of environment setup, model integration, and evaluation pipelines. Molecule Agent streamlines the workflow into an accessible, reproducible, and scientifically rigorous tool, saving both time and computational resources. 

## Why CAi?
- One-click deployment for end-to-end molecular design workflows
- Web-based interaction for chemical researchers
- Integrated generation, evaluation, and screening
- Flexible tool selection for standalone or workflow-based use

## Getting started
### 1.Setup

Create a file named `.env` in the directory `CAi/`：

```bash
# CAi/.env
LLM_API_KEY=your_api_key_here
LLM_BASE_URL=your_llm_base_url_here
LLM_MODEL=claude-sonnet-4-5-20250929

TOOL_SERVER_HOST=0.0.0.0
TOOL_SERVER_PORT=8001
```

### 2. Install dependencies

```bash
conda create -n CAi python==3.11
conda activate CAi 
pip install -e .
```

### 3. Install tool environments

Each tool operates within a separate Conda environment.  You can install them as needed:

```bash
cd CAi/additional_tools/server

# Install all tool environments (may take time)
bash install_all.sh

# Install selected tools only
bash install_all.sh vina scscore toxicity
```

### 4. Launch tool backend services
Before starting the service, please download the tool source code from our [Google Drive](https://drive.google.com/drive/folders/1tjYJrMcVJnMopzbTyrf9KskvAxg2Xfin?usp=sharing), place and extract it into the directory CAi/additional_tools/server/tools/.

```bash
# Run inside 'CAi/'
python additional_tools/server/app.py

# After startup, the service listens on http://0.0.0.0:8001. Available ndpoints:
# GET /tools — List all loaded tools
# POST /run/{tool}/{action} — Submit a tool task
# GET /job/{job_id} — Query task status
```

### 5.Start the Agent UI

```bash
# Run in the directory 'CAi_copilot/'
python CAi/main.py
# To modify the LLM backbone model, update env file.
```

## Example 
### Scaffold-Based Analog
```text
Given the penicillin core scaffold
CC1(C)S[C@@H]2(NC(=O)*)C(=O)N2[C@H]1C(=O)O,
generate 10 scaffold-based analogs using DrugEx3, Reinvent 4, LibINVENT, and RNN-based Constrained Scaffold Generation, then rank them by SC score.
```
### De Novo Design
```text
Using BamA as the target protein, with 7NRE.pdbqt as the target structure and [33.489, 8.39, 4.238] as the binding center coordinates, generate candidate small molecules with RXNFlow and Reinvent 4, then rank them by Vina score.
```
### Molecule Evaluation
```text
For the generated molecules, calculate toxicity、MIC to evaluate chemical features, and filter out candidates with poor safety and activity.
```

## File Structure 

```
CAi_copilot/
├── CAi/
│   ├── config.py                        # Global Configuration
│   ├── .env                             # Local Environment Variables
│   ├── main.py                          # Agent Main Function Entry
│   ├── additional_tools/
│   │   ├── __init__.py
│   │   ├── template_tools.py            # Tool Functions Callable by Agent
│   │   └── server/
│   │       ├── app.py                   # Tool Execution Backend (FastAPI)
│   │       ├── job_manager.py           # Job Sandbox Management
│   │       ├── install_all.sh           # One-click Install for All Tools
│   │       └── tools/                   # Each Tool（config.json + run.py）
│   └── CAi_agent/
│       ├── agent.py                     # A1pro Agent 
│       ├── ui.py                        # Gradio UI
│       └── skills/                      # Agent skill descriptions
└── base_CAi/                            # basic tools 
```
## Tool Workflow 

Tool Call Chain:

```
Agent launchs(template_tools.py)
    │  POST /run/{tool}/{action}
    ▼
tool running - FastAPI (app.py)  →  JobManager
    │  conda run -n <env> python run.py
    │  cwd = workspace/jobs/<uuid>/
    ▼
tool call (run.py)  →  result.json
    ▼
Agent receives results
```

## Tool Description

| Module Type                     | Tool                                      | Tool Function Name                      | Detailed Description                                                                                                                                                                                                                                                                                              |
| ------------------------------- | ----------------------------------------- | --------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Constrained Scaffold Generation | RNN-based Constrained Scaffold Generation | `run_constrained_scaffold_generation()` | Takes a predefined molecular scaffold as input and generates structurally related analogs while preserving the core scaffold. This tool is suitable for lead expansion, scaffold optimization, and targeted analog exploration, and can be combined with downstream evaluation modules for ranking and filtering. |
| Constrained Scaffold Generation | Reinvent 4                                | `run_reinvent_scaffold_generation()`    | Performs scaffold-based molecular generation while preserving the core structure and exploring diverse substituent combinations and chemical space. It is well suited for scaffold-based lead optimization and target-oriented molecular design.                                                                  |
| Constrained Scaffold Generation | LibINVENT                                 | `run_libinvent_generation()`            | Generates focused molecular libraries around a given scaffold, especially for systematic R-group expansion and controllable molecular design. This tool is useful for candidate library construction and downstream screening.                                                                                    |
| Constrained Scaffold Generation | DrugEx3                                   | `run_drugex3_scaffold_generation()`     | Applies deep generative modeling to scaffold-conditioned molecular design, enabling diverse candidate generation from a predefined core structure. It is suitable for large-scale analog exploration and optimization.                                                                                            |
| De Novo Molecular Design        | RXNFlow                                   | `run_rxnflow_design()`                  | Performs de novo small-molecule generation without requiring a fixed scaffold, based on a target protein, target-site information, or a specified chemical space. It is useful for target-driven drug discovery and novel candidate generation.                                                                   |
| De Novo Molecular Design        | Reinvent 4                                | `run_reinvent_denovo_design()`          | Uses Reinvent 4 for de novo molecular generation, allowing users to explore candidate molecules that satisfy specific design objectives without relying on a predefined scaffold. It can be integrated with downstream evaluation and screening workflows.                                                        |
| Retrosynthesis Evaluation       | SC Score                                  | `calculate_sc_score()`                  | Evaluates the structural synthesizability of generated molecules by measuring their consistency with known synthesis patterns and their potential feasibility. It can be used for early-stage filtering of candidates.                                                                                            |
| Retrosynthesis Evaluation       | SA Score                                  | `calculate_sa_score()`                  | Estimates the synthesis difficulty and structural complexity of a molecule, helping identify candidates that may be too complex or impractical for real-world synthesis.                                                                                                                                          |
| Multi-dimensional Evaluation    | Vina Score                                | `calculate_vina_score()`                | Computes docking scores based on protein and ligand input files to estimate protein–ligand binding affinity. This tool supports ranking and filtering in target-driven molecular design workflows.                                                                                                                |
| Multi-dimensional Evaluation    | Toxicity Prediction                       | `predict_toxicity()`                    | Predicts hepatotoxicity risk for candidate molecules using a ChemBERTa-based model, providing an early-stage safety assessment for molecular screening.                                                                                                                                                           |
| Multi-dimensional Evaluation    | Toxicity Shapley Visualization            | `visualize_toxicity_shapley()`          | Provides interpretability for toxicity predictions by generating Shapley value visualizations, highlighting which substructures or chemical features contribute most to the predicted toxicity.                                                                                                                   |
| Multi-dimensional Evaluation    | MIC Prediction                            | `predict_mic()`                         | Predicts the minimum inhibitory concentration (MIC) of candidate molecules using a Chemprop-based model, supporting antibacterial activity assessment and candidate prioritization in antimicrobial design tasks.                                                                                                 |

---

## Tool Extention

To add custom tools, follow the guidance below. Each tool requires three steps:

**1. Create Tool Dir `<your_tool>/`**

```
additional_tools/server/tools/<your_tool>/
├── config.json    # conda environment setup and running sources
└── run.py         # load params.json and output result.json
```

```json
# `config.json` template
{
  "name": "mytool",
  "conda_env": "mytool_env",
  "gpu": false
}
```

**2. Prepare the script of `run.py`**

```python
# `run.py` template
import json

def main():
    params = json.load(open("params.json"))
    # add the function you need here
    result = {"success": True, "summary": {...}, "results": [...]}
    with open("result.json", "w") as f:
        json.dump(result, f)

if __name__ == "__main__":
    main()
```

**3. Register Agent tool functions in `template_tools.py`**

```python
# `template_tools.py` template
def my_tool(smiles: str) -> str:
    """
    Tool Description 
    (Please describe the function call process in detail here)
    """
    payload = {"smiles": smiles}
    result = _call_worker_api("mytool", payload)
    return json.dumps(result, ensure_ascii=False)
```

See detailed development guide at [CAi/start.md](CAi/start.md)。


## Contribution

CAi Molecule Design Copilot contributes a unified agentic workflow for molecular generation, evaluation, and candidate selection. By integrating scaffold-based design, de novo generation, synthesizability assessment, toxicity prediction, antibacterial activity prediction, and docking-based evaluation into one system, it reduces the overhead of fragmented molecular design pipelines and makes advanced workflows more accessible to drug discovery researchers.

The platform is currently applied to scaffold-based analog generation, target-aware de novo molecular design, and multi-objective candidate screening. Through its web-based interface and natural-language interaction, CAi enables researchers to perform molecular design experiments more efficiently, reproducibly, and with greater flexibility in tool selection and workflow composition.

## Citation

If you use this project in your research or workflow, please cite it as:

```bibtex
@misc{cai_molecule_design_copilot_2026,
  author       = {Datalab},
  title        = {CAi Molecule Design Copilot},
  year         = {2026},
  month        = Apr,
  publisher    = {Github},
  note         = {An agentic platform for molecular generation, evaluation, and candidate selection}
}