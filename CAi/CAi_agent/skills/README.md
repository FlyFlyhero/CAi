# Skill Document Format

Skills are Standard Operating Procedures (SOPs) for recurring drug discovery tasks. Each skill is a Markdown file in the `skills/` directory.

## File Structure

```markdown
# Skill Title (plain text, no "Skill: " prefix)

## Description
One or two paragraphs describing what this skill does and when to use it.
This text appears in the agent's prompt as the skill summary.

## Metadata

**Category**: Category name (e.g., Generative Chemistry, Structure-Based Design)
**Required Tools**: tool_name_1, tool_name_2, ...
**Difficulty**: Easy | Medium | Hard | Expert
**Use Cases**: Use case 1, Use case 2, Use case 3

---

## Workflow / Core Workflow
Step-by-step instructions the agent should follow...

## When to use
Conditions that trigger this skill...

## Notes
Additional guidance, caveats, edge cases...
```

## Required Sections

### `# Title` (H1)
The skill's display name. Keep it concise and descriptive. Do NOT prefix with "Skill: ".

### `## Description`
A brief description (1-2 paragraphs) of what the skill accomplishes. This is what the agent sees in its prompt when deciding which skill to load. Make it actionable and specific.

### `## Metadata`
Structured metadata used by the skill loader:

| Field | Description |
|---|---|
| `**Category**` | Broad classification (Generative Chemistry, Structure-Based Design, Structure Retrieval, Post-Processing, Molecular Analysis) |
| `**Required Tools**` | Comma-separated tool names this skill depends on |
| `**Difficulty**` | One of: Easy, Medium, Hard, Expert |
| `**Use Cases**` | Comma-separated scenarios where this skill applies |

### Body Sections (after `---`)
The rest of the document contains the actual workflow instructions. Use clear H2/H3 headings. Common sections include:

- **When to use** — triggers and conditions
- **Core workflow** — numbered step-by-step instructions
- **Default behavior** — what to do when the user doesn't specify
- **Output expectations** — what the final result should contain
- **Notes** — caveats, edge cases, limitations

## Naming Convention

File names use `snake_case.md` and serve as the skill ID. Examples:
- `full_analog_design.md` → skill ID: `full_analog_design`
- `id_protein_search.md` → skill ID: `id_protein_search`

## How Skills Are Loaded

The `SkillLoader` class (`loader.py`) scans `*.md` files in the skills directory. For each file:
1. Extracts the title from the first H1 line
2. Extracts the description from the `## Description` section
3. Parses structured fields from the `## Metadata` section
4. Stores the full content for retrieval via `get_skill_content('<skill_id>')`

Skills are displayed in the agent prompt by `SkillsSection`, which shows the skill ID, name, description, and use cases.
