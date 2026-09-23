import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { TagIndex, decodeArtifact, extractToken, normalizeTag } from "../web/search.js";

const FIXTURES = join(dirname(fileURLToPath(import.meta.url)), "fixtures");

function readBuffer(path) {
  const bytes = readFileSync(path);
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
}

const artifactBuffer = readBuffer(join(FIXTURES, "artifact.bin"));
const expected = JSON.parse(readFileSync(join(FIXTURES, "queries.json"), "utf8"));

function project(hit) {
  return {
    name: hit.name,
    category: hit.category,
    postCount: hit.postCount,
    deprecated: hit.deprecated,
    alias: hit.alias,
    rank: hit.rank,
    nameLength: hit.nameLength,
  };
}

test("decodeArtifact reads the header and sections", () => {
  const artifact = decodeArtifact(artifactBuffer);
  assert.equal(artifact.threshold, expected.threshold);
  assert.equal(artifact.name(0), "1girl");
  assert.ok(artifact.nTags > 0);
});

test("search matches the Python fixture for every query", () => {
  const index = new TagIndex(decodeArtifact(artifactBuffer));
  for (const [query, hits] of Object.entries(expected.queries)) {
    assert.deepEqual(index.search(query).map(project), hits, `query ${JSON.stringify(query)}`);
  }
});

test("limit is respected", () => {
  const index = new TagIndex(decodeArtifact(artifactBuffer));
  assert.ok(index.search("blue_h", { limit: 2 }).length <= 2);
});

test("deprecated matches are excluded by default and can be included", () => {
  const index = new TagIndex(decodeArtifact(artifactBuffer));
  assert.deepEqual(index.search("old_tag"), []);
  const included = index.search("old_tag", { excludeDeprecated: false });
  assert.ok(included.length >= 1);
  assert.equal(included[0].deprecated, true);
});

test("normalizeTag folds case and spaces to underscores", () => {
  assert.equal(normalizeTag("  Blue Hair "), "blue_hair");
});

test("extractToken reads the token under the caret only", () => {
  assert.equal(extractToken("1girl, blue_h", 13), "blue_h");
  assert.equal(extractToken("blue_hair, 1girl", 4), "blue");
  assert.equal(extractToken("", 0), "");
});

test("overlay results match the Python fixture", () => {
  const overlay = expected.overlay;
  const main = decodeArtifact(readBuffer(join(FIXTURES, "overlay-main.bin")));
  const custom = decodeArtifact(readBuffer(join(FIXTURES, "overlay.bin")));
  const index = new TagIndex(main, custom);
  for (const [query, hits] of Object.entries(overlay.queries)) {
    assert.deepEqual(index.search(query, { limit: overlay.limit }).map(project), hits, `query ${JSON.stringify(query)}`);
  }
});

test("overlay precedence holds under a category filter", () => {
  const main = decodeArtifact(readBuffer(join(FIXTURES, "overlay-main.bin")));
  const custom = decodeArtifact(readBuffer(join(FIXTURES, "overlay.bin")));
  const index = new TagIndex(main, custom);
  assert.deepEqual(
    index.search("c", { limit: 2, categories: new Set([0]) }).map((hit) => hit.name),
    ["c1", "c2"],
  );
});

test("astral names tie-break by code point, not UTF-16 code unit", () => {
  const index = new TagIndex(decodeArtifact(artifactBuffer));
  assert.deepEqual(index.search("x").map((hit) => hit.name), ["x\ue000x", "x\u{10000}"]);
});
