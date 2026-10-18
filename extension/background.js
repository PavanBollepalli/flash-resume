// background.js — tiny service worker. `chrome.downloads` is only available on
// extension pages (popup, history) and here; content scripts route the rare
// fallback download through this worker instead of duplicating the logic.

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "FR_DOWNLOAD") {
    chrome.downloads.download(
      {
        url: msg.dataUrl, // data:application/pdf;base64,...
        filename: msg.filename, // e.g. "2026-10-17-1432_Stripe_SeniorSWE.pdf"
        conflictAction: "uniquify",
        saveAs: false,
      },
      () => sendResponse({ ok: !chrome.runtime.lastError })
    );
    return true; // keep the message channel open for the async callback
  }
});