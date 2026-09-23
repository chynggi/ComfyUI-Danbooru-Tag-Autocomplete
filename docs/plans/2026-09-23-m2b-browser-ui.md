# Danbooru Tag Autocomplete — M2b Browser UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the autocomplete actually appear: typing in any multiline prompt field shows ranked Danbooru tag suggestions, and accepting one inserts the canonical tag without disturbing the text around it.

**Architecture:** The pure logic is separated from the DOM so it can be tested under Node without a browser: `web/insert.js` owns token ranges and insertion planning, `web/keys.js` owns the keyboard decision table, and both are covered by `node --test`. The DOM layers (`web/caret.js`, `web/dropdown.js`) and the ComfyUI entry point (`web/dtautocomplete.js`) sit on top and are verified by the spec's manual checklist.

**Tech Stack:** Browser ES modules, Node 22 (`node --test`, no npm dependencies), ComfyUI 0.37.0 / frontend 1.53.6. No build step, no bundler.

**Spec:** `docs/specs/2026-09-22-tag-autocomplete-design.md` (§9 and §15 in particular)

## Global Constraints

- Project license: MIT.
- Node runtime dependencies: **0** additional in the ComfyUI process; the browser loads no third-party code. `node:test` and `node:assert` only, for the tests.
- Every file under `web/` is loaded by ComfyUI as an extension module: `server.py` globs `EXTENSION_WEB_DIRS` recursively for `**/*.js` and the frontend `import()`s each one. A module with no top-level side effects is therefore required; only `dtautocomplete.js` registers anything.
- Use **`widget.element`** for the textarea. Never `widget.inputEl` — it is a deprecated alias that was `undefined` in frontend 1.40.2 and broke another extension.
- Hook all `ComfyWidgets.STRING` widgets with `multiline: true`, plus a `MutationObserver` fallback for `textarea.comfy-multiline-input`, so Nodes 2.0's Vue path is covered too.
- **When the dropdown is closed, no key is intercepted.** A prompt field must behave exactly as it did before the extension loaded.
- Never let a failure in this extension break the prompt field: a missing database, a failed fetch, a parse error, or an internal exception disables only the dropdown, logs once, and leaves typing untouched.
- The extension's registered `name` is `danbooruTagAutocomplete`; settings ids are `DanbooruTagAutocomplete.*`.
- The search contract is `web/search.js`'s `TagIndex`, which is verified against `tests/fixtures/` by `tests/test_search_js.mjs`. Do not change its behaviour here.
- Commit message style: `Add ...`, `Fix ...`, `Use ...`, `Remove ...` (short, imperative).
- Never modify files outside this repo folder.

## File Structure

| File | Responsibility |
|---|---|
| `web/insert.js` (create) | Pure token range and insertion planning; no DOM |
| `web/keys.js` (create) | Pure keyboard decision table and selection movement; no DOM |
| `web/caret.js` (create) | Caret pixel coordinates inside a textarea, via a mirror element |
| `web/dropdown.js` (create) | The suggestion list: DOM, selection state, mouse and keyboard dispatch |
| `web/dtautocomplete.js` (create) | ComfyUI entry point: widget hook, observer, settings, database loading, status UX |
| `tests/test_insert_js.mjs` (create) | Node tests for `web/insert.js` |
| `tests/test_keys_js.mjs` (create) | Node tests for `web/keys.js` |
| `tests/test_web_assets.mjs` (create) | Every `web/*.js` parses and imports only paths that exist |
| `README.md` (modify) | The manual browser checklist and how to use custom tags |

M2a is complete and merged: `web/search.js`, the `/danbooru-tag-autocomplete/*` routes and
`tests/fixtures/` are all in place and are consumed here.

---

### Task 1: Insertion planning

**Files:**
- Create: `web/insert.js`
- Create: `tests/test_insert_js.mjs`

**Interfaces:**
- Consumes: nothing.
- Produces (ES module exports):
  - `TOKEN_SEPARATORS: Set<string>` — `,`, `\n`, `;`
  - `tokenRange(text: string, caret: number) -> { start: number, end: number }` — the range to replace: from just after the last separator before the caret, to the next separator at or after the caret, with trailing whitespace trimmed but never past the caret
  - `planInsertion(text: string, caret: number, replacement: string, options?: { replaceUnderscores?: boolean }) -> { start: number, end: number, replacement: string, text: string, caret: number }` — `replacement` is the string to insert, including any `", "` suffix; `text` and `caret` are the whole updated value and the caret position after it

**Semantics.** The token under the caret is the whole separator-delimited run containing it,
because a Danbooru tag may contain spaces (`blue hair` normalizes to `blue_hair`), so
`tokenRange` must agree with `extractToken` in `web/search.js` about where a token begins and
ends. Whitespace immediately after a separator is **not** part of the token, so it survives the
replacement: `"1girl, blue_h"` yields the range `[7, 13)`, keeping the space after the comma.
Trailing whitespace is trimmed from the end of the range, so `"1girl, blue_h , x"` keeps its
space before the comma.

Accepting therefore replaces the entire run containing the caret, which is what A1111's
tagcomplete does. A caret in the middle of a run replaces that whole run — the common case is a
caret at the end of what the user just typed, and a malformed run like `"1girl, blue_h x"` is
replaced as one token rather than partially.

A `", "` suffix is appended unless the character after the range is already a separator or
whitespace, so `"1girl, blue_h"` becomes `"1girl, blue_hair, "`, `"1girl, blue_h, x"` becomes
`"1girl, blue_hair, x"` with its existing comma untouched, and an empty field becomes
`"1girl, "` rather than `"1girl"`.

The range and the inserted string are returned alongside the whole updated value so the
caller can either assign `text` or select `[start, end)` and insert `replacement`, which is
what keeps the change on the browser's undo stack.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_insert_js.mjs`:

```javascript
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
    ["1girl, blue_h , x", 13, "blue_hair"],
    ["1girl, blue_h x", 13, "blue_hair"],
    ["blue_hair, 1girl", 4, "blue"],
    ["", 0, "1girl"],
  ];
  for (const [text, caret, replacement] of cases) {
    const plan = planInsertion(text, caret, replacement);
    assert.equal(plan.text, text.slice(0, plan.start) + plan.replacement + text.slice(plan.end));
    assert.equal(plan.caret, plan.start + plan.replacement.length);
  }
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `node --test tests/test_insert_js.mjs`
Expected: FAIL with `Cannot find module .../web/insert.js`

- [ ] **Step 3: Write `web/insert.js`**

```javascript
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `node --test tests/test_insert_js.mjs`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add web/insert.js tests/test_insert_js.mjs
git commit -m "Add prompt insertion planning"
```

---

### Task 2: Keyboard decisions

**Files:**
- Create: `web/keys.js`
- Create: `tests/test_keys_js.mjs`

**Interfaces:**
- Consumes: nothing.
- Produces (ES module exports):
  - `ACTION_IGNORE`, `ACTION_UP`, `ACTION_DOWN`, `ACTION_PAGE_UP`, `ACTION_PAGE_DOWN`, `ACTION_ACCEPT`, `ACTION_CLOSE` — string constants
  - `keyAction(event: { key: string, shiftKey?: boolean }, state: { open: boolean, insertOnTab: boolean, insertOnEnter: boolean }) -> string`
  - `nextIndex(index: number, action: string, count: number, page?: number) -> number` — `page` defaults to 10

**Semantics.** `keyAction` returns `ACTION_IGNORE` for everything while the dropdown is
closed. That is the whole point of the module: the spec's hard requirement is that a prompt
field behaves exactly as it did before the extension loaded, and making the decision a pure
function is what lets it be tested rather than hoped for.

`Escape` closes; `Tab` and `Enter` accept only when their settings allow it, and `Enter`
accepts only without Shift so multi-line prompts still work.

`nextIndex` wraps around on the arrow keys and clamps on page keys, and returns `-1` for an
empty list. When nothing is selected yet (`index < 0`) the first movement selects an end of the
list — the first row for a downward action, the last row for an upward one — which is the
standard listbox behaviour. That case is defined here rather than left to the caller because
`nextIndex` is a pure function and an undefined input is a defect waiting to happen; it is also
pinned by its own test.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_keys_js.mjs`:

