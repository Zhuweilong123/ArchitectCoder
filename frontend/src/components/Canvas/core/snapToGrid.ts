export interface CanvasPosition {
  x: number;
  y: number;
}

/** Returns a stable grid-aligned position when snapping is enabled. */
export function snapCanvasPosition(
  position: CanvasPosition,
  enabled: boolean,
  gridSize = 20,
): CanvasPosition {
  if (!enabled || !Number.isFinite(gridSize) || gridSize <= 0) return position;
  return {
    x: Math.round(position.x / gridSize) * gridSize,
    y: Math.round(position.y / gridSize) * gridSize,
  };
}
