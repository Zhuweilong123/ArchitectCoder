const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');

const toolbar = fs.readFileSync(require.resolve('../src/components/Toolbar/Toolbar.tsx'), 'utf8');
const parsed = ts.createSourceFile('Toolbar.tsx', toolbar, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
function action(name, context) {
  let initializer;
  function visit(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(parsed) === name) initializer = node.initializer;
    ts.forEachChild(node, visit);
  }
  visit(parsed);
  const compiled = ts.transpileModule(`exports.action = ${initializer.getText(parsed)};`, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  const exports = {};
  vm.runInNewContext(compiled, { exports, ...context });
  return exports.action;
}

function saveContext(projectRoot, currentWorkspacePath) {
  const result = { root: null };
  const project = { name: 'engine', revision: 1 };
  const context = {
    projectRoot, currentWorkspacePath, currentWorkspaceSafe: true,
    currentFilepath: 'D:/workspace/design/uml/engine.umlproj',
    umlReviewBlocksSave: false, currentFileSafe: { current: true },
    useDiagramStore: { getState: () => ({ getProjectSnapshot: () => project }) },
    saveProject: async () => ({ filepath: 'D:/workspace/design/uml/engine.umlproj', filename: 'engine.umlproj' }),
    markSaved() {}, setCurrentFilepath() {},
    setCurrentWorkspacePath: path => { result.root = path; },
    pathDirName: path => path.slice(0, path.lastIndexOf('/')),
    message: { success() {}, warning() {} }, showProjectSaveError: error => { throw error; },
    project, saveFilename: 'engine', setSaving() {}, setSaveAsVisible() {},
    normalizePath: path => path, fileStem: path => path.replace(/\.umlproj$/, ''),
  };
  return { context, result };
}

for (const name of ['handleSave', 'handleSaveAs']) {
  test(`${name} keeps the selected project root when saving a nested design`, async () => {
    const { context, result } = saveContext('D:/workspace', 'D:/workspace/design/uml');
    await action(name, context)();
    assert.equal(result.root, 'D:/workspace');
  });
  test(`${name} preserves an existing workspace without an explicit project root`, async () => {
    const { context, result } = saveContext('', 'D:/workspace');
    await action(name, context)();
    assert.equal(result.root, 'D:/workspace');
  });
}

test('restart restores the persisted project root, independently of the design file', () => {
  const source = fs.readFileSync(require.resolve('../src/stores/diagramStore.ts'), 'utf8');
  const parsed = ts.createSourceFile('diagramStore.ts', source, ts.ScriptTarget.Latest, true);
  let initializer;
  function visit(node) {
    if (!initializer && ts.isPropertyAssignment(node) && node.name.getText(parsed) === 'currentWorkspacePath') initializer = node.initializer;
    ts.forEachChild(node, visit);
  }
  visit(parsed);
  const code = ts.transpileModule('exports.root = ' + initializer.getText(parsed) + ';', {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
  }).outputText;
  for (const value of ['D:/workspace', '', 'project']) {
    const exports = {};
    vm.runInNewContext(code, { exports, localStorage: { getItem: () => value } });
    assert.equal(exports.root, value === 'D:/workspace' ? value : null);
  }
});
