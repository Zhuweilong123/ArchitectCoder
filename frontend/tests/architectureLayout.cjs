const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const ts = require('typescript');

require.extensions['.ts'] = (module, filename) => {
  const source = fs.readFileSync(filename, 'utf8');
  module._compile(ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText, filename);
};

const { layoutClasses, getClassNodeSize } = require('../src/utils/classLayout.ts');
const { layoutComponents, getComponentHeaderHeight } = require('../src/utils/componentLayout.ts');
const { orderArchitectureGraph } = require('../src/utils/architectureGraph.ts');

const cls = (id, x = 0) => ({
  id, name: id, stereotype: 'class', attributes: [], methods: [],
  position: { x, y: 0 }, size: { width: 200, height: 150 }, note: '',
  provided_interfaces: [], required_interfaces: [],
});
const rel = (source, target, type) => ({ source, target, type });

test('class layout groups contracts, implementations and their support', () => {
  const classes = ['facade', 'lateral', 'projected', 'lqr', 'mpc', 'longitudinal', 'pid', 'model', 'qp']
    .map((id, index) => cls(id, index * 230));
  const diagram = { classes, relations: [
    rel('facade', 'lqr', 'aggregation'), rel('facade', 'pid', 'association'),
    rel('projected', 'lateral', 'inheritance'), rel('lqr', 'projected', 'inheritance'),
    rel('mpc', 'projected', 'inheritance'), rel('pid', 'longitudinal', 'inheritance'),
    rel('lqr', 'model', 'dependency'), rel('mpc', 'model', 'dependency'),
    rel('mpc', 'qp', 'dependency'),
  ] };
  const first = layoutClasses(diagram);
  assert.deepEqual([...layoutClasses(diagram)], [...first]);
  const settled = layoutClasses({ ...diagram, classes: classes.map((item) => ({
    ...item, position: first.get(item.id),
  })) });
  assert.deepEqual([...settled], [...first]);
  assert.equal(first.size, classes.length);
  assert.ok(first.get('facade').y < first.get('lateral').y);
  assert.ok(first.get('lateral').y < first.get('projected').y);
  assert.ok(first.get('projected').y < first.get('lqr').y);
  assert.ok(first.get('lqr').y < first.get('model').y);
  assert.ok(first.get('longitudinal').y < first.get('pid').y);
  assert.ok(Math.abs(first.get('lqr').x - first.get('mpc').x) < 300);
  for (let i = 0; i < classes.length; i++) {
    for (let j = i + 1; j < classes.length; j++) {
      const a = first.get(classes[i].id);
      const b = first.get(classes[j].id);
      const sa = getClassNodeSize(classes[i]);
      const sb = getClassNodeSize(classes[j]);
      assert.ok(a.x + sa.width <= b.x || b.x + sb.width <= a.x
        || a.y + sa.height <= b.y || b.y + sb.height <= a.y,
      `${classes[i].id} overlaps ${classes[j].id}`);
    }
  }
});

test('shared layering keeps cycles together', () => {
  const ordered = orderArchitectureGraph(['entry', 'a', 'b', 'exit'], [
    { source: 'entry', target: 'a' }, { source: 'a', target: 'b' },
    { source: 'b', target: 'a' }, { source: 'b', target: 'exit' },
  ], new Map([['entry', 0], ['a', 1], ['b', 2], ['exit', 3]]));
  assert.equal(ordered.levels.get('a'), ordered.levels.get('b'));
  assert.ok(ordered.levels.get('entry') < ordered.levels.get('a'));
  assert.ok(ordered.levels.get('a') < ordered.levels.get('exit'));
});

test('composition and aggregation form local families without absorbing a facade', () => {
  const classes = ['facade', 'abstract', 'concrete', 'map', 'lane', 'node']
    .map((id, index) => cls(id, index * 250));
  const positions = layoutClasses({ classes, relations: [
    rel('concrete', 'abstract', 'inheritance'),
    rel('facade', 'concrete', 'aggregation'),
    rel('map', 'lane', 'composition'), rel('map', 'node', 'composition'),
  ] });
  assert.ok(positions.get('facade').y < positions.get('abstract').y);
  assert.ok(positions.get('abstract').y < positions.get('concrete').y);
  assert.ok(positions.get('map').y < positions.get('lane').y);
  assert.equal(positions.get('lane').y, positions.get('node').y);
  assert.ok(Math.abs(positions.get('lane').x - positions.get('node').x) < 300);
});

