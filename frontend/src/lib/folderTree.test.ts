import { describe, expect, it } from "vitest";
import { buildFolderTree, flattenTree } from "./folderTree";
import type { Track } from "./types";

function track(overrides: Partial<Track>): Track {
  return {
    id: 1,
    media_root: "D:/Media",
    relative_path: "Song.flac",
    artist: null,
    title: "Song",
    outputs: {
      instrumental: false,
      vocals: false,
      lead_vocals: false,
      backing_vocals: false,
      drums: false,
      bass: false,
      guitar: false,
      piano: false,
      other: false,
      lrc: false,
    },
    lrc_state: null,
    stem_count: 0,
    ...overrides,
  };
}

describe("buildFolderTree", () => {
  it("groups tracks under nested folders by media root", () => {
    const tracks = [
      track({ id: 1, relative_path: "ABBA/Arrival/Dancing Queen.flac" }),
      track({ id: 2, relative_path: "ABBA/Arrival/Money Money Money.flac" }),
      track({ id: 3, relative_path: "Queen/A Night at the Opera/Bohemian Rhapsody.flac" }),
    ];

    const tree = buildFolderTree(tracks);

    expect(tree).toHaveLength(1);
    const [mediaRoot] = tree;
    expect(mediaRoot.name).toBe("D:/Media");
    expect(mediaRoot.children.map((child) => child.name)).toEqual(["ABBA", "Queen"]);

    const abba = mediaRoot.children[0];
    expect(abba.children[0].name).toBe("Arrival");
    expect(abba.children[0].tracks).toHaveLength(2);
  });

  it("returns an empty list for no tracks", () => {
    expect(buildFolderTree([])).toEqual([]);
  });

  it("normalizes Windows backslash media roots into forward-slash paths", () => {
    const tracks = [track({ id: 1, media_root: "D:\\Media", relative_path: "ABBA\\Song.flac" })];
    const tree = buildFolderTree(tracks);
    expect(tree[0].name).toBe("D:/Media");
    expect(tree[0].children[0].path).toBe("D:/Media/ABBA");
  });

  it("keeps a user-created empty folder in the tree", () => {
    const tree = buildFolderTree([], [{
      path: "D:/Media/New folder",
      media_root: "D:/Media",
      relative_path: "New folder",
      name: "New folder",
    }]);

    expect(tree[0].name).toBe("D:/Media");
    expect(tree[0].children[0].name).toBe("New folder");
    expect(tree[0].children[0].tracks).toEqual([]);
  });
});

describe("flattenTree", () => {
  it("flattens nested folders depth-first with depth values", () => {
    const tracks = [
      track({ id: 1, relative_path: "ABBA/Arrival/Dancing Queen.flac" }),
      track({ id: 3, relative_path: "Queen/A Night at the Opera/Bohemian Rhapsody.flac" }),
    ];

    const rows = flattenTree(buildFolderTree(tracks));

    expect(rows.map((row) => [row.node.name, row.depth])).toEqual([
      ["D:/Media", 0],
      ["ABBA", 1],
      ["Arrival", 2],
      ["Queen", 1],
      ["A Night at the Opera", 2],
    ]);
  });

  it("omits descendants of collapsed folders while retaining the folder row", () => {
    const tracks = [
      track({ id: 1, relative_path: "ABBA/Arrival/Dancing Queen.flac" }),
      track({ id: 2, relative_path: "Queen/Greatest Hits/Somebody to Love.flac" }),
    ];

    const rows = flattenTree(buildFolderTree(tracks), new Set(["D:/Media/ABBA"]));

    expect(rows.map((row) => row.node.name)).toEqual([
      "D:/Media",
      "ABBA",
      "Queen",
      "Greatest Hits",
    ]);
  });
});
