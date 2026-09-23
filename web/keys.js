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
