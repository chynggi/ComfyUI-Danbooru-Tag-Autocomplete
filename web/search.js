// Browser port of artifact.TagIndex. The Python implementation in artifact.py is the
// reference; tests/test_search_js.mjs asserts this file produces identical results for
// every query in tests/fixtures/queries.json.
//
// Artifact layout (format version 1, little-endian, header 64 bytes, sections 4-byte
// aligned): names, name_offsets, category, post_count, tag_flags, alias_names,
// alias_offsets, alias_target.

export const RANK_EXACT = 0;
export const RANK_NAME_PREFIX = 1;
export const RANK_ALIAS_PREFIX = 2;

const MAGIC = 0x31415444; // "DTA1"
const FORMAT_VERSION = 1;
const HEADER_SIZE = 64;

const decoder = new TextDecoder("utf-8", { fatal: true });

export function normalizeTag(name) {
  return name.trim().toLowerCase().replace(/ /g, "_");
}

const TOKEN_SEPARATORS = new Set([",", "\n", ";"]);

export function extractToken(text, caret) {
  const stop = Math.max(0, Math.min(caret, text.length));
  let start = 0;
  for (let index = stop - 1; index >= 0; index -= 1) {
    if (TOKEN_SEPARATORS.has(text[index])) {
      start = index + 1;
      break;
    }
  }
  return normalizeTag(text.slice(start, stop));
}

export class ArtifactView {
  constructor(buffer) {
    if (buffer.byteLength < HEADER_SIZE) {
      throw new Error("artifact is smaller than the 64-byte header");
    }
    const view = new DataView(buffer);
    if (view.getUint32(0, true) !== MAGIC) {
      throw new Error("bad magic");
    }
    if (view.getUint16(4, true) !== FORMAT_VERSION) {
      throw new Error(`unsupported format_version: ${view.getUint16(4, true)}`);
    }
    const offsets = (position) => view.getUint32(position, true);
    const offNames = offsets(20);
    const offNameOffsets = offsets(24);
    const offCategory = offsets(28);
    const offPostCount = offsets(32);
    const offTagFlags = offsets(36);
    const offAliasNames = offsets(40);
    const offAliasOffsets = offsets(44);
    const offAliasTarget = offsets(48);
    const lengthNames = offsets(52);
    const lengthAliasNames = offsets(56);

    for (const [label, offset] of [
      ["names", offNames],
      ["name_offsets", offNameOffsets],
      ["category", offCategory],
      ["post_count", offPostCount],
      ["tag_flags", offTagFlags],
      ["alias_names", offAliasNames],
      ["alias_offsets", offAliasOffsets],
      ["alias_target", offAliasTarget],
    ]) {
      if (offset % 4 !== 0) {
        throw new Error(`section ${label} is not 4-byte aligned: ${offset}`);
      }
    }

    this.threshold = offsets(16);
    this.nTags = offsets(8);
    this.nAliases = offsets(12);
    this.names = new Uint8Array(buffer, offNames, lengthNames);
    this.nameOffsets = new Uint32Array(buffer, offNameOffsets, this.nTags + 1);
    this.category = new Uint8Array(buffer, offCategory, this.nTags);
    this.postCount = new Uint32Array(buffer, offPostCount, this.nTags);
    this.tagFlags = new Uint8Array(buffer, offTagFlags, Math.ceil(this.nTags / 8));
    this.aliasNames = new Uint8Array(buffer, offAliasNames, lengthAliasNames);
    this.aliasOffsets = new Uint32Array(buffer, offAliasOffsets, this.nAliases + 1);
    this.aliasTarget = new Uint32Array(buffer, offAliasTarget, this.nAliases);
  }

  nameBytes(index) {
    return this.names.subarray(this.nameOffsets[index], this.nameOffsets[index + 1]);
  }

  name(index) {
    return decoder.decode(this.nameBytes(index));
  }

  alias(index) {
    return decoder.decode(this.aliasNames.subarray(this.aliasOffsets[index], this.aliasOffsets[index + 1]));
  }

  deprecated(index) {
    return (this.tagFlags[index >> 3] & (1 << (index & 7))) !== 0;
  }
}

export function decodeArtifact(buffer) {
  return new ArtifactView(buffer);
}

function compareSlice(bytes, start, end, key) {
  const shared = Math.min(end - start, key.length);
  for (let index = 0; index < shared; index += 1) {
    const left = bytes[start + index];
    const right = key[index];
    if (left !== right) {
      return left < right ? -1 : 1;
    }
  }
  const length = end - start;
  if (length === key.length) {
    return 0;
  }
  return length < key.length ? -1 : 1;
}

