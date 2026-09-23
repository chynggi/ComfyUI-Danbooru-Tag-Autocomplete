import assert from "node:assert/strict";
import test from "node:test";

import { TOKEN_SEPARATORS, planInsertion, tokenRange } from "../web/insert.js";

test("the separator set is the one the index tokenizer uses", () => {
  assert.deepEqual([...TOKEN_SEPARATORS].sort(), ["\n", ",", ";"]);
});

test("tokenRange covers the run up to the next separator", () => {
  assert.deepEqual(tokenRange("1girl, blue_h", 13), { start: 7, end: 13 });
  assert.deepEqual(tokenRange("blue_hair, 1girl", 4), { start: 0, end: 9 });
  assert.deepEqual(tokenRange("", 0), { start: 0, end: 0 });
});

test("tokenRange never returns a range that starts after the caret", () => {
  const range = tokenRange("1girl, blue_hair, solo", 14);
  assert.ok(range.start <= 14);
  assert.ok(range.end >= 14);
});

test("tokenRange keeps a space that precedes a separator", () => {
  assert.deepEqual(tokenRange("1girl, blue_h , x", 13), { start: 7, end: 13 });
});

test("tokenRange treats internal spaces as part of the token", () => {
  assert.deepEqual(tokenRange("blue hair", 9), { start: 0, end: 9 });
});

test("planInsertion replaces the token and appends a separator", () => {
  assert.deepEqual(planInsertion("1girl, blue_h", 13, "blue_hair"), {
    start: 7,
    end: 13,
    replacement: "blue_hair, ",
    text: "1girl, blue_hair, ",
    caret: 18,
  });
});

test("planInsertion keeps an existing separator and its spacing", () => {
  assert.deepEqual(planInsertion("1girl, blue_h, solo", 13, "blue_hair"), {
    start: 7,
    end: 13,
    replacement: "blue_hair",
    text: "1girl, blue_hair, solo",
    caret: 16,
  });
  assert.deepEqual(planInsertion("1girl, blue_h , x", 13, "blue_hair"), {
    start: 7,
    end: 13,
    replacement: "blue_hair",
    text: "1girl, blue_hair , x",
    caret: 16,
  });
});

test("planInsertion replaces a token in the middle of the text", () => {
  assert.deepEqual(planInsertion("blue_hair, 1girl", 4, "blue"), {
    start: 0,
    end: 9,
    replacement: "blue",
    text: "blue, 1girl",
    caret: 4,
  });
});

test("planInsertion maps underscores to spaces when asked", () => {
  assert.deepEqual(planInsertion("blue_h", 6, "blue_hair", { replaceUnderscores: true }), {
    start: 0,
    end: 6,
    replacement: "blue hair, ",
    text: "blue hair, ",
    caret: 11,
  });
});

test("planInsertion on an empty field produces a trailing separator and caret", () => {
  assert.deepEqual(planInsertion("", 0, "1girl"), {
    start: 0,
    end: 0,
    replacement: "1girl, ",
    text: "1girl, ",
    caret: 7,
  });
});

test("planInsertion replaces the whole run a caret sits inside", () => {
  assert.deepEqual(planInsertion("1girl, blue_h x", 13, "blue_hair"), {
    start: 7,
    end: 15,
    replacement: "blue_hair, ",
    text: "1girl, blue_hair, ",
    caret: 18,
  });
});

test("planInsertion keeps a space that follows the range", () => {
  assert.deepEqual(planInsertion("1girl, blue_h , x", 13, "blue_hair").text, "1girl, blue_hair , x");
});

test("planInsertion's own text and caret agree with splicing its replacement", () => {
  const cases = [
    ["1girl, blue_h", 13, "blue_hair"],
    ["1girl, blue_h, solo", 13, "blue_hair"],
    ["blue_hair, 1girl", 4, "blue"],
    ["", 0, "1girl"],
  ];
  for (const [text, caret, replacement] of cases) {
    const plan = planInsertion(text, caret, replacement);
    assert.equal(plan.text, text.slice(0, plan.start) + plan.replacement + text.slice(plan.end));
    assert.equal(plan.caret, plan.start + plan.replacement.length);
  }
});
