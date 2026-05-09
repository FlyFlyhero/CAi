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
Given the penicillin core scaffold with the SMILES string:
`CC1CSC2(NC(=O)*)C(=O)N2[C@H]1C(=O)O`
Generate 10 scaffold-derived small molecule analogues by adopting LibINVENT and RNN-based Constrained Scaffold Generation respectively, then sort the generated analogues according to the SC score.
```
### De Novo Design
```text
Take HIV-1 protease as the target protein, and adopt `1HVR.pdb` as the target structure file. The coordinate of the binding site center is set to [15.2, 23.5, 6.8].
Invoke Rxnflow and Reinvent4 to generate candidate small molecules, and rank all candidates based on Vina score.
```
### Molecule Evaluation
```text
Calculate the toxicity and MIC values of the aforementioned generated small molecules, so as to evaluate their chemical characteristics and potential performance in the drug discovery and druggability development process.
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
| Module Type                       | Tool                                      | Function                                 | Detailed Description                                                                                                                                                                                                                                                                                              |
| --------------------------------- | ----------------------------------------- | ---------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Constrained Scaffold Generation   | RNN-based Constrained Scaffold Generation | `generate_scaffold_analogs`              | Takes a scaffold structure as input and generates structurally similar small molecules while preserving the core scaffold. The modified regions may include R-groups or linkers. It is suitable for lead expansion, scaffold optimization, and targeted analog exploration.                                       |
| Constrained Scaffold Generation   | LibINVENT                                 | `generate_libinvent_decorations`         | Generates modifiable molecular libraries centered on a scaffold. It is especially suitable for systematic R-group expansion and controllable molecular design around a fixed core scaffold, and supports reaction-type constraints to improve synthetic feasibility.                                              |
| Constrained Scaffold Generation   | Reinvent 4                                | `generate_molecules_reinvent4_libinvent` | Further enables the generation of reaction-constrained chemical libraries based on a scaffold under the guidance of multi-objective scoring functions.                                                                                                                                                            |
| De Novo Molecular Design          | RXNFlow                                   | `run_rxnflow_design()`                   | Performs de novo small-molecule generation without relying on a fixed scaffold, based on a target protein, target-related information, or a specified chemical space. It is suitable for target-driven drug design and novel candidate discovery.                                                                 |
| De Novo Molecular Design          | Reinvent 4                                | `generate_molecules_reinvent4_denovo`    | Performs multi-objective driven molecular generation under the guidance of multi-objective scoring functions.                                                                                                                                                                                                     |
| De Novo Molecular Generation      | Reinvent 4                                | `generate_molecules_reinvent4_mol2mol`   | Takes a complete molecule as input and generates structurally similar candidate molecules conditioned on that molecule under multi-objective optimization, enabling local optimization.                                                                                                                           |
| Retrosynthesis Evaluation         | SC Score                                  | `calculate_scscore`                      | Evaluates the structural synthesizability of generated molecules by measuring their consistency with known synthesis patterns and their potential feasibility. It can be used for preliminary synthesizability screening of candidate molecules.                                                                  |
| Binding Affinity Evaluation       | Vina Score                                | `perform_molecular_docking_vina`         | Requires protein and small-molecule files as input and calculates docking scores to predict the binding affinity between a molecule and its target protein, supporting the screening and ranking of target-oriented candidate molecules.                                                                          |
| ADMET Evaluation                  | Toxicity Prediction                       | `predict_molecule_toxicity`              | Predicts the hepatotoxicity risk of molecules using a model fine-tuned on the Toxcast hepatotoxicity dataset, providing safety references for early-stage drug screening. It also supports Shapley value visualization at the substructure level to show the contribution of different substructures to toxicity. |
| Antibacterial Activity Evaluation | MIC Prediction                            | `predict_antibacterial_pmic`             | Predicts the minimum inhibitory concentration (MIC) of molecules using a chemical property prediction model trained on all molecules with MIC-related data from ChEMBL, and supports the ranking and screening of candidate molecules in antimicrobial drug design tasks.                                         |

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