```javascript
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `node --test tests/test_keys_js.mjs`
Expected: FAIL with `Cannot find module .../web/keys.js`

- [ ] **Step 3: Write `web/keys.js`**

```javascript
// Pure keyboard decisions for the suggestion list. No DOM, so tests/test_keys_js.mjs can
// exercise them under Node.
//
// The invariant this module exists to make testable: while the list is closed, nothing is
// intercepted, so a prompt field behaves exactly as it did before the extension loaded.

export const ACTION_IGNORE = "ignore";
export const ACTION_UP = "up";
export const ACTION_DOWN = "down";
export const ACTION_PAGE_UP = "pageUp";
export const ACTION_PAGE_DOWN = "pageDown";
export const ACTION_ACCEPT = "accept";
export const ACTION_CLOSE = "close";

const PAGE_SIZE = 10;

export function keyAction(event, { open, insertOnTab, insertOnEnter }) {
  if (!open) {
    return ACTION_IGNORE;
  }
  switch (event.key) {
    case "ArrowUp":
      return ACTION_UP;
    case "ArrowDown":
      return ACTION_DOWN;
    case "PageUp":
      return ACTION_PAGE_UP;
    case "PageDown":
      return ACTION_PAGE_DOWN;
    case "Escape":
      return ACTION_CLOSE;
    case "Tab":
      return insertOnTab ? ACTION_ACCEPT : ACTION_IGNORE;
    case "Enter":
      return insertOnEnter && !event.shiftKey ? ACTION_ACCEPT : ACTION_IGNORE;
    default:
      return ACTION_IGNORE;
  }
}

