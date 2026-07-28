"""Auto-layout engine — repositions newly-generated UML elements to avoid overlap.

Three layout strategies, one per diagram type. Only repositions elements with
default (0,0) or near-zero coordinates — user-placed and existing elements are
preserved. Designed for minimal integration: call ``auto_layout(result)`` on the
optimization result dict, get back a layout-adjusted copy.
"""

from __future__ import annotations
import logging

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────

_CLASS_W, _CLASS_H = 200, 150       # default class node size
_COMP_W, _COMP_H = 200, 160          # default component size
_COMP_CHILD_W, _COMP_CHILD_H = 150, 100  # sub-component size
_SEQ_LL_GAP = 200                    # lifeline horizontal gap
_SEQ_START_X = 150                   # first lifeline X
_SEQ_START_Y = 190                   # first message Y
_SEQ_MSG_GAP = 45                    # message vertical gap
_GRID_COLS = 4                       # columns in grid layout
_GRID_GAP_X, _GRID_GAP_Y = 70, 60    # gap between grid cells


# ── Helpers ────────────────────────────────────────────────

def _is_new_position(x: float, y: float) -> bool:
    """Return True if the position looks LLM-generated (near origin / default)."""
    return (abs(x) < 10 and abs(y) < 10) or (x == 0 and y == 0)


def _classify_relations(relations: list[dict]) -> dict[str, list[str]]:
    """Build parent→children map for inheritance and realization relations.

    Returns {parent_id: [child_id, ...]}.
    Only includes inheritance and realization (vertical layout).
    """
    tree: dict[str, list[str]] = {}
    for r in relations:
        if r.get("type") in ("inheritance", "realization"):
            parent = r.get("target", "")
            child = r.get("source", "")
            if parent and child:
                tree.setdefault(parent, []).append(child)
    return tree


def _topological_order(classes: list[dict], tree: dict[str, list[str]]) -> list[str]:
    """Sort class IDs so parents come before children (topological order)."""
    in_degree: dict[str, int] = {}
    children: dict[str, list[str]] = {}
    all_ids = {c["id"] for c in classes}

    for cid in all_ids:
        in_degree.setdefault(cid, 0)
        children.setdefault(cid, [])

    for parent, kids in tree.items():
        for kid in kids:
            if kid in in_degree:
                in_degree[kid] += 1
            children.setdefault(parent, []).append(kid)

    # Kahn's algorithm
    queue = [cid for cid, deg in in_degree.items() if deg == 0]
    result = []
    while queue:
        node = queue.pop(0)
        result.append(node)
        for child in children.get(node, []):
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)

    # Append any remaining (cycles)
    for cid in all_ids:
        if cid not in result:
            result.append(cid)
    return result


def _grid_position(index: int, cols: int = _GRID_COLS,
                   gap_x: int = _GRID_GAP_X, gap_y: int = _GRID_GAP_Y,
                   cell_w: int = _CLASS_W, cell_h: int = _CLASS_H) -> tuple[float, float, int]:
    """Compute (x, y, row) for a grid cell at *index*."""
    col = index % cols
    row = index // cols
    x = 100.0 + col * (cell_w + gap_x)
    y = 80.0 + row * (cell_h + gap_y)
    return x, y, row


# ── Class Diagram Layout ───────────────────────────────────

