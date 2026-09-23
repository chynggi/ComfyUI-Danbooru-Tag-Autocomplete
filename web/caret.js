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
