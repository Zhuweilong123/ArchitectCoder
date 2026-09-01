---
name: uml-design-guide
description: Data-model schemas, enum values, naming conventions and cross-diagram consistency rules for authoring UML class / sequence / component diagrams as UML Designer JSON. Use whenever generating or modifying diagram JSON in a .umlproj file, or when a change in one diagram must stay consistent with the others.
---

# UML Design Guide

Reference pack for producing JSON that UML Designer can load directly. Load only
the file you need — each is 4-11 KB.

## Which file to load

| Task | File |
|---|---|
| Class diagram: classes, attributes, methods, relations | `class_diagram_guide.md` |
| Sequence diagram: lifelines, messages, combined fragments | `sequence_diagram_guide.md` |
| Component diagram: components, ports, interfaces, dependencies | `component_diagram_guide.md` |
| Touching more than one diagram, or wiring `component_id` / element refs across them | `cross_diagram_guide.md` |

Each `*_guide.md` covers, in order: data-model JSON schema → full enum lists →
formatting conventions → relation design → design principles → LLM output spec.

## Examples

Each `*_example.md` is the **final chapter of its matching guide** (§6 for class
and component, §7 for sequence), split into its own file. It contains only
complete worked JSON examples — no top-level title, no rules.

Load the guide first for the rules; load the example only when you need a
concrete full-diagram JSON to pattern-match against.

## Rules that apply to every diagram

- IDs follow `<kind>_<timestamp>_<random6>` and must never be purely numeric.
- Emit JSON that matches the schema exactly — do not invent fields or enum values;
  the full enum lists are in §2 of each guide.
- When editing one diagram, check `cross_diagram_guide.md` §4 (常见跨图错误)
  before assuming the change is local.

## Existing-project migration and recovery

- For a request to fully synchronize one existing `.umlproj` with another,
  treat the current valid project as the canonical source. Copy or transform
  that complete project first; do not reconstruct a large design
  diagram-by-diagram unless the request explicitly asks for a partial merge.
- A syntactically valid JSON object is not necessarily a usable UML project.
  A repaired project must contain a non-empty `diagrams` collection whose
  entries follow the relevant diagram schema.
- Do not create helper scripts merely to inspect a UML file. Use targeted
  reads, the design tools, or a direct workspace-local file operation, then
  validate the resulting project.
