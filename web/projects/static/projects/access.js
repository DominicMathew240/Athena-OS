/* The access screen: an administrator gives and takes away roles, for the whole console or for one project.
   Everything shown and sent here goes through /api/access; the server checks that the person is an
   administrator on every call, so nothing on this page grants anything by itself. */
"use strict";

const ROLES = ["viewer", "labeler", "trainer", "approver"];
const TITLES = { viewer: "Viewer", labeler: "Labeler", trainer: "Trainer", approver: "Approver" };
const PAGE = 25;

const $ = id => document.getElementById(id);
const statusLine = $("access-status");
const peopleList = $("people");
const searchBox = $("people-search");
const recordRows = $("record-rows");
const recordProject = $("record-project");
const recordPerson = $("record-person");
const recordMore = $("record-more");

let overview = null;
let shown = 0;
let recordToken = 0;

function csrfToken() {
  return document.querySelector('meta[name="csrf-token"]')?.content || "";
}

async function api(url, options = {}) {
  const { headers = {}, ...rest } = options;
  let response;
  try {
    response = await fetch(url, {
      credentials: "same-origin",
      ...rest,
      headers: { Accept: "application/json", "X-CSRFToken": csrfToken(), ...headers }
    });
  } catch (error) {
    throw new Error("Could not reach the Athena server. Check your connection and try again.");
  }
  let body = {};
  try {
    body = await response.json();
  } catch (error) {
    body = {};
  }
  if (!response.ok) throw new Error(body.error || `The server answered ${response.status}.`);
  return body;
}

