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
