# Unique Molecule Generation with Deduplication and Refill

## Description
Use this skill when a molecule generation workflow must return a required number of unique candidate molecules. This workflow removes duplicated SMILES structures from generated results and, if the number of unique molecules is still below the user-requested target, continues generating additional candidates until the target count is reached or a retry limit is hit.

## Metadata

**Category**: Post-Processing
**Required Tools**: rdkit
**Difficulty**: Easy
**Use Cases**: Molecule deduplication, Unique candidate generation, Generation result cleanup

---

## When to use
Use this skill when:
- the user requests a specific number of generated molecules,
- the generation tool may return duplicated SMILES,
- the workflow requires a final unique molecule set,
- and the system should automatically refill the missing number after deduplication.

## Supported generation tools
This skill can be applied after any of the following generation functions:
- `generate_scaffold_analogs`
- `generate_libinvent_decorations`
- `generate_molecules_for_pocket`
- `generate_molecules_reinvent4_denovo`
- `generate_molecules_reinvent4_libinvent`
- `generate_molecules_reinvent4_mol2mol`

## Core workflow
1. Run the selected generation tool and collect the generated molecules.
2. Extract the SMILES strings from the returned results.
3. Normalize SMILES if needed and remove duplicated molecular structures.
4. Count the number of unique molecules.
5. If the number of unique molecules is greater than or equal to the user-requested target, stop and return the top required number.
6. If the number of unique molecules is smaller than the user-requested target, calculate how many additional molecules are still needed.
7. Re-run the same generation tool to generate additional candidates.
8. Merge the new results with the existing molecule set and remove duplicates again.
9. Repeat this process until:
   - the required number of unique molecules is reached, or
   - the maximum retry limit is reached.
10. Return the final unique molecule set and a summary of the generation attempts.

## Default behavior
- Always deduplicate generated molecules by SMILES before returning results
- If the user requests a target count, try to satisfy that count with unique molecules only
- If duplicates reduce the final count, automatically generate additional molecules
- Stop retrying when the target count is reached or the retry limit is exceeded

## Deduplication rule
- Treat molecules with identical SMILES as duplicates
- Keep only one copy of each duplicated molecule
- If canonical SMILES normalization is available, use canonicalized SMILES for deduplication
- Deduplication should happen after every generation round, not only once at the end

## Refill rule
- If the user requests `N` molecules, the workflow should aim to return `N` unique molecules
- After each deduplication step, compute the missing number:
  `missing_count = target_count - current_unique_count`
- Generate additional molecules to fill the missing count
- The refill generation may request slightly more molecules than the exact missing count in order to reduce repeated duplication in later rounds
- Continue until target_count is met or retry limit is reached

## Retry rule
- Use a bounded retry strategy
- Do not keep generating indefinitely
- By default, retry at most 3 times unless the workflow explicitly allows more attempts
- If the workflow cannot produce enough unique molecules after repeated attempts, return the largest available unique set and clearly report that the requested count was not fully reached

## Recommended refill strategy
- Do not generate exactly the missing count in every refill round
- Instead, request a slightly larger number of additional molecules than `missing_count`
- This helps compensate for repeated duplicates and increases the chance of reaching the final unique target
- The oversampling factor can be kept small and stable, such as generating a modest surplus beyond the missing count

## Output expectations
The final result should include:
- the requested molecule count
- the final unique molecule count
- the deduplicated molecule list
- the number of generation rounds used
- the number of duplicate molecules removed
- whether the requested count was fully satisfied or only partially satisfied

## Notes
- This skill is a general post-generation control workflow and should not change the core behavior of the underlying generation model
- This skill should reuse the same generation function during refill
- This skill should preserve the original task type, such as scaffold-based generation, de novo generation, pocket-based generation, or mol2mol analog generation
- If the generation tool returns too few valid molecules repeatedly, return partial success rather than failing the entire workflow