def _layout_class_diagram(data: dict) -> dict:
    """Hierarchical + grid layout for class diagrams.

    1. Find inheritance/realization chains → parents above children
    2. Non-hierarchical classes → grid below the tree
    3. Only moves elements at (0,0) or near-zero positions
    """
    classes = data.get("classes", [])
    relations = data.get("relations", [])
    if not classes:
        return data

    cls_map = {c["id"]: c for c in classes}
    tree = _classify_relations(relations)

    # Split classes: in-tree (has inheritance) vs grid-only
    in_tree: set[str] = set()
    for parent, kids in tree.items():
        if parent in cls_map:
            in_tree.add(parent)
        for kid in kids:
            if kid in cls_map:
                in_tree.add(kid)
    grid_classes = [c for c in classes if c["id"] not in in_tree]

    # ── Determine which elements need repositioning ──
    # If ALL elements are user-placed, skip entirely
    new_count = sum(1 for c in classes if _is_new_position(
        c.get("position", {}).get("x", 0),
        c.get("position", {}).get("y", 0)))
    if new_count == 0:
        return data  # all user-positioned, nothing to do

    # ── Layout tree classes ──
    order = _topological_order(classes, tree)
    depths: dict[str, int] = {}
    for cid in order:
        parent_depth = -1
        for parent, kids in tree.items():
            if cid in kids:
                parent_depth = depths.get(parent, -1)
                break
        depths[cid] = parent_depth + 1

    # Compute max width per depth level
    depth_widths: dict[int, int] = {}
    for cid, d in depths.items():
        depth_widths[d] = depth_widths.get(d, 0) + 1

    # Place tree classes: depth→row, width→col within row
    placed_cols: dict[int, int] = {}
    tree_y = 60.0
    for cid in order:
        cls = cls_map.get(cid)
        if not cls:
            continue
        if not _is_new_position(cls.get("position", {}).get("x", 0),
                                cls.get("position", {}).get("y", 0)):
            continue  # user-placed, skip
        d = depths.get(cid, 0)
        col_in_depth = placed_cols.get(d, 0)
        placed_cols[d] = col_in_depth + 1
        x = 100.0 + col_in_depth * (_CLASS_W + _GRID_GAP_X)
        y = tree_y + d * (_CLASS_H + 100)  # extra gap for relation arrows
        cls["position"] = {"x": x, "y": y}

    # ── Layout grid classes (below the tree) ──
    tree_end_y = tree_y + (max(depths.values()) + 1) * (_CLASS_H + 100) if depths else 60.0
    for idx, cls in enumerate(grid_classes):
        if not _is_new_position(cls.get("position", {}).get("x", 0),
                                cls.get("position", {}).get("y", 0)):
            continue
        x, y, _ = _grid_position(idx, cols=_GRID_COLS, gap_x=_GRID_GAP_X,
                                 gap_y=_GRID_GAP_Y)
        y += tree_end_y
        cls["position"] = {"x": x, "y": y}

    logger.info(f"[Layout] Class diagram: {len(in_tree)} tree + {len(grid_classes)} grid, "
                f"{new_count} repositioned")
    return data


# ── Sequence Diagram Layout ────────────────────────────────

def _layout_sequence_diagram(data: dict) -> dict:
    """Linear horizontal + vertical layout for sequence diagrams.

    Lifelines: evenly spaced from left to right.
    Messages: y = _SEQ_START_Y + order * _SEQ_MSG_GAP.
    Fragments: wrap the messages they contain.
    """
    lifelines = data.get("lifelines", [])
    messages = data.get("messages", [])
    fragments = data.get("fragments", [])

    new_ll = sum(1 for ll in lifelines if _is_new_position(ll.get("x", 0), 0))
    if new_ll == 0 and not any(m.get("y", 0) == 0 for m in messages):
        return data

    # ── Lifelines ──
    for i, ll in enumerate(lifelines):
        if _is_new_position(ll.get("x", 0), 0):
            ll["x"] = float(_SEQ_START_X + i * _SEQ_LL_GAP)

    # ── Messages ──
    for msg in messages:
        order = msg.get("order", 1)
        y = float(_SEQ_START_Y + (order - 1) * _SEQ_MSG_GAP)
        if msg.get("y", 0) == 0 or _is_new_position(0, msg.get("y", 0)):
            msg["y"] = y
        # Fix order if out of sequence
        if msg.get("order", 1) <= 0:
            msg["order"] = 1

    # ── Fragments ──
    if fragments:
        # Determine fragment coverage from contained messages
        ll_xs = [ll.get("x", _SEQ_START_X) for ll in lifelines]
        min_x = min(ll_xs) - 60 if ll_xs else 80.0
        max_x = max(ll_xs) + 60 if ll_xs else 600.0
        width = max_x - min_x

        for i, frag in enumerate(fragments):
            if _is_new_position(frag.get("x", 0), frag.get("y_start", 0)):
                frag["x"] = min_x
                frag["width"] = width
                # Try to position fragment based on message Ys in range
                # Fallback: use sequential position
                frag["y_start"] = float(_SEQ_START_Y + i * _SEQ_MSG_GAP * 3 - 30)
                frag["y_end"] = float(frag["y_start"] + _SEQ_MSG_GAP * 3 + 30)

    logger.info(f"[Layout] Sequence diagram: {len(lifelines)} lifelines, "
                f"{len(messages)} messages, {new_ll} repositioned")
    return data


# ── Component Diagram Layout ───────────────────────────────