function lowerBound(bytes, offsets, count, key) {
  let low = 0;
  let high = count;
  while (low < high) {
    const mid = (low + high) >>> 1;
    if (compareSlice(bytes, offsets[mid], offsets[mid + 1], key) < 0) {
      low = mid + 1;
    } else {
      high = mid;
    }
  }
  return low;
}

function startsWith(bytes, start, end, key) {
  if (end - start < key.length) {
    return false;
  }
  for (let index = 0; index < key.length; index += 1) {
    if (bytes[start + index] !== key[index]) {
      return false;
    }
  }
  return true;
}

function hitSortKey(hit) {
  return [hit.rank, hit.rank === RANK_NAME_PREFIX ? hit.nameLength : 0, -hit.postCount, hit.name];
}

function compareHits(left, right) {
  const a = hitSortKey(left);
  const b = hitSortKey(right);
  for (let index = 0; index < a.length; index += 1) {
    if (a[index] < b[index]) return -1;
    if (a[index] > b[index]) return 1;
  }
  return 0;
}

export class TagIndex {
  constructor(main, custom = null) {
    this.main = main;
    this.custom = custom;
  }

  #nameHits(source, keyBytes, categories, excludeDeprecated) {
    const hits = [];
    for (let index = lowerBound(source.names, source.nameOffsets, source.nTags, keyBytes); index < source.nTags; index += 1) {
      const start = source.nameOffsets[index];
      const end = source.nameOffsets[index + 1];
      if (!startsWith(source.names, start, end, keyBytes)) {
        break;
      }
      if (excludeDeprecated && source.deprecated(index)) {
        continue;
      }
      const category = source.category[index];
      if (categories !== null && !categories.has(category)) {
        continue;
      }
      const rank = end - start === keyBytes.length ? RANK_EXACT : RANK_NAME_PREFIX;
      hits.push({
        name: source.name(index),
        category,
        postCount: source.postCount[index],
        deprecated: source.deprecated(index),
        alias: null,
        rank,
        nameLength: end - start,
      });
    }
    return hits;
  }

  #aliasHits(source, keyBytes, categories, excludeDeprecated) {
    const best = new Map();
    for (let index = lowerBound(source.aliasNames, source.aliasOffsets, source.nAliases, keyBytes); index < source.nAliases; index += 1) {
      const start = source.aliasOffsets[index];
      const end = source.aliasOffsets[index + 1];
      if (!startsWith(source.aliasNames, start, end, keyBytes)) {
        break;
      }
      const target = source.aliasTarget[index];
      if (excludeDeprecated && source.deprecated(target)) {
        continue;
      }
      const category = source.category[target];
      if (categories !== null && !categories.has(category)) {
        continue;
      }
      const name = source.name(target);
      const hit = {
        name,
        category,
        postCount: source.postCount[target],
        deprecated: source.deprecated(target),
        alias: source.alias(index),
        rank: RANK_ALIAS_PREFIX,
        nameLength: source.nameOffsets[target + 1] - source.nameOffsets[target],
      };
      const current = best.get(name);
      if (current === undefined || compareHits(hit, current) < 0) {
        best.set(name, hit);
      }
    }
    return best;
  }

  #searchSource(source, keyBytes, limit, categories, excludeDeprecated) {
    const combined = new Map();
    for (const hit of this.#nameHits(source, keyBytes, categories, excludeDeprecated)) {
      combined.set(hit.name, hit);
    }
    for (const [name, hit] of this.#aliasHits(source, keyBytes, categories, excludeDeprecated)) {
      const current = combined.get(name);
      if (current === undefined || compareHits(hit, current) < 0) {
        combined.set(name, hit);
      }
    }
    return [...combined.values()].sort(compareHits).slice(0, limit);
  }

  search(query, { limit = 32, categories = null, excludeDeprecated = true } = {}) {
    const key = normalizeTag(query);
    if (key === "") {
      return [];
    }
    const keyBytes = new TextEncoder().encode(key);
    const merged = new Map();
    if (this.custom === null) {
      for (const hit of this.#searchSource(this.main, keyBytes, limit, categories, excludeDeprecated)) {
        merged.set(hit.name, hit);
      }
    } else {
      // The overlay wins by name even when it ranks lower than the main entry it
      // replaces, so over-select main by the number of overlay names to keep the
      // bounded window exact. Keep this in step with TagIndex.search in artifact.py.
      const custom = this.#searchSource(
        this.custom,
        keyBytes,
        this.custom.nTags + this.custom.nAliases,
        categories,
        excludeDeprecated,
      );
      for (const hit of this.#searchSource(this.main, keyBytes, limit + custom.length, categories, excludeDeprecated)) {
        merged.set(hit.name, hit);
      }
      for (const hit of custom) {
        merged.set(hit.name, hit);
      }
    }
    return [...merged.values()].sort(compareHits).slice(0, limit);
  }
}
