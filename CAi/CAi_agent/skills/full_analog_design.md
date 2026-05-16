# Skill: Mol2Mol Analog Generation with Similarity and Diversity Control

## Purpose
Use this skill when the user provides a complete molecule and wants to generate structurally similar analog molecules while preserving a reasonable level of similarity to the input molecule and maintaining sufficient diversity across the generated set.

This workflow is designed for analog generation based on a full reference molecule using `generate_molecules_reinvent4_mol2mol`, followed by similarity filtering, diversity control, and ranking.

## When to use
Use this skill when:
- the user provides a complete molecule SMILES,
- the user wants to generate analogs rather than de novo molecules,
- the task is local optimization, lead expansion, or molecule-centered analog exploration,
- similarity to the original molecule should be preserved,
- and the final analog set should not be overly redundant.

## Core workflow
1. Use `generate_molecules_reinvent4_mol2mol` to generate candidate analog molecules from the input complete molecule.
2. Compute Tanimoto similarity between each generated molecule and the input reference molecule using RDKit fingerprints.
3. Remove molecules whose similarity to the input molecule is below the required threshold.
4. Evaluate pairwise similarity among the remaining generated molecules to estimate diversity.
5. Remove redundant molecules if the generated set is too homogeneous, so that the final analog list preserves sufficient diversity.
6. By default, compute `calculate_scscore` for the filtered molecules.
7. Rank the final candidates by SC score unless the user explicitly requests other evaluation metrics or ranking rules.
8. If the user requests additional metrics, compute them after similarity and diversity filtering.

## Default behavior
- Default generation tool: `generate_molecules_reinvent4_mol2mol`
- Default similarity metric: Tanimoto similarity based on RDKit fingerprints
- Default filtering rule: keep molecules above the minimum similarity threshold to the input molecule
- Default diversity rule: remove highly redundant molecules if overall diversity is too low
- Default ranking metric: `calculate_scscore`
- Default output: filtered and ranked analog molecules

## Similarity control
- Always compute similarity between each generated molecule and the input reference molecule.
- The generated analogs should remain reasonably similar to the starting molecule.
- If the user does not specify a similarity threshold, use a default threshold appropriate for analog generation.
- Molecules below the threshold should be excluded from the final result.

## Diversity control
- Similarity to the input molecule should not come at the cost of generating near-duplicate candidates.
- Estimate the diversity of the generated set using pairwise molecular similarity.
- If multiple generated molecules are too similar to each other, keep only a representative subset.
- The final returned set should preserve both relevance to the input molecule and diversity across candidates.

## Default ranking rule
- By default, compute `calculate_scscore` for the final candidate set.
- Rank candidates by SC score in ascending order, where lower scores indicate easier synthesis.
- If the user does not ask for other metrics, SC score is the default ranking criterion.

## Optional additional evaluation
If the user explicitly requests additional screening criteria, compute them after similarity and diversity filtering. Common optional metrics include:
- `predict_molecule_toxicity` for hepatotoxicity risk
- `predict_antibacterial_pmic` for antibacterial activity
- binding-affinity evaluation with Vina, if the user provides a target protein structure file and the corresponding binding pocket center
- other user-requested metrics supported by the system

For affinity evaluation, do not run docking unless the user provides both:
- a valid target protein structure file
- the binding pocket center coordinates for that target

If either of these is missing, skip affinity evaluation and continue with the other requested metrics.

## Ranking rule
- If the user specifies ranking criteria, follow the requested metrics and ranking order.
- If the user does not specify any extra criteria, rank by SC score only.
- If multiple metrics are requested, preserve the user’s ranking priority.
- Similarity and diversity filtering should always happen before final ranking.

## Output expectations
The final result should include:
- generated analog molecules
- similarity score to the input molecule for each candidate
- diversity-filtered candidate set
- SC score by default
- optional extra evaluation metrics if requested
- ranked analog list
- summary of filtering and ranking steps

## Notes
- This skill is intended for full-molecule analog generation, not scaffold-based generation.
- The input molecule should be a complete valid SMILES suitable for `generate_molecules_reinvent4_mol2mol`.
- Similarity filtering and diversity control are mandatory parts of this workflow.
- Additional evaluation metrics are optional and should only be computed when requested or clearly required by the task.