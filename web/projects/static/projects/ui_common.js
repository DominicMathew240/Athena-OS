/* Shared interface pieces for the console, the check-screen builder and the operator screen.

   Dialogs: every dialog on every page goes through manageDialog(), which keeps Tab and Shift+Tab inside the
   dialog (so focus never reaches the page behind it, such as the "Skip to main content" link) and puts focus
   back where it came from when the dialog closes.

   Confirmations: askConfirm() replaces the browser's own confirm box, so every "are you sure?" looks and reads
   the same: the safe answer comes first and has the focus, the risky answer is second.

   Icons: this interface uses no icon font, no images and no emoji. The only marks are the typed characters
   listed in ICONS, always hidden from screen readers and always next to words that say the same thing. */
(() => {
  "use strict";

  const ICONS = Object.freeze({ done: "\u2713", close: "\u00d7", warn: "!", unknown: "?", busy: "\u2026" });

  const TABBABLE = "a[href], button, input, select, textarea, summary, [tabindex]";

  function isTabbable(element) {
    if (element.disabled || element.hidden || element.closest("[hidden], [inert]")) return false;
    const index = element.getAttribute("tabindex");
    if (index !== null && Number(index) < 0) return false;
    if (element.tagName === "SUMMARY" && element.parentElement && element.parentElement.tagName !== "DETAILS") return false;
    // Content inside a closed <details> is not reachable (the summary itself is).
    const closed = element.closest("details:not([open])");
    if (closed && !(element.tagName === "SUMMARY" && element.parentElement === closed)) return false;
    if (element.getClientRects().length === 0) return false;
    if (element.type === "radio" && element.name) {
      // A group of radio buttons is one Tab stop: the checked one, or the first when none is checked.
      const group = Array.from(element.form ? element.form.elements : document.querySelectorAll("input[type=radio]"))
        .filter(item => item.type === "radio" && item.name === element.name && !item.disabled);
      const checked = group.find(item => item.checked);
      return (checked || group[0]) === element;
    }
    return true;
  }

  function tabbableIn(root) {
    return Array.from(root.querySelectorAll(TABBABLE)).filter(isTabbable);
  }

  function trapTab(dialog, event) {
    if (event.key !== "Tab" || event.defaultPrevented) return;
    const items = tabbableIn(dialog);
    if (!items.length) {
      event.preventDefault();
      return;
    }
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement;
    const inside = dialog.contains(active);
    if (event.shiftKey && (!inside || active === first || active === dialog)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (!inside || active === last)) {
      event.preventDefault();
      first.focus();
    }
  }

  /* Call once per <dialog>. `restore` may return the element that should get focus when the dialog closes
     (use it when the opener is rebuilt while the dialog is open); by default focus returns to the opener. */
  function manageDialog(dialog, { restore = null } = {}) {
    if (!dialog || dialog.dataset.dialogManaged === "true") return dialog;
    dialog.dataset.dialogManaged = "true";
    const open = dialog.showModal.bind(dialog);
    dialog.showModal = () => {
      const active = document.activeElement;
      dialog._opener = active && active !== document.body ? active : null;
      open();
    };
    dialog.addEventListener("keydown", event => trapTab(dialog, event));
    dialog.addEventListener("close", () => {
      if (dialog.dataset.keepFocus === "true") return; // the page moves focus somewhere better itself
      const opener = dialog._opener;
      dialog._opener = null;
      const target = (restore && restore()) || (opener && opener.isConnected ? opener : null);
      if (target && typeof target.focus === "function") target.focus({ preventScroll: true });
    });
    return dialog;
  }

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text != null) element.textContent = text;
    return element;
  }

  let confirmCount = 0;

  /* askConfirm({ title, message, keepLabel, confirmLabel }) -> Promise<boolean>
     `message` is a string, or an array of strings (one paragraph each). True only when the person picks
     the second, riskier answer. Escape and the first button both mean "no". */
  function askConfirm({ title, message, keepLabel = "Cancel", confirmLabel = "Remove" }) {
    return new Promise(resolve => {
      confirmCount += 1;
      const id = `ath-confirm-${confirmCount}`;
      const dialog = node("dialog", "confirm-dialog");
      dialog.setAttribute("role", "alertdialog");
      dialog.setAttribute("aria-labelledby", `${id}-title`);
      dialog.setAttribute("aria-describedby", `${id}-text`);
      const head = node("div", "dialog-head");
      const heading = node("div");
      const h2 = node("h2", "", title);
      h2.id = `${id}-title`;
      heading.append(h2);
      head.append(heading);
      const body = node("div", "confirm-body");
      body.id = `${id}-text`;
      (Array.isArray(message) ? message : [message]).forEach(paragraph => body.append(node("p", "", paragraph)));
      const actions = node("div", "form-actions");
      const keep = node("button", "primary", keepLabel);
      keep.type = "button";
      const go = node("button", "secondary danger", confirmLabel);
      go.type = "button";
      actions.append(keep, go);
      body.append(actions);
      dialog.append(head, body);
      document.body.append(dialog);
      manageDialog(dialog);
      let answer = false;
      keep.addEventListener("click", () => dialog.close());
      go.addEventListener("click", () => { answer = true; dialog.close(); });
      dialog.addEventListener("close", () => {
        // manageDialog's own close handler runs first and returns focus to the opener.
        dialog.remove();
        resolve(answer);
      });
      dialog.showModal();
      keep.focus();
    });
  }

  window.Athena = Object.freeze({ ICONS, manageDialog, askConfirm, tabbableIn });
})();
