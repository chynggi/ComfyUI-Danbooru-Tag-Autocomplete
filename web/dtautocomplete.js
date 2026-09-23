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
  let blurTimer = null;
  textarea.addEventListener("blur", () => {
    blurTimer = setTimeout(() => dropdown.hide(), 150);
  });
  textarea.addEventListener("focus", () => {
    clearTimeout(blurTimer);
    blurTimer = null;
  });
  textarea.addEventListener("keydown", (event) => {
    if (dropdown.handleKey(event, settings())) {
      event.preventDefault();
    }
  });
  return dropdown;
}

function observeTextareas() {
  for (const textarea of document.querySelectorAll("textarea.comfy-multiline-input")) {
    attach({ element: textarea });
  }
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
      // Arm the observer before waiting on the database. A prompt field that appears while the
      // download is still running would otherwise never be attached, because the observer only
      // sees nodes added after it starts. Suggestions stay off until `ready`, so this is safe.
      observeTextareas();
      index = await loadIndex();
      ready = true;
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