const sendJson = (url, method, payload) => api(url, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function say(message, tone = "ok") {
  statusLine.textContent = message;
  statusLine.dataset.tone = tone;
}

const titleOf = role => (role ? TITLES[role] : "No role");

function roleSelect(id, current, { none }) {
  const select = el("select");
  select.id = id;
  select.append(new Option(none, ""));
  ROLES.forEach(role => select.append(new Option(TITLES[role], role)));
  select.value = current || "";
  return select;
}

/* ---- the four roles --------------------------------------------------------------------------- */
function renderKey() {
  const key = $("role-key");
  key.replaceChildren();
  overview.roles.forEach(role => {
    const row = el("div", "role-key-row");
    row.append(el("dt", "", role.title), el("dd", "", role.summary));
    key.append(row);
  });
  $("unassigned-note").textContent =
    `People with no console-wide role are treated as ${TITLES[overview.unassigned_role]} on projects where they have no role of their own, and can never approve a model.`;
}

/* ---- people ----------------------------------------------------------------------------------- */
function projectName(id) {
  return overview.projects.find(project => project.id === id)?.name || "A deleted project";
}

function personCard(person) {
  const item = el("li", "person");
  item.dataset.user = String(person.user_id);
  const head = el("div", "person-head");
  const name = el("h3", "person-name", person.username);
  head.append(name, el("span", "person-ref", person.id));
  item.append(head);

  if (person.superuser) {
    item.append(el("p", "field-help", "Superuser: can do everything on every project, so a role would change nothing. This is how they are an administrator."));
    return item;
  }

  // Console-wide role
  const consoleRow = el("div", "person-row");
  const consoleId = `console-role-${person.user_id}`;
  const consoleLabel = el("label", "", "Role everywhere");
  consoleLabel.htmlFor = consoleId;
  const none = `No role (counts as ${TITLES[overview.unassigned_role]})`;
  const consoleSelect = roleSelect(consoleId, person.console_role, { none });
  const consoleSave = el("button", "secondary", "Save");
  consoleSave.type = "button";
  consoleSave.disabled = true;
  consoleSave.setAttribute("aria-label", `Save the role everywhere for ${person.username}`);
  consoleSelect.addEventListener("change", () => { consoleSave.disabled = (consoleSelect.value || null) === person.console_role; });
  consoleSave.addEventListener("click", () => changeConsole(person, consoleSelect, consoleSave));
  consoleRow.append(consoleLabel, consoleSelect, consoleSave);
  if (person.also_in_groups.length) {
    consoleRow.append(el("p", "field-help person-warn", `They are also in the ${person.also_in_groups.map(titleOf).join(", ")} group. Saving leaves them with one role only.`));
  }
  item.append(consoleRow);

  // Roles on single projects
  const projectBlock = el("div", "person-projects");
  projectBlock.append(el("h4", "", "Roles on single projects"));
  if (person.project_roles.length === 0) {
    projectBlock.append(el("p", "field-help", "None. Their role everywhere applies on every project."));
  } else {
    const list = el("ul", "project-roles");
    person.project_roles.forEach(entry => list.append(projectRoleRow(person, entry)));
    projectBlock.append(list);
  }
  projectBlock.append(addProjectRole(person));
  item.append(projectBlock);
  return item;
}

function projectRoleRow(person, entry) {
  const row = el("li", "project-role");
  const id = `project-role-${person.user_id}-${entry.project_id}`;
  const label = el("label", "", entry.project_name);
  label.htmlFor = id;
  const select = roleSelect(id, entry.role, { none: "Remove (use their role everywhere)" });
  select.options[0].remove(); // changing to "no role" is what the Remove button is for
  const save = el("button", "secondary", "Save");
  save.type = "button";
  save.disabled = true;
  save.setAttribute("aria-label", `Save the role on ${entry.project_name} for ${person.username}`);
  select.addEventListener("change", () => { save.disabled = select.value === entry.role; });
  save.addEventListener("click", () => changeProject(person, entry.project_id, entry.project_name, select.value, save));
  const remove = el("button", "secondary danger", "Remove");
  remove.type = "button";
  remove.setAttribute("aria-label", `Remove the role on ${entry.project_name} from ${person.username}`);
  remove.addEventListener("click", () => removeProject(person, entry, remove));
  row.append(label, select, save, remove);
  return row;
}

function addProjectRole(person) {
  const taken = new Set(person.project_roles.map(entry => entry.project_id));
  const free = overview.projects.filter(project => !taken.has(project.id));
  const form = el("form", "project-add");
  form.setAttribute("aria-label", `Give ${person.username} a role on one project`);
  if (free.length === 0) {
    form.append(el("p", "field-help", overview.projects.length ? "They have a role on every project." : "There are no projects yet."));
    return form;
  }
  const projectId = `add-project-${person.user_id}`;
  const roleId = `add-role-${person.user_id}`;
  const projectLabel = el("label", "", "Project");
  projectLabel.htmlFor = projectId;
  const projectSelect = el("select");
  projectSelect.id = projectId;
  projectSelect.append(new Option("Choose a project", ""));
  free.forEach(project => projectSelect.append(new Option(project.name, project.id)));
  const roleLabel = el("label", "", "Role there");
  roleLabel.htmlFor = roleId;
  const roleChoice = roleSelect(roleId, "", { none: "Choose a role" });
  const add = el("button", "secondary", "Give role");
  add.type = "submit";
  form.append(projectLabel, projectSelect, roleLabel, roleChoice, add);
  form.addEventListener("submit", event => {
    event.preventDefault();
    if (!projectSelect.value || !roleChoice.value) {
      say("Choose a project and a role first.", "error");
      (projectSelect.value ? roleChoice : projectSelect).focus();
      return;
    }
    const name = projectSelect.selectedOptions[0].textContent;
    void changeProject(person, projectSelect.value, name, roleChoice.value, add);
  });
  return form;
}

function visiblePeople() {
  const needle = searchBox.value.trim().toLowerCase();
  return overview.people.filter(person => !needle || person.username.toLowerCase().includes(needle));
}

function renderPeople(focusSelector) {
  const people = visiblePeople();
  peopleList.replaceChildren(...people.map(personCard));
  $("people-empty").hidden = people.length > 0;
  const total = overview.people.length;
  $("people-count").textContent = overview.people_truncated
    ? `Showing the first ${total} people.`
    : `${people.length} of ${total} ${total === 1 ? "person" : "people"} shown.`;
  if (focusSelector) peopleList.querySelector(focusSelector)?.focus();
}

function replacePerson(updated) {
  const index = overview.people.findIndex(person => person.user_id === updated.user_id);
  if (index >= 0) overview.people[index] = updated;
}

async function apply(person, button, request, success, focusSelector) {
  button.disabled = true;
  try {
    const result = await request();
    replacePerson(result.person);
    say(result.changed ? success : "Nothing changed: they already had that role.", "ok");
    renderPeople(focusSelector);
    await loadRecord(true);
  } catch (error) {
    say(error.message, "error");
    button.disabled = false;
  }
}

function changeConsole(person, select, button) {
  const role = select.value || null;
  const message = role
    ? `${person.username} now has the ${TITLES[role]} role everywhere (except on projects where they have a role of their own).`
    : `${person.username} no longer has a role everywhere. They count as ${TITLES[overview.unassigned_role]}.`;
  return apply(person, button, () => sendJson(`/api/access/people/${person.user_id}/console`, "PUT", { role }), message, `#console-role-${person.user_id}`);
}

function changeProject(person, projectId, name, role, button) {
  return apply(
    person, button,
    () => sendJson(`/api/access/people/${person.user_id}/projects`, "PUT", { project_id: projectId, role }),
    `${person.username} is now a ${TITLES[role]} on “${name}”.`,
    `#project-role-${person.user_id}-${projectId}`
  );
}

async function removeProject(person, entry, button) {
  const fallback = person.console_role ? TITLES[person.console_role] : `no role, so ${TITLES[overview.unassigned_role]}`;
  const ok = await Athena.askConfirm({
    title: `Remove ${person.username}’s role on “${entry.project_name}”?`,
    message: `On that project they will use their role everywhere (${fallback}). The change is written to the record.`,
    keepLabel: "Keep it",
    confirmLabel: "Remove the role"
  });
  if (!ok) return;
  return apply(
    person, button,
    () => api(`/api/access/people/${person.user_id}/projects?project=${encodeURIComponent(entry.project_id)}`, { method: "DELETE" }),
    `${person.username} no longer has a role of their own on “${entry.project_name}”.`,
    `#add-project-${person.user_id}`
  );
}

/* ---- record of changes ------------------------------------------------------------------------ */
function whenText(iso) {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

const personText = who => (who.username ? `${who.username} (${who.ref})` : `${who.ref} (account removed)`);

function recordRow(change) {
  const row = el("tr");
  const when = el("td");
  const time = el("time", "", whenText(change.at));
  time.dateTime = change.at;
  when.append(time);
  const where = change.project
    ? `${change.project.name}${change.project.deleted ? " (project deleted)" : ""}`
    : "Everywhere";
  const what = change.before && change.after
    ? `${titleOf(change.before)} changed to ${titleOf(change.after)}`
    : change.after ? `Given ${titleOf(change.after)}` : `${titleOf(change.before)} taken away`;
  const by = change.by.ref ? personText(change.by) : change.via_title;
  const changeCell = el("td", "", what);
  if (change.note) changeCell.append(el("div", "field-help", change.note));
  row.append(when, el("td", "", personText(change.who)), el("td", "", where), changeCell, el("td", "", change.by.ref ? `${by}` : by));
  return row;
}

async function loadRecord(fromStart) {
  const token = ++recordToken;
  if (fromStart) shown = 0;
  const query = new URLSearchParams({ limit: String(PAGE), offset: String(shown) });
  if (recordProject.value) query.set("project", recordProject.value);
  if (recordPerson.value) query.set("person", recordPerson.value);
  try {
    const body = await api(`/api/access/changes?${query}`);
    if (token !== recordToken) return;
    const rows = body.changes.map(recordRow);
    if (fromStart) recordRows.replaceChildren(...rows);
    else recordRows.append(...rows);
    shown += rows.length;
    $("record-empty").hidden = body.total > 0;
    $("record-empty").textContent = recordProject.value || recordPerson.value ? "No role changes match these filters." : "No role has been changed yet.";
    recordMore.hidden = shown >= body.total;
    $("record-count").textContent = body.total ? `Showing ${shown} of ${body.total}.` : "";
  } catch (error) {
    if (token === recordToken) say(`Could not load the record. ${error.message}`, "error");
  }
}

function fillFilters() {
  recordProject.replaceChildren(new Option("All changes", ""));
  overview.projects.forEach(project => recordProject.append(new Option(project.name, project.id)));
  recordPerson.replaceChildren(new Option("Everyone", ""));
  overview.people.forEach(person => recordPerson.append(new Option(person.username, person.id)));
}

async function start() {
  try {
    overview = await api("/api/access");
  } catch (error) {
    say(`Could not load the people. ${error.message}`, "error");
    return;
  }
  renderKey();
  fillFilters();
  renderPeople();
  await loadRecord(true);
}

searchBox.addEventListener("input", () => renderPeople());
recordProject.addEventListener("change", () => loadRecord(true));
recordPerson.addEventListener("change", () => loadRecord(true));
recordMore.addEventListener("click", () => loadRecord(false));
void start();