export function nextIndex(index, action, count, page = PAGE_SIZE) {
  if (count <= 0) {
    return -1;
  }
  if (index < 0) {
    // Nothing is selected yet: the first movement selects an end of the list.
    switch (action) {
      case ACTION_DOWN:
      case ACTION_PAGE_DOWN:
        return 0;
      case ACTION_UP:
      case ACTION_PAGE_UP:
        return count - 1;
      default:
        return index;
    }
  }
  switch (action) {
    case ACTION_DOWN:
      return (index + 1) % count;
    case ACTION_UP:
      return (index - 1 + count) % count;
    case ACTION_PAGE_DOWN:
      return Math.min(count - 1, index + page);
    case ACTION_PAGE_UP:
      return Math.max(0, index - page);
    default:
      return index;
  }
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `node --test tests/test_keys_js.mjs`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add web/keys.js tests/test_keys_js.mjs
git commit -m "Add suggestion list keyboard decisions"
```

---

### Task 3: Caret coordinates

**Files:**
- Create: `web/caret.js`

**Interfaces:**
- Consumes: nothing.
- Produces (ES module exports):
  - `caretCoordinates(textarea: HTMLTextAreaElement, position: number) -> { top: number, left: number, height: number }` — page coordinates of the caret at character `position`
  - `mirrorFor(textarea) -> HTMLDivElement` — the hidden mirror element, created once per textarea

**Why a mirror rather than a public API.** Browsers expose no way to ask a textarea where a
character is. The established technique, used by the MIT-licensed `textarea-caret-position`
and already ported by other ComfyUI extensions, is to clone the textarea's box and typography
into a hidden `div`, insert the text up to the position plus a marker span, and read the
marker's offset. That is what this module does.

**Verification is manual.** This module reads computed styles and lays out a real element, so
it cannot run under `node --test` without a DOM implementation, and this project takes no npm
dependencies. Its correctness is checked by the manual checklist in Task 6 — specifically the
first and last lines of a wrapped paragraph, where an off-by-one is visible.

- [ ] **Step 1: Write `web/caret.js`**

```javascript
// Caret pixel coordinates inside a textarea, via a hidden mirror element.
//
// The browser offers no API for this. The mirror technique: clone the textarea's box and
// typography into an off-screen div, put the text up to the caret in it followed by a marker
// span, and read the marker's offset. Ported from the MIT-licensed textarea-caret-position,
// which other ComfyUI extensions also port.

const MIRROR_PROPERTIES = [
  "boxSizing",
  "width",
  "height",
  "overflowX",
  "overflowY",
  "borderTopWidth",
  "borderRightWidth",
  "borderBottomWidth",
  "borderLeftWidth",
  "borderStyle",
  "paddingTop",
  "paddingRight",
  "paddingBottom",
  "paddingLeft",
  "fontStyle",
  "fontVariant",
  "fontWeight",
  "fontStretch",
  "fontSize",
  "fontSizeAdjust",
  "lineHeight",
  "fontFamily",
  "textAlign",
  "textTransform",
  "textIndent",
  "textDecoration",
  "letterSpacing",
  "wordSpacing",
  "tabSize",
  "MozTabSize",
];

function createMirror() {
  const mirror = document.createElement("div");
  mirror.setAttribute("aria-hidden", "true");
  const style = mirror.style;
  style.position = "absolute";
  style.top = "0";
  style.left = "0";
  style.visibility = "hidden";
  style.whiteSpace = "pre-wrap";
  style.wordWrap = "break-word";
  style.overflow = "hidden";
  document.body.appendChild(mirror);
  return mirror;
}

const mirrors = new WeakMap();

export function mirrorFor(textarea) {
  let mirror = mirrors.get(textarea);
  if (mirror === undefined || !mirror.isConnected) {
    mirror = createMirror();
    mirrors.set(textarea, mirror);
  }
  return mirror;
}

function copyStyles(textarea, mirror) {
  const computed = window.getComputedStyle(textarea);
  const style = mirror.style;
  for (const property of MIRROR_PROPERTIES) {
    style[property] = computed[property];
  }
  style.position = "absolute";
  style.visibility = "hidden";
  style.whiteSpace = "pre-wrap";
  style.wordWrap = "break-word";
  style.overflow = "hidden";
}

export function caretCoordinates(textarea, position) {
  const mirror = mirrorFor(textarea);
  copyStyles(textarea, mirror);

  const value = textarea.value;
  const stop = Math.max(0, Math.min(position, value.length));
  mirror.textContent = value.slice(0, stop);

  const marker = document.createElement("span");
  marker.textContent = value.slice(stop) || ".";
  mirror.appendChild(marker);

  // The mirror sits at the page origin, so the marker's offset is relative to the textarea's
  // text flow. The dropdown is an absolutely positioned child of body, so it needs page
  // coordinates: add the textarea's own position and subtract the text it has scrolled past.
  const rect = textarea.getBoundingClientRect();
  const coordinates = {
    top: rect.top + window.scrollY + marker.offsetTop + parseInt(mirror.style.borderTopWidth || "0", 10) - textarea.scrollTop,
    left: rect.left + window.scrollX + marker.offsetLeft + parseInt(mirror.style.borderLeftWidth || "0", 10) - textarea.scrollLeft,
    height: parseInt(mirror.style.lineHeight, 10) || marker.offsetHeight,
  };
  marker.remove();
  // A mirror is a permanent child of body, and a textarea the user deletes cannot take its
  // mirror with it. Clear the text so an abandoned mirror keeps no copy of the prompt; the
  // styles are recomputed on every call, so nothing is lost by clearing it here.
  mirror.textContent = "";
  return coordinates;
}
```

- [ ] **Step 2: Verify it parses and exports what the dropdown will use**

Run:
```bash
node -e "import('./web/caret.js').then((m) => console.log(Object.keys(m).sort().join(',')))"
```
Expected: `caretCoordinates,mirrorFor`

- [ ] **Step 3: Commit**

```bash
git add web/caret.js
git commit -m "Add caret coordinate helper"
```

---

### Task 4: The suggestion list

**Files:**
- Create: `web/dropdown.js`
- Create: `tests/test_dropdown_js.mjs`

**Interfaces:**
- Consumes: `web/caret.js`'s `caretCoordinates`, `web/keys.js`'s actions and `keyAction`/`nextIndex`
- Produces (ES module exports):
  - `CATEGORY_LABELS: Record<number, string>` — `{0: "general", 1: "artist", 3: "copyright", 4: "character", 5: "meta"}`
  - `CATEGORY_COLORS: Record<number, string>` — one colour per category, used for the badge
  - `formatPostCount(count: number) -> string` — `1200000` → `"1.2M"`, `82000` → `"82K"`
  - `class TagDropdown`:
    - `new TagDropdown(textarea, { onAccept })` — `onAccept(hit)` is called with the chosen hit; the owner performs the insertion
    - `get isOpen() -> boolean`
    - `show(hits, options?: { showPostCount?: boolean }) -> void` — renders `hits` and positions against the caret; an empty list hides
    - `hide() -> void`
    - `handleKey(event, settings) -> boolean` — returns `true` when the event was consumed, in which case the caller must `preventDefault()`
    - `accept() -> void` — calls `onAccept` with the highlighted hit, or does nothing when nothing is highlighted

**Verification.** Unlike `caret.js`, this module's logic is verified automatically. A
hand-written DOM stub in `tests/test_dropdown_js.mjs` is faithful for everything `TagDropdown`
uses — an element tree, `classList`, `textContent`, event listeners — and fake for layout, where
every metric is zero. So the tests pin which row is highlighted, which hit an accept reports, and
that a closed list consumes nothing, while the geometry stays on the manual checklist. The stub
costs no dependency; a hand-rolled stub that cannot lay anything out still catches the failure
this module is most likely to have, and it is the only way to test any of this without one.

**Behaviour.** The list is a `div.dtautocomplete` appended to `document.body`, so a node's
canvas ancestry cannot clip it, and it is positioned in page coordinates from
`caretCoordinates`. Each row shows the tag, a category badge, the post count and, when the
match came through an alias, `alias → canonical`. The highlighted row is marked with a class
and scrolled into view.

`handleKey` returns `false` for everything that is not a movement, accept or close action, so
typing, backspace, space and comma are untouched. That is the caller's cue to leave the event
alone.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dropdown_js.mjs`:

```javascript
// Verifies the suggestion list's state machine: what opens and closes it, which row is
// highlighted, and which hit an accept reports.
//
// The DOM here is a hand-written stub, not a browser. It is faithful for the parts TagDropdown
// actually uses — an element tree with `children`, `classList` transition recording,
// `textContent` and event listeners — and deliberately fake for layout: every metric is zero, so
// nothing about size, position or the textarea's own geometry is verified here. The stub is also
// fake for anything the module never does to an element, such as removing one.
//
// The stub exists because the project takes no npm dependencies, so there is no jsdom to drive a
// real textarea. A stub that cannot lay anything out is still enough to catch the failure this
// module is most likely to have: accepting or moving to the wrong row.

import assert from "node:assert/strict";
import test from "node:test";

function makeNode(tagName) {
  const node = {
    tagName,
    style: {},
    className: "",
    hidden: false,
    isConnected: true,
    offsetTop: 0,
    offsetLeft: 0,
    offsetHeight: 0,
    children: [],
    listeners: {},
    textContent: "",
    setAttribute() {},
    remove() {
      node.isConnected = false;
    },
    appendChild(child) {
      node.children.push(child);
      return child;
    },
    replaceChildren() {
      node.children = [];
    },
    addEventListener(type, handler) {
      (node.listeners[type] ??= []).push(handler);
    },
    classList: {
      classes: new Set(),
      toggle(name, on) {
        if (on) {
          node.classList.classes.add(name);
        } else {
          node.classList.classes.delete(name);
        }
      },
      contains(name) {
        return node.classList.classes.has(name);
      },
    },
    scrollIntoView() {},
    fire(type, event = {}) {
      for (const handler of node.listeners[type] ?? []) {
        handler(event);
      }
    },
  };
  return node;
}

globalThis.document = {
  createElement: (tagName) => makeNode(tagName),
  body: { appendChild() {} },
  getElementById: () => null,
};
globalThis.window = {
  getComputedStyle: () => new Proxy({}, { get: () => "0px" }),
  scrollX: 0,
  scrollY: 0,
};

const { TagDropdown, formatPostCount } = await import("../web/dropdown.js");

const HITS = [
  { name: "blue_hair", alias: null, category: 0, postCount: 1200000, deprecated: false },
  { name: "blue_archive", alias: "blue_arc", category: 3, postCount: 82000, deprecated: false },
  { name: "blue_eyes", alias: null, category: 0, postCount: 400, deprecated: true },
];

const OPEN = { insertOnTab: true, insertOnEnter: false };

function makeTextarea() {
  return {
    tagName: "TEXTAREA",
    value: "1girl, blue_h",
    selectionStart: 13,
    selectionEnd: 13,
    scrollTop: 0,
    scrollLeft: 0,
    getBoundingClientRect: () => ({ top: 10, left: 20 }),
  };
}

function makeDropdown() {
  const accepted = [];
  const dropdown = new TagDropdown(makeTextarea(), { onAccept: (hit) => accepted.push(hit.name) });
  return { dropdown, accepted };
}

test("the list is closed until hits arrive", () => {
  const { dropdown } = makeDropdown();
  assert.equal(dropdown.isOpen, false);
  dropdown.show([]);
  assert.equal(dropdown.isOpen, false);
  dropdown.show(HITS);
  assert.equal(dropdown.isOpen, true);
  assert.equal(dropdown.index, 0);
  assert.equal(dropdown.selected.name, "blue_hair");
});

test("arrows move the highlight and wrap at the ends", () => {
  const { dropdown } = makeDropdown();
  dropdown.show(HITS);
  assert.equal(dropdown.handleKey({ key: "ArrowDown" }, OPEN), true);
  assert.equal(dropdown.index, 1);
  dropdown.handleKey({ key: "ArrowDown" }, OPEN);
  dropdown.handleKey({ key: "ArrowDown" }, OPEN);
  assert.equal(dropdown.index, 0);
  dropdown.handleKey({ key: "ArrowUp" }, OPEN);
  assert.equal(dropdown.index, 2);
});

test("page keys clamp instead of wrapping", () => {
  const { dropdown } = makeDropdown();
  dropdown.show(HITS);
  assert.equal(dropdown.handleKey({ key: "PageDown" }, OPEN), true);
  assert.equal(dropdown.index, HITS.length - 1);
  dropdown.handleKey({ key: "PageUp" }, OPEN);
  assert.equal(dropdown.index, 0);
});

test("typing and separators are never consumed", () => {
  const { dropdown } = makeDropdown();
  dropdown.show(HITS);
  for (const key of ["a", "1", "_", ",", " ", "Backspace", "Delete"]) {
    assert.equal(dropdown.handleKey({ key }, OPEN), false, key);
  }
});

test("a closed list consumes no key at all", () => {
  const { dropdown } = makeDropdown();
  for (const key of ["ArrowDown", "ArrowUp", "PageDown", "PageUp", "Tab", "Enter", "Escape", "a"]) {
    assert.equal(dropdown.handleKey({ key }, { insertOnTab: true, insertOnEnter: true }), false, key);
  }
});

test("Tab accepts the highlighted hit and closes the list", () => {
  const { dropdown, accepted } = makeDropdown();
  dropdown.show(HITS);
  dropdown.handleKey({ key: "ArrowDown" }, OPEN);
  assert.equal(dropdown.handleKey({ key: "Tab" }, OPEN), true);
  assert.deepEqual(accepted, ["blue_archive"]);
  assert.equal(dropdown.isOpen, false);
});

test("Enter accepts only when it is allowed and Shift is not held", () => {
  const { dropdown, accepted } = makeDropdown();
  dropdown.show(HITS);
  assert.equal(dropdown.handleKey({ key: "Enter" }, { insertOnTab: false, insertOnEnter: true }), true);
  assert.deepEqual(accepted, ["blue_hair"]);
  dropdown.show(HITS);
  assert.equal(dropdown.handleKey({ key: "Enter", shiftKey: true }, { insertOnTab: false, insertOnEnter: true }), false);
  assert.deepEqual(accepted, ["blue_hair"]);
  dropdown.show(HITS);
  assert.equal(dropdown.handleKey({ key: "Enter" }, OPEN), false);
});

test("Escape closes without accepting", () => {
  const { dropdown, accepted } = makeDropdown();
  dropdown.show(HITS);
  assert.equal(dropdown.handleKey({ key: "Escape" }, OPEN), true);
  assert.equal(dropdown.isOpen, false);
  assert.deepEqual(accepted, []);
});

test("clicking a row accepts that row", () => {
  const { dropdown, accepted } = makeDropdown();
  dropdown.show(HITS);
  assert.equal(dropdown.index, 0);
  dropdown.element.children[1].fire("mousedown", { preventDefault() {} });
  assert.deepEqual(accepted, ["blue_archive"]);
});

test("hide clears the rendered rows", () => {
  const { dropdown } = makeDropdown();
  dropdown.show(HITS);
  assert.equal(dropdown.element.children.length, HITS.length);
  dropdown.hide();
  assert.equal(dropdown.element.children.length, 0);
  assert.equal(dropdown.isOpen, false);
});

test("showPostCount controls the post-count cell", () => {
  const { dropdown } = makeDropdown();
  dropdown.show(HITS, { showPostCount: false });
  assert.equal(dropdown.element.children[0].children.length, 2);
  dropdown.show(HITS, { showPostCount: true });
  assert.equal(dropdown.element.children[0].children.length, 3);
});

test("only the highlighted row is marked active", () => {
  const { dropdown } = makeDropdown();
  dropdown.show(HITS);
  assert.equal(dropdown.element.children[0].classList.contains("dtautocomplete-row-active"), true);
  assert.equal(dropdown.element.children[1].classList.contains("dtautocomplete-row-active"), false);
  dropdown.handleKey({ key: "ArrowDown" }, OPEN);
  assert.equal(dropdown.element.children[0].classList.contains("dtautocomplete-row-active"), false);
  assert.equal(dropdown.element.children[1].classList.contains("dtautocomplete-row-active"), true);
});

test("hovering a row highlights that row", () => {
  const { dropdown } = makeDropdown();
  dropdown.show(HITS);
  dropdown.element.children[2].fire("mouseenter");
  assert.equal(dropdown.index, 2);
  assert.equal(dropdown.element.children[2].classList.contains("dtautocomplete-row-active"), true);
});

test("accepting with nothing highlighted reports nothing", () => {
  const { dropdown, accepted } = makeDropdown();
  dropdown.show([]);
  dropdown.accept();
  assert.deepEqual(accepted, []);
});

test("formatPostCount rounds at its boundaries", () => {
  assert.equal(formatPostCount(0), "0");
  assert.equal(formatPostCount(999), "999");
  assert.equal(formatPostCount(1000), "1K");
  assert.equal(formatPostCount(82000), "82K");
  assert.equal(formatPostCount(1200000), "1.2M");
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `node --test tests/test_dropdown_js.mjs`
Expected: FAIL with `Cannot find module .../web/dropdown.js`

- [ ] **Step 3: Write `web/dropdown.js`**

```javascript
// The suggestion list: DOM, selection state, and mouse and keyboard dispatch.
//
// The list lives on document.body so no node's canvas ancestor can clip it. Nothing here
// knows how to search or how to edit text; it renders hits and reports the chosen one.

import { caretCoordinates } from "./caret.js";
import {
  ACTION_ACCEPT,
  ACTION_CLOSE,
  ACTION_IGNORE,
  keyAction,
  nextIndex,
} from "./keys.js";

const ROW_CLASS = "dtautocomplete-row";
const ACTIVE_CLASS = "dtautocomplete-row-active";
const MAX_VISIBLE_ROWS = 10;

export const CATEGORY_LABELS = { 0: "general", 1: "artist", 3: "copyright", 4: "character", 5: "meta" };
export const CATEGORY_COLORS = { 0: "#4a7ec4", 1: "#c4554a", 3: "#9a4ac4", 4: "#4a9a6a", 5: "#b8860b" };

export function formatPostCount(count) {
  if (count >= 1_000_000) {
    return `${(count / 1_000_000).toFixed(1)}M`;
  }
  if (count >= 1_000) {
    return `${Math.round(count / 1_000)}K`;
  }
  return String(count);
}

function label(text, color) {
  const span = document.createElement("span");
  span.textContent = text;
  span.style.color = color;
  return span;
}

export class TagDropdown {
  constructor(textarea, { onAccept }) {
    this.textarea = textarea;
    this.onAccept = onAccept;
    this.hits = [];
    this.index = -1;
    this.showPostCount = true;

    this.element = document.createElement("div");
    this.element.className = "dtautocomplete";
    Object.assign(this.element.style, {
      position: "absolute",
      zIndex: "10000",
      minWidth: "12rem",
      maxWidth: "28rem",
      maxHeight: `${MAX_VISIBLE_ROWS * 1.6}rem`,
      overflowY: "auto",
      padding: "0.2rem",
      border: "1px solid #444",
      borderRadius: "0.3rem",
      background: "#1e1e1e",
      color: "#e6e6e6",
      fontFamily: "monospace",
      fontSize: "0.8rem",
      boxShadow: "0 0.3rem 0.8rem rgba(0, 0, 0, 0.5)",
    });
    this.element.hidden = true;
    document.body.appendChild(this.element);
  }

  get isOpen() {
    return !this.element.hidden && this.hits.length > 0;
  }

  get selected() {
    return this.index >= 0 && this.index < this.hits.length ? this.hits[this.index] : null;
  }

  show(hits, { showPostCount = true } = {}) {
    this.hits = hits;
    this.showPostCount = showPostCount;
    this.index = hits.length > 0 ? 0 : -1;
    if (hits.length === 0) {
      this.hide();
      return;
    }
    this.#render();
    this.#position();
    this.element.hidden = false;
  }

  hide() {
    this.element.hidden = true;
    // Drop the rendered rows as well as hiding them: this element is a permanent child of body,
    // so a dropdown whose textarea is later deleted would otherwise keep the last tag list alive.
    // show() renders from scratch, so clearing here costs nothing.
    this.element.replaceChildren();
    this.hits = [];
    this.index = -1;
  }

  accept() {
    const hit = this.selected;
    if (hit === null) {
      return;
    }
    this.hide();
    this.onAccept(hit);
  }

  handleKey(event, settings) {
    const action = keyAction(event, {
      open: this.isOpen,
      insertOnTab: settings.insertOnTab,
      insertOnEnter: settings.insertOnEnter,
    });
    switch (action) {
      case ACTION_ACCEPT:
        this.accept();
        return true;
      case ACTION_CLOSE:
        this.hide();
        return true;
      case ACTION_IGNORE:
        return false;
      default: {
        const next = nextIndex(this.index, action, this.hits.length);
        if (next !== this.index) {
          this.#setIndex(next);
        }
        return true;
      }
    }
  }

  #setIndex(index) {
    this.index = index;
    const rows = this.element.children;
    for (let position = 0; position < rows.length; position += 1) {
      rows[position].classList.toggle(ACTIVE_CLASS, position === index);
      rows[position].style.background = position === index ? "#333" : "transparent";
    }
    const active = rows[index];
    if (active !== undefined && typeof active.scrollIntoView === "function") {
      active.scrollIntoView({ block: "nearest" });
    }
  }

  #render() {
    this.element.replaceChildren();
    this.hits.forEach((hit, position) => {
      const row = document.createElement("div");
      row.className = ROW_CLASS;
      row.style.display = "flex";
      row.style.gap = "0.5rem";
      row.style.alignItems = "center";
      row.style.padding = "0.15rem 0.35rem";
      row.style.cursor = "pointer";
      row.style.whiteSpace = "nowrap";

      const name = document.createElement("span");
      name.textContent = hit.name;
      row.appendChild(name);

      if (hit.alias !== null) {
        row.appendChild(label(`← ${hit.alias}`, "#888"));
      }
      row.appendChild(label(CATEGORY_LABELS[hit.category] ?? String(hit.category), CATEGORY_COLORS[hit.category] ?? "#888"));
      if (this.showPostCount) {
        row.appendChild(label(formatPostCount(hit.postCount), "#777"));
      }
      if (hit.deprecated) {
        row.appendChild(label("deprecated", "#c4554a"));
      }

      row.addEventListener("mouseenter", () => this.#setIndex(position));
      row.addEventListener("mousedown", (event) => {
        event.preventDefault();
        // Do not rely on the mouseenter that usually precedes this: when the list opens
        // under a stationary pointer no mouseenter fires until the pointer moves, and the
        // click would then accept whichever row was highlighted instead of the clicked one.
        this.#setIndex(position);
        this.accept();
      });
      this.element.appendChild(row);
    });
    this.#setIndex(this.index);
  }

  #position() {
    const coordinates = caretCoordinates(this.textarea, this.textarea.selectionStart);
    this.element.style.top = `${coordinates.top + coordinates.height}px`;
    this.element.style.left = `${coordinates.left}px`;
  }
}
```

- [ ] **Step 4: Run them to verify they pass**

Run: `node --test tests/test_dropdown_js.mjs`
Expected: PASS (15 tests)

- [ ] **Step 5: Prove the tests are not vacuous**

The `clicking a row accepts that row` case exists because a click must not depend on a preceding
`mouseenter`. Delete the `this.#setIndex(position);` line from inside the row's `mousedown`
handler and confirm that test fails, then put it back.

