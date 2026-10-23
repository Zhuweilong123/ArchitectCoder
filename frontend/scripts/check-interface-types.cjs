// Enforce structured types at application boundaries; tsc checks implicit any.
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const files = [
  'src/components/DiffViewer/DiffViewer.tsx', 'src/components/AgentChat/AgentChat.tsx',
  'src/components/Toolbar/Toolbar.tsx', 'src/components/Canvas/UMLEditor.tsx',
  'src/services/api.ts', 'src/services/agentChat.ts', 'src/services/apiErrors.ts',
  'src/services/designElementHandler.ts', 'src/types/designEvents.ts',
  'src/types/evaluation.ts', 'src/types/json.ts', 'src/types/trace.ts',
  'src/utils/traceNormalization.ts', 'src/components/TraceViewer/TraceViewer.tsx',
  'src/components/TraceViewer/traceNavigation.ts', 'src/components/TraceViewer/traceTimeline.ts',
];
let failed = false;
for (const file of files) {
  const source = ts.createSourceFile(file, fs.readFileSync(path.join(__dirname, '..', file), 'utf8'), ts.ScriptTarget.Latest, true);
  function visit(node) {
    if (node.kind === ts.SyntaxKind.AnyKeyword) {
      const location = source.getLineAndCharacterOfPosition(node.getStart(source));
      console.error(file + ':' + (location.line + 1) + ': explicit any is forbidden at application boundaries');
      failed = true;
    }
    ts.forEachChild(node, visit);
  }
  visit(source);
}
if (failed) process.exitCode = 1;
else console.log('Interface types checked: ' + files.length + ' files');
