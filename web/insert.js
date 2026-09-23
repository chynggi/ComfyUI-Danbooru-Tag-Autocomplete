// Pure text-editing helpers for a prompt textarea. No DOM, so tests/test_insert_js.mjs can
// exercise them under Node.
//
// The token under the caret is the whole separator-delimited run containing it, because a
// Danbooru tag may contain spaces ("blue hair" normalizes to "blue_hair").

export const TOKEN_SEPARATORS = new Set([",", "\n", ";"]);

const WHITESPACE = /\s/;

export function tokenRange(text, caret) {
  const stop = Math.max(0, Math.min(caret, text.length));
  let start = 0;
  for (let index = stop - 1; index >= 0; index -= 1) {
    if (TOKEN_SEPARATORS.has(text[index])) {
      start = index + 1;
      break;
    }
  }
  while (start < stop && WHITESPACE.test(text[start])) {
    start += 1;
  }
  let end = stop;
  while (end < text.length && !TOKEN_SEPARATORS.has(text[end])) {
    end += 1;
  }
  while (end > stop && WHITESPACE.test(text[end - 1])) {
    end -= 1;
  }
  return { start, end };
}

export function planInsertion(text, caret, replacement, { replaceUnderscores = false } = {}) {
  const { start, end } = tokenRange(text, caret);
  const inserted = replaceUnderscores ? replacement.replace(/_/g, " ") : replacement;
  const next = text[end];
  const suffix = next !== undefined && (TOKEN_SEPARATORS.has(next) || WHITESPACE.test(next)) ? "" : ", ";
  const insertion = inserted + suffix;
  return {
    start,
    end,
    replacement: insertion,
    text: text.slice(0, start) + insertion + text.slice(end),
    caret: start + insertion.length,
  };
}