Run: `node --test tests/test_dropdown_js.mjs`
Expected with the line removed: the `clicking a row accepts that row` case fails, 14 passed / 1 failed
Expected with the line restored: PASS (15 tests)

- [ ] **Step 6: Verify it parses**

Run: `node --check web/dropdown.js`
Expected: no output

- [ ] **Step 7: Commit**

```bash
git add web/dropdown.js tests/test_dropdown_js.mjs
git commit -m "Add tag suggestion dropdown"
```

---

### Task 5: The ComfyUI extension entry point

**Files:**
- Create: `web/dtautocomplete.js`

**Interfaces:**
- Consumes: `web/search.js`'s `TagIndex`/`decodeArtifact`/`extractToken`, `web/insert.js`'s `planInsertion`, `web/dropdown.js`'s `TagDropdown`; the `/danbooru-tag-autocomplete/{status,db,custom}` routes
- Produces:
  - `SETTING_IDS: Record<string, string>` — the eight setting ids
  - the registered extension `name` is `danbooruTagAutocomplete`
  - side effects on import: the widget hook, the observer, and the settings registration

`CategoryFilter` is a native single-value combo (`all` plus one entry per category) rather than
a multi-select, because this plan cannot verify the frontend's multi-select combo shape and a
guess would render as a broken control. Filtering to one category at a time is enough for the
intended use; a multi-select, if the frontend supports one, is a later change.

