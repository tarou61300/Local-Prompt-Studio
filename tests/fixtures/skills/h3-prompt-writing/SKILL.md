# Development test fixture: H3 Prompt Writing Skill

This small fixture exists only for offline automated tests and the mock demo.
Produce one coherent prompt, preserve requested dialogue, actions, and ordering.

## Workflow

1. Identify the input mode from the request: T2VA, I2VA, FL2VA, L2VA, or Ref2VA.
2. Select the matching schema.

## Base Modes

Use `integrated_multimodal_description`, `overall_soundscape`, and
`non_diegetic_music` for T2VA, I2VA, FL2VA, and L2VA.

## Full-Reference Mode

Use `subject_definitions`, `summary`, `retention_analysis`,
`detailed_description`, `overall_soundscape`, and `non_diegetic_music` for Ref2VA.

## Output Rules

Preserve the requested content.

