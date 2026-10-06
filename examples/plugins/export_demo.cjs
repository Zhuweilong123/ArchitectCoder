// Render public samples with the production offline viewer and graph renderer.
// Install frontend dependencies first. No backend or browser session is needed.
const fs = require('node:fs');
const path = require('node:path');
const repo = path.resolve(__dirname, '../..');
const ts = require(path.join(repo, 'frontend/node_modules/typescript'));
require.extensions['.ts'] = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText, filename);
const base = path.join(repo, 'frontend/src/components/PluginArchitecture');
const { createPluginArchitectureHtml } = require(path.join(base, 'pluginArchitectureHtml.ts'));
const { buildPluginGraph, wrapGraphLabel } = require(path.join(base, 'pluginGraph.ts'));
const { renderGraphSvg } = require(path.join(base, 'graphSvg.ts'));
const { edgePath } = require(path.join(base, 'graphPresentation.ts'));
const input = process.argv[2] ? path.resolve(process.argv[2]) : path.join(repo, 'docs/media/plugin-demo/plugin-plan.json');
const output = path.dirname(input);
const plan = JSON.parse(fs.readFileSync(input, 'utf8'));
if (plan.schema_version !== 1 || !plan.plugins.some(plugin => plugin.name === 'task_notes')) {
  throw new Error('Generate the task_notes demo plan with export_demo_plan.py first.');
}
for (const language of ['zh', 'en']) {
  const html = createPluginArchitectureHtml(plan, { language });
  fs.writeFileSync(path.join(output, `plugin-architecture-${language}.html`), html);
  const data = JSON.parse(html.match(/<script type="application\/json" id="data">([\s\S]*?)<\/script>/)[1]);
  const graph = buildPluginGraph(plan, 'organization', 'task_notes', { expandedPlugins: ['task_notes'] });
  const svg = renderGraphSvg(graph, 'organization', data.labels, edgePath, wrapGraphLabel);
  const styles = '<style>text{font-family:system-ui,"Segoe UI",sans-serif}.node-title{font-size:12px;font-weight:600;fill:#263449}.node-subtitle{font-size:11px;fill:#66768b}.column{font-size:14px;font-weight:600;fill:#5b6d85}</style>';
  const preview = svg.replace('<svg ', `<svg width="${graph.width}" height="${graph.height}" `)
    .replace('<defs>', `${styles}<rect width="100%" height="100%" fill="#f4f7fb"/><defs>`);
  fs.writeFileSync(path.join(output, `task-notes-${language}.svg`), preview);
  console.log(`Generated ${language} offline viewer and task_notes preview in ${output}`);
}