**Settings** (all under `DanbooruTagAutocomplete.`): `Enabled` (true), `SuggestionCount` (32),
`InsertOnTab` (true), `InsertOnEnter` (false), `ReplaceUnderscores` (false), `ShowPostCount`
(true), `CategoryFilter` (`all`), `ForceEnableWithOtherAutocomplete` (false).

**Loading and failure.** The spec separates two cases and so does this file. When the database is
unavailable — `/status` reports `missing` or `error`, or a download never finishes within sixty
seconds — the user gets a visible, dismissible banner plus a console log, and the autocomplete
stays off. That is `setup`'s catch. Any other exception — while building the index, or
while searching for a suggestion later — disables only the dropdown and logs once, without
showing a banner: the field keeps working, the user is not interrupted mid-keystroke, and the
condition is not one they could act on. That is `refresh`'s catch and the non-`status` half of
`setup`'s.

The spec asks for a toast in the first case. This file uses a small element it owns, because a
toast API is version-dependent and this plan cannot verify one, while a banner always renders.

**Never break the field.** If loading fails, if a search throws, or if the extension is
disabled, the textarea keeps working and simply has no suggestions. The widget hook, the
observer and the key handler all swallow their own errors.

**Coexistence.** If another registered extension's name contains `autocompleter`, this one
disables itself unless `ForceEnableWithOtherAutocomplete` is set.

- [ ] **Step 1: Write `web/dtautocomplete.js`**

