const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const ts = require('typescript');
require.extensions['.ts'] = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText, filename);

const { layoutComponents, getComponentChildTop } = require('../src/utils/componentLayout.ts');
const { routeComponentOuterEdges, componentRoutingObstacles, getComponentDelegationPorts } = require('../src/components/Canvas/core/componentRouting.ts');
const { getObstacleAvoidingEdgeVertices } = require('../src/components/Canvas/core/canvasCommon.ts');

const node = (id, extra = {}) => ({ id, name: id, parent_id: '', x: 0, y: 0,
  width: 200, height: 160, provided_interfaces: [], required_interfaces: [], ...extra });
const edge = (source, target, type = 'dependency') => ({ id: `${source}-${target}`, source, target, type });
const overviewEdges = () => [
  edge('planning', 'sim'), edge('control', 'planning'), edge('control', 'sim'),
  ...['sim', 'planning', 'control', 'routing', 'reference', 'analysis'].map((id) => edge('entry', id)),
  ...['sim', 'planning', 'control', 'routing', 'reference'].map((id) => edge(id, 'shared')),
  edge('reference', 'routing'), edge('reference', 'planning'), edge('analysis', 'control'), edge('analysis', 'sim'),
];
const routePoints = (route) => [route.ports.sourcePoint, ...route.vertices, route.ports.targetPoint];
const segments = (points) => points.slice(1).map((point, i) => [points[i], point]);
function sharedLength(a, b, c, d) {
  const axis = a.y === b.y && a.y === c.y && c.y === d.y ? 'x'
    : a.x === b.x && a.x === c.x && c.x === d.x ? 'y' : null;
  return axis ? Math.max(0, Math.min(Math.max(a[axis], b[axis]), Math.max(c[axis], d[axis]))
    - Math.max(Math.min(a[axis], b[axis]), Math.min(c[axis], d[axis]))) : 0;
}
function crosses(a, b, rect, clearance = 0) {
  return a.y === b.y
    ? a.y > rect.y - clearance && a.y < rect.y + rect.height + clearance
      && Math.min(a.x, b.x) < rect.x + rect.width + clearance && Math.max(a.x, b.x) > rect.x - clearance
    : a.x > rect.x - clearance && a.x < rect.x + rect.width + clearance
      && Math.min(a.y, b.y) < rect.y + rect.height + clearance && Math.max(a.y, b.y) > rect.y - clearance;
}
function verifyRoutes(routes, edges, rects) {
  for (const [id, route] of routes) {
    const relation = edges.find((edge) => edge.id === id);
    const points = routePoints(route);
    for (const [a, b] of segments(points)) {
      assert.ok(a.x === b.x || a.y === b.y, `${id} has a diagonal segment`);
      for (const rect of rects.filter((rect) => rect.id !== relation.source && rect.id !== relation.target)) {
        assert.equal(crosses(a, b, rect, 24), false, `${id} runs too close to ${rect.id}`);
      }
    }
    for (const [otherId, other] of routes) {
      if (id >= otherId) continue;
      for (const [a, b] of segments(points)) for (const [c, d] of segments(routePoints(other))) {
        assert.equal(sharedLength(a, b, c, d), 0, `${id} shares a segment with ${otherId}`);
      }
    }
  }
}

test('overview roles shorten global layering without changing UML relationships', () => {
  // Neutral names exercise structural entry/foundation detection; metrics identify the observer.
  const components = ['sim', 'planning', 'control', 'entry', 'shared', 'routing', 'reference', 'analysis']
    .map((id, index) => node(id, { name: `Module ${index}`,
      provided_interfaces: id === 'analysis' ? ['TrackingMetrics'] : [],
    }));
  const diagram = { components, comp_relations: overviewEdges() };
  const snapshot = JSON.stringify(diagram);
  const result = layoutComponents(diagram);
  const byId = new Map(result.components.map((component) => [component.id, component]));
  assert.equal(JSON.stringify(diagram), snapshot);
  assert.deepEqual(layoutComponents(result), result);
  assert.deepEqual(result.comp_relations, diagram.comp_relations.map((relation) => ({ ...relation, vertices: undefined })));
  assert.equal(byId.get('entry').x, byId.get('analysis').x);
  assert.ok(byId.get('analysis').y > byId.get('entry').y);
  assert.ok(byId.get('entry').x < byId.get('control').x);
  assert.ok(byId.get('shared').y >= byId.get('sim').y + byId.get('sim').height + 100);
  assert.ok(byId.get('control').x < byId.get('planning').x && byId.get('planning').x < byId.get('sim').x);
  assert.equal(new Set(result.components.map((component) => component.x)).size, 4);
});

