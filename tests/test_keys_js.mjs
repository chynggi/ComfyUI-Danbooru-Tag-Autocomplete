import assert from "node:assert/strict";
import test from "node:test";

import {
  ACTION_ACCEPT,
  ACTION_CLOSE,
  ACTION_DOWN,
  ACTION_IGNORE,
  ACTION_PAGE_DOWN,
  ACTION_PAGE_UP,
  ACTION_UP,
  keyAction,
  nextIndex,
} from "../web/keys.js";

const OPEN = { open: true, insertOnTab: true, insertOnEnter: false };

test("a closed dropdown ignores every key", () => {
  const closed = { open: false, insertOnTab: true, insertOnEnter: true };
  for (const key of ["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Tab", "Enter", "Escape", "a", ",", " "]) {
    assert.equal(keyAction({ key }, closed), ACTION_IGNORE, key);
  }
});

test("an open dropdown moves the selection", () => {
  assert.equal(keyAction({ key: "ArrowUp" }, OPEN), ACTION_UP);
  assert.equal(keyAction({ key: "ArrowDown" }, OPEN), ACTION_DOWN);
  assert.equal(keyAction({ key: "PageUp" }, OPEN), ACTION_PAGE_UP);
  assert.equal(keyAction({ key: "PageDown" }, OPEN), ACTION_PAGE_DOWN);
});

test("Escape closes an open dropdown", () => {
  assert.equal(keyAction({ key: "Escape" }, OPEN), ACTION_CLOSE);
});

test("Tab accepts only when the setting allows it", () => {
  assert.equal(keyAction({ key: "Tab" }, OPEN), ACTION_ACCEPT);
  assert.equal(keyAction({ key: "Tab" }, { ...OPEN, insertOnTab: false }), ACTION_IGNORE);
});

test("Enter accepts only when the setting allows it and Shift is not held", () => {
  assert.equal(keyAction({ key: "Enter" }, { ...OPEN, insertOnEnter: true }), ACTION_ACCEPT);
  assert.equal(keyAction({ key: "Enter", shiftKey: true }, { ...OPEN, insertOnEnter: true }), ACTION_IGNORE);
  assert.equal(keyAction({ key: "Enter" }, OPEN), ACTION_IGNORE);
});

test("ordinary typing is never intercepted", () => {
  for (const key of ["a", "1", "_", ",", " ", "Backspace", "Delete"]) {
    assert.equal(keyAction({ key }, OPEN), ACTION_IGNORE, key);
  }
});

test("nextIndex wraps on the arrow keys", () => {
  assert.equal(nextIndex(2, ACTION_DOWN, 3), 0);
  assert.equal(nextIndex(0, ACTION_UP, 3), 2);
});

test("nextIndex selects an end of the list when nothing is selected yet", () => {
  assert.equal(nextIndex(-1, ACTION_DOWN, 3), 0);
  assert.equal(nextIndex(-1, ACTION_PAGE_DOWN, 30), 0);
  assert.equal(nextIndex(-1, ACTION_UP, 3), 2);
  assert.equal(nextIndex(-1, ACTION_PAGE_UP, 30), 29);
});

test("nextIndex clamps on the page keys", () => {
  assert.equal(nextIndex(0, ACTION_PAGE_DOWN, 30), 10);
  assert.equal(nextIndex(29, ACTION_PAGE_DOWN, 30), 29);
  assert.equal(nextIndex(0, ACTION_PAGE_UP, 30), 0);
  assert.equal(nextIndex(29, ACTION_PAGE_UP, 30), 19);
});

test("nextIndex returns -1 for an empty list", () => {
  assert.equal(nextIndex(0, ACTION_DOWN, 0), -1);
  assert.equal(nextIndex(-1, ACTION_UP, 0), -1);
});

test("nextIndex leaves the index alone for a non-movement action", () => {
  assert.equal(nextIndex(4, ACTION_ACCEPT, 10), 4);
  assert.equal(nextIndex(4, ACTION_IGNORE, 10), 4);
});
