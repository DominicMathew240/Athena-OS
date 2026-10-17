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

  /* The stop after (or before) `from` in `items`, wrapping at either end. `from` need not be in the list: a
     heading with tabindex="-1", the dialog itself or a control that just became hidden still has a place in
     the document, so the next stop is the first listed item after it (or the last one before it). */
  function nextStop(items, from, backwards) {
    const FOLLOWING = Node.DOCUMENT_POSITION_FOLLOWING;
    if (backwards) {
      const before = items.filter(item => item !== from && (from.compareDocumentPosition(item) & FOLLOWING) === 0);
      return before.length ? before[before.length - 1] : items[items.length - 1];
    }
    const after = items.filter(item => item !== from && (from.compareDocumentPosition(item) & FOLLOWING) !== 0);
    return after.length ? after[0] : items[0];
  }

  /* Tab and Shift+Tab inside a modal dialog are stepped by script, through the dialog's own controls, and wrap
     at both ends. The browser's own next stop cannot be trusted: Safari skips buttons and links unless its
     "Press Tab to highlight each item" setting is on, so "the last control" is a different element there, and a
     trap that waits for the last button to be focused lets focus fall out of the dialog onto <body>. */
  function trapTab(dialog, event) {
    if (event.key !== "Tab" || event.defaultPrevented || event.ctrlKey || event.altKey || event.metaKey) return;
    event.preventDefault();
    const items = tabbableIn(dialog);
    if (!items.length) return;
    const active = document.activeElement;
    const from = active && dialog.contains(active) ? active : dialog;
    nextStop(items, from, event.shiftKey).focus();
  }

  /* Safari (and Firefox on macOS) do not focus a button when it is clicked: at the moment a dialog opens
     document.activeElement is <body>, or the nearest focusable ancestor (here <main>), never the button, so there
     would be nothing sensible to give focus back to. The control that was just clicked wins; a keyboard
     activation also fires a click on the focused control, so that case is covered too. */
  let lastClicked = null;
  let lastClickedAt = 0;
  document.addEventListener("click", event => {
    lastClicked = event.target instanceof Element ? event.target.closest("button, a[href], summary, input, select, textarea, [tabindex]") : null;
    lastClickedAt = Date.now();
  }, true);
  const recentlyClicked = () => (lastClicked && lastClicked.isConnected && Date.now() - lastClickedAt < 3000 ? lastClicked : null);

  /* Call once per <dialog>. `restore` may return the element that should get focus when the dialog closes
     (use it when the opener is rebuilt while the dialog is open); by default focus returns to the opener. */
  function manageDialog(dialog, { restore = null } = {}) {
    if (!dialog || dialog.dataset.dialogManaged === "true") return dialog;
    dialog.dataset.dialogManaged = "true";
    const open = dialog.showModal.bind(dialog);
    dialog.showModal = () => {
      const active = document.activeElement;
      dialog._opener = recentlyClicked() || (active && active !== document.body ? active : null);
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

  /* Roles. The server decides what the signed-in person may do and writes it onto <body> (data-role,
     data-can-label, data-can-train, data-can-approve). Nothing here decides anything: it only reads those
     marks and switches off the controls that would be refused. The server still refuses the call itself,
     so editing these attributes in the browser changes how the page looks, never what is allowed.

     Mark a control (or a whole form or section) with data-needs="label|train|approve" and it is switched
     off, with the reason in its title, whenever the person lacks that role. Controls added later are
     caught too, and a script that re-enables a switched-off control is overruled. */
  const marks = document.body ? document.body.dataset : {};
  const CONSOLE_ACCESS = Object.freeze({
    role: marks.role || "viewer",
    label: marks.canLabel === "1",
    train: marks.canTrain === "1",
    approve: marks.canApprove === "1",
    secondPerson: marks.secondPerson === "1",
    me: marks.me || "",
    scope: "console",
    projectId: ""
  });
  /* A role can also be given for one project. The server writes those onto <body> as data-project-roles (a JSON
     object keyed by project id) for the projects where this person has a role of their own; opening such a
     project (Athena.useProject) switches this page to that role. Like everything here it only changes how the
     page looks: the server still decides every call by the project in the address. */
  const PROJECT_ROLES = (() => {
    try {
      const parsed = JSON.parse(marks.projectRoles || "{}");
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch (error) {
      return {};
    }
  })();
  let ACCESS = CONSOLE_ACCESS;
  const ROLE_NAME = { label: "Labeler", train: "Trainer", approve: "Approver" };
  const ROLE_TITLE = { viewer: "Viewer", labeler: "Labeler", trainer: "Trainer", approver: "Approver" };

  function can(need, access = ACCESS) {
    return !need || need === "view" || access[need] === true;
  }

  // The same plain sentences the server sends with a refusal.
  function reasonFor(need, access = ACCESS) {
    if (can(need, access)) return "";
    if (access.role === "viewer") return `Your account can look but not change things. Ask an administrator for the ${ROLE_NAME[need]} role.`;
    if (need === "approve") return "Your account cannot approve a model for use or go back to the previous model. Ask an administrator for the Approver role.";
    return "Your account can label pictures but cannot teach the computer, move a model to trial or set up check screens. Ask an administrator for the Trainer role.";
  }

  // Things that are not about the open project (such as creating a project) follow the console-wide role.
  const accessFor = element => (element.dataset.needsScope === "console" ? CONSOLE_ACCESS : ACCESS);

  const CONTROLS = "button, input, select, textarea";

  // One shared listener for links that are switched off, so it can be left in place and simply stop acting.
  function blockSwitchedOffLink(event) {
    if (event.currentTarget.dataset.gated === "1") event.preventDefault();
  }

  function switchOff(control, reason) {
    if (control.dataset.gated === "1") return;
    control.dataset.gated = "1";
    if (control.tagName === "A") {
      control.setAttribute("aria-disabled", "true");
      control.setAttribute("tabindex", "-1");
      control.addEventListener("click", blockSwitchedOffLink);
    } else {
      control.dataset.wasDisabled = control.disabled ? "1" : "0";
      control.disabled = true;
    }
    if (!control.title) {
      control.title = reason;
      control.dataset.gatedTitle = reason;
    }
  }

  function switchOn(control) {
    if (control.dataset.gated !== "1") return;
    delete control.dataset.gated;
    if (control.tagName === "A") {
      control.removeAttribute("aria-disabled");
      control.removeAttribute("tabindex");
    } else {
      control.disabled = control.dataset.wasDisabled === "1"; // back to what the page itself had set
      delete control.dataset.wasDisabled;
    }
    if (control.dataset.gatedTitle !== undefined) {
      if (control.title === control.dataset.gatedTitle) control.removeAttribute("title");
      delete control.dataset.gatedTitle;
    }
  }

  function enforce(element) {
    const need = element.dataset.needs;
    const access = accessFor(element);
    if (can(need, access)) return;
    const reason = reasonFor(need, access);
    if (element.matches(CONTROLS) || element.tagName === "A") switchOff(element, reason);
    else element.querySelectorAll(`${CONTROLS}, a[href]`).forEach(control => switchOff(control, reason));
    element.dataset.gatedReason = reason;
  }

  function enforceWithin(root) {
    if (!root || root.nodeType !== 1) return;
    if (root.dataset.needs) enforce(root);
    const gatedParent = root.closest && root.closest("[data-needs]");
    if (gatedParent && gatedParent !== root) enforce(gatedParent);
    root.querySelectorAll("[data-needs]").forEach(enforce);
  }

  function refreshGateNotes() {
    // Say, once per area, why its controls are off. The text matches what the server answers with.
    document.querySelectorAll("[data-gate-note]").forEach(note => {
      const reason = reasonFor(note.dataset.gateNote);
      note.textContent = reason;
      note.hidden = !reason;
    });
  }

  function watchGates() {
    refreshGateNotes();
    enforceWithin(document.body);
    new MutationObserver(records => {
      for (const record of records) {
        if (record.type === "attributes") {
          // A script switched a gated control back on: switch it off again.
          const target = record.target;
          const owner = target.dataset.gated === "1" ? target : null;
          if (owner && owner.disabled === false && owner.tagName !== "A") owner.disabled = true;
          if (!owner && target.dataset.needs) enforce(target);
        } else {
          record.addedNodes.forEach(enforceWithin);
        }
      }
    }).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["disabled", "data-needs"] });
  }

  /* Gate one control from script, for buttons that are built or re-built in code. */
  function gate(element, need) {
    if (!element) return element;
    element.dataset.needs = need;
    enforce(element);
    return element;
  }

  /* Open a project (or none: pass null): use the role this person has there. Controls the new role allows
     are switched back on, the others off, and "athena:rolechange" is announced so the page can reword itself. */
  function useProject(projectId) {
    const own = projectId ? PROJECT_ROLES[projectId] : null;
    const next = own
      ? Object.freeze({
          ...CONSOLE_ACCESS,
          role: own.role,
          label: own.label === true,
          train: own.train === true,
          approve: own.approve === true,
          scope: "project",
          projectId
        })
      : CONSOLE_ACCESS;
    if (next.role === ACCESS.role && next.scope === ACCESS.scope && next.projectId === ACCESS.projectId) return ACCESS;
    ACCESS = next;
    if (document.body) {
      document.querySelectorAll("[data-gated='1']").forEach(switchOn);
      document.querySelectorAll("[data-gated-reason]").forEach(area => delete area.dataset.gatedReason);
      refreshGateNotes();
      enforceWithin(document.body);
    }
    document.dispatchEvent(new CustomEvent("athena:rolechange", { detail: ACCESS }));
    return ACCESS;
  }

  if (document.body) watchGates();

  window.Athena = Object.freeze({
    ICONS, manageDialog, askConfirm, tabbableIn, can, reasonFor, gate, useProject,
    get access() { return ACCESS; },
    roleTitle: role => ROLE_TITLE[role] || ""
  });
})();
