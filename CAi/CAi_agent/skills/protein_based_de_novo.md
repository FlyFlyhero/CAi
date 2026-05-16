# Skill: Target-Based De Novo Molecular Design with Vina Ranking

## Purpose
Use this skill when the user wants to generate novel small molecules for a target protein and evaluate them by binding affinity.  
This workflow is designed for target-aware de novo molecular generation using a protein structure file and a binding pocket definition, followed by Vina-based docking evaluation and optional extra property assessment.

## When to use
Use this skill when:
- the user provides a target protein structure file,
- and provides either a binding pocket center or a valid pocket definition,
- and wants to generate new candidate molecules for that target,
- and expects the generated molecules to be ranked by binding affinity or other requested metrics.

## Core workflow
1. Use the pocket-based generation tool `generate_molecules_for_pocket` to generate candidate molecules from the target protein and pocket definition.
2. Treat the generated molecules as candidate ligands for the same target protein.
3. For each generated candidate, run `perform_molecular_docking_vina` using the same receptor and binding center.
4. Use Vina score as the default ranking metric.
5. If the user explicitly requests additional evaluation criteria, compute the requested metrics after docking.
6. Return the ranked candidates, with docking affinity as the primary result unless the user specifies a different ranking rule.

## Default behavior
- Default generation tool: `generate_molecules_for_pocket`
- Default evaluation tool: `perform_molecular_docking_vina`
- Default ranking rule: sort by `best_docking_score_kcal_mol` in ascending order (more negative is better)
- Default output: top-ranked generated molecules with docking results

## Optional additional evaluation
If the user requests extra evaluation standards, compute them after Vina docking. Common optional metrics include:
- `calculate_scscore` for synthesizability
- `predict_molecule_toxicity` for hepatotoxicity risk
- `predict_antibacterial_pmic` for antibacterial activity

These extra metrics should be added only when the user explicitly asks for them, or when the user clearly requests multi-objective screening.

## Ranking rule
- If the user specifies ranking criteria, follow the user’s requested metrics and ordering logic.
- If the user does not specify any extra criteria, rank by Vina score only.
- If multiple metrics are requested, keep Vina score as the primary affinity indicator unless the user explicitly overrides the ranking priority.

## Output expectations
The final result should include:
- generated candidate molecules
- Vina docking score for each candidate
- optional extra evaluation metrics if requested
- ranked molecule list
- summary of the generation and evaluation workflow

## Notes
- This skill is intended for target-aware de novo design rather than scaffold-based generation.
- This skill assumes the receptor file and pocket definition are already prepared and valid before tool execution.
- Vina evaluation is the core default screening step in this workflow.
- Additional property evaluation is optional and should not be run unless requested by the user or required by the task.