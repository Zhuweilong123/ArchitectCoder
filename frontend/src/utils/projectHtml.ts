import type { Project } from '../types/uml';

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[char]!);
}

/** A self-contained read-only viewer. SVGs are images, never executable markup. */
export function createProjectHtml(project: Project, images: string[], language: string): string {
  if (!project.diagrams.length || images.length !== project.diagrams.length) {
    throw new Error('Every project diagram must have an exported SVG');
  }
  const en = language === 'en';
  const labels = en ? {
    directory: 'Diagrams', fit: 'Fit to window', reset: 'Actual size',
    help: 'Drag to pan · Scroll to zoom', offline: 'Offline design viewer',
    class: 'Class diagram', sequence: 'Sequence diagram', component: 'Component diagram',
    zoomIn: 'Zoom in', zoomOut: 'Zoom out', error: 'Unable to display this diagram',
  } : {
    directory: '图目录', fit: '适应窗口', reset: '原始大小',
    help: '拖动平移 · 滚轮缩放', offline: '离线设计阅读器',
    class: '类图', sequence: '时序图', component: '组件图',
    zoomIn: '放大', zoomOut: '缩小', error: '无法显示此图',
  };
  const entries = project.diagrams.map((diagram, index) => ({
    name: diagram.name,
    type: labels[diagram.diagram_type as 'class' | 'sequence' | 'component'] || diagram.diagram_type || labels.class,
    src: 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(images[index]),
  }));
  // Prevent names containing HTML/script delimiters from escaping the JSON block.
  const data = JSON.stringify(entries).replace(/</g, '\\u003c').replace(/>/g, '\\u003e').replace(/&/g, '\\u0026');
  const directory = entries.map((entry, index) => `<button type="button" class="entry" data-index="${index}" aria-pressed="false"><span>${escapeHtml(entry.name)}</span><small>${escapeHtml(entry.type)}</small></button>`).join('');
  return `<!doctype html>
<html lang="${en ? 'en' : 'zh-CN'}">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'; base-uri 'none'; form-action 'none'">
<title>${escapeHtml(project.name)} — UML</title>
<style>
*{box-sizing:border-box}body{margin:0;font:14px system-ui,-apple-system,'Segoe UI',sans-serif;color:#243248;background:#f3f6fa}
.app{height:100vh;height:100dvh;display:grid;grid-template-columns:260px minmax(0,1fr)}
aside{background:white;border-right:1px solid #dae2ed;overflow:auto;padding:24px 16px}h1{font-size:20px;overflow-wrap:anywhere;margin:0 0 8px}.subtitle,small{color:#64748b}h2{font-size:12px;color:#64748b;margin:30px 10px 12px}
button{font:inherit;cursor:pointer;border:1px solid #d5deeb;border-radius:8px;background:white;color:inherit;padding:8px 12px}button:hover{background:#edf3fc}button:focus-visible{outline:2px solid #2563eb;outline-offset:2px}
.entry{display:block;text-align:left;width:100%;margin:6px 0;padding:12px;border-color:transparent}.entry span{display:block;overflow-wrap:anywhere}.entry small{display:block;margin-top:5px}.entry[aria-pressed=true]{background:#eaf1ff;border-color:#b9cff8;color:#1d4ed8}
main{min-width:0;display:flex;flex-direction:column}header{display:flex;align-items:center;gap:16px;flex-wrap:wrap;padding:16px 24px;background:white;border-bottom:1px solid #dae2ed}.heading{flex:1;min-width:120px}.heading strong{display:block;overflow-wrap:anywhere}.heading small{display:block;margin-top:4px}.controls{display:flex;align-items:center;gap:6px;flex-wrap:wrap}output{min-width:64px;text-align:center;font-variant-numeric:tabular-nums}
#viewport{flex:1;position:relative;overflow:hidden;cursor:grab;touch-action:none;min-height:0;background-image:radial-gradient(#cdd7e5 1px,transparent 1px);background-size:20px 20px}#viewport.dragging{cursor:grabbing}#diagram{position:absolute;left:0;top:0;transform-origin:0 0;max-width:none;user-select:none;pointer-events:none}#error{position:absolute;inset:0;align-content:center;text-align:center;color:#b91c1c}
footer{background:white;border-top:1px solid #dae2ed;padding:10px 24px;color:#64748b;font-size:12px}
@media(max-width:700px){.app{grid-template-columns:1fr;grid-template-rows:auto minmax(0,1fr)}aside{max-height:180px;padding:12px;border-right:0;border-bottom:1px solid #dae2ed}aside h1{font-size:16px}aside h2,.subtitle{display:none}nav{display:flex;gap:6px;overflow-x:auto}.entry{min-width:140px;width:auto;flex-shrink:0;padding:8px}header{padding:12px}footer{padding:8px 12px}}
</style></head>
<body><div class="app"><aside><h1>${escapeHtml(project.name)}</h1><div class="subtitle">${labels.offline}</div><h2>${labels.directory} · ${entries.length}</h2><nav aria-label="${labels.directory}">${directory}</nav></aside>
<main><header><div class="heading"><strong id="name"></strong><small id="type"></small></div><div class="controls"><button id="out" aria-label="${labels.zoomOut}" title="${labels.zoomOut}">−</button><output id="zoom" aria-live="polite">100%</output><button id="in" aria-label="${labels.zoomIn}" title="${labels.zoomIn}">+</button><button id="fit">${labels.fit}</button><button id="reset">${labels.reset}</button></div></header>
<div id="viewport" tabindex="0" aria-label="${labels.help}"><img id="diagram" draggable="false" alt=""><div id="error" hidden>${labels.error}</div></div><footer>${labels.help}</footer></main></div>
<script type="application/json" id="data">${data}</script>
<script>
(() => {
  const entries = JSON.parse(document.getElementById('data').textContent);
  const viewport = document.getElementById('viewport');
  const img = document.getElementById('diagram');
  const buttons = Array.from(document.querySelectorAll('.entry'));
  let scale = 1, x = 0, y = 0, loaded = false, drag = null;
  const render = () => {
    img.style.transform = 'translate(' + x + 'px,' + y + 'px) scale(' + scale + ')';
    document.getElementById('zoom').textContent = Math.round(scale * 100) + '%';
  };
  const fit = () => {
    if (!loaded) return;
    scale = Math.min((Math.max(1, viewport.clientWidth - 48)) / img.naturalWidth, (Math.max(1, viewport.clientHeight - 48)) / img.naturalHeight, 1);
    x = (viewport.clientWidth - img.naturalWidth * scale) / 2;
    y = (viewport.clientHeight - img.naturalHeight * scale) / 2;
    render();
  };
  const zoom = (factor, px = viewport.clientWidth / 2, py = viewport.clientHeight / 2) => {
    if (!loaded) return;
    const next = Math.max(Math.min(scale, 0.01), Math.min(20, scale * factor));
    x = px - (px - x) * next / scale;
    y = py - (py - y) * next / scale;
    scale = next;
    render();
  };
  const select = (index) => {
    loaded = false; drag = null; viewport.classList.remove('dragging'); img.hidden = true;
    document.getElementById('error').hidden = true;
    buttons.forEach((button, i) => button.setAttribute('aria-pressed', String(i === index)));
    document.getElementById('name').textContent = entries[index].name;
    document.getElementById('type').textContent = entries[index].type;
    img.alt = entries[index].name; img.src = entries[index].src;
  };
  img.addEventListener('load', () => { loaded = img.naturalWidth > 0 && img.naturalHeight > 0; img.hidden = !loaded; fit(); });
  img.addEventListener('error', () => { loaded = false; document.getElementById('error').hidden = false; });
  buttons.forEach((button, index) => button.addEventListener('click', () => select(index)));
  document.getElementById('in').addEventListener('click', () => zoom(1.25));
  document.getElementById('out').addEventListener('click', () => zoom(0.8));
  document.getElementById('fit').addEventListener('click', fit);
  document.getElementById('reset').addEventListener('click', () => { if (!loaded) return; scale = 1; x = (viewport.clientWidth - img.naturalWidth) / 2; y = (viewport.clientHeight - img.naturalHeight) / 2; render(); });
  viewport.addEventListener('wheel', (event) => { event.preventDefault(); const rect = viewport.getBoundingClientRect(); zoom(Math.exp(-Math.max(-100, Math.min(100, event.deltaY)) * 0.002), event.clientX - rect.left, event.clientY - rect.top); }, { passive: false });
  viewport.addEventListener('pointerdown', (event) => { if (!loaded || event.button !== 0 || drag) return; viewport.setPointerCapture(event.pointerId); drag = { id: event.pointerId, px: event.clientX, py: event.clientY, x, y }; viewport.classList.add('dragging'); });
  viewport.addEventListener('pointermove', (event) => { if (!drag || drag.id !== event.pointerId) return; x = drag.x + event.clientX - drag.px; y = drag.y + event.clientY - drag.py; render(); });
  const stopDrag = () => { drag = null; viewport.classList.remove('dragging'); };
  viewport.addEventListener('pointerup', stopDrag); viewport.addEventListener('pointercancel', stopDrag); viewport.addEventListener('lostpointercapture', stopDrag);
  viewport.addEventListener('keydown', (event) => {
    if (event.key === '+' || event.key === '=') zoom(1.25);
    else if (event.key === '-') zoom(0.8);
    else if (event.key === '0') fit();
    else return;
    event.preventDefault();
  });
  new ResizeObserver(fit).observe(viewport);
  select(${Math.max(0, Math.min(project.active_diagram_index, entries.length - 1))});
})();
</script></body></html>`;
}
