"""Streaming extraction of UML elements from incremental JSON."""

from __future__ import annotations

import json

class JsonElementExtractor:
    """从流式 JSON 文本中通过 brace 深度追踪提取完整 JSON 对象。

    Elements at depth 4 inside arrays (classes, relations, lifelines, etc.) are
    extracted and classified. Nested sub-objects at depth 5+ (attributes, methods)
    are correctly ignored.

    Extracted from uml_optimizer.py (2026-08-04) — shared by v2 SSE and Agent paths.
    """

    # Keys whose appearance at depth 2 signals a section change.
    _SECTION_KEYS = ('class', 'sequence', 'component')

    def __init__(self):
        self._buf = ""
        self._pos = 0
        self._depth = 0        # brace depth ({ only, [ ] are ignored)
        self._in_str = False
        self._esc = False
        self._elem_start = -1   # buffer offset where current depth-4 element begins
        self._section = None    # 'class', 'sequence', or 'component'
        self._current_diagram_name = None  # current diagram name for element routing
        self._scan_pos = 0      # last position scanned for component_id
        self._seen_cids = set() # avoid duplicate diagram_meta emission
        self._seen_diagrams = set()  # avoid duplicate diagram_create emission
        self._diagram_scan_pos = 0   # last position scanned for diagram_create

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        """Feed a new text chunk. Returns (type, json_string) tuples for completed elements."""
        self._buf += chunk
        elements: list[tuple[str, str]] = []

        # ── Scan new content for diagram_create events ─────
        _new_for_dc = self._buf[self._diagram_scan_pos:]
        _dc_idx = 0
        while True:
            _dc_idx = _new_for_dc.find('"type"', _dc_idx)
            if _dc_idx < 0:
                break
            _colon = _new_for_dc.find(':', _dc_idx)
            if _colon < 0: break
            _vstart = _new_for_dc.find('"', _colon + 1)
            if _vstart < 0: break
            _vend = _new_for_dc.find('"', _vstart + 1)
            if _vend < 0: break
            _dtype = _new_for_dc[_vstart + 1:_vend]
            if _dtype in ('class', 'sequence', 'component'):
                _search_end = min(len(_new_for_dc), _vend + 300)
                _name_idx = _new_for_dc.find('"name"', _vend, _search_end)
                if _name_idx >= 0:
                    _ncolon = _new_for_dc.find(':', _name_idx)
                    if _ncolon >= 0:
                        _nvstart = _new_for_dc.find('"', _ncolon + 1)
                        if _nvstart >= 0 and _nvstart < _search_end:
                            _nvend = _new_for_dc.find('"', _nvstart + 1)
                            if _nvend >= 0 and _nvend < _search_end:
                                _dname = _new_for_dc[_nvstart + 1:_nvend]
                                _dkey = f"{_dtype}:{_dname}"
                                self._current_diagram_name = _dname
                                if _dkey not in self._seen_diagrams:
                                    self._seen_diagrams.add(_dkey)
                                    _cid = ""
                                    _cid_idx = _new_for_dc.find('"component_id"', _vend, _search_end)
                                    if _cid_idx >= 0:
                                        _ccolon = _new_for_dc.find(':', _cid_idx)
                                        if _ccolon >= 0:
                                            _cvstart = _new_for_dc.find('"', _ccolon + 1)
                                            if _cvstart >= 0 and _cvstart < _search_end:
                                                _cvend = _new_for_dc.find('"', _cvstart + 1)
                                                if _cvend >= 0:
                                                    _cid = _new_for_dc[_cvstart + 1:_cvend]
                                    elements.append(('diagram_create', json.dumps({
                                        'type': _dtype,
                                        'name': _dname,
                                        'component_id': _cid,
                                    })))
            _dc_idx = _vend + 1
        self._diagram_scan_pos = max(0, len(self._buf) - 1024)

        # ── Scan new content for component_id values ─────
        new_text = self._buf[self._scan_pos:]
        idx = 0
        while True:
            idx = new_text.find('"component_id"', idx)
            if idx < 0:
                break
            colon_idx = new_text.find(':', idx)
            if colon_idx < 0:
                break
            val_start = new_text.find('"', colon_idx + 1)
            if val_start < 0:
                break
            val_end = new_text.find('"', val_start + 1)
            if val_end < 0:
                break
            cid = new_text[val_start + 1:val_end]
            if cid and cid not in self._seen_cids:
                self._seen_cids.add(cid)
                elements.append(('diagram_meta', json.dumps({
                    'component_id': cid,
                    'diagram_type': self._section or 'class',
                })))
            idx = val_end + 1
        self._scan_pos = max(0, len(self._buf) - 512)

        while self._pos < len(self._buf):
            c = self._buf[self._pos]

            if self._esc:
                self._esc = False
            elif c == '\\' and self._in_str:
                self._esc = True
            elif c == '"':
                self._in_str = not self._in_str
                if not self._in_str and self._depth == 2:
                    self._update_section()
            elif not self._in_str:
                if c == '{':
                    if self._depth == 3:
                        self._elem_start = self._pos
                    self._depth += 1
                elif c == '}':
                    self._depth -= 1
                    if self._depth == 3 and self._elem_start >= 0:
                        txt = self._buf[self._elem_start:self._pos + 1]
                        try:
                            obj = json.loads(txt)
                            tp = self._classify(obj)
                            if tp:
                                # Inject diagram_name for frontend routing
                                if self._current_diagram_name:
                                    obj["diagram_name"] = self._current_diagram_name
                                elements.append((tp, json.dumps(obj, ensure_ascii=False)))
                        except json.JSONDecodeError:
                            pass  # incomplete object — wait for more data
                        self._elem_start = -1

            self._pos += 1

        # Trim consumed prefix to bound memory (512-char window)
        _window = 512
        if self._elem_start >= 0:
            _keep = max(0, self._elem_start - _window)
            self._buf = self._buf[_keep:]
            self._pos -= _keep
            self._elem_start -= _keep
        else:
            _keep = max(0, self._pos - _window)
            self._buf = self._buf[_keep:]
            self._pos -= _keep

        return elements

    def _update_section(self):
        """Called when a string key closes at depth 2 — update section context."""
        j = self._pos - 1
        while j >= 0 and self._buf[j] != '"':
            j -= 1
        if j >= 0:
            key = self._buf[j + 1:self._pos]
            if key in self._SECTION_KEYS:
                self._section = key

    def _classify(self, obj: dict) -> str | None:
        """Determine element type from JSON keys with section-context-aware relation vs comp_rel."""
        if 'stereotype' in obj:
            return 'class'
        if 'from_lifeline' in obj:
            return 'message'
        if 'y_start' in obj or 'y_end' in obj:
            return 'fragment'
        if 'class_ref' in obj:
            return 'lifeline'
        if 'source' in obj and 'target' in obj:
            return 'comp_rel' if self._section == 'component' else 'relation'
        if 'parent_id' in obj or 'provided_interfaces' in obj:
            return 'component'
        return None


__all__ = ["JsonElementExtractor"]
