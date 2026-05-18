# Property-Constrained De Novo Molecule Generation

## Description
Use this skill when the user does not provide a scaffold, reference molecule, or target-guided design input, but wants to generate novel molecules that satisfy one or more desired molecular property criteria.

This workflow is designed for unconstrained de novo molecular generation using `generate_molecules_reinvent4_denovo`, followed by property-based filtering, optional model-based evaluation, and final ranking.

## Metadata

**Category**: Generative Chemistry
**Required Tools**: generate_molecules_reinvent4_denovo, rdkit, calculate_scscore, predict_molecule_toxicity, predict_antibacterial_pmic
**Difficulty**: Medium
**Use Cases**: Property-driven de novo design, Molecular property optimization, Unconstrained molecular generation

---

## When to use
Use this skill when:
- the user does not provide any starting molecule or scaffold,
- the user wants brand-new candidate molecules,
- the user specifies desired molecular properties or target ranges,
- the task is property-oriented de novo generation rather than scaffold optimization or target-based generation.

## Supported property types
This skill supports two categories of criteria:

### 1. Basic molecular descriptors
These can be directly computed from SMILES using RDKit or equivalent chemistry utilities:
- Molecular_Weight
- nHA
- nHD
- TPSA
- nRot
- nStereo
- Fsp3
- logP
- MCE-18

### 2. Model-based evaluation metrics
These require downstream evaluation tools after generation:
- `calculate_scscore` for synthesizability
- `predict_molecule_toxicity` for hepatotoxicity risk
- `predict_antibacterial_pmic` for antibacterial activity
- `perform_molecular_docking_vina` for binding affinity, only if the user provides both a target protein structure file and the corresponding binding pocket center

## Core workflow
1. Use `generate_molecules_reinvent4_denovo` to generate a candidate pool of de novo molecules.
2. Compute the requested basic molecular descriptors for each generated molecule.
3. Filter out molecules that do not satisfy the user-specified descriptor constraints or target ranges.
4. If the user requests additional model-based metrics, evaluate the filtered molecules with the requested tools.
5. Rank the remaining molecules according to the user's requested priority.
6. If the user does not specify a ranking rule, rank the filtered molecules by default using SC score if synthesizability is requested or available; otherwise rank by closeness to the requested descriptor targets.
7. Return the top-ranked molecules with the corresponding descriptor values and optional evaluation results.

## Default behavior
- Default generation tool: `generate_molecules_reinvent4_denovo`
- Default mode: generate first, then filter and rank
- Do not assume any scaffold or target information
- If the user only specifies descriptor constraints, perform descriptor-based filtering only
- If the user requests model-based evaluation, perform those evaluations only after descriptor filtering

## Descriptor filtering rule
- Treat user-specified descriptor values as hard constraints if they are expressed as thresholds or ranges
- Treat target values as soft preferences if the user expresses them as approximate goals
- Keep molecules that best satisfy the requested descriptor profile
- Exclude molecules that clearly violate the user's required ranges

## Default ranking rule
- If the user specifies ranking priorities, follow the user's requested order
- If the user requests SC score, rank by `calculate_scscore` in ascending order
- If the user does not request SC score and only provides descriptor goals, rank by overall closeness to the requested descriptor profile
- If the user requests docking-based affinity evaluation, only perform it when both the target protein file and pocket center are available

## Optional additional evaluation
If the user explicitly requests additional screening criteria, compute them after descriptor filtering. Common optional metrics include:
- `calculate_scscore` for synthesizability
- `predict_molecule_toxicity` for hepatotoxicity risk
- `predict_antibacterial_pmic` for antibacterial activity
- `perform_molecular_docking_vina` for binding affinity, only if the user provides both a target protein structure file and the corresponding binding pocket center

If either the target protein file or the pocket center is missing, skip affinity evaluation and continue with the remaining requested metrics.

## Output expectations
The final result should include:
- generated de novo molecules
- computed molecular descriptors for each retained candidate
- optional additional evaluation metrics if requested
- filtered molecule set
- ranked final candidates
- summary of descriptor filtering and ranking behavior

## Notes
- This skill is intended for unconstrained de novo generation, not scaffold-based generation or full-molecule analog generation.
- Descriptor calculation should happen before optional model-based evaluation.
- If no valid molecules satisfy all requested hard constraints, return the closest candidates instead of failing completely, unless the user explicitly requests strict filtering only.
- Affinity evaluation is optional and requires additional target information from the user.