test('component layout preserves containment and sorts related children', () => {
  const comp = (id, parent_id = '', x = 0) => ({ id, name: id, parent_id,
    x, y: 0, width: 180, height: 120, provided_interfaces: [], required_interfaces: [],
  });
  const diagram = { components: [comp('parent'), comp('consumer', 'parent', 400),
    comp('provider', 'parent', 0), comp('peer')], comp_relations: [
    rel('consumer', 'provider', 'dependency'), rel('parent', 'peer', 'dependency'),
  ] };
  const result = layoutComponents(diagram);
  assert.ok(result);
  const byId = new Map(result.components.map((component) => [component.id, component]));
  const parent = byId.get('parent');
  const consumer = byId.get('consumer');
  const provider = byId.get('provider');
  assert.ok(parent.x < byId.get('peer').x);
  assert.ok(consumer.x < provider.x);
  for (const child of [consumer, provider]) {
    assert.ok(child.x >= parent.x + 20);
    assert.ok(child.y >= parent.y + getComponentHeaderHeight(parent) + 36);
    assert.ok(child.x + child.width <= parent.x + parent.width);
    assert.ok(child.y + child.height <= parent.y + parent.height);
  }
});

if (process.argv[2]) {
  const project = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  for (const diagram of project.diagrams || []) {
    if (diagram.diagram_type === 'class' && diagram.classes?.length) {
      test(`real class diagram: ${diagram.name}`, () => {
        const positions = layoutClasses(diagram);
        assert.equal(positions.size, diagram.classes.length);
        const settled = layoutClasses({ ...diagram, classes: diagram.classes.map((item) => ({
          ...item, position: positions.get(item.id),
        })) });
        assert.deepEqual([...settled], [...positions]);
        for (let i = 0; i < diagram.classes.length; i++) {
          for (let j = i + 1; j < diagram.classes.length; j++) {
            const a = diagram.classes[i];
            const b = diagram.classes[j];
            const pa = positions.get(a.id);
            const pb = positions.get(b.id);
            const sa = getClassNodeSize(a);
            const sb = getClassNodeSize(b);
            assert.ok(pa.x + sa.width <= pb.x || pb.x + sb.width <= pa.x
              || pa.y + sa.height <= pb.y || pb.y + sb.height <= pa.y,
            `${a.name} overlaps ${b.name}`);
          }
        }
        if (diagram.name === 'Control Module Classes' || diagram.name === 'Routing Module Classes') {
          console.log(`${diagram.name}:`, diagram.classes.map((cls) =>
            `${cls.name}@${Math.round(positions.get(cls.id).x)},${Math.round(positions.get(cls.id).y)}`,
          ).join('  '));
        }
      });
    }
    if (diagram.diagram_type === 'component' && diagram.components?.length > 1) {
      test(`real component diagram: ${diagram.name}`, () => {
        const result = layoutComponents(diagram);
        assert.ok(result);
        assert.equal(result.components.length, diagram.components.length);
        assert.deepEqual(layoutComponents(diagram), result);
        assert.deepEqual(layoutComponents(result), result);
        const byId = new Map(result.components.map((component) => [component.id, component]));
        result.components.forEach((component) => {
          if (!component.parent_id) return;
          const parent = byId.get(component.parent_id);
          if (!parent) return;
          assert.ok(component.x >= parent.x && component.y >= parent.y,
            `${component.name} starts outside ${parent.name}`);
          assert.ok(component.x + component.width <= parent.x + parent.width
            && component.y + component.height <= parent.y + parent.height,
          `${component.name} extends outside ${parent.name}`);
        });
        for (let i = 0; i < result.components.length; i++) {
          for (let j = i + 1; j < result.components.length; j++) {
            const a = result.components[i];
            const b = result.components[j];
            if (a.parent_id === b.id || b.parent_id === a.id) continue;
            if (a.parent_id !== b.parent_id) continue;
            assert.ok(a.x + a.width <= b.x || b.x + b.width <= a.x
              || a.y + a.height <= b.y || b.y + b.height <= a.y,
            `${a.name} overlaps ${b.name}`);
          }
        }
      });
    }
  }
}
