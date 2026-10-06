# task_notes

[中文快速上手](../../../docs/plugin-quickstart.md) · [English quickstart](../../../docs/plugin-quickstart.en.md)

Minimal directory-discovered ArchitectCoder plugin. `describe()` is an on-demand service whose default phase is `prepare`; `observe_start(context)` runs at `run_start` and writes a `plugin_example` event to the current Trace. It creates no files or database resources.

Run from the repository root with backend dependencies installed:

```shell
python backend/plugin_dev.py check examples/plugins/task_notes --instantiate
python backend/plugin_dev.py run examples/plugins/task_notes --method describe
python backend/plugin_dev.py run examples/plugins/task_notes --stage run_start
```

These local checks need no model credentials or running application. To load the example in the application, add `"../examples/plugins"` to `PLUGIN_ROOTS` in `backend/.env`, enable Trace, and restart the backend. The quickstart explains graph navigation and execution history.