```javascript
// ComfyUI entry point: hook every multiline prompt field and show tag suggestions in it.
//
// Everything here is defensive by design. A prompt field must behave exactly as it did
// before this extension loaded, so every failure path disables the suggestions and leaves
// typing untouched.

import { app } from "../../scripts/app.js";
import { ComfyWidgets } from "../../scripts/widgets.js";
import { TagDropdown } from "./dropdown.js";
import { planInsertion } from "./insert.js";
import { TagIndex, decodeArtifact, extractToken } from "./search.js";

const EXTENSION_NAME = "danbooruTagAutocomplete";
const PREFIX = "DanbooruTagAutocomplete";
const ROUTES = "/danbooru-tag-autocomplete";
const POLL_INTERVAL_MS = 2000;
const POLL_ATTEMPTS = 30;
const BANNER_ID = "dtautocomplete-status";

export const SETTING_IDS = {
  enabled: `${PREFIX}.Enabled`,
  suggestionCount: `${PREFIX}.SuggestionCount`,
  insertOnTab: `${PREFIX}.InsertOnTab`,
  insertOnEnter: `${PREFIX}.InsertOnEnter`,
  replaceUnderscores: `${PREFIX}.ReplaceUnderscores`,
  showPostCount: `${PREFIX}.ShowPostCount`,
  categoryFilter: `${PREFIX}.CategoryFilter`,
  forceEnable: `${PREFIX}.ForceEnableWithOtherAutocomplete`,
};

const CATEGORY_CHOICES = {
  general: 0,
  artist: 1,
  copyright: 3,
  character: 4,
  meta: 5,
};

let index = null;
let ready = false;
const warned = new Set();

function setting(id, fallback) {
  try {
    const value = app.extensionManager?.setting?.get?.(id);
    return value === undefined ? fallback : value;
  } catch (error) {
    return fallback;
  }
}

function settings() {
  const chosen = setting(SETTING_IDS.categoryFilter, "all");
  const value = CATEGORY_CHOICES[chosen];
  return {
    enabled: setting(SETTING_IDS.enabled, true),
    suggestionCount: setting(SETTING_IDS.suggestionCount, 32),
    insertOnTab: setting(SETTING_IDS.insertOnTab, true),
    insertOnEnter: setting(SETTING_IDS.insertOnEnter, false),
    replaceUnderscores: setting(SETTING_IDS.replaceUnderscores, false),
    showPostCount: setting(SETTING_IDS.showPostCount, true),
    categories: value === undefined ? null : new Set([value]),
  };
}

function showBanner(message) {
  let banner = document.getElementById(BANNER_ID);
  if (banner === null) {
    banner = document.createElement("div");
    banner.id = BANNER_ID;
    Object.assign(banner.style, {
      position: "fixed",
      bottom: "0.75rem",
      left: "50%",
      transform: "translateX(-50%)",
      zIndex: "10001",
      maxWidth: "40rem",
      padding: "0.5rem 0.9rem",
      border: "1px solid #c4554a",
      borderRadius: "0.3rem",
      background: "#2a1e1e",
      color: "#f0d0cc",
      fontFamily: "monospace",
      fontSize: "0.8rem",
      cursor: "pointer",
    });
    banner.addEventListener("click", () => banner.remove());
    document.body.appendChild(banner);
  }
  banner.textContent = `${message} (click to dismiss)`;
}

// One flag per path, not one for the whole file: a problem attaching to a widget must not
// consume the only warning a later search failure would have produced.
function warn(key, message, error) {
  if (warned.has(key)) {
    return;
  }
  warned.add(key);
  console.warn(`[${EXTENSION_NAME}] ${message}`, error ?? "");
}

async function fetchStatus() {
  const response = await fetch(`${ROUTES}/status`);
  if (!response.ok) {
    throw new Error(`status ${response.status}`);
  }
  return response.json();
}

async function fetchArtifact() {
  const response = await fetch(`${ROUTES}/db`);
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.error ?? `db ${response.status}`);
  }
  return decodeArtifact(await response.arrayBuffer());
}

async function fetchCustom(main) {
  const response = await fetch(`${ROUTES}/custom`);
  if (!response.ok) {
    return new TagIndex(main);
  }
  const payload = await response.json();
  for (const warning of payload.warnings ?? []) {
    console.warn(`[${EXTENSION_NAME}] ${warning}`);
  }
  if (!payload.available || payload.bytes === null) {
    return new TagIndex(main);
  }
  const binary = atob(payload.bytes);
  const bytes = new Uint8Array(binary.length);
  for (let position = 0; position < binary.length; position += 1) {
    bytes[position] = binary.charCodeAt(position);
  }
  return new TagIndex(main, decodeArtifact(bytes.buffer));
}

async function loadIndex() {
  let status = await fetchStatus();
  for (let attempt = 0; status.state === "downloading" && attempt < POLL_ATTEMPTS; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
    status = await fetchStatus();
  }
  if (status.state !== "ready") {
    const error = new Error(status.error ?? status.state ?? "the tag database is unavailable");
    error.databaseUnavailable = true;
    throw error;
  }
  return fetchCustom(await fetchArtifact());
}

function otherAutocompletePresent() {
  const extensions = app.extensions;
  if (!extensions) {
    return false;
  }
  const names = typeof extensions.keys === "function" ? [...extensions.keys()] : Object.keys(extensions);
  return names.some((name) => name !== EXTENSION_NAME && name.toLowerCase().includes("autocompleter"));
}

function attach(widget) {
  const textarea = widget?.element;
  if (!textarea || textarea.tagName !== "TEXTAREA" || textarea.dataset.dtaAttached === "1") {
    return null;
  }
  textarea.dataset.dtaAttached = "1";

  const dropdown = new TagDropdown(textarea, {
    onAccept: (hit) => {
      const current = settings();
      const plan = planInsertion(textarea.value, textarea.selectionStart, hit.name, {
        replaceUnderscores: current.replaceUnderscores,
      });
      // Select the range and insert, rather than assigning the whole value, so the browser's
      // undo stack keeps the change. execCommand is deprecated but is still the only way to
      // insert into a textarea as a native edit; when it is unavailable, setRangeText plus an
      // input event is the fallback the spec calls for.
      textarea.focus();
      textarea.setSelectionRange(plan.start, plan.end);
      let inserted = false;
      try {
        inserted = document.execCommand("insertText", false, plan.replacement);
      } catch (error) {
        inserted = false;
      }
      if (!inserted) {
        textarea.setRangeText(plan.replacement, plan.start, plan.end, "end");
        textarea.dispatchEvent(new InputEvent("input", { bubbles: true }));
      }
      textarea.selectionStart = plan.caret;
      textarea.selectionEnd = plan.caret;
      dropdown.hide();
    },
  });

  const refresh = () => {
    const current = settings();
    if (!ready || !current.enabled) {
      dropdown.hide();
      return;
    }
    try {
      const query = extractToken(textarea.value, textarea.selectionStart);
      if (query.length === 0) {
        dropdown.hide();
        return;
      }
      dropdown.show(index.search(query, { limit: current.suggestionCount, categories: current.categories }), {
        showPostCount: current.showPostCount,
      });
    } catch (error) {
      warn("search", "search failed; suggestions are off", error);
      dropdown.hide();
      ready = false;
    }
  };

  textarea.addEventListener("input", refresh);
  textarea.addEventListener("click", refresh);
  textarea.addEventListener("blur", () => setTimeout(() => dropdown.hide(), 150));
  textarea.addEventListener("keydown", (event) => {
    if (dropdown.handleKey(event, settings())) {
      event.preventDefault();
    }
  });
  return dropdown;
}

function observeTextareas() {
  const observer = new MutationObserver((records) => {
    for (const record of records) {
      for (const node of record.addedNodes) {
        if (node.nodeType !== Node.ELEMENT_NODE) {
          continue;
        }
        if (node.matches?.("textarea.comfy-multiline-input")) {
          attach({ element: node });
        }
        for (const found of node.querySelectorAll?.("textarea.comfy-multiline-input") ?? []) {
          attach({ element: found });
        }
      }
    }
  });
  observer.observe(document.body, { childList: true, subtree: true });
}

app.registerExtension({
  name: EXTENSION_NAME,
  settings: [
    { id: SETTING_IDS.enabled, name: "Enable tag autocomplete", type: "boolean", defaultValue: true },
    { id: SETTING_IDS.suggestionCount, name: "Suggestion count", type: "number", defaultValue: 32, attrs: { min: 1, max: 200 } },
    { id: SETTING_IDS.insertOnTab, name: "Insert with Tab", type: "boolean", defaultValue: true },
    { id: SETTING_IDS.insertOnEnter, name: "Insert with Enter", type: "boolean", defaultValue: false },
    { id: SETTING_IDS.replaceUnderscores, name: "Insert spaces instead of underscores", type: "boolean", defaultValue: false },
    { id: SETTING_IDS.showPostCount, name: "Show post counts", type: "boolean", defaultValue: true },
    { id: SETTING_IDS.categoryFilter, name: "Category to show", type: "combo", defaultValue: "all", options: ["all", ...Object.keys(CATEGORY_CHOICES)] },
    { id: SETTING_IDS.forceEnable, name: "Enable alongside another autocomplete", type: "boolean", defaultValue: false },
  ],
  init() {
    const STRING = ComfyWidgets.STRING;
    ComfyWidgets.STRING = function (node, inputName, inputData) {
      const result = STRING.apply(this, arguments);
      try {
        if (inputData?.[1]?.multiline) {
          attach(result?.widget);
        }
      } catch (error) {
        warn("attach", "could not attach to a prompt field", error);
      }
      return result;
    };
  },
  async setup() {
    try {
      if (otherAutocompletePresent() && !setting(SETTING_IDS.forceEnable, false)) {
        console.info(`[${EXTENSION_NAME}] another autocomplete extension is active; staying off`);
        return;
      }
      index = await loadIndex();
      ready = true;
      observeTextareas();
    } catch (error) {
      // The spec separates the two cases. A database that is missing, errored or not downloaded
      // in time is something the user can act on, so it gets a visible explanation. Any other
      // exception — while building the index or while searching — is only logged, because it is
      // not a condition the user can fix and a banner would not tell them anything useful.
      if (error?.databaseUnavailable === true) {
        showBanner(`Danbooru tag autocomplete is unavailable: ${error.message ?? String(error)}`);
      }
      warn("load", "suggestions are off; the tag database could not be loaded", error);
    }
  },
});
```

