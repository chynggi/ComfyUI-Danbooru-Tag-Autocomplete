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
