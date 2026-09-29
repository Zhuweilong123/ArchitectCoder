# Project-owned memory and knowledge graph

New projects are saved under `project/<name>_<timestamp>/design/`. A project
with a conventional `design/` directory keeps its persistent Agent state in
the sibling `.architectcoder/` directory:

```text
project/<project>/
  design/<project>.umlproj
  src/
  test/
  .architectcoder/
    project.json
    memories.db
    knowledge_graph.db
```

`project.json` contains a stable project ID. The local memory and knowledge
graph providers use the project file path to locate the project root and its
database. A standalone project file in the shared `project/` directory uses
`project/.architectcoder/<file-stem>/` to avoid mixing unrelated projects.
Explicit database path overrides remain available for deployments that manage
storage themselves.

Existing `temp/data/memories.db` and `temp/data/knowledge_graph.db` are left
untouched. New default reads and writes use only project-owned databases; no
automatic migration or implicit merge is performed. Runtime records such as
traces, run state, audits, evaluations, and candidate artifacts remain under
`temp/`, configured by `runtime_dir`. The old `temp/uml_files` directory is no
longer used as a project or runtime path.

Back up `.architectcoder/` together with the project's design, source and
tests. The knowledge graph can be rebuilt, while project memory cannot be
recovered from source files alone.