test('related children retain their own levels and nested containers fit below interface headers', () => {
  const components = [node('parent', { provided_interfaces: ['A'], required_interfaces: ['B', 'C'] }),
    node('consumer', { parent_id: 'parent' }), node('provider', { parent_id: 'parent' }),
    node('nested', { parent_id: 'provider', provided_interfaces: ['D'] }),
    node('leaf', { parent_id: 'nested' }), node('isolated', { parent_id: 'parent' }), node('other')];
  const diagram = { components, comp_relations: [edge('consumer', 'provider'), edge('parent', 'consumer', 'delegation')] };
  const result = layoutComponents(diagram);
  const byId = new Map(result.components.map((component) => [component.id, component]));
  assert.deepEqual(layoutComponents(result), result);
  assert.ok(byId.get('consumer').x < byId.get('provider').x);
  result.components.filter((component) => component.parent_id).forEach((child) => {
    const parent = byId.get(child.parent_id);
    assert.ok(child.y >= parent.y + getComponentChildTop(parent));
    assert.ok(child.x >= parent.x + 20 && child.x + child.width <= parent.x + parent.width - 20);
    assert.ok(child.y + child.height <= parent.y + parent.height - 20);
  });
});

test('long component chains wrap into compact bands and cycles stay in one region', () => {
  const diagram = { components: Array.from({ length: 7 }, (_, i) => node(`n${i}`)),
    comp_relations: Array.from({ length: 6 }, (_, i) => edge(`n${i}`, `n${i + 1}`)) };
  const result = layoutComponents(diagram);
  assert.equal(new Set(result.components.map(({ x }) => x)).size, 3);
  assert.deepEqual(layoutComponents(result), result);
  const cycle = layoutComponents({ components: [node('api'), node('analysis'), node('utility')],
    comp_relations: [edge('api', 'analysis'), edge('analysis', 'utility'), edge('utility', 'api')] });
  assert.equal(new Set(cycle.components.map(({ x }) => x)).size, 1);
});

test('independent component families and isolated nodes keep their local spacing', () => {
  const result = layoutComponents({ components: [node('a', { height: 700 }), node('b'),
    node('c'), node('d'), node('p'), node('q')], comp_relations: [edge('a', 'b'), edge('c', 'd')] });
  const byId = new Map(result.components.map((component) => [component.id, component]));
  assert.equal(byId.get('c').y, byId.get('d').y);
  assert.equal(byId.get('p').y, byId.get('q').y);
  assert.equal(byId.get('q').x - byId.get('p').x, byId.get('p').width + 100);
  assert.equal(byId.get('b').y + byId.get('b').height / 2, byId.get('a').y + byId.get('a').height / 2);
  assert.deepEqual(layoutComponents(result), result);
});

test('component overview routes remain clear and individually traceable through shared corridors', () => {
  const rects = [node('sim', { x: 2182, y: 447, width: 554, height: 454 }),
    node('planning', { x: 1488, y: 223, width: 554, height: 346 }),
    node('control', { x: 794, y: 80, width: 554, height: 632 }),
    node('entry', { x: 100, y: 179, width: 554, height: 544 }),
    node('shared', { x: 2182, y: 1001, width: 554, height: 410 }),
    node('routing', { x: 1488, y: 669, width: 554, height: 456 }),
    node('reference', { x: 794, y: 812, width: 554, height: 456 }),
    node('analysis', { x: 100, y: 823, width: 554, height: 346 })];
  const edges = overviewEdges();
  const routes = routeComponentOuterEdges(edges, rects);
  assert.equal(routes.size, edges.length);
  verifyRoutes(routes, edges, rects);
  assert.deepEqual(routeComponentOuterEdges([...edges].reverse(), rects), routes);
});

test('reciprocal and parallel component dependencies use different paths', () => {
  const rects = [node('a'), node('b', { x: 500 })];
  const edges = [edge('a', 'b'), edge('b', 'a'), { ...edge('a', 'b'), id: 'parallel' }];
  verifyRoutes(routeComponentOuterEdges(edges, rects), edges, rects);
});

test('manual routes are preserved while automatic routes avoid their occupied channels', () => {
  const rects = [node('a'), node('b', { x: 500 }), node('c', { y: 300 })];
  const manual = { ...edge('a', 'b'), vertices: [{ x: 240, y: 80 }, { x: 450, y: 80 }] };
  const edges = [manual, edge('c', 'b')];
  const snapshot = JSON.stringify(edges);
  const routes = routeComponentOuterEdges(edges, rects);
  assert.deepEqual(routes.get(manual.id).vertices, manual.vertices);
  assert.equal(JSON.stringify(edges), snapshot);
  verifyRoutes(routes, edges, rects);
});

test('inner routes can traverse their containing component while avoiding sibling elements', () => {
  const result = layoutComponents({ components: [node('parent'),
    node('a', { parent_id: 'parent' }), node('b', { parent_id: 'parent' }),
    node('c', { parent_id: 'parent' })], comp_relations: [edge('a', 'b'), edge('parent', 'c', 'delegation')] });
  const byId = new Map(result.components.map((component) => [component.id, component]));
  const obstacles = componentRoutingObstacles('a', 'b', result.components, result.components);
  assert.ok(!obstacles.some(({ id }) => id === 'parent'));
  const ports = getComponentDelegationPorts(byId.get('parent'), byId.get('c'), result.components);
  const turns = getObstacleAvoidingEdgeVertices(edge('parent', 'c', 'delegation'), result.comp_relations,
    result.components, 12, { source: ports.source, target: ports.target });
  for (const [a, b] of segments([ports.source, ...turns, ports.target])) {
    for (const rect of result.components.filter(({ id }) => !['parent', 'c'].includes(id))) {
      assert.equal(crosses(a, b, rect, 12), false);
    }
  }
});
