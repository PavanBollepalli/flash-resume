// mainworld.js — runs in the page's MAIN world (Chrome 111+) so it can attach
// a File to the ATS `<input type="file">`. The isolated-world content script
// (content.js) hands it the PDF as base64 over window.postMessage.
//
// Why MAIN world: Chrome blocks assigning `input.files` from an isolated world;
// here we run as the page's own code and use the native HTMLInputElement
// `files` setter so React/hook-based ATS forms still fire `change`.
//
// content.js may post into embedded form iframes too (Workday / ATS widgets),
// so we accept senders from the top frame or any ancestor and always report the
// result back to window.top, where content.js is listening.

function isTrustedSender(e) {
  // Same-window post (content.js -> this frame) or a parent/top frame reaching
  // into an iframe. Identity comparison only — no cross-origin property access.
  return e.source === window || e.source === window.parent || e.source === window.top;
}

function respond(result) {
  const target = window.top !== window ? window.top : window;
  target.postMessage({ type: "FR_SET_FILE_RESULT", ...result }, "*");
}

window.addEventListener("message", (e) => {
  if (!e.data || e.data.type !== "FR_SET_FILE" || !isTrustedSender(e)) return;

  // 1. Find the file input: prefer anything declaring pdf/accept, then a
  //    resume/cv/attachment-named input, then the first file input as fallback.
  const candidates = Array.from(document.querySelectorAll('input[type="file"]'));
  const input =
    candidates.find((i) => (i.accept || "").toLowerCase().includes("pdf")) ||
    candidates.find((i) =>
      ["resume", "cv", "attachment"].some((k) =>
        ((i.className || "") + " " + (i.name || "") + " " + (i.id || ""))
          .toLowerCase()
          .includes(k)
      )
    ) ||
    candidates[0];

  if (!input) {
    respond({ ok: false, reason: "no-input" });
    return;
  }

  // 2. Decode base64 -> bytes -> a real File, then attach via the native setter.
  try {
    const bytes = Uint8Array.from(atob(e.data.base64), (c) => c.charCodeAt(0));
    const file = new File([bytes], e.data.filename, { type: "application/pdf" });
    const dt = new DataTransfer();
    dt.items.add(file);
    const setter = Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "files"
    ).set;
    setter.call(input, dt.files);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    respond({ ok: true, reason: "attached" });
  } catch (err) {
    respond({ ok: false, reason: "attach-error" });
  }
});