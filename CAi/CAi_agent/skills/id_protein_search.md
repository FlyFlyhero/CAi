# PDB Structure Retrieval by ID

## Description
Use this skill when the user wants to work with a target protein structure but has not provided a local protein file. This workflow supports automatic retrieval of a protein structure file only when the user provides a valid PDB ID.

## Metadata

**Category**: Structure Retrieval
**Required Tools**: fetch_pdb_structure
**Difficulty**: Easy
**Use Cases**: Target preparation for docking, Protein structure retrieval, Structure-based workflow setup

---

## When to use
Use this skill when:
- the user does not provide a local protein structure file,
- and the user provides a PDB ID,
- and the downstream workflow requires a protein structure file for target-aware generation, docking, or structure-based evaluation.

## When not to use
Do not use this skill when:
- the user has already provided a valid local protein structure file,
- the user provides only a protein or target name without a PDB ID,
- the task does not require a protein structure file.

## Core rule
If the user does not provide a local protein structure file, attempt automatic download only when the user provides a valid PDB ID.
Do not attempt structure retrieval from a protein name alone.
If the user provides only a target name, ask for the PDB ID instead of the protein name.

## Core workflow
1. Check whether the user has already provided a local protein structure file.
2. If a local protein file is already available, do not run this skill.
3. If no local protein file is provided, check whether the user provides a valid PDB ID.
4. If a valid PDB ID is available, download the corresponding protein structure file from the PDB source using the fixed download rule.
5. Save the downloaded file to the local working directory.
6. Return the saved local file path for downstream workflows.
7. If the user provides only a protein or target name, do not try to search by name; instead, ask the user to provide the PDB ID.

## Default behavior
- Prefer a user-provided local protein file over downloading a new one
- Only support direct retrieval by PDB ID
- Save the downloaded structure as a local file for later reuse
- Return the local file path as the main output

## Output expectations
The final result should include:
- the PDB ID used for retrieval
- the local saved file path
- the download status
- a short summary indicating whether retrieval succeeded or failed

## Failure handling
- If the PDB ID is missing, ask the user to provide it
- If the PDB ID is invalid or not found, return a clear retrieval failure message
- If the download fails because of network or server issues, return the error and stop the retrieval workflow
- Do not continue with downstream structure-based workflows unless a valid local protein file is available

## Notes
- This skill is designed for direct PDB ID-based structure retrieval only
- This skill does not perform web search by protein name
- This skill should be used as a preparation step for structure-based generation, docking, or target-aware evaluation
