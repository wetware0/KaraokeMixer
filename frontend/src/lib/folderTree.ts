import type { LibraryFolder, Track } from "./types";

export interface FolderNode {
  name: string;
  path: string;
  children: FolderNode[];
  tracks: Track[];
}

export function buildFolderTree(tracks: Track[], folders: LibraryFolder[] = []): FolderNode[] {
  const roots: FolderNode[] = [];
  const rootsByPath = new Map<string, FolderNode>();
  const childMapsByPath = new Map<string, Map<string, FolderNode>>();

  function getOrCreate(
    siblings: FolderNode[],
    siblingsByPath: Map<string, FolderNode>,
    path: string,
    name: string
  ): FolderNode {
    let node = siblingsByPath.get(path);
    if (!node) {
      node = { name, path, children: [], tracks: [] };
      siblingsByPath.set(path, node);
      siblings.push(node);
    }
    return node;
  }

  function ensurePath(normalizedRoot: string, folderSegments: string[]): FolderNode {
    const segments = [normalizedRoot, ...folderSegments];

    let parentArray = roots;
    let parentMap = rootsByPath;
    let path = "";
    let node: FolderNode | null = null;

    for (const segment of segments) {
      path = path ? `${path}/${segment}` : segment;
      node = getOrCreate(parentArray, parentMap, path, segment);

      let childMap = childMapsByPath.get(path);
      if (!childMap) {
        childMap = new Map<string, FolderNode>();
        childMapsByPath.set(path, childMap);
      }
      parentArray = node.children;
      parentMap = childMap;
    }

    return node!;
  }

  for (const folder of folders) {
    const normalizedRoot = folder.media_root.replace(/\\/g, "/");
    const folderSegments = folder.relative_path ? folder.relative_path.split(/[\\/]/).filter(Boolean) : [];
    ensurePath(normalizedRoot, folderSegments);
  }

  for (const track of tracks) {
    const normalizedRoot = track.media_root.replace(/\\/g, "/");
    const folderSegments = track.relative_path.split(/[\\/]/).slice(0, -1);
    ensurePath(normalizedRoot, folderSegments).tracks.push(track);
  }

  function sortNodes(nodes: FolderNode[]): void {
    nodes.sort((left, right) => left.name.localeCompare(right.name, undefined, { sensitivity: "base" }));
    nodes.forEach((node) => sortNodes(node.children));
  }
  sortNodes(roots);

  return roots;
}

export function flattenTree(
  nodes: FolderNode[],
  collapsedPaths: ReadonlySet<string> = new Set<string>(),
  depth = 0
): Array<{ node: FolderNode; depth: number }> {
  const rows: Array<{ node: FolderNode; depth: number }> = [];
  for (const node of nodes) {
    rows.push({ node, depth });
    if (!collapsedPaths.has(node.path)) {
      rows.push(...flattenTree(node.children, collapsedPaths, depth + 1));
    }
  }
  return rows;
}