- [ ] **Step 2: Verify it parses and every relative import resolves**

Run: `node --check web/dtautocomplete.js`
Expected: no output

- [ ] **Step 3: Commit**

```bash
git add web/dtautocomplete.js
git commit -m "Add autocomplete extension entry point"
```

---

### Task 6: Asset guard and the manual checklist

**Files:**
- Create: `tests/test_web_assets.mjs`
- Modify: `README.md`

**Interfaces:**
- Consumes: every file under `web/`
- Produces: nothing new; this task is the guard for the previous five and the documentation of what only a browser can verify

**Why this shape.** ComfyUI loads every `web/**/*.js` file as an extension module, so a
syntax error or a mistyped relative import in any of them is a real failure that no other test
catches — the DOM-free modules are covered by Node tests, and the DOM ones cannot be exercised
without a browser. A static guard catches the realistic failure cheaply.

The browser behaviour itself is verified by the checklist below, which is the spec's own
approach for the frontend.

- [ ] **Step 1: Write `tests/test_web_assets.mjs`**

```javascript
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { dirname, join, resolve, sep } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const WEB = join(REPO_ROOT, "web");

function webModules() {
  // Model how ComfyUI collects extensions: server.py globs `**/*.js` under the web directory,
  // so a module in a subdirectory is loaded too and has to be checked here.
  return readdirSync(WEB, { recursive: true })
    .filter((name) => name.endsWith(".js"))
    .map((name) => name.split(sep).join("/"))
    .sort();
}

test("every browser module parses", () => {
  const modules = webModules();
  assert.ok(modules.length >= 5, `expected the browser modules, found ${modules.length}`);
  for (const name of modules) {
    execFileSync(process.execPath, ["--check", join(WEB, name)], { stdio: "pipe" });
  }
});

test("every sibling import resolves to a file that exists", () => {
  for (const name of webModules()) {
    const source = readFileSync(join(WEB, name), "utf8");
    for (const match of source.matchAll(/from\s+"(\.[^"]+)"/g)) {
      if (!match[1].startsWith("./")) {
        continue;
      }
      assert.ok(existsSync(resolve(WEB, match[1])), `${name} imports ${match[1]}, which does not exist`);
    }
  }
});

test("the only outside imports are ComfyUI's own frontend modules", () => {
  // `../../scripts/*.js` is not on disk inside this repo: ComfyUI serves the frontend from its
  // own package at /scripts/, and this module is loaded from /extensions/danbooruTagAutocomplete/,
  // so the path is correct at runtime and unresolvable from here. Pin the allowed set to the two
  // modules the extension actually needs, so a typo is caught. The pattern sees a double-quoted
  // relative specifier, which is the only import form this repo uses; a bare package specifier,
  // a side-effect import or a dynamic import would each need their own check.
  const allowed = new Set(["../../scripts/app.js", "../../scripts/widgets.js"]);
  for (const name of webModules()) {
    const source = readFileSync(join(WEB, name), "utf8");
    for (const match of source.matchAll(/from\s+"(\.[^"]+)"/g)) {
      if (match[1].startsWith("./")) {
        continue;
      }
      assert.ok(allowed.has(match[1]), `${name} imports ${match[1]}, which is not a ComfyUI frontend module`);
    }
  }
});

test("only the entry point registers an extension", () => {
  for (const name of webModules()) {
    const source = readFileSync(join(WEB, name), "utf8");
    assert.equal(
      source.includes("app.registerExtension"),
      name === "dtautocomplete.js",
      `${name} should ${name === "dtautocomplete.js" ? "" : "not "}register an extension`,
    );
  }
});
```

- [ ] **Step 2: Run it to verify it passes**

Run: `node --test tests/test_web_assets.mjs`
Expected: PASS (4 tests)

- [ ] **Step 3: Append the manual checklist to `README.md`**

```markdown
## Browser checklist

The browser behaviour is verified by hand, as the design specifies. Run ComfyUI, add a
`CLIPTextEncode` node, and walk these in order.

- Typing `1girl, blue_h` in the positive prompt shows a list starting `blue_hair`, then
  `blue_hairband`.
- The list shows a category badge, a post count, and `← alias` for alias matches.
- `↑`/`↓` move the highlight and wrap at the ends; `PageUp`/`PageDown` jump ten rows.
- `Tab` inserts the highlighted tag; `Escape` closes the list; with `Enter` insertion left off
  (the default), `Enter` adds a newline and leaves the list open.
- Accepting `blue_hair` writes `1girl, blue_hair, ` and puts the caret after the new separator.
- Accepting inside `1girl, blue_h, solo` leaves the existing comma and spacing alone.
- With a long prompt that wraps, the list appears at the caret on the **first** line of the
  paragraph and on the **last** line, not at the field's top-left or at a fixed offset.
- After scrolling inside a tall prompt field so the caret is no longer on the visible first line,
  the list still appears beside the caret.
- After scrolling the page itself, the list still appears beside the caret.
- The negative prompt field, and any other node with a multiline string input, behaves the same.
- Two `CLIPTextEncode` nodes can be used one after the other with no cross-talk.
- Delete a node with a prompt field and add a new one; suggestions still appear in the new field.
- Typing a plain sentence with no matches leaves the field exactly as before: no interception,
  no swallowed keys, no flicker.
- With Nodes 2.0 (`Modern Node Design`) enabled, the same checks pass.
- With `Enable tag autocomplete` turned off in the settings, no list ever appears.
- With another autocomplete extension active, this one stays off and logs why; turning on
  `Enable alongside another autocomplete` makes it appear.
- Deleting `user/danbooru-tag-autocomplete/` and reloading the page starts a fresh download, and
  suggestions come back once it has finished.
- With the download blocked (offline), a dismissible banner explains why and typing still works.
- A `user/danbooru-tag-autocomplete/custom_tags.csv` with `my_tag,general,0,` and
  `my_old,general,0,my_tag` makes `my_old` suggest `my_tag`.

## Custom tags

Put a `custom_tags.csv` in `user/danbooru-tag-autocomplete/`. A row whose last column is empty
declares a tag; a row whose last column holds names declares the row's name as an alias of the
first of them, so `my_old,general,0,my_tag` means typing `my_old` suggests `my_tag`. A JSON
file with the same shape is accepted instead. Custom entries win over the downloaded database.
```

- [ ] **Step 4: Run everything**

Run:
```bash
node --test tests/test_insert_js.mjs tests/test_keys_js.mjs tests/test_dropdown_js.mjs tests/test_web_assets.mjs tests/test_search_js.mjs
.venv/bin/python -m pytest -q
```
Expected: Node PASS (insert 13 + keys 11 + dropdown 15 + web_assets 4 + search 9 = 52), pytest PASS (156)

- [ ] **Step 5: Commit**

```bash
git add tests/test_web_assets.mjs README.md
git commit -m "Add browser asset guard and checklist"
```

---

### Task 7: Recover when the cached artifact is deleted

**Files:**
- Modify: `store.py`
- Modify: `tests/test_store.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `store.status()` keeps its signature. Its result changes in exactly one situation: when
  the artifact files are gone but the module still holds `STATE_READY`, it now reports
  `STATE_MISSING` instead of `STATE_READY`.

**The defect.** Task 6's reviewer found it while reading the checklist it was reviewing, and it is
a real one. `status()` reports `READY` when the artifact and metadata files exist, and otherwise
falls back to the module-level `_state`. After a completed download that `_state` is `READY`, so
deleting the cache directory — which the checklist does, and which a user forcing a re-download
would do — leaves `status()` still answering `READY`. The status route only starts a download when
the state is `MISSING` (`routes.py`), so nothing restarts it, `/db` answers 404, and the frontend
goes quiet with no way back other than restarting ComfyUI. `ensure_download()` already handles
missing files correctly; it is simply never called.

**The fix.** Report `MISSING` when the files are gone and the state claims otherwise, so the
existing route logic recovers on its own. Deliberately the smallest change that makes the existing
recovery path reachable, rather than a new mechanism.

**Why this task is in M2b.** It is not browser UX, and the reviewer flagged the underlying
behaviour as pre-existing. It is here because M2b's own checklist is the only thing that exercises
it, because Task 5's failure routing sends a `/db` 404 to the quiet path so the user would see
nothing at all, and because the fix is three lines with a test. Deferring it would mean shipping a
checklist line whose behaviour is broken.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_store.py`:

```python
def test_status_reports_missing_when_a_ready_cache_is_deleted(store):
    build_artifact(store.cache_dir())
    # A completed download leaves the module holding STATE_READY. Deleting the cache afterwards
    # must not leave it claiming to be ready, because the status route only starts a new download
    # when the state is MISSING.
    store._state = store.STATE_READY
    assert store.status().state == store.STATE_READY

    (store.cache_dir() / "tags.bin.gz").unlink()

    assert store.status().state == store.STATE_MISSING


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the permission bits")
def test_status_never_raises_when_the_cache_is_unreadable(store):
    # Path.exists() re-raises EACCES, so probing an unreadable cache would otherwise make
    # status() raise and the status route answer 500. An unreadable cache is not a ready one.
    build_artifact(store.cache_dir())
    store._state = store.STATE_READY
    os.chmod(store.cache_dir(), 0o000)
    try:
        assert store.status().state == store.STATE_MISSING
        store.ensure_download()  # must not raise either
    finally:
        os.chmod(store.cache_dir(), 0o755)
```

`os` needs importing at the top of `tests/test_store.py`, alongside the imports already there.

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_store.py -q -k "ready_cache_is_deleted or unreadable"`
Expected: FAIL, `assert 'ready' == 'missing'` for the first, and `PermissionError` for the second

- [ ] **Step 3: Fix `store.py`**

Replace `status` with:

```python
def status() -> Status:
    """Report the cache state. Never raises."""
    ready = artifact_path().exists() and metadata_path().exists()
    with _lock:
        if ready and _state != STATE_DOWNLOADING:
            return Status(STATE_READY, _data_version(), None)
        if not ready and _state == STATE_READY:
            # The cache was deleted after a completed load. Reporting READY would leave the
            # status route with nothing to do while /db answers 404, so the frontend would go
            # quiet with no way back but a restart. Report MISSING so a download starts.
            return Status(STATE_MISSING, _data_version(), None)
        return Status(_state, _data_version(), _error)
```

The existence check is computed before the lock and the decision is made inside it, which is what
the original did too; `_data_version()` was already called under the lock.

**Also in this commit, after Task 7's review.** `Path.exists()` re-raises `EACCES` on the runtime
this project targets — measured on the project's own interpreter, Python 3.13.15, where
`Path('…/chmod-000-dir/file').exists()` raises `PermissionError`, while `os.path.exists()` returns
False. So an unreadable cache directory makes `status()` raise, which contradicts its "Never
raises" docstring and makes the status route answer 500 — and a failed `/status` sends the
frontend down the same quiet path as the bug above, so the user sees nothing. `ensure_download()`
probes the same two paths and raises the same way, and the route calls it immediately after a
`MISSING` status, so both call sites need the same treatment. Add one helper and use it in each:

```python
def _cache_present() -> bool:
    """Whether both cache files are readable. An unreadable cache counts as absent so callers
    can recover: `Path.exists` re-raises EACCES rather than reporting absence."""
    try:
        return artifact_path().exists() and metadata_path().exists()
    except OSError:
        return False
```

Then `status` uses `ready = _cache_present()` and `ensure_download` uses `ready = _cache_present()`
in place of its own two-path probe. Nothing else changes.

This is a pre-existing defect, not one this task introduced — the old `status` probed the same way
— but it produces the same silent failure this task exists to remove, and both call sites are in
the file already being changed, so it is fixed here rather than filed for later.

Nothing else changes: files present and not downloading is still
`READY` whether or not a download is in flight, and a failed download is still `ERROR` and is still
not retried automatically.

- [ ] **Step 4: Run the suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (158 tests)

- [ ] **Step 5: Commit**

```bash
git add store.py tests/test_store.py
git commit -m "Recover when the cached artifact is deleted"
```

---

## M2b completion criteria

- `node --test tests/test_insert_js.mjs tests/test_keys_js.mjs tests/test_dropdown_js.mjs tests/test_web_assets.mjs tests/test_search_js.mjs`
  passes.
- `.venv/bin/python -m pytest -q` passes, including the two tests Task 7 adds (158 tests).
- Every `web/*.js` parses, every relative import resolves, and only `dtautocomplete.js`
  registers an extension.
- The manual checklist in `README.md` is written and ready to walk.
- No file outside `custom_nodes/ComfyUI-Danbooru-Tag-Autocomplete` was modified.

## After M2b

- **The checklist is the gate.** Walking it needs a running ComfyUI; the controller or the
  human partner runs it, and any failure becomes its own task.
- **M3** then publishes the release asset that makes the real database download path live, and
  its workflow must run `pytest` after the build so the real-artifact validation and latency
  gates execute. M2b's `I4`-equivalent question — whether the distributed one-character gate
  holds on a CI runner — is answered there.