def _layout_component_diagram(data: dict) -> dict:
    """Flow layout for component diagrams. Respects parent-child nesting.

    Algorithm:
    1. Group children by parent_id
    2. Layout children inside each parent, compute actual size needed
    3. Expand parent if too small for children
    4. Flow-layout top-level components left-to-right, wrap to next row
    5. Only repositions near-zero coordinates (user-placed elements preserved)
    """
    components = data.get("components", [])
    if not components:
        return data

    top = [c for c in components if not c.get("parent_id")]
    children = [c for c in components if c.get("parent_id")]

    new_top = sum(1 for c in top if _is_new_position(c.get("x", 0), c.get("y", 0)))
    new_child = sum(1 for c in children if _is_new_position(c.get("x", 0), c.get("y", 0)))
    if new_top == 0 and new_child == 0:
        return data

    # ── Group children by parent ──
    parent_map: dict[str, list[dict]] = {}
    for c in children:
        pid = c.get("parent_id", "")
        parent_map.setdefault(pid, []).append(c)

    # ── Phase 1: Compute child rows & expand parents FIRST ──
    # Store child row layout info keyed by parent_id
    child_layout: dict[str, list[list[dict]]] = {}  # parent_id → [row, row, ...]

    for pid, kids in parent_map.items():
        parent = next((c for c in components if c["id"] == pid), None)
        if parent:
            parent["width"] = parent.get("width") or _COMP_W
            parent["height"] = parent.get("height") or _COMP_H

        # Ensure child sizes
        for kid in kids:
            kid["width"] = kid.get("width") or _COMP_CHILD_W
            kid["height"] = kid.get("height") or _COMP_CHILD_H

        # Group children into rows (max 5 per row or ~650px)
        rows: list[list[dict]] = []
        cur_row: list[dict] = []
        cur_w = 0.0
        max_w = 0.0

        for kid in kids:
            cur_row.append(kid)
            cur_w += kid["width"] + 12
            if len(cur_row) >= 5 or cur_w > 680:
                rows.append(cur_row)
                max_w = max(max_w, cur_w - 12)
                cur_row = []
                cur_w = 0.0
        if cur_row:
            rows.append(cur_row)
            max_w = max(max_w, cur_w - 12)

        child_layout[pid] = rows

        # Expand parent to fit children if too small
        if parent and rows:
            needed_w = max_w + 40
            needed_h = 38.0 + len(rows) * (_COMP_CHILD_H + 8) + 20
            if needed_w > parent.get("width", _COMP_W):
                parent["width"] = needed_w
            if needed_h > parent.get("height", _COMP_H):
                parent["height"] = needed_h

    # ── Phase 2: Flow-layout top-level components ──
    _START_X, _START_Y = 80.0, 60.0
    _FLOW_GAP_X, _FLOW_GAP_Y = 60.0, 60.0
    _MAX_ROW_WIDTH = 1400.0  # pixels before wrapping to next row

    cursor_x = _START_X
    cursor_y = _START_Y
    row_height = 0.0

    for comp in top:
        if not _is_new_position(comp.get("x", 0), comp.get("y", 0)):
            # User-placed: update cursor to avoid placing new components on top
            continue

        cw = comp.get("width", _COMP_W)
        ch = comp.get("height", _COMP_H)

        # Wrap to next row if this component would overflow
        if cursor_x + cw > _START_X + _MAX_ROW_WIDTH and cursor_x > _START_X:
            cursor_x = _START_X
            cursor_y += row_height + _FLOW_GAP_Y
            row_height = 0.0

        comp["x"] = cursor_x
        comp["y"] = cursor_y

        cursor_x += cw + _FLOW_GAP_X
        row_height = max(row_height, ch)

    # ── Phase 3: Position children inside parents (AFTER parents are placed) ──
    for pid, rows in child_layout.items():
        parent = next((c for c in components if c["id"] == pid), None)
        if not parent:
            continue
        parent_w = parent.get("width", _COMP_W)
        px, py = parent["x"], parent["y"]

        for ri, crow in enumerate(rows):
            row_total_w = sum(k["width"] for k in crow) + (len(crow) - 1) * 12
            # Center the row inside parent
            row_start_x = px + max(10.0, (parent_w - row_total_w) / 2)
            cx = row_start_x
            for kid in crow:
                if _is_new_position(kid.get("x", 0), kid.get("y", 0)):
                    kid["x"] = cx
                    kid["y"] = py + 38.0 + ri * (_COMP_CHILD_H + 8)
                cx += kid["width"] + 12

    logger.info(f"[Layout] Component diagram: {len(top)} top + {len(children)} children, "
                f"{new_top + new_child} repositioned")
    return data


# ── Public API ─────────────────────────────────────────────

def auto_layout(result: dict) -> dict:
    """Apply auto-layout to all diagrams in an optimization result.

    Only repositions elements at (0,0) or near-zero coordinates — existing
    user-placed elements are preserved. Modifies the dict in-place.

    Usage::

        from app.services.layout_engine import auto_layout
        result = auto_layout(result)

    """
    diagrams = result.get("diagrams", [])
    for diag in diagrams:
        dtype = diag.get("type", "class")
        data = diag.get("data") or diag

        if dtype == "class":
            _layout_class_diagram(data)
        elif dtype == "sequence":
            _layout_sequence_diagram(data)
        elif dtype == "component":
            _layout_component_diagram(data)

        # Sync back if using data wrapper
        if diag.get("data") is not data:
            diag["data"] = data

    return result
