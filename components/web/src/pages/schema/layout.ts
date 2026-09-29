import ELK from "elkjs/lib/elk.bundled.js";

const elk = new ELK();

export interface Box { id: string; width: number; height: number }
export interface Link { id: string; source: string; target: string }
export interface Placed { x: number; y: number; width: number; height: number }

const LAYERED = {
  "elk.algorithm": "layered",
  "elk.direction": "RIGHT",
  "elk.edgeRouting": "ORTHOGONAL",
  "elk.spacing.nodeNode": "56",
  "elk.layered.spacing.nodeNodeBetweenLayers": "120",
  "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
  "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
};

async function layered(boxes: Box[], links: Link[], direction: "RIGHT" | "DOWN"): Promise<Map<string, Placed>> {
  const ids = new Set(boxes.map((box) => box.id));
  const graph = await elk.layout({
    id: "root",
    layoutOptions: { ...LAYERED, "elk.direction": direction },
    children: boxes.map((box) => ({ ...box })),
    edges: links
      .filter((link) => link.source !== link.target && ids.has(link.source) && ids.has(link.target))
      .map((link) => ({ id: link.id, sources: [link.source], targets: [link.target] })),
  });
  return new Map((graph.children ?? []).map((child) => [
    child.id, { x: child.x ?? 0, y: child.y ?? 0, width: child.width ?? 0, height: child.height ?? 0 },
  ]));
}

function extent(placed: Map<string, Placed>): { width: number; height: number } {
  let width = 0;
  let height = 0;
  placed.forEach((box) => {
    width = Math.max(width, box.x + box.width);
    height = Math.max(height, box.y + box.height);
  });
  return { width: Math.max(width, 1), height: Math.max(height, 1) };
}

/**
 * Layered layout with referencing tables before the tables they point to. Both left-to-right and
 * top-to-bottom are tried; the one that can be drawn larger in the canvas wins, so a chain of
 * lookups runs across the screen and a row of sibling tables stacks down it.
 */
export async function layoutFlat(boxes: Box[], links: Link[], canvas = { width: 1600, height: 1000 }): Promise<Map<string, Placed>> {
  const across = await layered(boxes, links, "RIGHT");
  if (boxes.length < 3) return across;
  const down = await layered(boxes, links, "DOWN");
  const scale = (placed: Map<string, Placed>) => {
    const size = extent(placed);
    return Math.min(canvas.width / size.width, canvas.height / size.height);
  };
  return scale(down) > scale(across) * 1.05 ? down : across;
}

export interface Grouped {
  groups: Map<string, Placed>;
  /** Child positions relative to their group. */
  nodes: Map<string, Placed>;
}

/**
 * Pack each area's tables into a box, then arrange the boxes by the links between areas.
 * Table edges are drawn by the canvas; ELK only sees area-to-area links.
 */
export async function layoutGrouped(
  groups: { id: string; boxes: Box[] }[], groupLinks: Link[],
): Promise<Grouped> {
  const ids = new Set(groups.map((group) => group.id));
  const graph = await elk.layout({
    id: "root",
    layoutOptions: { ...LAYERED, "elk.spacing.nodeNode": "70", "elk.layered.spacing.nodeNodeBetweenLayers": "110" },
    children: groups.map((group) => ({
      id: group.id,
      layoutOptions: {
        "elk.algorithm": "rectpacking",
        "elk.aspectRatio": "1.4",
        "elk.spacing.nodeNode": "26",
        "elk.padding": "[top=64,left=22,bottom=22,right=22]",
      },
      children: group.boxes.map((box) => ({ ...box })),
    })),
    edges: groupLinks
      .filter((link) => link.source !== link.target && ids.has(link.source) && ids.has(link.target))
      .map((link) => ({ id: link.id, sources: [link.source], targets: [link.target] })),
  });
  const placedGroups = new Map<string, Placed>();
  const nodes = new Map<string, Placed>();
  (graph.children ?? []).forEach((group) => {
    placedGroups.set(group.id, { x: group.x ?? 0, y: group.y ?? 0, width: group.width ?? 0, height: group.height ?? 0 });
    ((group.children ?? []) as { id: string; x?: number; y?: number; width?: number; height?: number }[]).forEach((child) => nodes.set(child.id, {
      x: child.x ?? 0, y: child.y ?? 0, width: child.width ?? 0, height: child.height ?? 0,
    }));
  });
  return { groups: placedGroups, nodes };
}
