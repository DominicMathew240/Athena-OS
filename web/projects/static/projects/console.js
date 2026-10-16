const modalityInfo = {
  image: { label: "Pictures", family: "visual" },
  video: { label: "Video", family: "visual" },
  audio: { label: "Audio", family: "audio" },
  speech: { label: "Speech", family: "audio" },
  text: { label: "Text", family: "language" },
  table: { label: "Tables", family: "structured" },
  time_series: { label: "Measurements over time", family: "structured" },
  depth: { label: "Depth maps", family: "structured" },
  point_cloud: { label: "3D scans", family: "structured" },
  custom: { label: "Other", family: "custom" }
};
let projects = [];
let currentFilter = "all";
let selectedId = null;
let lastCount = 0;
let loadError = "";
const tableBody = document.getElementById("dataset-rows");
const mobileRows = document.getElementById("mobile-rows");
const emptyState = document.getElementById("empty-state");
const emptyCopy = document.getElementById("empty-copy");
const emptyCreate = document.getElementById("empty-create");
const notice = document.getElementById("action-notice");
const noticeMessage = document.getElementById("action-message");
const deleteSelected = document.getElementById("delete-selected-project");
const projectDialog = document.getElementById("project-dialog");
const projectForm = document.getElementById("create-project-form");
const formError = document.getElementById("project-form-error");
const customModalityField = document.getElementById("custom-modality-field");
const customModalityInput = document.getElementById("custom-modality");
const imageAssetsPanel = document.getElementById("image-assets-panel");
const imageUploadForm = document.getElementById("image-upload-form");
const imageUploadInput = document.getElementById("image-upload");
const MAX_PICTURE_BYTES = 20 * 1024 * 1024; // the server enforces the same limit; this only lets one bad file be skipped on its own
const autoAfterUpload = document.getElementById("autolabel-after-upload");
const autoAfterUploadField = document.getElementById("autolabel-after-upload-field");
const assetList = document.getElementById("asset-list");
const assetStatus = document.getElementById("asset-status");
const imageAssetsHeading = document.getElementById("image-assets-heading");
imageAssetsHeading.tabIndex = -1;
const annotationDialog = document.getElementById("annotation-dialog");
const annotationImage = document.getElementById("annotation-image");
const annotationCanvas = document.getElementById("annotation-canvas");
const annotationStage = document.getElementById("annotation-stage");
const annotationStageScroll = document.getElementById("annotation-stage-scroll");
const annotationZoomIn = document.getElementById("annotation-zoom-in");
const annotationZoomOut = document.getElementById("annotation-zoom-out");
const annotationZoomReset = document.getElementById("annotation-zoom-reset");
const annotationZoomLevel = document.getElementById("annotation-zoom-level");
const annotationUndo = document.getElementById("annotation-undo");
const annotationRedo = document.getElementById("annotation-redo");
const annotationLabel = document.getElementById("annotation-label");
const annotationList = document.getElementById("annotation-list");
const annotationStatus = document.getElementById("annotation-status");
const annotationFilename = document.getElementById("annotation-filename");
const keyboardBoxForm = document.getElementById("keyboard-box-form");
const annotationHelpDetails = document.getElementById("annotation-help-details");
let activeAnnotationAsset = null;
let boxAnnotations = [];
let drawingBox = null;
let selectedAnnotationId = null;
let boxEditPreview = null;
let annotationZoom = 1;
let annotationUndoStack = [];
let annotationRedoStack = [];
let annotationHistoryBusy = false;
let annotationMutationBusy = false;
let currentAssets = [];
let autolabelInfo = { enabled: false, worker_online: false, checked: false };
let autolabelLastJob = null;
let autolabelTimer = null;
let imageJobTimer = null;
const autolabelPanel = document.getElementById("autolabel-panel");
const autolabelForm = document.getElementById("autolabel-form");
const autolabelRun = document.getElementById("autolabel-run");
const autolabelCancel = document.getElementById("autolabel-cancel");
const autolabelStrictness = document.getElementById("autolabel-strictness");
const autolabelState = document.getElementById("autolabel-state");
const autolabelProgress = document.getElementById("autolabel-progress");
const autolabelBar = document.getElementById("autolabel-bar");
const autolabelProgressText = document.getElementById("autolabel-progress-text");
const autolabelReview = document.getElementById("autolabel-review");
const autolabelReviewText = document.getElementById("autolabel-review-text");
const autolabelThreshold = document.getElementById("autolabel-accept-threshold");
const annotationAuto = document.getElementById("annotation-auto");
const annotationAutolabel = document.getElementById("annotation-autolabel");
const annotationAcceptAll = document.getElementById("annotation-accept-all");
const annotationAutoNote = document.getElementById("annotation-auto-note");
const searchable = project => `${project.name} ${project.id} ${project.modality} ${project.custom_modality || ""} ${project.task} ${project.labels.join(" ")}`.toLowerCase();

function projectState(project) {
  if (project.modality !== "image") return { text: "No labelling tools yet", tone: "plugin" };
  if (!project.labels.length) return { text: "Next: add labels", tone: "review" };
  if (!project.image_count) return { text: "Next: add pictures", tone: "ready" };
  if (project.pending_boxes) return { text: `${project.pending_boxes.toLocaleString()} to review`, tone: "review" };
  if (project.accepted_boxes) return { text: `${project.accepted_boxes.toLocaleString()} ${project.accepted_boxes === 1 ? "box" : "boxes"} saved`, tone: "live" };
  return { text: "Ready: mark items", tone: "ready" };
}

function displayType(project) {
  const type = modalityInfo[project.modality]?.label || "Other";
  return project.modality === "custom" && project.custom_modality ? `${type}: ${project.custom_modality}` : type;
}

// Keeps the project table's state column current after images or boxes change.
async function refreshProjectCounts() {
  try {
    const body = await api("/api/projects");
    projects = body.projects;
    render();
  } catch (error) {
    // The table keeps its last known state; the action that triggered this reports its own errors.
  }
}

function projectFamily(project) {
  return modalityInfo[project.modality]?.family || "custom";
}

function visibleProjects() {
  const term = document.getElementById("search").value.trim().toLowerCase();
  return projects.filter(project => (currentFilter === "all" || projectFamily(project) === currentFilter) && (!term || searchable(project).includes(term)));
}

function announceCount(count) {
  const tile = document.getElementById("project-count");
  tile.textContent = String(count).padStart(2, "0");
  if (count !== lastCount) {
    tile.classList.remove("bump");
    requestAnimationFrame(() => tile.classList.add("bump"));
  }
  lastCount = count;
}

async function selectProject(id, { reveal = false } = {}) {
  if (id !== selectedId && labelsAreDirty() && !(await Athena.askConfirm({
    title: "Open another project without saving your labels?",
    message: "You have label changes that are not saved. If you open another project they will be lost.",
    keepLabel: "Go back to my labels",
    confirmLabel: "Open it anyway"
  }))) return;
  selectedId = id;
  // The address remembers which project is open, so Back, a reload or "All projects" in the builder return to it.
  if (id) history.replaceState(null, "", `#project=${id}`);
  render();
  const selected = projects.find(project => project.id === id);
  if (!selected) {
    imageAssetsPanel.hidden = true;
    datasetPanel.hidden = true;
    trPanel.hidden = true;
    if (appsPanel) appsPanel.hidden = true;
    autolabelPanel.hidden = true;
    stopAutolabelPolling();
    assetList.replaceChildren();
    return;
  }
  void loadAssets(selected.id);
  void refreshAutolabel(selected.id);
  // The project workspace sits below the project list; bring it into view when a person opens a project.
  if (reveal) revealWorkspace();
}

function render() {
  const shown = visibleProjects();
  tableBody.replaceChildren();
  mobileRows.replaceChildren();
  document.querySelector(".desktop-table").hidden = shown.length === 0;
  emptyState.hidden = shown.length > 0;
  emptyCreate.hidden = projects.length > 0;
  emptyCopy.textContent = loadError || (projects.length === 0
    ? "No projects yet. Create one to start labelling. A project holds your pictures, your labels, and your boxes."
    : "No projects match this filter or search. Choose “All” or clear the search box.");

  shown.forEach(project => {
    const shownType = displayType(project);
    const labelSummary = project.labels.length ? project.labels.join(", ") : "No labels yet";
    const meta = project.modality === "image" ? plural(project.image_count || 0, "picture", "pictures") : "Not labelled here yet";
    const projectStatus = projectState(project);
    const current = String(project.id === selectedId);

    const row = document.createElement("tr");
    row.setAttribute("aria-current", current);
    const nameCell = document.createElement("td");
    const name = document.createElement("div");
    name.className = "project-name";
    name.textContent = project.name;
    const id = document.createElement("div");
    id.className = "dataset-id";
    id.textContent = meta;
    nameCell.append(name, id);
    const typeCell = document.createElement("td");
    const tag = document.createElement("span");
    tag.className = "type-tag";
    tag.textContent = shownType;
    typeCell.append(tag);
    const taskCell = document.createElement("td");
    taskCell.textContent = project.task;
    const labelsCell = document.createElement("td");
    labelsCell.textContent = labelSummary;
    const stateCell = document.createElement("td");
    const state = document.createElement("span");
    state.className = `state ${projectStatus.tone}`;
    state.textContent = projectStatus.text;
    stateCell.append(state);
    const actionCell = document.createElement("td");
    const action = document.createElement("button");
    action.className = "row-action";
    action.type = "button";
    action.textContent = "Open";
    action.setAttribute("aria-label", `Open project: ${project.name}`);
    action.addEventListener("click", event => { event.stopPropagation(); selectProject(project.id, { reveal: true }); });
    actionCell.append(action);
    row.append(nameCell, typeCell, taskCell, labelsCell, stateCell, actionCell);
    row.addEventListener("click", () => selectProject(project.id, { reveal: true }));
    tableBody.append(row);

    const card = document.createElement("div");
    card.className = "mobile-row";
    card.setAttribute("role", "listitem");
    card.setAttribute("aria-current", current);
    const select = document.createElement("div");
    select.className = "select";
    const mobileName = document.createElement("span");
    mobileName.className = "project-name";
    mobileName.textContent = project.name;
    const details = document.createElement("span");
    details.className = "details";
    const mobileType = document.createElement("span");
    mobileType.className = "type-tag";
    mobileType.textContent = shownType;
    const mobileTask = document.createElement("span");
    mobileTask.className = "task";
    mobileTask.textContent = project.task;
    const mobileState = document.createElement("span");
    mobileState.className = `state ${projectStatus.tone}`;
    mobileState.textContent = projectStatus.text;
    details.append(mobileType, mobileState);
    const mobileTaskLine = document.createElement("span");
    mobileTaskLine.className = "task-line-text";
    mobileTaskLine.append(mobileTask);
    select.append(mobileName, mobileTaskLine, details);
    const mobileAction = document.createElement("button");
    mobileAction.className = "row-action";
    mobileAction.type = "button";
    mobileAction.textContent = "Open";
    mobileAction.setAttribute("aria-label", `Open project: ${project.name}`);
    mobileAction.addEventListener("click", () => selectProject(project.id, { reveal: true }));
    card.append(select, mobileAction);
    mobileRows.append(card);
  });
  announceCount(projects.length);
  renderWorkspace();
}

async function loadProjects() {
  try {
    const body = await api("/api/projects");
    projects = body.projects;
    loadError = "";
    const remembered = (/^#project=([0-9a-f-]{36})$/i.exec(location.hash) || [])[1];
    if (!selectedId && remembered) selectedId = remembered;
    if (!projects.some(project => project.id === selectedId)) {
      selectedId = projects[0]?.id || null;
    }
  } catch (error) {
    loadError = `Could not load your projects. ${error.message} Reload the page to try again.`;
  }
  render();
  if (selectedId) selectProject(selectedId);
}

const formatBytes = bytes => bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024)).toLocaleString()} KB`;

async function loadAssets(projectId) {
  const project = projects.find(candidate => candidate.id === projectId);
  imageAssetsPanel.hidden = !project || project.modality !== "image";
  datasetPanel.hidden = imageAssetsPanel.hidden;
  trPanel.hidden = imageAssetsPanel.hidden;
  if (appsPanel) appsPanel.hidden = imageAssetsPanel.hidden;
  assetList.replaceChildren();
  if (imageAssetsPanel.hidden) return;
  assetStatus.textContent = "Loading pictures…";
  try {
    const body = await api(`/api/projects/${encodeURIComponent(projectId)}/assets`);
    if (selectedId !== projectId) return;
    currentAssets = body.assets;
    body.assets.forEach(asset => {
      const row = document.createElement("article");
      row.className = "asset-row";
      row.dataset.assetId = asset.id;
      row.tabIndex = -1;
      const image = document.createElement("img");
      image.src = asset.content_url;
      image.alt = "";
      image.loading = "lazy";
      const details = document.createElement("div");
      const name = document.createElement("div");
      name.className = "asset-name";
      name.textContent = asset.filename;
      const metadata = document.createElement("div");
      metadata.className = "asset-meta";
      metadata.textContent = `${asset.width} × ${asset.height} pixels · ${formatBytes(asset.size_bytes)}`;
      const counts = document.createElement("div");
      counts.className = "asset-counts";
      const accepted = document.createElement("span");
      accepted.className = "count-chip";
      accepted.textContent = `${asset.accepted_boxes} ${asset.accepted_boxes === 1 ? "box" : "boxes"} saved`;
      counts.append(accepted);
      if (asset.pending_boxes) {
        const review = document.createElement("span");
        review.className = "count-chip review";
        review.textContent = `${asset.pending_boxes} to review`;
        counts.append(review);
      }
      details.append(name, metadata, counts);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "asset-remove";
      remove.textContent = "Remove picture";
      remove.setAttribute("aria-label", `Remove picture ${asset.filename}`);
      remove.addEventListener("click", () => removeAsset(projectId, asset));
      const annotate = document.createElement("button");
      annotate.type = "button";
      annotate.className = "asset-annotate";
      const annotateText = asset.pending_boxes ? "Review suggestions" : "Mark items";
      annotate.textContent = annotateText;
      annotate.setAttribute("aria-label", `${annotateText} in ${asset.filename}`);
      annotate.addEventListener("click", () => openAnnotation(projectId, asset));
      const actions = document.createElement("div");
      actions.className = "asset-actions";
      actions.append(annotate, remove);
      row.append(image, details, actions);
      assetList.append(row);
    });
    assetStatus.textContent = body.assets.length
      ? `${plural(body.assets.length, "picture", "pictures")} in this project.`
      : "No pictures yet. Choose files above to add your first ones.";
    void refreshProjectCounts();
    refreshDatasets(projectId);
  } catch (error) {
    if (selectedId === projectId) assetStatus.textContent = `Could not load the pictures. ${error.message}`;
  }
}

function annotationEndpoint() {
  if (!activeAnnotationAsset) return "";
  return `/api/projects/${encodeURIComponent(activeAnnotationAsset.projectId)}/assets/${encodeURIComponent(activeAnnotationAsset.asset.id)}/annotations`;
}

function updateAnnotationHistoryControls() {
  const busy = annotationHistoryBusy || annotationMutationBusy;
  annotationUndo.disabled = busy || annotationUndoStack.length === 0;
  annotationRedo.disabled = busy || annotationRedoStack.length === 0;
}

function pushAnnotationHistory(entry) {
  annotationUndoStack.push(entry);
  if (annotationUndoStack.length > 100) annotationUndoStack.shift();
  annotationRedoStack = [];
  updateAnnotationHistoryControls();
}

function remapAnnotationHistoryId(previousId, nextId) {
  [...annotationUndoStack, ...annotationRedoStack].forEach(entry => {
    if (entry.currentId === previousId) entry.currentId = nextId;
  });
}

function annotationValues(box) {
  return {
    label: box.label,
    x: box.x,
    y: box.y,
    width: box.width,
    height: box.height
  };
}

// Update history also tracks review status so Undo can restore a proposal to "needs review".
function annotationState(box) {
  return { ...annotationValues(box), status: box.status };
}

const isProposal = box => box.origin === "model" && box.status === "suggested";

async function mutateAnnotation(method, annotationId = null, data = null) {
  const endpoint = annotationId
    ? `${annotationEndpoint()}/${encodeURIComponent(annotationId)}`
    : annotationEndpoint();
  const headers = {};
  const options = { method, headers };
  if (data) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(data);
  }
  return api(endpoint, options);
}

async function runAnnotationHistory(direction) {
  if (annotationHistoryBusy || annotationMutationBusy || drawingBox) return;
  const source = direction === "undo" ? annotationUndoStack : annotationRedoStack;
  const destination = direction === "undo" ? annotationRedoStack : annotationUndoStack;
  const entry = source[source.length - 1];
  if (!entry) return;
  annotationHistoryBusy = true;
  updateAnnotationHistoryControls();
  try {
    if (entry.type === "create") {
      if (direction === "undo") {
        await mutateAnnotation("DELETE", entry.currentId);
        selectedAnnotationId = null;
      } else {
        const previousId = entry.currentId;
        const body = await mutateAnnotation("POST", null, entry.data);
        entry.currentId = body.annotation.id;
        remapAnnotationHistoryId(previousId, entry.currentId);
        selectedAnnotationId = entry.currentId;
      }
    } else if (entry.type === "delete") {
      if (direction === "undo") {
        const previousId = entry.currentId;
        const body = await mutateAnnotation("POST", null, entry.data);
        entry.currentId = body.annotation.id;
        remapAnnotationHistoryId(previousId, entry.currentId);
        selectedAnnotationId = entry.currentId;
      } else {
        await mutateAnnotation("DELETE", entry.currentId);
        selectedAnnotationId = null;
      }
    } else {
      await mutateAnnotation("PATCH", entry.currentId, direction === "undo" ? entry.before : entry.after);
      selectedAnnotationId = entry.currentId;
    }
    source.pop();
    destination.push(entry);
    if (await loadBoxAnnotations()) {
      annotationStatus.textContent = `${direction === "undo" ? "Undid" : "Redid"} the last change.`;
    }
  } catch (error) {
    annotationStatus.textContent = `That change was not saved. ${error.message}`;
  } finally {
    annotationHistoryBusy = false;
    updateAnnotationHistoryControls();
    renderBoxAnnotationList();
  }
}

function setAnnotationZoom(nextZoom, resetScroll = false) {
  const previousZoom = annotationZoom;
  annotationZoom = Math.min(4, Math.max(0.5, Math.round(nextZoom * 100) / 100));
  annotationStage.style.zoom = String(annotationZoom);
  annotationZoomLevel.value = `${Math.round(annotationZoom * 100)}%`;
  annotationZoomIn.disabled = annotationZoom >= 4;
  annotationZoomOut.disabled = annotationZoom <= 0.5;
  annotationStageScroll.classList.toggle("is-zoomed", annotationZoom > 1);
  const ratio = previousZoom ? annotationZoom / previousZoom : 1;
  window.requestAnimationFrame(() => {
    if (resetScroll || annotationZoom === 1) {
      annotationStageScroll.scrollLeft = 0;
      annotationStageScroll.scrollTop = 0;
    } else {
      annotationStageScroll.scrollLeft =
        (annotationStageScroll.scrollLeft + annotationStageScroll.clientWidth / 2) * ratio -
        annotationStageScroll.clientWidth / 2;
      annotationStageScroll.scrollTop =
        (annotationStageScroll.scrollTop + annotationStageScroll.clientHeight / 2) * ratio -
        annotationStageScroll.clientHeight / 2;
    }
    drawAnnotations();
  });
}

async function openAnnotation(projectId, asset) {
  const project = projects.find(candidate => candidate.id === projectId);
  if (!project) return;
  activeAnnotationAsset = { projectId, asset };
  boxAnnotations = [];
  annotationUndoStack = [];
  annotationRedoStack = [];
  annotationHistoryBusy = false;
  annotationMutationBusy = false;
  updateAnnotationHistoryControls();
  drawingBox = null;
  selectedAnnotationId = null;
  boxEditPreview = null;
  annotationList.replaceChildren();
  annotationFilename.textContent = `${asset.filename} · ${asset.width} × ${asset.height} pixels`;
  annotationLabel.replaceChildren();
  project.labels.forEach(label => {
    const option = document.createElement("option");
    option.value = label;
    option.textContent = label;
    annotationLabel.append(option);
  });
  annotationLabel.disabled = project.labels.length === 0;
  annotationDialog.showModal();
  annotationHelpDetails.open = window.innerWidth > 850;
annotationAuto.hidden = !autolabelInfo.enabled;
annotationAcceptAll.hidden = true;
annotationAutoNote.textContent = "";
annotationAutolabel.disabled = !project.labels.length;
  setAnnotationZoom(1, true);
  annotationImage.src = asset.content_url;
  annotationStatus.textContent = project.labels.length
    ? "Loading boxes…"
    : "This project has no labels yet. Close this window, add at least one label, then open the picture again.";
  if (project.labels.length) await loadBoxAnnotations();
  annotationLabel.focus();
}

async function loadBoxAnnotations() {
  if (!activeAnnotationAsset) return;
  const requestedAsset = activeAnnotationAsset;
  try {
    const body = await api(annotationEndpoint());
    if (activeAnnotationAsset !== requestedAsset) return;
    boxAnnotations = body.annotations;
    if (!boxAnnotations.some(box => box.id === selectedAnnotationId)) selectedAnnotationId = null;
    renderBoxAnnotationList();
    drawAnnotations();
    const waiting = boxAnnotations.filter(isProposal).length;
    annotationAcceptAll.hidden = waiting === 0;
    const done = boxAnnotations.length - waiting;
    annotationStatus.textContent = boxAnnotations.length
      ? `${plural(boxAnnotations.length, "box", "boxes")} in this picture: ${done} accepted${waiting ? `, ${waiting} suggested by the computer and waiting for your review` : ""}.`
      : "No boxes yet. Pick a label and drag across the picture to draw one, or ask the computer to suggest boxes.";
    updateAnnotationHistoryControls();
    return true;
  } catch (error) {
    annotationStatus.textContent = `Could not load the boxes. ${error.message}`;
    return false;
  }
}

function renderBoxAnnotationList() {
  annotationList.replaceChildren();
  const project = projects.find(candidate => candidate.id === activeAnnotationAsset?.projectId);
  if (!project) return;
  boxAnnotations.forEach((box, index) => {
    const entry = document.createElement("article");
    entry.className = "annotation-entry";
entry.dataset.status = box.status;
const proposal = isProposal(box);
    const selectBox = document.createElement("button");
    selectBox.type = "button";
    selectBox.className = "annotation-select";
    selectBox.textContent = `Select box ${index + 1}`;
    selectBox.setAttribute("aria-label", `Select box ${index + 1}, ${box.label}`);
    selectBox.setAttribute("aria-pressed", String(box.id === selectedAnnotationId));
    selectBox.addEventListener("click", () => {
      selectedAnnotationId = box.id;
      renderBoxAnnotationList();
      drawAnnotations();
      annotationCanvas.focus();
    });
    const label = document.createElement("label");
    label.className = "visually-hidden";
    label.textContent = `Label for box ${index + 1}`;
    const select = document.createElement("select");
    select.setAttribute("aria-label", `Label for box ${index + 1}`);
    select.disabled = annotationHistoryBusy || annotationMutationBusy;
    project.labels.forEach(name => {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      select.append(option);
    });
    select.value = box.label;
    select.addEventListener("change", () => updateBoxAnnotation(box, { label: select.value }));
    const coords = document.createElement("div");
    coords.className = "annotation-coordinates";
    const pct = value => `${Math.round(value * 100)}%`;
    coords.textContent = `Box ${index + 1}: ${pct(box.x)} from the left, ${pct(box.y)} from the top, ${pct(box.width)} wide, ${pct(box.height)} tall`;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "annotation-remove";
    remove.disabled = annotationHistoryBusy || annotationMutationBusy;
    remove.textContent = proposal ? "Reject" : "Remove box";
    remove.setAttribute("aria-label", `${proposal ? "Reject suggested" : "Remove"} box ${index + 1}, ${box.label}`);
    remove.addEventListener("click", () => deleteBoxAnnotation(box));
    label.append(select);
    const actions = document.createElement("div");
actions.className = "annotation-entry-actions";
if (proposal) {
  const accept = document.createElement("button");
  accept.type = "button";
  accept.className = "annotation-accept";
  accept.disabled = annotationHistoryBusy || annotationMutationBusy;
  accept.textContent = "Accept";
  accept.setAttribute("aria-label", `Accept proposed box ${index + 1}, ${box.label}`);
  accept.addEventListener("click", () => updateBoxAnnotation(box, { status: "accepted" }));
  actions.append(accept);
}
actions.append(remove);
entry.append(selectBox);
if (box.origin === "model") {
  const tag = document.createElement("span");
  tag.className = proposal ? "proposal-tag" : "proposal-tag done";
  const score = box.confidence == null ? "" : ` · computer is ${Math.round(box.confidence * 100)}% sure`;
  tag.textContent = proposal ? `Needs your review${score}` : `Suggested by computer, accepted by you${score}`;
  tag.title = "The percentage is the computer’s own guess. It is not a measured accuracy, so check the box yourself.";
  entry.append(tag);
}
entry.append(label, coords, actions);
    annotationList.append(entry);
  });
}

function drawAnnotations(draft = null) {
  const bounds = annotationCanvas.getBoundingClientRect();
  if (!bounds.width || !bounds.height) return;
  const ratio = window.devicePixelRatio || 1;
  const pixelWidth = Math.round(bounds.width * ratio);
  const pixelHeight = Math.round(bounds.height * ratio);
  if (annotationCanvas.width !== pixelWidth || annotationCanvas.height !== pixelHeight) {
    annotationCanvas.width = pixelWidth;
    annotationCanvas.height = pixelHeight;
  }
  const context = annotationCanvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, bounds.width, bounds.height);
  context.lineWidth = 2;
  context.font = "12px -apple-system, BlinkMacSystemFont, sans-serif";
  boxAnnotations.forEach((box, index) => {
    const displayed = boxEditPreview?.id === box.id ? boxEditPreview : box;
    const x = displayed.x * bounds.width;
    const y = displayed.y * bounds.height;
    const width = displayed.width * bounds.width;
    const height = displayed.height * bounds.height;
    const selected = box.id === selectedAnnotationId;
    const proposal = isProposal(box);
    context.strokeStyle = selected ? "#65c9bd" : proposal ? "#ff9d8c" : "#f0b642";
    context.fillStyle = proposal ? "rgba(255, 157, 140, .08)" : "rgba(240, 182, 66, .10)";
    context.fillRect(x, y, width, height);
    context.lineWidth = selected ? 3 : 2;
    context.setLineDash(proposal ? [7, 5] : []);
    context.strokeRect(x, y, width, height);
    context.setLineDash([]);
    const badge = `${index + 1} · ${box.label}${proposal ? " · suggested" : ""}`;
    const badgeWidth = context.measureText(badge).width + 10;
    context.fillStyle = "#101211";
    const badgeX = Math.max(0, Math.min(x, bounds.width - badgeWidth));
    context.fillRect(badgeX, Math.max(0, y - 19), badgeWidth, 18);
    context.fillStyle = "#fffefa";
    context.fillText(badge, badgeX + 5, Math.max(12, y - 6));
    if (selected) {
      context.fillStyle = "#65c9bd";
      for (const [handleX, handleY] of [[x, y], [x + width, y + height]]) {
        context.fillRect(handleX - 5, handleY - 5, 10, 10);
      }
    }
  });
  if (draft) {
    const x = draft.x * bounds.width;
    const y = draft.y * bounds.height;
    context.setLineDash([6, 4]);
    context.strokeStyle = "#ffffff";
    context.strokeRect(x, y, draft.width * bounds.width, draft.height * bounds.height);
    context.setLineDash([]);
  }
}

function pointerCoordinates(event) {
  const bounds = annotationCanvas.getBoundingClientRect();
  return {
    x: Math.min(1, Math.max(0, (event.clientX - bounds.left) / bounds.width)),
    y: Math.min(1, Math.max(0, (event.clientY - bounds.top) / bounds.height))
  };
}

function boxAt(point) {
  return [...boxAnnotations].reverse().find(box =>
    point.x >= box.x && point.x <= box.x + box.width &&
    point.y >= box.y && point.y <= box.y + box.height
  );
}

function resizeHandleAt(point) {
  const box = boxAnnotations.find(candidate => candidate.id === selectedAnnotationId);
  const bounds = annotationCanvas.getBoundingClientRect();
  if (!box || !bounds.width || !bounds.height) return null;
  const handles = [
    { name: "nw", x: box.x, y: box.y },
    { name: "se", x: box.x + box.width, y: box.y + box.height }
  ];
  return handles.find(handle =>
    Math.abs((point.x - handle.x) * bounds.width) <= 12 &&
    Math.abs((point.y - handle.y) * bounds.height) <= 12
  )?.name || null;
}

function previewBoxEdit() {
  if (!drawingBox || drawingBox.mode === "create") return null;
  const { original, start, end, mode } = drawingBox;
  if (mode === "move") {
    return {
      ...original,
      x: Math.min(1 - original.width, Math.max(0, original.x + end.x - start.x)),
      y: Math.min(1 - original.height, Math.max(0, original.y + end.y - start.y))
    };
  }
  const minSize = 0.002;
  if (drawingBox.handle === "nw") {
    const right = original.x + original.width;
    const bottom = original.y + original.height;
    const x = Math.min(right - minSize, Math.max(0, end.x));
    const y = Math.min(bottom - minSize, Math.max(0, end.y));
    return { ...original, x, y, width: right - x, height: bottom - y };
  }
  return {
    ...original,
    width: Math.max(minSize, Math.min(1 - original.x, end.x - original.x)),
    height: Math.max(minSize, Math.min(1 - original.y, end.y - original.y))
  };
}

async function saveBoxAnnotation(box) {
  if (!activeAnnotationAsset || annotationHistoryBusy || annotationMutationBusy) return false;
  annotationMutationBusy = true;
  updateAnnotationHistoryControls();
  try {
    const body = await mutateAnnotation("POST", null, { label: annotationLabel.value, ...box });
    selectedAnnotationId = body.annotation.id;
    pushAnnotationHistory({
      type: "create",
      currentId: body.annotation.id,
      data: annotationValues(body.annotation)
    });
    await loadBoxAnnotations();
    return true;
  } catch (error) {
    annotationStatus.textContent = `That change was not saved. ${error.message}`;
    return false;
  } finally {
    annotationMutationBusy = false;
    updateAnnotationHistoryControls();
    renderBoxAnnotationList();
  }
}

async function updateBoxAnnotation(box, changes) {
  if (!activeAnnotationAsset || annotationHistoryBusy || annotationMutationBusy) return;
  annotationMutationBusy = true;
  updateAnnotationHistoryControls();
  try {
    const before = annotationState(box);
    const target = { ...annotationValues(box), ...changes };
    const body = await mutateAnnotation("PATCH", box.id, target);
    const after = annotationState(body.annotation);
    if (JSON.stringify(before) !== JSON.stringify(after)) {
      pushAnnotationHistory({ type: "update", currentId: box.id, before, after });
    }
    await loadBoxAnnotations();
  } catch (error) {
    annotationStatus.textContent = `That change was not saved. ${error.message}`;
  } finally {
    annotationMutationBusy = false;
    updateAnnotationHistoryControls();
    renderBoxAnnotationList();
  }
}

async function deleteBoxAnnotation(box) {
  if (!activeAnnotationAsset || annotationHistoryBusy || annotationMutationBusy) return;
  const proposal = isProposal(box);
  if (!proposal && !(await Athena.askConfirm({
    title: `Remove this “${box.label}” box?`,
    message: "You can bring it back with Undo until you close the picture window.",
    keepLabel: "Keep the box",
    confirmLabel: "Remove box"
  }))) return;
  if (!activeAnnotationAsset || annotationHistoryBusy || annotationMutationBusy) return;
  annotationMutationBusy = true;
  updateAnnotationHistoryControls();
  try {
    await mutateAnnotation("DELETE", box.id);
    // A rejected proposal cannot be restored as a person-drawn box without losing its provenance.
    if (!proposal) {
      pushAnnotationHistory({
        type: "delete",
        currentId: box.id,
        data: annotationValues(box)
      });
    }
    if (selectedAnnotationId === box.id) selectedAnnotationId = null;
    await loadBoxAnnotations();
  } catch (error) {
    annotationStatus.textContent = `That change was not saved. ${error.message}`;
  } finally {
    annotationMutationBusy = false;
    updateAnnotationHistoryControls();
    renderBoxAnnotationList();
  }
}

annotationCanvas.addEventListener("pointerdown", event => {
  if (!activeAnnotationAsset || annotationLabel.disabled || annotationHistoryBusy || annotationMutationBusy || event.button !== 0) return;
  const point = pointerCoordinates(event);
  const handle = resizeHandleAt(point);
  const hit = boxAt(point);
  if (handle && selectedAnnotationId) {
    const original = boxAnnotations.find(box => box.id === selectedAnnotationId);
    drawingBox = { mode: "resize", handle, original, pointerId: event.pointerId, start: point, end: point };
  } else if (hit) {
    selectedAnnotationId = hit.id;
    drawingBox = { mode: "move", original: hit, pointerId: event.pointerId, start: point, end: point };
    renderBoxAnnotationList();
  } else {
    selectedAnnotationId = null;
    drawingBox = { mode: "create", pointerId: event.pointerId, start: point, end: point };
    renderBoxAnnotationList();
  }
  annotationCanvas.setPointerCapture(event.pointerId);
  drawAnnotations();
});
annotationCanvas.addEventListener("pointermove", event => {
  if (!drawingBox || drawingBox.pointerId !== event.pointerId) return;
  drawingBox.end = pointerCoordinates(event);
  if (drawingBox.mode === "create") {
    const { start, end } = drawingBox;
    drawAnnotations({
      x: Math.min(start.x, end.x),
      y: Math.min(start.y, end.y),
      width: Math.abs(end.x - start.x),
      height: Math.abs(end.y - start.y)
    });
    return;
  }
  boxEditPreview = previewBoxEdit();
  drawAnnotations();
});
annotationCanvas.addEventListener("pointerup", async event => {
  if (!drawingBox || drawingBox.pointerId !== event.pointerId) return;
  drawingBox.end = pointerCoordinates(event);
  const operation = drawingBox;
  const updated = operation.mode === "create" ? null : previewBoxEdit();
  drawingBox = null;
  if (operation.mode === "create") {
    const { start, end } = operation;
    const box = {
      x: Math.min(start.x, end.x),
      y: Math.min(start.y, end.y),
      width: Math.abs(end.x - start.x),
      height: Math.abs(end.y - start.y)
    };
    if (box.width < 0.002 || box.height < 0.002) {
      annotationStatus.textContent = "That box is too small. Drag a little farther to draw around the item.";
      drawAnnotations();
      return;
    }
    await saveBoxAnnotation(box);
    return;
  }
  boxEditPreview = null;
  drawAnnotations();
  if (!updated || !operation.original) return;
  const changed = ["x", "y", "width", "height"].some(field =>
    Math.abs(updated[field] - operation.original[field]) > 0.00000001
  );
  if (changed) await updateBoxAnnotation(operation.original, updated);
});
annotationCanvas.addEventListener("pointercancel", () => {
  drawingBox = null;
  boxEditPreview = null;
  drawAnnotations();
});
annotationCanvas.addEventListener("keydown", event => {
  if (annotationHistoryBusy || annotationMutationBusy) return;
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "z") {
    event.preventDefault();
    void runAnnotationHistory(event.shiftKey ? "redo" : "undo");
    return;
  }
  if (!selectedAnnotationId || !["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(event.key)) return;
  if (event.repeat) return;
  const box = boxAnnotations.find(candidate => candidate.id === selectedAnnotationId);
  if (!box) return;
  event.preventDefault();
  const step = 0.01;
  const changes = { x: box.x, y: box.y, width: box.width, height: box.height };
  if (event.key === "ArrowLeft") {
    if (event.shiftKey) changes.width = Math.max(0.002, box.width - step);
    else changes.x = Math.max(0, box.x - step);
  } else if (event.key === "ArrowRight") {
    if (event.shiftKey) changes.width = Math.min(1 - box.x, box.width + step);
    else changes.x = Math.min(1 - box.width, box.x + step);
  } else if (event.key === "ArrowUp") {
    if (event.shiftKey) changes.height = Math.max(0.002, box.height - step);
    else changes.y = Math.max(0, box.y - step);
  } else if (event.shiftKey) {
    changes.height = Math.min(1 - box.y, box.height + step);
  } else {
    changes.y = Math.min(1 - box.height, box.y + step);
  }
  void updateBoxAnnotation(box, changes);
});
keyboardBoxForm.addEventListener("submit", async event => {
  event.preventDefault();
  if (annotationHistoryBusy || annotationMutationBusy) return;
  if (!activeAnnotationAsset || annotationLabel.disabled) {
    annotationStatus.textContent = "Add at least one label to this project before adding a box.";
    return;
  }
  const values = new FormData(keyboardBoxForm);
  const x = Number(values.get("x"));
  const y = Number(values.get("y"));
  const width = Number(values.get("width"));
  const height = Number(values.get("height"));
  if (x + width > 100 || y + height > 100) {
    annotationStatus.textContent = "That box would go past the edge of the picture. Make it narrower or shorter, or move it toward the top-left.";
    return;
  }
  const saved = await saveBoxAnnotation({
    x: x / 100,
    y: y / 100,
    width: width / 100,
    height: height / 100
  });
  if (saved) keyboardBoxForm.reset();
});
annotationImage.addEventListener("load", () => window.requestAnimationFrame(() => drawAnnotations()));
annotationImage.addEventListener("error", () => {
  annotationStatus.textContent = "Could not load this picture. It may have been removed. Close this window and reload the page.";
});
window.addEventListener("resize", () => drawAnnotations());
annotationDialog.addEventListener("close", () => {
  stopImageJobPolling();
  const returnToId = activeAnnotationAsset ? activeAnnotationAsset.asset.id : null;
  const reloaded = loadAssets(selectedId);
  reloaded.then(() => {
    const row = returnToId ? [...assetList.children].find(child => child.dataset.assetId === returnToId) : null;
    const target = (row && row.querySelector(".asset-annotate")) || imageAssetsHeading;
    target.focus({ preventScroll: true });
  });
  void refreshAutolabel(selectedId);
  activeAnnotationAsset = null;
  boxAnnotations = [];
  annotationUndoStack = [];
  annotationRedoStack = [];
  annotationHistoryBusy = false;
  annotationMutationBusy = false;
  updateAnnotationHistoryControls();
  drawingBox = null;
  selectedAnnotationId = null;
  boxEditPreview = null;
  annotationList.replaceChildren();
  drawAnnotations();
});
Athena.manageDialog(projectDialog);
annotationDialog.dataset.keepFocus = "true"; // the picture list is rebuilt when this closes; focus goes back after that
Athena.manageDialog(annotationDialog);
document.getElementById("close-annotation-dialog").addEventListener("click", () => annotationDialog.close());
document.getElementById("done-annotation").addEventListener("click", () => annotationDialog.close());
annotationZoomIn.addEventListener("click", () => setAnnotationZoom(annotationZoom + 0.25));
annotationZoomOut.addEventListener("click", () => setAnnotationZoom(annotationZoom - 0.25));
annotationZoomReset.addEventListener("click", () => setAnnotationZoom(1, true));
annotationUndo.addEventListener("click", () => void runAnnotationHistory("undo"));
annotationRedo.addEventListener("click", () => void runAnnotationHistory("redo"));

function snapshotNames(numbers) {
  const names = numbers.map(number => String(number));
  if (names.length === 1) return `snapshot ${names[0]}`;
  return `snapshots ${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

async function removeAsset(projectId, asset) {
  const sure = await Athena.askConfirm({
    title: `Remove the picture “${asset.filename}”?`,
    message: "This also removes all of its boxes and its stored file. This cannot be undone.",
    keepLabel: "Keep the picture",
    confirmLabel: "Remove picture"
  });
  if (!sure) return;
  const address = `/api/projects/${encodeURIComponent(projectId)}/assets/${encodeURIComponent(asset.id)}`;
  let result;
  try {
    try {
      result = await api(address, { method: "DELETE", expectStatus: [409] });
    } catch (error) {
      const inSnapshots = error.status === 409 && error.body && Array.isArray(error.body.snapshots) ? error.body.snapshots : [];
      if (!inSnapshots.length) throw error;
      // The picture is part of saved snapshots. Say what removing it would cost, and let the person choose.
      const names = snapshotNames(inSnapshots);
      const plural1 = inSnapshots.length === 1;
      const anyway = await Athena.askConfirm({
        title: `“${asset.filename}” is in ${names}`,
        message: [
          `If you remove this picture, ${plural1 ? "that snapshot keeps" : "those snapshots keep"} ${plural1 ? "its" : "their"} ${plural1 ? "number" : "numbers"} and ${plural1 ? "its" : "their"} counts, but ${plural1 ? "it" : "they"} can no longer be downloaded, because the picture would be missing.`,
          "Your other snapshots are not affected. You can save a new snapshot afterwards."
        ],
        keepLabel: "Keep the picture",
        confirmLabel: "Remove it anyway"
      });
      if (!anyway) {
        assetStatus.textContent = `The picture “${asset.filename}” was kept.`;
        return;
      }
      result = await api(`${address}?remove_from_snapshots=1`, { method: "DELETE" });
    }
  } catch (error) {
    assetStatus.textContent = `The picture was not removed. ${error.message}`;
    return;
  }
  await loadAssets(projectId);
  await refreshAutolabel(projectId);
  const marked = result.snapshots_marked_incomplete || [];
  assetStatus.textContent = `Removed “${asset.filename}”.${marked.length ? ` ${snapshotNames(marked).replace(/^./, letter => letter.toUpperCase())} can no longer be downloaded.` : ""}`;
  imageAssetsHeading.focus({ preventScroll: true });
}

function openProjectDialog() {
  formError.dataset.open = "false";
  formError.textContent = "";
  projectDialog.showModal();
  document.getElementById("project-name").focus();
}

function updateCustomModalityField() {
  const isCustom = document.getElementById("project-modality").value === "custom";
  customModalityField.hidden = !isCustom;
  customModalityInput.required = isCustom;
}

function csrfToken() {
  return document.querySelector('meta[name="csrf-token"]')?.content || "";
}

function showSessionEnded() {
  if (document.getElementById("session-banner")) return;
  const banner = document.createElement("p");
  banner.id = "session-banner";
  banner.className = "session-banner";
  banner.setAttribute("role", "alert");
  banner.append("Your session has ended, so your latest changes were not saved. ");
  const link = document.createElement("a");
  link.href = "/login/";
  link.textContent = "Sign in again";
  banner.append(link, " to keep working.");
  document.querySelector(".main").prepend(banner);
}

// --- Save state -------------------------------------------------------------------------
// One place tells the person whether their work is safe. Every write goes through api().
const saveStates = [
  { box: document.getElementById("save-state"), text: document.getElementById("save-state-text") },
  { box: document.getElementById("annotation-save-state"), text: document.getElementById("annotation-save-state-text") }
];
let pendingWrites = 0;

function setSaveState(state, message = "") {
  const text = {
    saving: "Saving…",
    saved: `All changes saved at ${new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}.`,
    error: `Not saved. ${message}`
  }[state] || "Nothing to save yet.";
  saveStates.forEach(({ box, text: label }) => {
    box.dataset.state = state;
    label.textContent = text;
  });
}

// "name: This field is required." -> "Project name is required. Fill it in and try again."
const fieldNames = { name: "Project name", modality: "Kind of files", custom_modality: "Kind of files", task: "What the project is for", labels: "Labels" };
const knownMessages = [
  [/auto-label job is already queued or running/i, "Auto-suggest is already working on this project. Wait for it to finish, or press “Stop suggesting”, then try again."],
  [/auto-labelling is turned off/i, "Auto-suggest is turned off for this console. Ask your administrator to turn it on."],
  [/add at least one label to this project before auto-labelling/i, "Add at least one label to this project before asking for suggestions."],
  [/add images to this project first/i, "Add pictures to this project first, then ask for suggestions."]
];
function friendlyError(message) {
  const known = knownMessages.find(([pattern]) => pattern.test(message));
  if (known) return known[1];
  const match = /^(name|modality|custom_modality|task|labels): (.*)$/s.exec(message);
  if (!match) return message;
  const [, field, detail] = match;
  if (detail === "This field is required.") return `${fieldNames[field]} is required. Fill it in and try again.`;
  return field === "labels" ? detail : `${fieldNames[field]}: ${detail}`;
}

// All server calls go through here so every failure reads as a plain-language message.
async function api(url, options = {}) {
  const { headers = {}, expectStatus = [], ...rest } = options;
  const writes = String(rest.method || "GET").toUpperCase() !== "GET";
  if (writes) {
    pendingWrites += 1;
    setSaveState("saving");
  }
  // `quiet` is for a refusal the page handles itself (for example "this picture is in a snapshot"): it is not a failed save.
  const finish = (error, quiet = false) => {
    if (!writes) return;
    pendingWrites -= 1;
    if (quiet) { if (pendingWrites === 0) setSaveState("idle"); }
    else if (error) setSaveState("error", error.message);
    else if (pendingWrites === 0) setSaveState("saved");
  };
  let response;
  try {
    response = await fetch(url, {
      credentials: "same-origin",
      ...rest,
      headers: { Accept: "application/json", "X-CSRFToken": csrfToken(), ...headers }
    });
  } catch (error) {
    const failure = new Error("Could not reach the Athena server. Check your connection and try again.");
    finish(failure);
    throw failure;
  }
  let body = {};
  try {
    body = await response.json();
  } catch (error) {
    body = {};
  }
  if (response.status === 401) {
    showSessionEnded();
    const failure = new Error("Your session has ended. Sign in again to continue.");
    finish(failure);
    throw failure;
  }
  if (!response.ok) {
    let message;
    if (response.status === 403 && !body.error) message = "This page is out of date. Reload it and try again.";
    else if (body.error) message = friendlyError(body.error);
    else message = `The server could not complete that request (error ${response.status}). Try again in a moment; if it keeps happening, tell your administrator.`;
    const failure = new Error(message);
    failure.status = response.status;
    failure.body = body;
    finish(failure, expectStatus.includes(response.status));
    throw failure;
  }
  finish(null);
  return body;
}

function showNotice(message) {
  notice.dataset.open = "true";
  noticeMessage.textContent = message;
}

async function deleteCurrentProject() {
  const selected = selectedProject();
  if (!selected || !(await Athena.askConfirm({
    title: `Delete the project “${selected.name}”?`,
    message: "This removes the project, its labels, its boxes and all of its pictures from the server. This cannot be undone.",
    keepLabel: "Keep the project",
    confirmLabel: "Delete project"
  }))) return;
  deleteSelected.disabled = true;
  try {
    await api(`/api/projects/${encodeURIComponent(selected.id)}`, { method: "DELETE" });
    stopAutolabelPolling();
    projects = projects.filter(project => project.id !== selected.id);
    selectedId = null;
    history.replaceState(null, "", location.pathname);
    imageAssetsPanel.hidden = true;
    autolabelPanel.hidden = true;
    assetList.replaceChildren();
    render();
    showNotice(`The project “${selected.name}” was deleted.`);
    document.getElementById("open-project-dialog").focus();
  } catch (error) {
    showNotice(`The project was not deleted. ${error.message}`);
  } finally {
    deleteSelected.disabled = false;
  }
}

document.querySelectorAll(".filter").forEach(button => button.addEventListener("click", () => {
  currentFilter = button.dataset.filter;
  document.querySelectorAll(".filter").forEach(candidate => candidate.setAttribute("aria-pressed", String(candidate === button)));
  render();
}));
document.getElementById("search").addEventListener("input", render);
document.getElementById("open-project-dialog").addEventListener("click", openProjectDialog);
emptyCreate.addEventListener("click", openProjectDialog);
document.getElementById("close-project-dialog").addEventListener("click", () => projectDialog.close());
document.getElementById("cancel-project").addEventListener("click", () => projectDialog.close());
document.getElementById("project-modality").addEventListener("change", updateCustomModalityField);
deleteSelected.addEventListener("click", deleteCurrentProject);
imageUploadForm.addEventListener("submit", async event => {
  event.preventDefault();
  const selected = selectedProject();
  const files = Array.from(imageUploadInput.files || []);
  if (!selected || selected.modality !== "image") {
    assetStatus.textContent = "Open a picture project before adding pictures.";
    return;
  }
  if (!files.length) {
    assetStatus.textContent = "Choose one or more pictures first, then press “Add pictures”.";
    return;
  }
  // Look at each file first, so one wrong file does not cost the person all the others.
  const skipped = [];
  const usable = [];
  files.forEach(file => {
    const isPicture = /^image\/(jpeg|png|webp)$/.test(file.type) || (!file.type && /\.(jpe?g|png|webp)$/i.test(file.name));
    if (!isPicture) skipped.push(`“${file.name}” is not a JPEG, PNG or WebP picture`);
    else if (file.size > MAX_PICTURE_BYTES) skipped.push(`“${file.name}” is ${Math.ceil(file.size / 1048576)} MB, and the limit is 20 MB`);
    else usable.push(file);
  });
  const skippedNote = skipped.length ? ` Left out: ${skipped.join("; ")}.` : "";
  if (!usable.length) {
    assetStatus.textContent = `Nothing was added.${skippedNote}`;
    return;
  }
  const submit = document.getElementById("upload-images");
  submit.disabled = true;
  assetStatus.textContent = "Checking and saving pictures…";
  try {
    const upload = new FormData();
    usable.forEach(file => upload.append("images", file));
    const body = await api(`/api/projects/${encodeURIComponent(selected.id)}/assets`, {
      method: "POST",
      body: upload
    });
    imageUploadForm.reset();
    await loadAssets(selected.id);
    let followUp = "";
    if (autoAfterUpload.checked && !autoAfterUploadField.hidden) {
      try {
        await api(`/api/projects/${encodeURIComponent(selected.id)}/autolabel`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ only_unlabeled: true, min_confidence: Number(autolabelStrictness.value) })
        });
        followUp = " Auto-suggest has started; its suggestions will need your review.";
      } catch (error) {
        followUp = ` Auto-suggest did not start. ${error.message}`;
      }
    }
    await refreshAutolabel(selected.id);
    assetStatus.textContent = `Added ${plural(body.assets.length, "picture", "pictures")}. ${assetStatus.textContent}${skippedNote}${followUp}`;
  } catch (error) {
    assetStatus.textContent = `The pictures were not added. ${error.message}${skippedNote}`;
  } finally {
    submit.disabled = false;
  }
});
projectForm.addEventListener("submit", async event => {
  event.preventDefault();
  formError.dataset.open = "false";
  formError.textContent = "";
  const formData = new FormData(projectForm);
  const modality = String(formData.get("modality"));
  const labels = String(formData.get("labels"))
    .split(",")
    .map(label => label.trim())
    .filter(Boolean);
  const payload = {
    name: String(formData.get("name")),
    modality,
    task: String(formData.get("task")),
    labels
  };
  if (modality === "custom") payload.custom_modality = String(formData.get("custom_modality"));
  const submit = projectForm.querySelector('[type="submit"]');
  submit.disabled = true;
  try {
    const body = await api("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    projects.unshift(body.project);
    selectedId = body.project.id;
    projectForm.reset();
    updateCustomModalityField();
    projectDialog.close();
    render();
    void loadAssets(body.project.id);
    void refreshAutolabel(body.project.id);
    showNotice(`The project “${body.project.name}” was created. Next: ${body.project.modality === "image" ? "add some pictures" : "check its labels"}.`);
    revealWorkspace();
  } catch (error) {
    formError.textContent = `The project was not created. ${error.message}`;
    formError.dataset.open = "true";
  } finally {
    submit.disabled = false;
  }
});

// --- Auto-suggest ---------------------------------------------------------------------
const percent = value => `${Math.round(value * 100)}%`;
const isActiveJob = job => Boolean(job) && (job.status === "queued" || job.status === "running");
const plural = (count, one, many) => `${count.toLocaleString()} ${count === 1 ? one : many}`;

function renderAutolabelBadge() {
  const badge = document.getElementById("autolabel-badge");
  badge.textContent = !autolabelInfo.checked ? "Checking…" : !autolabelInfo.enabled ? "Off" : autolabelInfo.worker_online ? "Ready" : "Not running";
  badge.title = !autolabelInfo.enabled
    ? "Auto-suggest is turned off for this console."
    : autolabelInfo.worker_online
      ? "The helper program that suggests boxes is running."
      : "The helper program that suggests boxes has not reported in. Requests wait in line until an administrator starts it.";
}

async function loadAutolabelStatus() {
  try {
    autolabelInfo = { ...(await api("/api/autolabel")), checked: true };
  } catch (error) {
    autolabelInfo = { enabled: false, worker_online: false, checked: true };
  }
  renderAutolabelBadge();
}

function stopAutolabelPolling() {
  window.clearTimeout(autolabelTimer);
  autolabelTimer = null;
  autolabelLastJob = null;
}

function stopImageJobPolling() {
  window.clearInterval(imageJobTimer);
  imageJobTimer = null;
}

function describeJob(job, workerOnline, workerLoading = false) {
  const images = plural(job.total_assets, "picture", "pictures");
  if (job.status === "queued" || (job.status === "running" && !job.total_assets)) {
    if (workerOnline && workerLoading) {
      return "Waiting to start. The helper program is getting ready. The very first time, this downloads about 1 GB and can take several minutes; later starts take about a minute.";
    }
  }
  if (job.status === "queued") {
    return workerOnline
      ? "Waiting to start. The helper program will begin shortly."
      : "Waiting in line, but the helper program that suggests boxes is not running. Ask your administrator to start it (see the deployment guide); the request is kept until then.";
  }
  if (job.status === "running") {
    return job.total_assets
      ? `Working on picture ${Math.min(job.processed_assets + 1, job.total_assets)} of ${job.total_assets}. ${plural(job.boxes_proposed, "box", "boxes")} suggested so far.`
      : "Getting ready and finding pictures…";
  }
  if (job.status === "succeeded") {
    return `Finished: ${plural(job.boxes_proposed, "box", "boxes")} suggested across ${images}. They all need your review.${job.error ? ` ${job.error}` : ""}`;
  }
  if (job.status === "cancelled" && !job.total_assets) return "Stopped before it started. Nothing was suggested.";
  if (job.status === "cancelled") return `Stopped after ${job.processed_assets} of ${images}. Suggestions made so far are kept for your review.`;
  return `It did not finish: ${job.error || "the helper program reported an error."} Fix the problem if you can, then run it again, or tell your administrator.`;
}

async function refreshAutolabel(projectId) {
  const project = projects.find(candidate => candidate.id === projectId);
  window.clearTimeout(autolabelTimer);
  autolabelTimer = null;
  if (!project || project.modality !== "image" || selectedId !== projectId) {
    autolabelPanel.hidden = true;
    return;
  }
  try {
    const info = await api(`/api/projects/${encodeURIComponent(projectId)}/autolabel`);
    if (selectedId !== projectId) return;
    autolabelInfo = { ...info, checked: true };
    renderAutolabelBadge();
    autolabelPanel.hidden = !info.enabled;
    autoAfterUploadField.hidden = !info.enabled || project.labels.length === 0;
    if (!info.enabled) return;
    const job = info.jobs[0] || null;
    const active = isActiveJob(job);
    if (autolabelLastJob && isActiveJob(autolabelLastJob) && !active) void loadAssets(projectId);
    autolabelLastJob = job;
    autolabelRun.disabled = active || project.labels.length === 0 || currentAssets.length === 0;
    autolabelCancel.hidden = !active;
    autolabelCancel.disabled = Boolean(job?.cancel_requested);
    autolabelCancel.textContent = job?.cancel_requested ? "Stopping…" : "Stop suggesting";
    autolabelState.textContent = active
      ? (job.status === "queued" ? "Waiting to start" : "Working")
      : info.worker_online ? "Helper program is running" : "Helper program is not running";
    autolabelState.dataset.tone = !active && !info.worker_online ? "warn" : "";
    const showJob = job && (active || Date.now() - Date.parse(job.finished_at || job.created_at) < 10 * 60 * 1000);
    autolabelProgress.hidden = !showJob;
    if (showJob) {
      autolabelBar.max = Math.max(job.total_assets, 1);
      autolabelBar.value = job.status === "succeeded" ? autolabelBar.max : job.processed_assets;
      autolabelProgressText.textContent = describeJob(job, info.worker_online, info.worker_loading);
    }
    if (!project.labels.length) {
      autolabelState.textContent = "Add labels first";
      autolabelState.dataset.tone = "warn";
    }
    autolabelReview.hidden = info.pending_boxes === 0;
    autolabelReviewText.textContent = `${plural(info.pending_boxes, "suggested box needs", "suggested boxes need")} your review. Nothing here counts until you accept it. The percentage is the computer’s own guess, not a measured accuracy, so look at the boxes before accepting many at once.`;
    if (active) autolabelTimer = window.setTimeout(() => refreshAutolabel(projectId), 2000);
  } catch (error) {
    if (selectedId === projectId) {
      autolabelPanel.hidden = false;
      autolabelState.textContent = `Could not check suggestions. ${error.message}`;
      autolabelState.dataset.tone = "warn";
    }
  }
}

autolabelForm.addEventListener("submit", async event => {
  event.preventDefault();
  const project = selectedProject();
  if (!project) return;
  autolabelRun.disabled = true;
  try {
    await api(`/api/projects/${encodeURIComponent(project.id)}/autolabel`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ only_unlabeled: true, min_confidence: Number(autolabelStrictness.value) })
    });
  } catch (error) {
    autolabelProgress.hidden = false;
    autolabelProgressText.textContent = `Suggestions were not started. ${error.message}`;
  }
  await refreshAutolabel(project.id);
});

autolabelCancel.addEventListener("click", async () => {
  const job = autolabelLastJob;
  if (!job || !selectedId) return;
  autolabelCancel.disabled = true;
  try {
    await api(`/api/projects/${encodeURIComponent(selectedId)}/autolabel/${encodeURIComponent(job.id)}/cancel`, { method: "POST" });
  } catch (error) {
    autolabelProgressText.textContent = `Could not stop it. ${error.message}`;
  }
  await refreshAutolabel(selectedId);
});

async function reviewProposals(projectId, body) {
  return api(`/api/projects/${encodeURIComponent(projectId)}/annotations/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
}

document.getElementById("autolabel-accept").addEventListener("click", async () => {
  if (!selectedId) return;
  try {
    const result = await reviewProposals(selectedId, { action: "accept", min_confidence: Number(autolabelThreshold.value) });
    assetStatus.textContent = `Accepted ${plural(result.updated, "suggested box", "suggested boxes")}. ${result.pending_boxes ? `${plural(result.pending_boxes, "box", "boxes")} still need your review.` : "Nothing is waiting for review."}`;
  } catch (error) {
    assetStatus.textContent = `Suggestions were not accepted. ${error.message}`;
  }
  await loadAssets(selectedId);
  await refreshAutolabel(selectedId);
});

document.getElementById("autolabel-reject").addEventListener("click", async () => {
  if (!selectedId || !(await Athena.askConfirm({
    title: "Reject every suggested box?",
    message: "All suggested boxes in this project will be removed. Boxes you drew or already accepted are kept.",
    keepLabel: "Keep the suggestions",
    confirmLabel: "Reject all suggestions"
  }))) return;
  try {
    const result = await reviewProposals(selectedId, { action: "reject" });
    assetStatus.textContent = `Rejected ${plural(result.updated, "suggested box", "suggested boxes")}.`;
  } catch (error) {
    assetStatus.textContent = `Suggestions were not rejected. ${error.message}`;
  }
  await loadAssets(selectedId);
  await refreshAutolabel(selectedId);
});

annotationAutolabel.addEventListener("click", async () => {
  if (!activeAnnotationAsset) return;
  const { projectId, asset } = activeAnnotationAsset;
  annotationAutolabel.disabled = true;
  annotationAutoNote.textContent = "Request sent. Suggestions will appear here when the helper program finishes this picture.";
  try {
    await api(`/api/projects/${encodeURIComponent(projectId)}/autolabel`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ asset_id: asset.id, only_unlabeled: false, min_confidence: Number(autolabelStrictness.value) })
    });
  } catch (error) {
    annotationAutoNote.textContent = `Suggestions were not started. ${error.message}`;
    annotationAutolabel.disabled = false;
    return;
  }
  stopImageJobPolling();
  imageJobTimer = window.setInterval(async () => {
    if (!activeAnnotationAsset || activeAnnotationAsset.asset.id !== asset.id) return stopImageJobPolling();
    try {
      const info = await api(`/api/projects/${encodeURIComponent(projectId)}/autolabel`);
      const job = info.jobs[0];
      if (isActiveJob(job)) {
        annotationAutoNote.textContent = describeJob(job, info.worker_online, info.worker_loading);
        return;
      }
      stopImageJobPolling();
      annotationAutoNote.textContent = job ? describeJob(job, info.worker_online, info.worker_loading) : "";
      annotationAutolabel.disabled = false;
      await loadBoxAnnotations();
      renderBoxAnnotationList();
    } catch (error) {
      stopImageJobPolling();
      annotationAutoNote.textContent = error.message;
      annotationAutolabel.disabled = false;
    }
  }, 2000);
});

annotationAcceptAll.addEventListener("click", async () => {
  if (!activeAnnotationAsset) return;
  try {
    const result = await reviewProposals(activeAnnotationAsset.projectId, { action: "accept", asset_id: activeAnnotationAsset.asset.id });
    await loadBoxAnnotations();
    renderBoxAnnotationList();
    annotationStatus.textContent = `Accepted ${plural(result.updated, "suggested box", "suggested boxes")} on this picture. ${annotationStatus.textContent}`;
  } catch (error) {
    annotationStatus.textContent = `Suggestions were not accepted. ${error.message}`;
  }
});

// --- Label editor ---------------------------------------------------------------------
// Edits are drafted locally and sent in one request, so a rename and the boxes it touches
// change together on the server. Each row remembers the name it started with (`was`).
const labelsPanel = document.getElementById("labels-panel");
const labelListEl = document.getElementById("label-list");
const labelAddForm = document.getElementById("label-add-form");
const labelNew = document.getElementById("label-new");
const labelError = document.getElementById("label-error");
const labelSave = document.getElementById("label-save");
const labelDiscard = document.getElementById("label-discard");
const labelsDirtyNote = document.getElementById("labels-dirty");
const MAX_LABELS = 50;
const MAX_LABEL_LENGTH = 64;
let labelDraft = [];
let labelDraftProjectId = null;
let labelDraftSignature = "";
let labelKeyCounter = 0;
let labelSaving = false;

const selectedProject = () => projects.find(project => project.id === selectedId) || null;
const labelUses = (project, name) => (name && project.label_counts && project.label_counts[name]) || 0;
const labelSignature = project => JSON.stringify([project.id, project.labels, project.label_counts || {}]);

function resetLabelDraft(project) {
  labelDraft = project.labels.map(name => ({ key: ++labelKeyCounter, was: name, name, removed: false, moveTo: "" }));
  labelDraftProjectId = project.id;
  labelDraftSignature = labelSignature(project);
}

function labelsAreDirty() {
  const project = selectedProject();
  if (!project || labelDraftProjectId !== project.id) return false;
  return !(labelDraft.length === project.labels.length &&
    labelDraft.every((item, index) => !item.removed && item.was === project.labels[index] && item.name.trim() === project.labels[index]));
}

function setLabelError(message = "") {
  labelError.textContent = message;
  labelError.dataset.open = message ? "true" : "false";
}

function updateLabelControls() {
  const dirty = labelsAreDirty();
  labelSave.disabled = !dirty || labelSaving;
  labelDiscard.disabled = !dirty || labelSaving;
  labelsDirtyNote.dataset.state = dirty ? "dirty" : "clean";
  labelsDirtyNote.textContent = dirty ? "You have unsaved changes. Press “Save labels” to keep them." : "No unsaved changes.";
}

function refreshLabelNotes() {
  const project = selectedProject();
  if (!project) return;
  labelListEl.querySelectorAll("[data-label-key]").forEach(row => {
    const item = labelDraft.find(candidate => candidate.key === Number(row.dataset.labelKey));
    const note = row.querySelector(".label-note");
    if (!item || !note) return;
    const uses = item.was ? labelUses(project, item.was) : 0;
    const renamed = item.was && item.name.trim() !== item.was;
    note.textContent = uses
      ? `Used on ${plural(uses, "box", "boxes")}.${renamed ? " Saving will rename them too." : ""}`
      : item.was ? "Not used on any box yet." : "New label. Not saved yet.";
  });
}

function renderLabelEditor(focus = null) {
  const project = selectedProject();
  labelListEl.replaceChildren();
  if (!project) return;
  const active = labelDraft.filter(item => !item.removed);
  labelDraft.forEach(item => {
    const uses = item.was ? labelUses(project, item.was) : 0;
    const row = document.createElement("li");
    row.className = "label-row";
    row.dataset.labelKey = String(item.key);
    row.dataset.removed = String(item.removed);
    if (item.removed) {
      const text = document.createElement("div");
      text.className = "label-removed";
      const title = document.createElement("strong");
      title.textContent = `“${item.name}” will be removed when you save.`;
      text.append(title);
      if (uses) {
        const move = document.createElement("label");
        move.className = "label-move";
        move.append(`${plural(uses, "box still uses", "boxes still use")} it. Move ${uses === 1 ? "it" : "them"} to: `);
        const select = document.createElement("select");
        const blank = document.createElement("option");
        blank.value = "";
        blank.textContent = "Choose a label…";
        select.append(blank);
        active.forEach(other => {
          const option = document.createElement("option");
          option.value = String(other.key);
          option.textContent = other.name.trim() || "(unnamed label)";
          select.append(option);
        });
        select.value = active.some(other => String(other.key) === item.moveTo) ? item.moveTo : "";
        select.addEventListener("change", () => { item.moveTo = select.value; setLabelError(); });
        move.append(select);
        text.append(move);
      } else {
        const none = document.createElement("span");
        none.className = "label-note";
        none.textContent = "No boxes use it, so nothing else changes.";
        text.append(none);
      }
      const keep = document.createElement("button");
      keep.type = "button";
      keep.className = "secondary";
      keep.textContent = "Keep this label";
      keep.setAttribute("aria-label", `Keep the label “${item.name}”`);
      keep.addEventListener("click", () => {
        item.removed = false;
        setLabelError();
        renderLabelEditor({ key: item.key, part: "name" });
      });
      row.append(text, keep);
      labelListEl.append(row);
      return;
    }
    const position = active.indexOf(item);
    const number = document.createElement("span");
    number.className = "label-num";
    number.textContent = String(position + 1);
    number.setAttribute("aria-hidden", "true");
    const field = document.createElement("div");
    field.className = "label-field";
    const caption = document.createElement("label");
    caption.className = "visually-hidden";
    caption.htmlFor = `label-name-${item.key}`;
    caption.textContent = `Label ${position + 1} name`;
    const input = document.createElement("input");
    input.id = `label-name-${item.key}`;
    input.className = "label-name-input";
    input.value = item.name;
    input.maxLength = MAX_LABEL_LENGTH;
    input.autocomplete = "off";
    input.addEventListener("input", () => {
      item.name = input.value;
      setLabelError();
      refreshLabelNotes();
      updateLabelControls();
    });
    const note = document.createElement("span");
    note.className = "label-note";
    field.append(caption, input, note);
    const actions = document.createElement("div");
    actions.className = "label-actions";
    const makeButton = (part, text, hint, onClick, disabled) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = part === "remove" ? "secondary danger" : "secondary";
      button.dataset.part = part;
      button.textContent = text;
      button.setAttribute("aria-label", `${hint} “${item.name || "unnamed label"}”`);
      button.disabled = disabled;
      button.addEventListener("click", onClick);
      return button;
    };
    const move = delta => () => {
      const from = labelDraft.indexOf(item);
      let to = from + delta;
      while (labelDraft[to] && labelDraft[to].removed) to += delta;
      if (!labelDraft[to]) return;
      labelDraft.splice(from, 1);
      labelDraft.splice(to, 0, item);
      setLabelError();
      const stillCan = delta < 0 ? labelDraft.slice(0, labelDraft.indexOf(item)).some(other => !other.removed) : labelDraft.slice(labelDraft.indexOf(item) + 1).some(other => !other.removed);
      renderLabelEditor({ key: item.key, part: stillCan ? (delta < 0 ? "up" : "down") : "name" });
      labelsDirtyNote.textContent = `“${item.name}” moved to position ${active.indexOf(item) + 1 + delta} of ${active.length}. You have unsaved changes.`;
    };
    actions.append(
      makeButton("up", "Move up", "Move up:", move(-1), position === 0),
      makeButton("down", "Move down", "Move down:", move(1), position === active.length - 1),
      makeButton("remove", "Remove", "Remove the label", () => {
        if (item.was === null) {
          labelDraft = labelDraft.filter(candidate => candidate !== item);
        } else {
          item.removed = true;
          item.moveTo = "";
        }
        setLabelError();
        renderLabelEditor({ key: null, part: "list" });
      }, false)
    );
    row.append(number, field, actions);
    labelListEl.append(row);
  });
  if (!labelDraft.length) {
    const empty = document.createElement("li");
    empty.className = "label-empty";
    empty.textContent = "No labels yet. Type a name below and press “Add label”.";
    labelListEl.append(empty);
  }
  refreshLabelNotes();
  updateLabelControls();
  if (focus) {
    const target = focus.key == null
      ? (labelListEl.querySelector("button, input, select") || labelNew)
      : labelListEl.querySelector(`[data-label-key="${focus.key}"] ${focus.part === "name" ? "input" : `[data-part="${focus.part}"]`}`)
        || labelListEl.querySelector(`[data-label-key="${focus.key}"] input`);
    (target || labelNew).focus();
  }
}

function syncLabelEditor(project) {
  const changedProject = labelDraftProjectId !== project.id;
  if (changedProject || (!labelsAreDirty() && labelDraftSignature !== labelSignature(project))) {
    resetLabelDraft(project);
    setLabelError();
    renderLabelEditor();
  } else {
    updateLabelControls();
  }
}

function validateLabelDraft() {
  const project = selectedProject();
  const active = labelDraft.filter(item => !item.removed);
  if (active.length > MAX_LABELS) return { error: `A project can have at most ${MAX_LABELS} labels. Remove some and try again.` };
  const seen = new Map();
  for (const [index, item] of active.entries()) {
    const name = item.name.trim();
    if (!name) return { error: `Label ${index + 1} is empty. Type a name, or remove that label.`, key: item.key };
    if (name.length > MAX_LABEL_LENGTH) return { error: `“${name.slice(0, 20)}…” is too long. Label names can be up to ${MAX_LABEL_LENGTH} characters.`, key: item.key };
    const lowered = name.toLowerCase();
    if (seen.has(lowered)) return { error: `“${name}” appears twice (capital letters do not make a label different). Give each label its own name.`, key: item.key };
    seen.set(lowered, item.key);
  }
  const reassign = {};
  for (const item of labelDraft.filter(candidate => candidate.removed)) {
    const uses = labelUses(project, item.was);
    if (!uses) continue;
    const target = active.find(other => String(other.key) === item.moveTo);
    if (!target) return { error: `Choose where the ${plural(uses, "box", "boxes")} labelled “${item.name}” should go, or press “Keep this label”.` };
    reassign[item.was] = target.name.trim();
  }
  const labels = active.map(item => (item.was === null ? item.name.trim() : { name: item.name.trim(), was: item.was }));
  return { payload: Object.keys(reassign).length ? { labels, reassign } : { labels } };
}

async function saveLabels() {
  const project = selectedProject();
  if (!project || labelSaving) return;
  const checked = validateLabelDraft();
  if (checked.error) {
    setLabelError(checked.error);
    const field = checked.key ? document.getElementById(`label-name-${checked.key}`) : null;
    if (field) field.focus();
    return;
  }
  labelSaving = true;
  setLabelError();
  updateLabelControls();
  try {
    const body = await api(`/api/projects/${encodeURIComponent(project.id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(checked.payload)
    });
    projects = projects.map(candidate => (candidate.id === project.id ? body.project : candidate));
    resetLabelDraft(body.project);
    labelSaving = false;
    render();
    renderLabelEditor();
    showNotice(`Labels saved.${body.boxes_updated ? ` ${plural(body.boxes_updated, "existing box was", "existing boxes were")} updated to match.` : ""}`);
    void refreshAutolabel(project.id);
    void loadAssets(project.id);
  } catch (error) {
    labelSaving = false;
    setLabelError(`Your label changes were not saved. ${error.message}`);
    updateLabelControls();
  }
}

labelAddForm.addEventListener("submit", event => {
  event.preventDefault();
  const project = selectedProject();
  if (!project) return;
  const name = labelNew.value.trim();
  const active = labelDraft.filter(item => !item.removed);
  if (!name) return setLabelError("Type a name for the new label first, then press “Add label”.");
  if (name.length > MAX_LABEL_LENGTH) return setLabelError(`That name is too long. Label names can be up to ${MAX_LABEL_LENGTH} characters.`);
  if (active.length >= MAX_LABELS) return setLabelError(`A project can have at most ${MAX_LABELS} labels. Remove one before adding another.`);
  if (active.some(item => item.name.trim().toLowerCase() === name.toLowerCase())) return setLabelError(`“${name}” is already a label in this project. Choose a different name.`);
  labelDraft.push({ key: ++labelKeyCounter, was: null, name, removed: false, moveTo: "" });
  labelNew.value = "";
  setLabelError();
  renderLabelEditor();
  labelsDirtyNote.textContent = `Added “${name}”. You have unsaved changes; press “Save labels” to keep them.`;
  labelNew.focus();
});
labelSave.addEventListener("click", saveLabels);
labelDiscard.addEventListener("click", () => {
  const project = selectedProject();
  if (!project) return;
  resetLabelDraft(project);
  setLabelError();
  renderLabelEditor();
  labelsDirtyNote.textContent = "Changes discarded.";
});
window.addEventListener("beforeunload", event => {
  if (labelsAreDirty()) {
    event.preventDefault();
    event.returnValue = "";
  }
});

// --- Training sets (snapshots) -------------------------------------------------------------
// Plain-language front end for the dataset API: readiness check, frozen snapshots, and downloads.
const datasetPanel = document.getElementById("dataset-panel");
const datasetHeading = document.getElementById("dataset-heading");
const datasetCountNote = document.getElementById("dataset-count-note");
const datasetRecheck = document.getElementById("dataset-recheck");
const datasetHeadline = document.getElementById("dataset-headline");
const datasetFacts = document.getElementById("dataset-facts");
const datasetProblems = document.getElementById("dataset-problems");
const datasetProblemsHeading = document.getElementById("dataset-problems-heading");
const datasetProblemList = document.getElementById("dataset-problem-list");
const datasetProblemsMore = document.getElementById("dataset-problems-more");
const datasetGroups = document.getElementById("dataset-groups");
const datasetGroupsIntro = document.getElementById("dataset-groups-intro");
const datasetGroupNotes = document.getElementById("dataset-group-notes");
const datasetForm = document.getElementById("dataset-form");
const datasetAdvanced = document.getElementById("dataset-advanced");
const datasetError = document.getElementById("dataset-error");
const datasetSave = document.getElementById("dataset-save");
const datasetSaveHelp = document.getElementById("dataset-save-help");
const splitInputs = { train: document.getElementById("split-train"), val: document.getElementById("split-val"), test: document.getElementById("split-test") };
const splitSeed = document.getElementById("split-seed");
const splitTotal = document.getElementById("split-total");
const splitReset = document.getElementById("split-reset");
const snapshotList = document.getElementById("snapshot-list");
const snapshotsStatus = document.getElementById("snapshots-status");

const SPLIT_NAMES = ["train", "val", "test"];
const SPLIT_TITLES = { train: "Training", val: "Checking", test: "Final test" };
const SPLIT_DEFAULTS = { train: 80, val: 10, test: 10 };
const MAX_SPLIT_SEED = 2147483647;
const DOWNLOAD_FORMATS = [
  { value: "coco", label: "COCO — most tools", help: "One file of box positions plus your pictures. Choose this if you are not sure." },
  { value: "yolo", label: "YOLO — YOLO-style tools", help: "One small text file per picture. Choose this if your tool says it needs YOLO." }
];
const PROBLEM_DISPLAY_LIMIT = 20;
const dsState = { projectId: null, readiness: null, ready: false, saving: false, snapshots: [], details: new Map(), readinessToken: 0, snapshotToken: 0, readinessTimer: null };

function dsNode(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

const dsAssetName = (assetId, detail = null) => {
  const fromProject = currentAssets.find(asset => asset.id === assetId);
  if (fromProject) return fromProject.filename;
  const fromSnapshot = detail?.manifest?.images?.find(image => image.asset_id === assetId);
  return fromSnapshot ? fromSnapshot.filename : null;
};

function readSplitForm() {
  const shares = {};
  for (const name of SPLIT_NAMES) {
    const raw = splitInputs[name].value.trim();
    const value = raw === "" ? NaN : Number(raw);
    if (!Number.isFinite(value) || value < 0 || value > 100) {
      return { ok: false, total: null, message: `${SPLIT_TITLES[name]} amount must be a number from 0 to 100.`, focus: splitInputs[name] };
    }
    shares[name] = value;
  }
  const total = SPLIT_NAMES.reduce((sum, name) => sum + shares[name], 0);
  if (Math.abs(total - 100) > 0.0001) {
    return { ok: false, total, message: `The three amounts must add up to 100%. They add up to ${Math.round(total * 100) / 100}% now.`, focus: splitInputs.train };
  }
  const rawSeed = splitSeed.value.trim();
  const seed = rawSeed === "" ? 0 : Number(rawSeed);
  if (!Number.isInteger(seed) || seed < 0 || seed > MAX_SPLIT_SEED) {
    return { ok: false, total, message: `The shuffle number must be a whole number from 0 to ${MAX_SPLIT_SEED.toLocaleString()}.`, focus: splitSeed };
  }
  const split = {};
  SPLIT_NAMES.forEach(name => { split[name] = Math.round(shares[name] * 10000) / 1000000; });
  return { ok: true, total, shares, seed, split };
}

function updateSplitTotal() {
  const parsed = readSplitForm();
  splitTotal.textContent = parsed.ok ? "Total: 100%. That works." : parsed.message;
  splitTotal.dataset.state = parsed.ok ? "ok" : "bad";
  return parsed;
}

function resetSplitForm() {
  SPLIT_NAMES.forEach(name => { splitInputs[name].value = String(SPLIT_DEFAULTS[name]); });
  splitSeed.value = "0";
  updateSplitTotal();
}

function setDatasetError(message = "") {
  datasetError.textContent = message;
  datasetError.dataset.open = message ? "true" : "false";
}

function updateSaveControls() {
  const parsed = readSplitForm();
  let reason = "";
  if (dsState.saving) reason = "Saving your snapshot…";
  else if (!dsState.readiness) reason = "Checking your work…";
  else if (!dsState.readiness.counts.accepted_boxes) reason = "You need at least one accepted box before you can save a snapshot.";
  else if (!dsState.ready) reason = "Fix the problems listed above first, then press “Check again”.";
  else if (!parsed.ok) reason = parsed.message;
  datasetSave.disabled = Boolean(reason);
  datasetSaveHelp.textContent = reason || "Saving does not change your pictures or boxes. You can save as many snapshots as you like.";
}

function showAsset(assetId, { openIt }) {
  const project = selectedProject();
  const asset = currentAssets.find(candidate => candidate.id === assetId);
  if (!project || !asset) {
    snapshotsStatus.textContent = "That picture is no longer in this project.";
    return;
  }
  if (openIt) {
    void openAnnotation(project.id, asset);
    return;
  }
  const row = [...assetList.children].find(child => child.dataset.assetId === assetId);
  if (!row) return;
  row.scrollIntoView({ block: "center" });
  row.classList.add("asset-row-highlight");
  window.setTimeout(() => row.classList.remove("asset-row-highlight"), 5000);
  (row.querySelector(".asset-remove") || row).focus({ preventScroll: true });
}

// What each blocking problem means, and what to do about it. The server's own wording is the fallback.
function describeProblem(problem) {
  const name = dsAssetName(problem.asset_id);
  const picture = name ? `the picture “${name}”` : "a picture";
  const Picture = name ? `The picture “${name}”` : "A picture";
  switch (problem.code) {
    case "file_missing":
      return { text: `${Picture} is missing its file on the server.`, fix: "Fix: remove this picture and add it again, or just remove it.", button: "Go to this picture", openIt: false };
    case "hash_mismatch":
      return { text: `The file for ${picture} has changed since you added it.`, fix: "Fix: remove this picture and add it again.", button: "Go to this picture", openIt: false };
    case "label_not_in_project":
      return { text: `A box on ${picture} uses a label that is not in your label list any more.`, fix: "Fix: open the picture and give that box a label from your list, or delete the box.", button: "Open this picture", openIt: true };
    case "geometry_out_of_range":
      return { text: `A box on ${picture} sticks out past the edge of the picture.`, fix: "Fix: open the picture and redraw or delete that box.", button: "Open this picture", openIt: true };
    case "non_positive_size":
      return { text: `A box on ${picture} has no width or no height.`, fix: "Fix: open the picture and redraw or delete that box.", button: "Open this picture", openIt: true };
    case "label_unsupported_characters":
      return { text: "One of your labels has a line break or a hidden character in its name.", fix: "Fix: rename that label in the Labels section above.", button: "Go to labels", openIt: false, toLabels: true };
    case "too_many_images":
      return { text: "This project has more pictures than one snapshot can hold.", fix: "Fix: break the work into smaller projects.", button: null };
    default:
      return { text: problem.message, fix: "", button: problem.asset_id ? "Go to this picture" : null, openIt: false };
  }
}

function renderProblems(data) {
  datasetProblemList.replaceChildren();
  const problems = data.problems || [];
  datasetProblems.hidden = problems.length === 0;
  datasetProblemsMore.hidden = true;
  if (!problems.length) return;
  datasetProblemsHeading.textContent = `Fix ${plural(data.problem_count || problems.length, "problem", "problems")} first`;
  problems.slice(0, PROBLEM_DISPLAY_LIMIT).forEach(problem => {
    const info = describeProblem(problem);
    const item = dsNode("li", "dataset-problem");
    const body = dsNode("div", "dataset-problem-body");
    body.append(dsNode("p", "dataset-problem-text", info.text));
    if (info.fix) body.append(dsNode("p", "dataset-problem-fix", info.fix));
    item.append(body);
    if (info.button) {
      const button = dsNode("button", "secondary", info.button);
      button.type = "button";
      const name = dsAssetName(problem.asset_id);
      button.setAttribute("aria-label", info.toLabels ? "Go to the labels section" : `${info.button}${name ? `: ${name}` : ""}`);
      button.addEventListener("click", () => {
        if (info.toLabels) scrollAndFocus(labelNew);
        else showAsset(problem.asset_id, { openIt: info.openIt });
      });
      item.append(button);
    }
    datasetProblemList.append(item);
  });
  const hidden = (data.problem_count || problems.length) - Math.min(problems.length, PROBLEM_DISPLAY_LIMIT);
  if (hidden > 0) {
    datasetProblemsMore.hidden = false;
    datasetProblemsMore.textContent = `…and ${plural(hidden, "more problem", "more problems")}. Fix these and press “Check again” to see the rest.`;
  }
}

function renderReadiness(data) {
  const counts = data.counts;
  const left = data.excluded || {};
  const waiting = counts.suggested_boxes || 0;
  const noBoxPictures = left.images_without_accepted_boxes || 0;
  const hasProblems = (data.problem_count || 0) > 0;
  datasetFacts.replaceChildren();

  if (!counts.accepted_boxes) {
    datasetHeadline.textContent = "Not ready yet: you have no accepted boxes.";
  } else if (hasProblems) {
    datasetHeadline.textContent = `Not ready yet: ${plural(data.problem_count, "thing needs", "things need")} fixing before you can save a snapshot.`;
  } else {
    datasetHeadline.textContent = `Ready. A snapshot made now would hold ${plural(counts.images, "picture", "pictures")} and ${plural(counts.accepted_boxes, "box", "boxes")}.`;
  }
  datasetHeadline.dataset.state = counts.accepted_boxes && !hasProblems ? "ready" : "blocked";

  const addFact = (text, action = null) => {
    const item = dsNode("li", "dataset-fact");
    item.append(dsNode("span", "dataset-fact-text", text));
    if (action) {
      const button = dsNode("button", "secondary", action.label);
      button.type = "button";
      if (action.aria) button.setAttribute("aria-label", action.aria);
      button.addEventListener("click", action.run);
      item.append(button);
    }
    datasetFacts.append(item);
  };
  const project = selectedProject();
  const firstWaiting = currentAssets.find(asset => asset.pending_boxes) || null;
  const firstAny = currentAssets[0] || null;

  if (!counts.images_total) {
    addFact("You have not added any pictures yet.", { label: "Add pictures", run: () => scrollAndFocus(imageUploadInput) });
  } else if (!counts.accepted_boxes) {
    addFact("None of your boxes are accepted yet. Draw a box on a picture, or accept the computer’s suggestions.", firstAny ? { label: "Open a picture", run: () => openAnnotation(project.id, firstWaiting || firstAny) } : null);
  } else {
    // When everything is fine the headline already says this; only repeat it while something blocks saving.
    if (hasProblems) addFact(`${plural(counts.images, "picture", "pictures")} with ${plural(counts.accepted_boxes, "accepted box", "accepted boxes")} can go into the snapshot once the problems below are fixed.`);
  }
  if (waiting) {
    addFact(`${plural(waiting, "box still says", "boxes still say")} “Needs your review”, so ${waiting === 1 ? "it is" : "they are"} left out. Accept ${waiting === 1 ? "it" : "them"} if you want ${waiting === 1 ? "it" : "them"} included.`,
      firstWaiting ? { label: "Review these boxes", run: () => openAnnotation(project.id, firstWaiting) } : null);
  }
  if (noBoxPictures && counts.accepted_boxes) {
    addFact(`${plural(noBoxPictures, "picture has", "pictures have")} no accepted boxes, so ${noBoxPictures === 1 ? "it is" : "they are"} left out.`);
  }
  renderProblems(data);
  renderGroupNotes(data);
  updateSaveControls();
}

function renderGroupNotes(data) {
  datasetGroupNotes.replaceChildren();
  const preview = data.preview;
  datasetGroups.hidden = !preview || !preview.split_counts;
  if (datasetGroups.hidden) return;
  const counts = preview.split_counts;
  datasetGroupsIntro.textContent = `If you save now, pictures would be divided into groups like this: ${SPLIT_NAMES.map(name => `${SPLIT_TITLES[name]} ${plural(counts[name].images, "picture", "pictures")}`).join(", ")}.`;
  const notes = [];
  SPLIT_NAMES.forEach(name => {
    if (counts[name].images === 0) notes.push(`No pictures would go into “${SPLIT_TITLES[name]}”. You can still save, but you would have no ${name === "test" ? "honest final score" : name === "val" ? "way to check progress while learning" : "pictures to learn from"}. Add more pictures, or change the amounts under “Advanced”.`);
  });
  Object.entries(preview.class_balance || {}).forEach(([label, row]) => {
    if (row.total === 0) notes.push(`The label “${label}” has no accepted boxes yet, so a model cannot learn to find it.`);
    else if (row.train === 0) notes.push(`The label “${label}” has no examples in “Training”, so a model cannot learn to find it.`);
  });
  const duplicates = preview.duplicates;
  if (duplicates && duplicates.groups) notes.push(duplicateWarning(duplicates));
  notes.forEach(note => datasetGroupNotes.append(dsNode("li", "dataset-note", note)));
  datasetGroupNotes.hidden = notes.length === 0;
}

function duplicateWarning(duplicates) {
  return `Warning: ${plural(duplicates.groups, "set", "sets")} of identical pictures found (${plural(duplicates.images_in_groups, "picture", "pictures")} in all). Copies stay together in one group, so a model is never tested on a picture it learned from, but they make your set look bigger than it really is. Only exact copies are found. Consider removing the extras.`;
}

function readinessUrl(projectId, parsed) {
  const query = new URLSearchParams();
  if (parsed.ok) {
    query.set("seed", String(parsed.seed));
    SPLIT_NAMES.forEach(name => query.set(name, String(parsed.split[name])));
  }
  return `/api/projects/${encodeURIComponent(projectId)}/datasets/readiness${query.size ? `?${query}` : ""}`;
}

async function loadDatasetReadiness(projectId) {
  const token = ++dsState.readinessToken;
  datasetRecheck.disabled = true;
  datasetRecheck.setAttribute("aria-busy", "true");
  if (!dsState.readiness) datasetHeadline.textContent = "Checking your pictures and boxes…";
  try {
    const data = await api(readinessUrl(projectId, readSplitForm()));
    if (token !== dsState.readinessToken || selectedId !== projectId) return null;
    dsState.readiness = data;
    dsState.ready = Boolean(data.ready);
    renderReadiness(data);
    return data;
  } catch (error) {
    if (token !== dsState.readinessToken || selectedId !== projectId) return null;
    dsState.readiness = null;
    dsState.ready = false;
    datasetHeadline.textContent = `Could not check your work. ${error.message}`;
    datasetHeadline.dataset.state = "blocked";
    updateSaveControls();
    return null;
  } finally {
    if (token === dsState.readinessToken) {
      datasetRecheck.disabled = false;
      datasetRecheck.removeAttribute("aria-busy");
    }
  }
}

function scheduleReadiness() {
  window.clearTimeout(dsState.readinessTimer);
  dsState.readinessTimer = window.setTimeout(() => {
    if (datasetPanel.hidden || !dsState.projectId || !readSplitForm().ok) return;
    void loadDatasetReadiness(dsState.projectId);
  }, 450);
}

// --- Snapshot list -------------------------------------------------------------------------
const formatWhen = iso => new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });

function leftOutText(dataset) {
  const parts = [];
  const waiting = dataset.excluded?.suggested_boxes || 0;
  const empty = dataset.excluded?.images_without_accepted_boxes || 0;
  if (waiting) parts.push(`${plural(waiting, "box", "boxes")} still waiting for review`);
  if (empty) parts.push(`${plural(empty, "picture", "pictures")} with no accepted boxes`);
  return parts.length ? `Left out when it was saved: ${parts.join(" and ")}.` : "";
}

function buildCountsTable(dataset, detail) {
  const manifest = detail.manifest;
  const table = dsNode("table", "snapshot-table");
  table.append(dsNode("caption", "visually-hidden", `Boxes per label in snapshot ${dataset.version}, for each group of pictures`));
  const head = dsNode("thead");
  const headRow = dsNode("tr");
  const corner = dsNode("th", "", "Label");
  corner.scope = "col";
  headRow.append(corner);
  [...SPLIT_NAMES.map(name => SPLIT_TITLES[name]), "All"].forEach(title => {
    const cell = dsNode("th", "num", title);
    cell.scope = "col";
    headRow.append(cell);
  });
  head.append(headRow);
  const body = dsNode("tbody");
  const addRow = (title, values, className = "") => {
    const row = dsNode("tr", className);
    const rowHead = dsNode("th", "row-label", title);
    rowHead.scope = "row";
    row.append(rowHead);
    values.forEach(value => row.append(dsNode("td", "num", value.toLocaleString())));
    body.append(row);
  };
  const balance = manifest.class_balance || {};
  const labels = [...(manifest.label_names || []), ...Object.keys(balance).filter(label => !(manifest.label_names || []).includes(label))];
  labels.forEach(label => {
    const row = balance[label] || { train: 0, val: 0, test: 0, total: 0 };
    addRow(label, [row.train, row.val, row.test, row.total]);
  });
  const groups = manifest.split_counts || {};
  const pictureValues = SPLIT_NAMES.map(name => groups[name]?.images || 0);
  addRow("Pictures", [...pictureValues, pictureValues.reduce((sum, value) => sum + value, 0)], "total-row");
  const boxValues = SPLIT_NAMES.map(name => groups[name]?.boxes || 0);
  addRow("All boxes", [...boxValues, boxValues.reduce((sum, value) => sum + value, 0)], "total-row");
  table.append(head, body);
  return table;
}

async function fillSnapshotDetails(projectId, dataset, container) {
  container.replaceChildren(dsNode("p", "asset-help", "Loading the counts…"));
  let detail = dsState.details.get(dataset.id);
  try {
    if (!detail) {
      const body = await api(`/api/projects/${encodeURIComponent(projectId)}/datasets/${encodeURIComponent(dataset.id)}`);
      detail = body.dataset;
      dsState.details.set(dataset.id, detail);
    }
  } catch (error) {
    if (selectedId === projectId) container.replaceChildren(dsNode("p", "asset-help", `Could not load the counts. ${error.message}`));
    return;
  }
  if (selectedId !== projectId) return;
  container.replaceChildren();
  const duplicates = detail.manifest?.duplicates;
  if (duplicates && duplicates.groups) container.append(dsNode("p", "dataset-note", duplicateWarning(duplicates)));
  const config = detail.split_config || dataset.split_config || {};
  const percent = value => `${Math.round((value || 0) * 1000) / 10}%`;
  container.append(dsNode("p", "asset-help", `Pictures were divided up as Training ${percent(config.train)}, Checking ${percent(config.val)}, Final test ${percent(config.test)} (shuffle number ${config.seed ?? 0}).`));
  container.append(buildCountsTable(dataset, detail));
}

function filenameFrom(response, fallback) {
  const match = /filename="?([^";]+)"?/i.exec(response.headers.get("Content-Disposition") || "");
  return match ? match[1] : fallback;
}

function exportFailureMessage(status, body, dataset) {
  const detail = dsState.details.get(dataset.id);
  if (status === 409 && body.incomplete) {
    const missing = body.missing_pictures || 0;
    return `Snapshot ${dataset.version} can no longer be downloaded: ${plural(missing, "picture", "pictures")} ${missing === 1 ? "was" : "were"} removed from the project after you saved it. Save a new snapshot to get a download.`;
  }
  if (status === 409) {
    const names = [];
    (body.problems || []).forEach(problem => {
      const name = dsAssetName(problem.asset_id, detail);
      if (!names.includes(name || "a picture that is no longer in the project")) names.push(name || "a picture that is no longer in the project");
    });
    const list = names.length ? ` Affected: ${names.slice(0, 3).join(", ")}${names.length > 3 ? `, and ${names.length - 3} more` : ""}.` : "";
    return `Snapshot ${dataset.version} cannot be downloaded because some of its pictures are missing or have changed since you saved it.${list} Add the missing pictures again as the same files, or save a new snapshot without them. Your other snapshots are not affected.`;
  }
  if (status === 413) return `Snapshot ${dataset.version} is too big to download in one go. Ask your administrator to raise the download size limit, or save a smaller snapshot.`;
  if (status === 404) return "That snapshot could not be found. Reload the page and try again.";
  if (status === 401) return "Your session has ended. Sign in again to download.";
  return `The download did not work (error ${status}). Try again in a moment; if it keeps happening, tell your administrator.`;
}

async function downloadSnapshot(dataset, format, button, status) {
  const choice = DOWNLOAD_FORMATS.find(item => item.value === format) || DOWNLOAD_FORMATS[0];
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  status.dataset.state = "busy";
  status.textContent = `Preparing snapshot ${dataset.version} as ${choice.value.toUpperCase()}. This can take a while for big sets…`;
  try {
    const response = await fetch(`/api/projects/${encodeURIComponent(dataset.project_id)}/datasets/${encodeURIComponent(dataset.id)}/export?format=${encodeURIComponent(choice.value)}`, { credentials: "same-origin" });
    if (!response.ok) {
      let body = {};
      try { body = await response.json(); } catch (error) { body = {}; }
      if (response.status === 401) showSessionEnded();
      status.dataset.state = "error";
      status.textContent = exportFailureMessage(response.status, body, dataset);
      // Someone removed a picture after this page loaded: redraw the list so the card shows the Incomplete badge.
      if (response.status === 409 && body.incomplete) void loadSnapshots(dataset.project_id, dataset.id);
      return;
    }
    const blob = await response.blob();
    const fileName = filenameFrom(response, `athena-snapshot-${dataset.version}-${choice.value}.zip`);
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = fileName;
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 30000);
    status.dataset.state = "done";
    status.textContent = `Download started: ${fileName} (${formatBytes(blob.size)}). Look in your Downloads folder.`;
  } catch (error) {
    status.dataset.state = "error";
    status.textContent = "The download did not finish. Check your connection and try again.";
  } finally {
    button.disabled = false;
    button.removeAttribute("aria-busy");
  }
}

function buildSnapshotItem(projectId, dataset, open) {
  const item = dsNode("li", "snapshot");
  item.dataset.snapshotId = dataset.id;
  const title = dsNode("h5", "snapshot-title", `Snapshot ${dataset.version}`);
  title.id = `snapshot-title-${dataset.id}`;
  title.tabIndex = -1;
  const incomplete = Boolean(dataset.is_incomplete);
  const missing = dataset.missing_pictures || 0;
  const meta = dsNode("p", "snapshot-meta");
  const time = dsNode("time", "", formatWhen(dataset.created_at));
  time.dateTime = dataset.created_at;
  meta.append("Saved ", time, ` · ${plural(dataset.counts.images, "picture", "pictures")} · ${plural(dataset.counts.boxes, "box", "boxes")}`);
  item.setAttribute("aria-labelledby", title.id);
  const heading = dsNode("div", "snapshot-head");
  heading.append(title);
  if (incomplete) {
    heading.append(badgeNode("incomplete", `Incomplete: ${plural(missing, "picture", "pictures")} removed`));
  }
  item.append(heading, meta);
  let incompleteNote = null;
  if (incomplete) {
    incompleteNote = dsNode("p", "dataset-note", `Snapshot ${dataset.version} can no longer be downloaded. ${plural(missing, "picture", "pictures")} of its ${dataset.counts.images} ${missing === 1 ? "was" : "were"} removed from the project after you saved it. Its numbers and counts are kept as they were. To get a download, save a new snapshot.`);
    incompleteNote.id = `snapshot-incomplete-${dataset.id}`;
    item.append(incompleteNote);
    if (dataset.incomplete_reason) {
      const why = dsNode("details", "snapshot-details");
      why.append(dsNode("summary", "", "Details for your administrator"));
      why.append(dsNode("pre", "snapshot-log", dataset.incomplete_reason));
      item.append(why);
    }
  }
  const left = leftOutText(dataset);
  if (left) item.append(dsNode("p", "asset-help", left));

  const details = dsNode("details", "snapshot-details");
  details.append(dsNode("summary", "", `Boxes per label in snapshot ${dataset.version}`));
  const inner = dsNode("div", "snapshot-inner");
  details.append(inner);
  details.addEventListener("toggle", () => { if (details.open && !inner.dataset.loaded) { inner.dataset.loaded = "true"; void fillSnapshotDetails(projectId, dataset, inner); } });
  if (open) details.open = true;
  item.append(details);

  const download = dsNode("form", "snapshot-download");
  download.noValidate = true;
  const fieldset = dsNode("fieldset", "snapshot-format");
  fieldset.append(dsNode("legend", "", `Download snapshot ${dataset.version} as`));
  DOWNLOAD_FORMATS.forEach((choice, index) => {
    const id = `format-${dataset.id}-${choice.value}`;
    const label = dsNode("label", "format-choice");
    label.htmlFor = id;
    const radio = document.createElement("input");
    radio.type = "radio";
    radio.name = `format-${dataset.id}`;
    radio.id = id;
    radio.value = choice.value;
    radio.checked = index === 0;
    const text = dsNode("span", "format-text");
    text.append(dsNode("strong", "", choice.label), dsNode("span", "field-help", choice.help));
    label.append(radio, text);
    fieldset.append(label);
  });
  const button = dsNode("button", "primary", "Download for training");
  button.type = "submit";
  button.setAttribute("aria-label", `Download snapshot ${dataset.version} for training`);
  if (incomplete) {
    // A disabled button cannot be focused, so the reason is also written next to it and linked to it.
    fieldset.disabled = true;
    button.disabled = true;
    button.setAttribute("aria-describedby", incompleteNote.id);
  }
  const status = dsNode("p", "download-status");
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  download.addEventListener("submit", event => {
    event.preventDefault();
    const format = download.querySelector("input[type=radio]:checked")?.value || "coco";
    void downloadSnapshot(dataset, format, button, status);
  });
  download.append(fieldset, button, status);
  item.append(download);
  return item;
}

function renderSnapshots(projectId, focusId = null) {
  snapshotList.replaceChildren();
  const count = dsState.snapshots.length;
  datasetCountNote.textContent = count ? `${plural(count, "snapshot", "snapshots")} saved` : "";
  const broken = dsState.snapshots.filter(dataset => dataset.is_incomplete).length;
  snapshotsStatus.textContent = count
    ? `${plural(count, "snapshot", "snapshots")}, newest first. A snapshot never changes after it is saved.${broken ? ` ${plural(broken, "snapshot cannot", "snapshots cannot")} be downloaded because pictures were removed.` : ""}`
    : "No snapshots yet. When your work is ready, press “Save a snapshot” above.";
  dsState.snapshots.forEach((dataset, index) => snapshotList.append(buildSnapshotItem(projectId, dataset, index === 0)));
  void trOnSnapshots();
  if (focusId) {
    const title = document.getElementById(`snapshot-title-${focusId}`);
    if (title) { title.scrollIntoView({ block: "center" }); title.focus({ preventScroll: true }); }
  }
}

async function loadSnapshots(projectId, focusId = null) {
  const token = ++dsState.snapshotToken;
  try {
    const body = await api(`/api/projects/${encodeURIComponent(projectId)}/datasets`);
    if (token !== dsState.snapshotToken || selectedId !== projectId) return;
    dsState.snapshots = body.datasets;
    renderSnapshots(projectId, focusId);
  } catch (error) {
    if (token === dsState.snapshotToken && selectedId === projectId) snapshotsStatus.textContent = `Could not load your snapshots. ${error.message}`;
  }
}

function resetDatasetPanel(projectId) {
  dsState.projectId = projectId;
  dsState.readiness = null;
  dsState.ready = false;
  dsState.snapshots = [];
  dsState.details = new Map();
  dsState.readinessToken += 1;
  dsState.snapshotToken += 1;
  window.clearTimeout(dsState.readinessTimer);
  datasetFacts.replaceChildren();
  datasetProblems.hidden = true;
  datasetGroups.hidden = true;
  snapshotList.replaceChildren();
  snapshotsStatus.textContent = "";
  datasetCountNote.textContent = "";
  datasetHeadline.textContent = "Checking your pictures and boxes…";
  datasetHeadline.dataset.state = "blocked";
  setDatasetError();
  resetSplitForm();
  updateSaveControls();
}

// Called whenever the picture list has been (re)loaded, because that is when boxes may have changed.
function refreshDatasets(projectId) {
  if (dsState.projectId !== projectId) resetDatasetPanel(projectId);
  void loadDatasetReadiness(projectId);
  void loadSnapshots(projectId);
  refreshTraining(projectId);
  void loadCheckScreens(projectId);
}

async function saveSnapshot() {
  const projectId = selectedId;
  const parsed = updateSplitTotal();
  setDatasetError();
  if (!parsed.ok) {
    datasetAdvanced.open = true;
    setDatasetError(parsed.message);
    parsed.focus.focus();
    return;
  }
  dsState.saving = true;
  updateSaveControls();
  try {
    const body = await api(`/api/projects/${encodeURIComponent(projectId)}/datasets`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ seed: parsed.seed, split: parsed.split })
    });
    const dataset = body.dataset;
    dsState.saving = false;
    dsState.details.set(dataset.id, dataset);
    await loadSnapshots(projectId, dataset.id);
    snapshotsStatus.textContent = `Snapshot ${dataset.version} saved: ${plural(dataset.counts.images, "picture", "pictures")} and ${plural(dataset.counts.boxes, "box", "boxes")}. It will never change. Choose a format below to download it.`;
    void loadDatasetReadiness(projectId);
    void refreshProjectCounts();
  } catch (error) {
    dsState.saving = false;
    if (error.status === 422) {
      setDatasetError("Your snapshot was not saved because some things need fixing. They are listed under “Is your work ready?” above. Nothing was changed.");
      await loadDatasetReadiness(projectId);
      datasetProblemsHeading.tabIndex = -1;
      if (!datasetProblems.hidden) datasetProblemsHeading.focus();
    } else if (error.status === 409) {
      setDatasetError("Your boxes changed while the snapshot was being prepared, so nothing was saved. Press “Save a snapshot” again.");
      void loadDatasetReadiness(projectId);
    } else if (error.status === 400 && error.body?.excluded) {
      setDatasetError("There are no accepted boxes yet, so there is nothing to save. Draw or accept at least one box first. Boxes marked “Needs your review” are left out.");
      void loadDatasetReadiness(projectId);
    } else {
      setDatasetError(`Your snapshot was not saved. ${error.message}`);
    }
  } finally {
    dsState.saving = false;
    updateSaveControls();
  }
}

datasetForm.addEventListener("submit", event => { event.preventDefault(); if (!datasetSave.disabled) void saveSnapshot(); });
datasetRecheck.addEventListener("click", () => { if (dsState.projectId) void loadDatasetReadiness(dsState.projectId); });
splitReset.addEventListener("click", () => { resetSplitForm(); setDatasetError(); updateSaveControls(); scheduleReadiness(); });
[...Object.values(splitInputs), splitSeed].forEach(input => input.addEventListener("input", () => {
  setDatasetError();
  updateSplitTotal();
  updateSaveControls();
  scheduleReadiness();
}));
updateSplitTotal();

// --- end of training sets ---

// --- Teach the computer (training, results and approval) ---------------------------------
// Plain-language wording rules (checked by tests_training_ui.py): no "epoch", "batch", "precision",
// "recall", "threshold", "backend", "worker" and similar words in anything a person reads.
const trPanel = document.getElementById("train-panel");
const trHeading = document.getElementById("train-heading");
const trNote = document.getElementById("train-note");
const trRecheck = document.getElementById("tr-recheck");
const trHeadline = document.getElementById("tr-headline");
const trFacts = document.getElementById("tr-facts");
const trLicence = document.getElementById("tr-licence");
const trLicenceBody = document.getElementById("tr-licence-body");
const trForm = document.getElementById("tr-form");
const trFields = document.getElementById("tr-fields");
const trSnapshot = document.getElementById("tr-snapshot");
const trSnapshotHelp = document.getElementById("tr-snapshot-help");
const trTool = document.getElementById("tr-tool");
const trToolHelp = document.getElementById("tr-tool-help");
const trAdvanced = document.getElementById("tr-advanced");
const trReset = document.getElementById("tr-reset");
const trLockNote = document.getElementById("tr-lock-note");
const trError = document.getElementById("tr-error");
const trStartButton = document.getElementById("tr-start");
const trStartHelp = document.getElementById("tr-start-help");
const trRunSection = document.getElementById("tr-run");
const trRunHeading = document.getElementById("tr-run-heading");
const trRunCard = document.getElementById("tr-run-card");
const trRunBadge = document.getElementById("tr-run-badge");
const trRunTitle = document.getElementById("tr-run-title");
const trBar = document.getElementById("tr-bar");
const trRunText = document.getElementById("tr-run-text");
const trRunDetail = document.getElementById("tr-run-detail");
const trNext = document.getElementById("tr-next");
const trSeeResults = document.getElementById("tr-see-results");
const trCancel = document.getElementById("tr-cancel");
const trLocked = document.getElementById("tr-locked");
const trLog = document.getElementById("tr-log");
const trAnnounceEl = document.getElementById("tr-announce");
const trModelsStatus = document.getElementById("tr-models-status");
const trModelList = document.getElementById("tr-model-list");
const trHistory = document.getElementById("tr-history");
const trHistoryStatus = document.getElementById("tr-history-status");
const trDialog = document.getElementById("decision-dialog");
Athena.manageDialog(trDialog);
const trDialogTitle = document.getElementById("decision-title");
const trDialogLead = document.getElementById("decision-lead");
const trDialogBody = document.getElementById("decision-body");
const trDialogForm = document.getElementById("decision-form");
const trDialogNote = document.getElementById("decision-note");
const trDialogNoteLabel = document.getElementById("decision-note-label");
const trDialogNoteHelp = document.getElementById("decision-note-help");
const trDialogCount = document.getElementById("decision-count");
const trDialogError = document.getElementById("decision-error");
const trDialogConfirm = document.getElementById("decision-confirm");

const TR_POLL_MS = 2000;
const TR_HISTORY_MODELS = 15;
const TR_HISTORY_ROWS = 40;
const TR_STALE_WARNING_SECONDS = 120;
const TR_NOTE_LIMIT = 2000;
const TR_STAGES = {
  candidate: { label: "Candidate" },
  staged: { label: "Ready for trial" },
  approved: { label: "Approved" },
  retired: { label: "Retired" }
};
const TR_RUN_BADGES = {
  queued: { label: "Waiting", title: "Waiting to start" },
  running: { label: "Teaching", title: "Teaching in progress" },
  succeeded: { label: "Finished", title: "Teaching finished" },
  failed: { label: "Did not finish", title: "Teaching did not finish" },
  cancelled: { label: "Stopped", title: "Teaching was stopped" }
};
const TR_TOOLS = {
  "torchvision-fasterrcnn": {
    label: "Built-in teaching tool (torchvision)",
    licence: "This tool is free, open-source software (BSD-3-Clause licence) that you may use at work. It starts from nothing: no ready-made model is downloaded, so there is no second licence to check. The models you teach with it belong to you. You are still responsible for having the right to use your own pictures.",
    speed: "It uses this computer’s processor only, so teaching can take from minutes to hours. With few pictures it learns little, so read the results carefully."
  }
};
// The settings a person can change. A setting is shown only when the chosen tool offers it.
const TR_SETTINGS = [
  { keys: ["epochs"], input: "tr-rounds", message: "tr-rounds-msg", min: 1, max: 100, name: "Rounds of learning", warnAbove: 20, warn: "More rounds take longer." },
  { keys: ["batch_size"], input: "tr-batch", message: "tr-batch-msg", min: 1, max: 16, name: "Pictures looked at together" },
  { keys: ["imgsz", "image_size", "img_size"], input: "tr-size", message: "tr-size-msg", min: 32, max: 2048, name: "Picture size while learning" }
].map(spec => ({ ...spec, field: document.getElementById(spec.input), msg: document.getElementById(spec.message), activeKey: null, defaultValue: null }));

const trState = {
  projectId: null, backends: null, readiness: null, runs: [], run: null, models: [], approvedId: null,
  snapshotSignature: "", snapshotGroups: new Map(), busy: false, pollTimer: null, pollToken: 0,
  readyToken: 0, modelToken: 0, evaluations: new Map(), openResults: new Set(), lastAnnounce: "",
  lastQuarter: -1, lastStatus: "", trouble: false, decisionBusy: false, workerOnline: null, flash: ""
};

const trUrl = path => `/api/projects/${encodeURIComponent(trState.projectId)}${path}`;
const trPct = value => (value === null || value === undefined ? null : `${Math.round(value * 100)}%`);
const trWho = ref => (!ref ? "Athena (automatic)" : ref === trPanel.dataset.me ? "You" : `Another person (${ref})`);
const trToolLabel = name => TR_TOOLS[name]?.label || name;
const trIsActive = run => Boolean(run) && (run.status === "queued" || run.status === "running");
const trModelName = model => `Model ${model.version}`;

function trAnnounce(message) {
  trAnnounceEl.textContent = "";
  window.setTimeout(() => { trAnnounceEl.textContent = message; }, 40);
}

function trDuration(milliseconds) {
  const seconds = Math.max(0, Math.round(milliseconds / 1000));
  if (seconds < 60) return plural(seconds, "second", "seconds");
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return plural(minutes, "minute", "minutes");
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${plural(hours, "hour", "hours")} ${plural(rest, "minute", "minutes")}` : plural(hours, "hour", "hours");
}

// One badge for every status in the console: colour, border style and words always agree. A typed mark
// appears only where it adds meaning (good: check, needs attention: "!") and is hidden from screen readers.
const BADGE_MARKS = { approved: Athena.ICONS.done, succeeded: Athena.ICONS.done, failed: Athena.ICONS.warn, incomplete: Athena.ICONS.warn };

function badgeNode(stage, label) {
  const badge = dsNode("span", "stage-badge");
  badge.dataset.stage = stage;
  if (BADGE_MARKS[stage]) {
    const mark = dsNode("span", "", BADGE_MARKS[stage]);
    mark.setAttribute("aria-hidden", "true");
    badge.append(mark);
  }
  badge.append(label);
  return badge;
}

function trStageBadge(stage) {
  const info = TR_STAGES[stage] || { label: stage };
  return badgeNode(stage, info.label);
}

// Server messages mention internal setting names; say them the way the form does.
function trPlainMessage(text) {
  return String(text || "")
    .replace(/\bepochs\b/gi, "Rounds of learning")
    .replace(/\bbatch_size\b/gi, "Pictures looked at together")
    .replace(/\blearning_rate\b/gi, "Learning speed")
    .replace(/\bbackend\b/gi, "teaching tool")
    .replace(/\bdataset version\b/gi, "snapshot")
    .replace(/\bdataset\b/gi, "snapshot")
    .replace(/\bStage it\b/g, "Move it to trial")
    .replace(/\bstaged\b/gi, "ready for trial");
}

function trSetError(message = "") {
  trError.textContent = message;
  trError.dataset.open = message ? "true" : "false";
}

// --- Readiness: what is ready and what is missing ---------------------------------------
function trToolRunnable(tool) {
  return Boolean(tool) && (tool.available_in_web_process === true || tool.worker_reports_available === true);
}

function trSelectedTool() {
  return trState.backends?.backends.find(tool => tool.name === trTool.value) || null;
}

function trSelectedSnapshot() {
  return dsState.snapshots.find(snapshot => snapshot.id === trSnapshot.value) || null;
}

function trGroups(snapshot) {
  return snapshot ? trState.snapshotGroups.get(snapshot.id) || null : null;
}

function trNodeFact(tone, stateText, bodyNodes, action = null) {
  const item = dsNode("li", "dataset-fact");
  const state = dsNode("span", "fact-state", stateText);
  state.dataset.tone = tone;
  const body = dsNode("div", "train-fact-body");
  body.append(...bodyNodes);
  item.append(state, body);
  if (action) {
    const button = dsNode("button", "secondary", action.label);
    button.type = "button";
    button.addEventListener("click", action.run);
    item.append(button);
  }
  return item;
}

const trText = text => dsNode("p", "", text);

function trRenderReadiness() {
  const readiness = trState.readiness;
  const tool = trSelectedTool();
  const snapshot = trSelectedSnapshot();
  const groups = trGroups(snapshot);
  const active = trIsActive(trState.run);
  const worker = readiness?.worker || trState.backends?.worker || { online: false, loading: false };
  trState.workerOnline = worker.online;
  trFacts.replaceChildren();
  const problems = [];
  let helperNote = "";

  // 1. Snapshot
  if (!dsState.snapshots.length) {
    problems.push("you have no snapshot yet");
    trFacts.append(trNodeFact("fix", "Fix this", [trText("You have no snapshot yet. A snapshot is the frozen copy of your labelled pictures that the computer learns from.")],
      { label: "Make a snapshot", run: () => scrollAndFocus(datasetHeading) }));
  } else if (!groups) {
    trFacts.append(trNodeFact("note", "Checking", [trText("Looking at the snapshot you chose…")]));
  } else if (!groups.train.images) {
    problems.push(`snapshot ${snapshot.version} has no Training pictures`);
    trFacts.append(trNodeFact("fix", "Fix this", [trText(`Snapshot ${snapshot.version} has no pictures in the Training group, so there is nothing to learn from. Choose another snapshot, or save a new one that gives Training a bigger share.`)],
      { label: "Make a new snapshot", run: () => scrollAndFocus(datasetHeading) }));
  } else {
    trFacts.append(trNodeFact("ok", "Ready", [trText(`Snapshot ${snapshot.version} is chosen. It has ${plural(groups.train.images, "Training picture", "Training pictures")} to learn from, ${plural(groups.val.images, "Checking picture", "Checking pictures")} and ${plural(groups.test.images, "Final test picture", "Final test pictures")}.`)]));
    if (!groups.test.images) {
      trFacts.append(trNodeFact("note", "Please note", [
        trText("This snapshot has no Final test pictures. You can still teach the computer, but it cannot be checked on pictures it has never seen, so you will get no results, and the model cannot be moved to trial or approved."),
        trText("To get results, save a new snapshot with a Final test amount above 0 (open “Advanced” under “Save a snapshot”), then teach again.")
      ], { label: "Make a new snapshot", run: () => scrollAndFocus(datasetHeading) }));
    }
  }

  // 2. Teaching tool
  if (!tool) {
    problems.push("no teaching tool is installed");
    trFacts.append(trNodeFact("fix", "Fix this", [trText("No teaching tool is installed on this server. Ask your administrator to install one.")]));
  } else if (!trToolRunnable(tool)) {
    problems.push(`${trToolLabel(tool.name)} cannot run here`);
    const details = readiness?.backend || {};
    const body = [trText(worker.online
      ? `${trToolLabel(tool.name)} cannot run here yet. The software it needs is not installed where the helper program runs. Ask your administrator to install it.`
      : `Athena cannot tell whether ${trToolLabel(tool.name)} is installed, because the helper program that uses it is not running. Start the helper program (see below), then press “Check again”.`)];
    if (details.how_to_enable && worker.online) body.push(trText(`For your administrator: ${details.how_to_enable}`));
    trFacts.append(trNodeFact("fix", "Fix this", body));
  } else {
    trFacts.append(trNodeFact("ok", "Ready", [trText(`${trToolLabel(tool.name)} is available.`)]));
  }

  // 3. Helper program
  if (worker.online && !worker.loading) {
    trFacts.append(trNodeFact("ok", "Running", [trText("The teaching helper program is running. It does the slow work in the background, so you can leave this page open or come back later.")]));
  } else if (worker.online) {
    trFacts.append(trNodeFact("note", "Starting", [trText("The teaching helper program is starting up. This can take a minute.")]));
  } else {
    helperNote = "the helper program is not running, so teaching will wait in line until an administrator starts it";
    const how = dsNode("p", "");
    how.append(`The teaching helper program is not running.${trToolRunnable(tool) ? " You can still press “Start teaching” and your request waits in line, but nothing will be learned until an administrator starts the program." : ""} To start it, run `);
    how.append(dsNode("code", "", "python manage.py train_worker"));
    how.append(" on the server, in the environment where the teaching tool is installed (the deployment guide has the steps). Then press “Check again”.");
    trFacts.append(trNodeFact("note", "Not running", [how]));
  }

  // 4. Something already going
  if (active) {
    problems.push("something is already being taught");
    trFacts.append(trNodeFact("fix", "In progress", [trText("Something is already being taught for this project. Wait for it to finish, or stop it under “Teaching progress”. Its settings are locked while it runs.")],
      { label: "Go to progress", run: () => scrollAndFocus(trRunHeading) }));
  }

  if (problems.length) {
    trHeadline.dataset.state = "blocked";
    trHeadline.textContent = `Not ready yet: ${problems[0]}${problems.length > 1 ? ` (and ${plural(problems.length - 1, "other thing", "other things")})` : ""}.`;
  } else {
    trHeadline.dataset.state = "ready";
    trHeadline.textContent = helperNote
      ? `You can start, but ${helperNote}.`
      : groups && !groups.test.images
        ? "You can start, but there will be no results to read, because the snapshot has no Final test pictures."
        : "Ready. You can start teaching.";
  }
  trRenderLicence(tool);
  trUpdateStart(problems);
}

function trRenderLicence(tool) {
  trLicenceBody.replaceChildren();
  trLicence.hidden = !tool;
  if (!tool) return;
  const known = TR_TOOLS[tool.name];
  if (known) {
    trLicenceBody.append(trText(known.licence), trText(known.speed));
  } else {
    trLicenceBody.append(trText(`${tool.name} is a teaching tool added by your administrator. Its author describes the licence like this:`), trText(tool.license_note || "No licence note was provided. Ask your administrator before relying on this tool."));
  }
  if (known && tool.license_note) {
    const raw = dsNode("details", "snapshot-details");
    raw.append(dsNode("summary", "", "The tool’s own wording"), trText(tool.license_note));
    trLicenceBody.append(raw);
  }
}

// --- The form: snapshot, tool and settings ------------------------------------------------
function trSnapshotLabel(snapshot) {
  return `Snapshot ${snapshot.version} · saved ${formatWhen(snapshot.created_at)} · ${plural(snapshot.counts.images, "picture", "pictures")}${snapshot.is_incomplete ? " · incomplete" : ""}`;
}

async function trLoadGroups(snapshot) {
  if (!snapshot || trState.snapshotGroups.has(snapshot.id)) return;
  const projectId = trState.projectId;
  try {
    let detail = dsState.details.get(snapshot.id);
    if (!detail) {
      const body = await api(`/api/projects/${encodeURIComponent(projectId)}/datasets/${encodeURIComponent(snapshot.id)}`);
      detail = body.dataset;
      dsState.details.set(snapshot.id, detail);
    }
    if (trState.projectId !== projectId) return;
    const counts = detail.manifest?.split_counts || {};
    const read = name => ({ images: counts[name]?.images || 0, boxes: counts[name]?.boxes || 0 });
    trState.snapshotGroups.set(snapshot.id, { train: read("train"), val: read("val"), test: read("test") });
  } catch (error) {
    // The facts keep saying "Looking at the snapshot"; the next re-check tries again.
  }
}

// Called whenever the snapshot list was (re)loaded by the training-set panel.
async function trOnSnapshots() {
  if (!trState.projectId) return;
  const signature = dsState.snapshots.map(snapshot => snapshot.id).join(",");
  if (signature !== trState.snapshotSignature) {
    const previousIds = trState.snapshotSignature ? trState.snapshotSignature.split(",") : [];
    const keep = trSnapshot.value;
    trSnapshot.replaceChildren();
    dsState.snapshots.forEach(snapshot => {
      const option = dsNode("option", "", trSnapshotLabel(snapshot));
      option.value = snapshot.id;
      trSnapshot.append(option);
    });
    const newest = dsState.snapshots[0];
    const hasNew = newest && previousIds.length && !previousIds.includes(newest.id);
    trSnapshot.value = !hasNew && keep && dsState.snapshots.some(snapshot => snapshot.id === keep) ? keep : newest?.id || "";
    trState.snapshotSignature = signature;
  }
  trSnapshot.disabled = !dsState.snapshots.length;
  trSnapshotHelp.textContent = dsState.snapshots.length ? "" : "Save a snapshot first, under “Make a training set” above.";
  trRenderReadiness();
  await trLoadGroups(trSelectedSnapshot());
  if (trState.projectId) trRenderReadiness();
  trUpdateSnapshotHelp();
}

function trUpdateSnapshotHelp() {
  const snapshot = trSelectedSnapshot();
  const groups = trGroups(snapshot);
  if (!snapshot || !groups) return;
  trSnapshotHelp.textContent = `Training ${groups.train.images} · Checking ${groups.val.images} · Final test ${groups.test.images} pictures. The computer learns from Training only; Final test is kept back to check it fairly.`;
}

function trRenderTools() {
  const data = trState.backends;
  const keep = trTool.value;
  trTool.replaceChildren();
  (data?.backends || []).forEach(tool => {
    const usable = trToolRunnable(tool);
    const option = dsNode("option", "", usable ? trToolLabel(tool.name) : `${trToolLabel(tool.name)} (not available here)`);
    option.value = tool.name;
    option.disabled = !usable;
    trTool.append(option);
  });
  const options = [...trTool.options];
  const wanted = options.find(option => option.value === keep && !option.disabled)
    || options.find(option => option.value === data?.default && !option.disabled)
    || options.find(option => !option.disabled) || options[0];
  if (wanted) trTool.value = wanted.value;
  trTool.disabled = !options.length;
  const tool = trSelectedTool();
  trToolHelp.textContent = tool ? (TR_TOOLS[tool.name]?.speed || "") : "";
  trConfigureSettings(tool);
}

// Show only the settings this tool offers, filled with its safe starting values.
function trConfigureSettings(tool) {
  const defaults = tool?.default_config || {};
  TR_SETTINGS.forEach(spec => {
    const key = spec.keys.find(candidate => candidate in defaults) || null;
    const changed = key !== spec.activeKey;
    spec.activeKey = key;
    spec.defaultValue = key ? defaults[key] : null;
    spec.field.closest("label").hidden = !key;
    if (key && (changed || spec.field.value === "")) spec.field.value = String(defaults[key]);
    if (!key) { spec.field.value = ""; spec.msg.textContent = ""; }
  });
  trValidateSettings();
}

function trValidateField(spec) {
  if (!spec.activeKey) return { ok: true, value: null };
  const raw = spec.field.value.trim();
  const range = `a whole number from ${spec.min} to ${spec.max}`;
  let problem = "";
  const value = raw === "" ? NaN : Number(raw);
  if (raw === "") problem = `Enter ${range}.`;
  else if (!Number.isFinite(value) || !Number.isInteger(value)) problem = `Use ${range}, with no decimals.`;
  else if (value < spec.min) problem = `${spec.name} is too low. Use ${range}.`;
  else if (value > spec.max) problem = `${spec.name} is too high. Use ${range}.`;
  spec.field.setAttribute("aria-invalid", problem ? "true" : "false");
  spec.msg.dataset.state = problem ? "bad" : "good";
  spec.msg.textContent = problem || (spec.warnAbove && value > spec.warnAbove ? spec.warn : "");
  return { ok: !problem, value, message: problem };
}

function trValidateSettings() {
  const results = TR_SETTINGS.map(spec => ({ spec, ...trValidateField(spec) }));
  const bad = results.find(result => !result.ok) || null;
  const config = {};
  results.forEach(({ spec, ok, value }) => { if (ok && spec.activeKey) config[spec.activeKey] = value; });
  trUpdateStart();
  return { ok: !bad, config, bad };
}

function trUpdateStart(problems = null) {
  const active = trIsActive(trState.run);
  trFields.disabled = active || trState.busy;
  trLockNote.hidden = !active;
  trLockNote.textContent = active ? "Teaching is in progress, so these choices are locked. The values it is using are shown under “Teaching progress”." : "";
  const tool = trSelectedTool();
  const snapshot = trSelectedSnapshot();
  const groups = trGroups(snapshot);
  const settings = TR_SETTINGS.every(spec => !spec.activeKey || trValidateFieldQuiet(spec));
  let why = "";
  if (active) why = "Something is already being taught. Wait for it to finish or stop it first.";
  else if (!dsState.snapshots.length) why = "Save a snapshot first.";
  else if (!tool || !trToolRunnable(tool)) why = "The teaching tool is not available here yet. See “Is everything ready?”.";
  else if (groups && !groups.train.images) why = `Snapshot ${snapshot.version} has no Training pictures. Choose another snapshot.`;
  else if (!settings) why = "Fix the highlighted advanced setting first.";
  else if (trState.busy) why = "Starting…";
  trStartButton.disabled = Boolean(why) && why !== "Starting…" ? true : trState.busy;
  trStartHelp.textContent = why
    ? (why === "Starting…" ? "" : `“Start teaching” is switched off: ${why[0].toLowerCase()}${why.slice(1)}`)
    : "Starting teaching does not switch anything on. When it finishes you read the results and decide.";
}

function trValidateFieldQuiet(spec) {
  const value = Number(spec.field.value.trim());
  return spec.field.value.trim() !== "" && Number.isInteger(value) && value >= spec.min && value <= spec.max;
}

function trResetSettings() {
  TR_SETTINGS.forEach(spec => { if (spec.activeKey) spec.field.value = String(spec.defaultValue); });
  trValidateSettings();
  trSetError();
}

// --- Loading ------------------------------------------------------------------------------
async function trLoadReadiness(projectId) {
  const token = ++trState.readyToken;
  try {
    const backends = await api(`/api/projects/${encodeURIComponent(projectId)}/training/backends`);
    if (token !== trState.readyToken || trState.projectId !== projectId) return;
    trState.backends = backends;
    trRenderTools();
    const query = trTool.value ? `?backend=${encodeURIComponent(trTool.value)}` : "";
    const readiness = await api(`/api/projects/${encodeURIComponent(projectId)}/training/readiness${query}`);
    if (token !== trState.readyToken || trState.projectId !== projectId) return;
    trState.readiness = readiness;
  } catch (error) {
    if (token === trState.readyToken && trState.projectId === projectId) {
      trHeadline.dataset.state = "blocked";
      trHeadline.textContent = `Could not check what is ready. ${error.message}`;
    }
    return;
  }
  trRenderReadiness();
  await trLoadGroups(trSelectedSnapshot());
  if (trState.projectId === projectId) { trRenderReadiness(); trUpdateSnapshotHelp(); }
}

function resetTraining(projectId) {
  trStopPolling();
  Object.assign(trState, {
    projectId, backends: null, readiness: null, runs: [], run: null, models: [], approvedId: null,
    snapshotSignature: "", snapshotGroups: new Map(), busy: false, evaluations: new Map(), openResults: new Set(),
    lastQuarter: -1, lastStatus: "", trouble: false, workerOnline: null, flash: ""
  });
  trState.readyToken += 1;
  trState.modelToken += 1;
  trFacts.replaceChildren();
  trSnapshot.replaceChildren();
  trTool.replaceChildren();
  trModelList.replaceChildren();
  trHistory.replaceChildren();
  trRunSection.hidden = true;
  trHeadline.dataset.state = "blocked";
  trHeadline.textContent = "Checking what is ready…";
  trModelsStatus.textContent = "";
  trNote.textContent = "";
  trSetError();
}

// Called whenever the picture list has been (re)loaded, like refreshDatasets.
function refreshTraining(projectId) {
  trPanel.hidden = datasetPanel.hidden;
  if (trPanel.hidden) return;
  if (trState.projectId !== projectId) resetTraining(projectId);
  void trLoadReadiness(projectId);
  void trLoadModels(projectId);
}

async function trLoadModels(projectId, { focusModelId = null } = {}) {
  const token = ++trState.modelToken;
  let modelBody;
  let runBody;
  try {
    [modelBody, runBody] = await Promise.all([
      api(`/api/projects/${encodeURIComponent(projectId)}/models`),
      api(`/api/projects/${encodeURIComponent(projectId)}/training/runs`)
    ]);
  } catch (error) {
    if (token === trState.modelToken && trState.projectId === projectId) trModelsStatus.textContent = `Could not load your models. ${error.message}`;
    return;
  }
  if (token !== trState.modelToken || trState.projectId !== projectId) return;
  trState.models = modelBody.models;
  trState.approvedId = modelBody.approved_model_id;
  trState.runs = [...runBody.runs].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
  const latest = trState.runs[0] || null;
  if (latest && (!trState.run || trState.run.id !== latest.id || trState.run.status !== latest.status || trState.run.logs === undefined)) {
    try {
      const detail = await api(`/api/projects/${encodeURIComponent(projectId)}/training/runs/${encodeURIComponent(latest.id)}`);
      if (token !== trState.modelToken || trState.projectId !== projectId) return;
      trState.run = detail.run;
    } catch (error) {
      trState.run = latest;
    }
  }
  trRenderRun();
  trRenderModels();
  if (trIsActive(trState.run) && !trState.pollTimer) trPoll(trState.run.id, 300);
  trUpdateStart();
  trRenderReadiness();
  if (focusModelId) trFocusModel(focusModelId);
  void trLoadHistory(projectId);
}

function trFocusModel(modelId) {
  const title = document.getElementById(`model-title-${modelId}`);
  if (title) { title.scrollIntoView({ block: "center" }); title.focus({ preventScroll: true }); }
}

// --- Starting, watching and stopping a run --------------------------------------------------
function trStartError(error) {
  const text = trPlainMessage(error.message);
  if (error.status === 409) return /already/i.test(text) ? "Something is already being taught for this project. Wait for it to finish, or stop it first." : text;
  if (error.status === 422) return "That snapshot has no Training pictures, so there is nothing to learn from. Choose another snapshot, or save a new one.";
  if (error.status === 503) return `The teaching tool cannot run right now. ${text}`;
  return text;
}

async function trStart() {
  const projectId = trState.projectId;
  const settings = trValidateSettings();
  trSetError();
  if (!settings.ok) {
    trAdvanced.open = true;
    trSetError(settings.bad.message);
    settings.bad.spec.field.focus();
    return;
  }
  const snapshot = trSelectedSnapshot();
  const tool = trSelectedTool();
  if (!snapshot || !tool) return;
  trState.busy = true;
  trUpdateStart();
  try {
    const body = await api(`/api/projects/${encodeURIComponent(projectId)}/training/runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dataset_id: snapshot.id, backend: tool.name, config: settings.config })
    });
    if (trState.projectId !== projectId) return;
    trState.run = body.run;
    trState.runs.unshift(body.run);
    trState.workerOnline = body.worker_online;
    trState.lastQuarter = -1;
    trState.lastStatus = "";
    trState.flash = "";
    trRenderRun();
    trAnnounce(body.worker_online ? "Teaching has started." : "Teaching is waiting in line because the helper program is not running.");
    trPoll(body.run.id, 600);
    scrollAndFocus(trRunHeading);
    void trLoadReadiness(projectId);
  } catch (error) {
    trSetError(`Teaching did not start. ${trStartError(error)}`);
  } finally {
    trState.busy = false;
    trUpdateStart();
  }
}

function trStopPolling() {
  window.clearTimeout(trState.pollTimer);
  trState.pollTimer = null;
  trState.pollToken += 1;
}

function trPoll(runId, delay = TR_POLL_MS) {
  window.clearTimeout(trState.pollTimer);
  const projectId = trState.projectId;
  const token = ++trState.pollToken;
  const tick = async () => {
    if (token !== trState.pollToken || trState.projectId !== projectId) return;
    try {
      const body = await api(`/api/projects/${encodeURIComponent(projectId)}/training/runs/${encodeURIComponent(runId)}`);
      if (token !== trState.pollToken || trState.projectId !== projectId) return;
      trState.trouble = false;
      if (body.run.status === "queued") {
        try {
          const backends = await api(`/api/projects/${encodeURIComponent(projectId)}/training/backends`);
          trState.workerOnline = backends.worker.online;
        } catch (error) { /* keep the last known state */ }
      }
      trApplyRun(body.run);
      if (trIsActive(body.run)) trState.pollTimer = window.setTimeout(tick, TR_POLL_MS);
      else { trState.pollTimer = null; await trAfterFinish(body.run); }
    } catch (error) {
      if (token !== trState.pollToken) return;
      if (error.status === 401 || error.status === 404) return;
      trState.trouble = true;
      trRenderRun();
      trState.pollTimer = window.setTimeout(tick, 5000);
    }
  };
  trState.pollTimer = window.setTimeout(tick, delay);
}

function trApplyRun(run) {
  const previous = trState.run;
  trState.run = run;
  const index = trState.runs.findIndex(candidate => candidate.id === run.id);
  if (index >= 0) trState.runs[index] = run;
  trRenderRun();
  trUpdateStart();
  if (run.status === "running" && (!previous || previous.status !== "running") && trState.lastStatus !== "running") {
    trAnnounce("Teaching has begun.");
  } else if (run.status === "running") {
    const quarter = Math.floor((run.progress || 0) * 4);
    if (quarter > trState.lastQuarter && quarter < 4) trAnnounce(`Teaching is about ${Math.round((run.progress || 0) * 100)} percent done.`);
    trState.lastQuarter = Math.max(trState.lastQuarter, quarter);
  }
  trState.lastStatus = run.status;
}

async function trAfterFinish(run) {
  const projectId = trState.projectId;
  await trLoadModels(projectId);
  void trLoadReadiness(projectId);
  if (run.status === "succeeded") {
    const model = trState.models.find(candidate => run.model_version_ids.includes(candidate.id));
    if (model) {
      trState.openResults.add(model.id);
      trRenderModels();
      void trEnsureResults(model.id);
    }
    trAnnounce(model
      ? `Teaching finished. ${trModelName(model)} was added as a Candidate. It is not in use. Its results are shown below.`
      : "Teaching finished. The new model is a Candidate and is not in use.");
  } else if (run.status === "failed") {
    trAnnounce("Teaching did not finish. Nothing was approved or changed. The reason is shown under Teaching progress.");
  } else {
    trAnnounce("Teaching was stopped. Nothing was kept.");
  }
}

async function trCancelRun() {
  const run = trState.run;
  if (!run || !trIsActive(run)) return;
  if (!(await Athena.askConfirm({
    title: "Stop teaching?",
    message: "Nothing is kept from a stopped run, and nothing else changes. You can start again whenever you like.",
    keepLabel: "Keep teaching",
    confirmLabel: "Stop teaching"
  }))) return;
  if (!trState.run || !trIsActive(trState.run)) return;
  trCancel.disabled = true;
  try {
    const body = await api(`/api/projects/${encodeURIComponent(trState.projectId)}/training/runs/${encodeURIComponent(run.id)}/cancel`, { method: "POST" });
    trApplyRun(body.run);
    trAnnounce(trIsActive(body.run) ? "Stopping. The helper program will stop at the next safe point." : "Teaching was stopped before it began.");
    if (trIsActive(body.run)) trPoll(body.run.id, 800);
    else await trAfterFinish(body.run);
  } catch (error) {
    trRunDetail.textContent = `It could not be stopped. ${error.message}`;
    if (error.status === 409) trPoll(run.id, 200);
  } finally {
    trCancel.disabled = false;
  }
}

function trFailureAdvice(error) {
  const text = String(error || "");
  const steps = [];
  if (/restarted|stopped reporting|heartbeat|no sign/i.test(text)) {
    steps.push("The helper program stopped while it was working. Ask your administrator to start it again, then press “Check again” above.");
  } else if (/memory/i.test(text)) {
    steps.push("The computer ran out of memory. Open “Advanced” and lower “Pictures looked at together” to 1 or 2, then start again.");
  } else if (/time limit|exceeded/i.test(text)) {
    steps.push("It took longer than the allowed time. Open “Advanced”, lower “Rounds of learning”, and start again.");
  } else if (/missing or changed on disk|create a new snapshot/i.test(text)) {
    steps.push("Some pictures in this snapshot are missing or have changed on the server, so it can no longer be used. Check the pictures are all still there, save a new snapshot, then start again.");
  } else if (/no images|nothing to learn|train split/i.test(text)) {
    steps.push("The snapshot has no Training pictures. Save a new snapshot that gives Training a larger share, then start again.");
  } else if (/not installed|cannot run|unavailable/i.test(text)) {
    steps.push("The teaching tool is not installed where the helper program runs. Ask your administrator to install it.");
  } else {
    steps.push("Check that the helper program is running (see “Is everything ready?” above), then press “Start teaching” again.");
  }
  steps.push("Nothing was approved and no model was added or changed by this failure.");
  steps.push("If it fails again, send your administrator the details below the progress bar.");
  return steps;
}

function trRenderRun() {
  const run = trState.run;
  trRunSection.hidden = !run;
  if (!run) return;
  const badge = TR_RUN_BADGES[run.status] || { label: run.status, title: run.status };
  const active = trIsActive(run);
  trRunCard.dataset.state = run.status;
  trRunBadge.textContent = badge.label;
  trRunBadge.dataset.stage = run.status;
  trRunTitle.textContent = `${badge.title}: snapshot ${run.dataset.version}`;
  trBar.hidden = !active;
  if (run.status === "queued") trBar.removeAttribute("value");
  else trBar.value = run.progress || 0;

  const percent = Math.round((run.progress || 0) * 100);
  const model = trState.models.find(candidate => run.model_version_ids.includes(candidate.id));
  let text = "";
  let detail = "";
  if (run.status === "queued") {
    text = trState.workerOnline === false
      ? "Waiting in line, but the helper program is not running. Ask your administrator to start it (the steps are under “Is everything ready?”). Your request is kept until then."
      : "Waiting in line. The helper program will pick this up in a moment.";
    detail = `Requested ${formatWhen(run.created_at)}.`;
  } else if (run.status === "running") {
    text = run.cancel_requested
      ? "Stopping… The helper program stops at the next safe point. Nothing is kept from a stopped run."
      : (run.progress || 0) >= 0.9
        ? `Checking the new model on the Final test pictures… about ${percent}% done overall.`
        : `Learning from your Training pictures… about ${percent}% done overall.`;
    const started = run.started_at ? new Date(run.started_at).getTime() : null;
    const beat = run.heartbeat_at ? new Date(run.heartbeat_at).getTime() : null;
    const parts = [];
    if (started) parts.push(`Started ${formatWhen(run.started_at)}, running for ${trDuration(Date.now() - started)}`);
    if (beat) parts.push(`the helper last reported ${trDuration(Date.now() - beat)} ago`);
    detail = parts.length ? `${parts.join("; ")}.` : "";
    if (beat && (Date.now() - beat) / 1000 > TR_STALE_WARNING_SECONDS) {
      detail += " The helper has been quiet for a while and may have stopped. If it stays quiet for 10 minutes, Athena marks this run as failed.";
    }
    if (trState.trouble) detail += " Lost contact with the server; trying again.";
  } else if (run.status === "succeeded") {
    text = `Finished. ${model ? trModelName(model) : "A new model"} was added to the list below as a Candidate. It is not switched on, and nothing else changed. Read its results, then decide.`;
    const took = run.started_at && run.finished_at ? ` It took ${trDuration(new Date(run.finished_at) - new Date(run.started_at))}.` : "";
    detail = run.finished_at ? `Finished ${formatWhen(run.finished_at)}.${took}` : "";
  } else if (run.status === "failed") {
    text = "Teaching did not finish. Nothing was approved or changed.";
    const when = run.finished_at ? `Stopped ${formatWhen(run.finished_at)}. ` : "";
    detail = `${when}${run.error ? `What the helper reported: ${trPlainMessage(run.error)}` : "The helper did not say why."}`;
  } else {
    text = "You stopped this run. Nothing was kept and nothing was changed. You can start again whenever you like.";
    detail = run.finished_at ? `Stopped ${formatWhen(run.finished_at)}.` : "";
  }
  trRunText.textContent = text;
  trRunDetail.textContent = detail;
  trNext.replaceChildren();
  trNext.hidden = run.status !== "failed";
  if (run.status === "failed") {
    trNext.append(dsNode("h5", "", "What to do next"));
    const list = dsNode("ol");
    trFailureAdvice(run.error).forEach(step => list.append(dsNode("li", "", step)));
    trNext.append(list);
  }
  trCancel.hidden = !active;
  trCancel.textContent = run.cancel_requested ? "Stopping…" : "Stop teaching";
  trCancel.disabled = Boolean(run.cancel_requested);
  trSeeResults.hidden = !(run.status === "succeeded" && model);
  trSeeResults.onclick = model ? () => { trState.openResults.add(model.id); trRenderModels(); void trEnsureResults(model.id); trFocusModel(model.id); } : null;

  trLocked.replaceChildren();
  const addSetting = (name, value) => {
    const row = dsNode("div");
    row.append(dsNode("dt", "", name), dsNode("dd", "", value));
    trLocked.append(row);
  };
  addSetting("Snapshot", `Snapshot ${run.dataset.version}`);
  addSetting("Teaching tool", trToolLabel(run.backend));
  TR_SETTINGS.forEach(spec => {
    const key = spec.keys.find(candidate => candidate in run.config);
    if (key) addSetting(spec.name, String(run.config[key]));
  });
  trLog.textContent = (run.logs || "").split("\n").slice(-40).join("\n") || "No notes yet.";
  trNote.textContent = active ? "Teaching in progress" : "";
}

// --- Models, results and decisions ------------------------------------------------------------
function trRunFor(model) {
  return trState.runs.find(run => run.id === model.lineage?.training_run_id) || null;
}

function trResultSummary(model) {
  const result = model.evaluation || {};
  if (!result.available) {
    return "No results: there were no Final test pictures to check it on, so nobody can say how well it works yet.";
  }
  const found = trPct(result.recall);
  const right = trPct(result.precision);
  const checked = `Checked on ${plural(result.test_images, "Final test picture", "Final test pictures")} (${plural(result.test_boxes, "box", "boxes")}): `;
  const foundText = found ? `found ${found} of the boxes people drew` : "could not be measured on how many boxes it found";
  const rightText = right ? `was right ${right} of the time it drew a box` : "drew no boxes it was sure about";
  return `${checked}${foundText}, and ${rightText}.`;
}

function trStateText(model) {
  const previous = trState.models.find(candidate => candidate.id === model.previous_approved_id);
  if (model.stage === "candidate") {
    return model.evaluation?.available
      ? "Candidate: just taught and not in use. Read the results, then move it to trial if you want to consider it."
      : "Candidate: just taught and not in use. It cannot be moved to trial because it has no results.";
  }
  if (model.stage === "staged") {
    return `Ready for trial: ${trWho(model.staged_by)} chose this model on ${formatWhen(model.staged_at)}. It is not in use yet. Approve it to make it the model to rely on.`;
  }
  if (model.stage === "approved") {
    const earlier = previous ? ` The model it replaced, ${trModelName(previous)}, is kept and can be brought back.` : "";
    return `${trWho(model.approved_by)} approved this model on ${formatWhen(model.approved_at)}. It is the model to rely on.${model.approval_note ? ` Note: “${model.approval_note}”` : ""}${earlier ? ` ${earlier.trim()}` : ""}`;
  }
  const replacer = trState.models.find(candidate => candidate.previous_approved_id === model.id && candidate.stage === "approved");
  return `Retired${model.retired_at ? ` on ${formatWhen(model.retired_at)}` : ""}. It is kept saved but is not in use.${replacer ? ` You can bring it back with “Go back to previous model” on ${trModelName(replacer)}.` : " A retired model cannot be approved again; teach again to get a fresh Candidate."}`;
}

function trActionButton(label, enabled, run, primary = false) {
  const button = dsNode("button", primary ? "primary" : "secondary", label);
  button.type = "button";
  button.disabled = !enabled;
  if (enabled) button.addEventListener("click", () => run(button));
  return button;
}

function trBuildModel(model) {
  const item = dsNode("li", "model-card");
  item.dataset.stage = model.stage;
  item.dataset.modelId = model.id;
  const title = dsNode("h5", "", trModelName(model));
  title.id = `model-title-${model.id}`;
  title.tabIndex = -1;
  item.setAttribute("aria-labelledby", title.id);
  const head = dsNode("div", "model-head");
  head.append(title, trStageBadge(model.stage));
  const origin = model.lineage?.origin === "training_run"
    ? `Taught from snapshot ${model.lineage.dataset_version} with ${trToolLabel(model.backend)}`
    : "Brought in from elsewhere";
  const meta = dsNode("p", "model-meta", `${origin} · ${formatWhen(model.created_at)}`);
  const summary = dsNode("p", "model-summary", trResultSummary(model));
  const stateText = dsNode("p", "model-state-text", trStateText(model));
  item.append(head, meta, summary, stateText);
  if (model.in_use_by_apps?.length) {
    item.append(dsNode("p", "model-state-text", `Used by: ${model.in_use_by_apps.map(app => app.name).join(", ")}.`));
  }

  const actions = dsNode("div", "model-actions");
  const resultsId = `model-results-${model.id}`;
  const open = trState.openResults.has(model.id);
  const toggle = dsNode("button", "secondary", open ? "Hide results" : "Show results");
  toggle.type = "button";
  toggle.setAttribute("aria-expanded", String(open));
  toggle.setAttribute("aria-controls", resultsId);
  toggle.setAttribute("aria-label", `${open ? "Hide" : "Show"} results for ${trModelName(model)}`);
  toggle.addEventListener("click", () => {
    if (trState.openResults.has(model.id)) trState.openResults.delete(model.id); else trState.openResults.add(model.id);
    const nowOpen = trState.openResults.has(model.id);
    toggle.textContent = nowOpen ? "Hide results" : "Show results";
    toggle.setAttribute("aria-expanded", String(nowOpen));
    toggle.setAttribute("aria-label", `${nowOpen ? "Hide" : "Show"} results for ${trModelName(model)}`);
    results.hidden = !nowOpen;
    if (nowOpen) void trEnsureResults(model.id);
  });
  actions.append(toggle);

  const whyId = `model-why-${model.id}`;
  let why = "";
  if (model.stage === "candidate") {
    const canStage = Boolean(model.evaluation?.available);
    const trial = trActionButton("Move to trial", canStage, button => trOpenDecision("trial", model, button));
    trial.setAttribute("aria-label", `Move ${trModelName(model)} to trial`);
    const approve = trActionButton("Approve for use", false, () => {}, true);
    approve.setAttribute("aria-label", `Approve ${trModelName(model)} for use`);
    actions.append(trial, approve);
    why = canStage
      ? "“Approve for use” unlocks after you move the model to trial."
      : "“Move to trial” is switched off because there are no results to read. Teach again from a snapshot that has Final test pictures. “Approve for use” needs a trial first.";
  } else if (model.stage === "staged") {
    const approve = trActionButton("Approve for use", true, button => trOpenDecision("approve", model, button), true);
    approve.setAttribute("aria-label", `Approve ${trModelName(model)} for use`);
    actions.append(approve);
  } else if (model.stage === "approved") {
    const previous = trState.models.find(candidate => candidate.id === model.previous_approved_id && candidate.stage === "retired");
    const back = trActionButton("Go back to previous model", Boolean(previous), button => trOpenDecision("rollback", model, button));
    back.setAttribute("aria-label", `Go back to the previous model instead of ${trModelName(model)}`);
    actions.append(back);
    if (!previous) why = "“Go back to previous model” is switched off because no earlier model was ever approved.";
  }
  item.append(actions);
  if (why) {
    const reason = dsNode("p", "model-why", why);
    reason.id = whyId;
    item.append(reason);
    actions.querySelectorAll("button[disabled]").forEach(button => button.setAttribute("aria-describedby", whyId));
  }
  const results = dsNode("div", "model-results");
  results.id = resultsId;
  results.dataset.modelId = model.id;
  results.setAttribute("role", "region");
  results.setAttribute("aria-label", `Results for ${trModelName(model)}`);
  results.hidden = !open;
  item.append(results);
  return item;
}

function trRenderModels() {
  trModelList.replaceChildren();
  const count = trState.models.length;
  trNote.textContent = trIsActive(trState.run) ? "Teaching in progress" : count ? plural(count, "model", "models") : "";
  const summary = count
    ? `${plural(count, "model", "models")}, newest first.${trState.approvedId ? "" : " None is approved yet, so nothing is in use."}`
    : "No models yet. When teaching finishes, the new model appears here as a Candidate.";
  trModelsStatus.textContent = trState.flash ? `${trState.flash} ${summary}` : summary;
  trState.models.forEach(model => trModelList.append(trBuildModel(model)));
  trState.models.forEach(model => { if (trState.openResults.has(model.id)) void trEnsureResults(model.id); });
}

async function trEnsureResults(modelId) {
  const container = trModelList.querySelector(`.model-results[data-model-id="${modelId}"]`);
  if (!container) return;
  const model = trState.models.find(candidate => candidate.id === modelId);
  const cached = trState.evaluations.get(modelId);
  if (cached) { trFillResults(container, model, cached); return; }
  container.replaceChildren(dsNode("p", "asset-help", "Loading the results…"));
  const projectId = trState.projectId;
  try {
    const body = await api(`/api/projects/${encodeURIComponent(projectId)}/models/${encodeURIComponent(modelId)}/evaluation`);
    if (trState.projectId !== projectId) return;
    trState.evaluations.set(modelId, body);
    const live = trModelList.querySelector(`.model-results[data-model-id="${modelId}"]`);
    if (live) trFillResults(live, model, body);
  } catch (error) {
    const live = trModelList.querySelector(`.model-results[data-model-id="${modelId}"]`);
    if (live) live.replaceChildren(dsNode("p", "asset-help", `Could not load the results. ${error.message}`));
  }
}

function trLimitSentence(raw) {
  let match = /^Only (\d+) test box\(es\) for ['"](.*)['"]/.exec(raw);
  if (match) return `Only ${plural(Number(match[1]), "test box", "test boxes")} for “${match[2]}”. Treat its numbers as a rough hint.`;
  match = /^No test boxes for ['"](.*)['"]/.exec(raw);
  if (match) return `There are no test boxes for “${match[1]}”, so nothing could be measured about it.`;
  return null;
}

function trBlock(title, ...children) {
  const block = dsNode("div", "result-block");
  block.append(dsNode("h6", "", title), ...children);
  return block;
}

function trList(items, ordered = false) {
  const list = dsNode(ordered ? "ol" : "ul");
  items.forEach(text => list.append(dsNode("li", "", text)));
  return list;
}

function trResultsTable(numbers, name) {
  const wrap = dsNode("div", "result-scroll");
  wrap.setAttribute("role", "region");
  wrap.setAttribute("tabindex", "0");
  wrap.setAttribute("aria-label", `Results for each label, ${name}. Scrolls sideways on a narrow screen.`);
  const table = dsNode("table", "result-table");
  table.append(dsNode("caption", "", "How it did on each label, on the Final test pictures"));
  const head = dsNode("thead");
  const headRow = dsNode("tr");
  ["Label", "Test boxes", "Found", "Missed", "Wrong boxes it drew", "Right when it drew a box"].forEach((title, index) => {
    const cell = dsNode("th", index ? "num" : "", title);
    cell.scope = "col";
    headRow.append(cell);
  });
  head.append(headRow);
  const body = dsNode("tbody");
  Object.entries(numbers.per_label || {}).forEach(([label, row]) => {
    const tr = dsNode("tr");
    const rowHead = dsNode("th", "", label);
    rowHead.scope = "row";
    tr.append(rowHead, dsNode("td", "num", String(row.ground_truth_boxes)));
    if (!row.ground_truth_boxes) {
      const cell = dsNode("td", "result-note", "Nothing to measure: there are no test boxes for this label.");
      cell.colSpan = 4;
      tr.append(cell);
    } else {
      const found = dsNode("td", "num");
      found.append(`${row.true_positives} of ${row.ground_truth_boxes} (${trPct(row.recall)})`);
      const meter = document.createElement("meter");
      meter.min = 0; meter.max = 1; meter.value = row.recall || 0;
      meter.setAttribute("aria-label", `${label}: found ${trPct(row.recall)} of its test boxes`);
      found.append(meter);
      tr.append(found, dsNode("td", "num", String(row.false_negatives)), dsNode("td", "num", String(row.false_positives)),
        dsNode("td", "num", row.precision === null ? "It drew none" : trPct(row.precision)));
    }
    body.append(tr);
  });
  table.append(head, body);
  wrap.append(table);
  return wrap;
}

function trFillResults(container, model, payload) {
  container.replaceChildren();
  const report = payload.report;
  const numbers = report.numbers || {};
  const name = trModelName(model);
  const run = trRunFor(model);

  if (!report.available) {
    const reason = /test split has no images/i.test(numbers.reason || "")
      ? "The snapshot this model was taught from had no Final test pictures, so nothing was held back to check it on."
      : trPlainMessage(numbers.reason || "No Final test pictures were available.");
    const headline = dsNode("p", "result-headline", `Results are not available for ${name}.`);
    headline.dataset.state = "none";
    container.append(headline,
      trBlock("Why", trText(reason)),
      trBlock("What this means", trText("Athena cannot say how well this model works. It cannot be moved to trial or approved, because a person would be deciding without evidence.")),
      trBlock("How to get results", trList([
        "Under “Make a training set”, open “Advanced” in “Save a snapshot” and give Final test an amount above 0.",
        "Save the snapshot, choose it above, and press “Start teaching” again.",
        "Read the results of the new model here."
      ], true)));
    return;
  }

  const data = numbers.data;
  const overall = numbers.overall;
  const sure = Math.round(numbers.confidence_threshold * 100);
  const total = overall.true_positives + overall.false_negatives;
  const headline = dsNode("p", "result-headline", `${name} was checked on ${plural(data.images, "Final test picture", "Final test pictures")} with ${plural(data.boxes, "box", "boxes")} that people had drawn. These pictures were kept back: the computer never saw them while it was learning.`);
  container.append(headline);

  const lines = [
    `It found ${overall.true_positives} of the ${plural(total, "box", "boxes")} people had drawn${total ? ` (${trPct(overall.recall)})` : ""}.`,
    `It missed ${overall.false_negatives}.`,
    `It drew ${plural(overall.false_positives, "box", "boxes")} that ${overall.false_positives === 1 ? "was" : "were"} wrong.`,
    overall.precision === null
      ? "It drew no boxes it was sure about, so how often it is right could not be measured."
      : `When it drew a box, it was right ${trPct(overall.precision)} of the time.`
  ];
  container.append(trBlock("How well did it do?", trList(lines),
    dsNode("p", "asset-help", `Only boxes the computer was at least ${sure}% sure about are counted. A box counts as right when it has the right label and covers at least half of the same area as a box a person drew.`)));
  container.append(trBlock("Label by label", trResultsTable(numbers, name), dsNode("p", "result-scroll-hint", "On a narrow screen, swipe the table sideways to see every column.")));

  const limits = [];
  const fewPictures = (numbers.limits || []).some(text => /^Only \d+ test image/.test(text));
  limits.push(`This report rests on only ${plural(data.images, "picture", "pictures")} and ${plural(data.boxes, "box", "boxes")}.${fewPictures ? " That is very few: one or two mistakes can change every number a lot." : ""}`);
  (numbers.limits || []).map(trLimitSentence).filter(Boolean).forEach(sentence => limits.push(sentence));
  limits.push("These numbers describe those test pictures only. They are not a guarantee about real-world use: other cameras, lighting, items or times of day can give worse results.");
  limits.push("Pictures that look almost the same (for example neighbouring frames of a video) can make the score look better than it really is.");
  limits.push("The numbers count mistakes but cannot say how serious each one would be where you work. That judgement is yours.");
  const limitBlock = trBlock("Limits: how far to trust this", trList(limits));
  limitBlock.classList.add("result-limits");
  container.append(limitBlock);

  const rows = [["Taught from", `Snapshot ${payload.model.lineage?.dataset_version ?? "?"}`], ["Teaching tool", trToolLabel(model.backend)]];
  if (run) TR_SETTINGS.forEach(spec => { const key = spec.keys.find(candidate => candidate in run.config); if (key) rows.push([spec.name, String(run.config[key])]); });
  const settings = dsNode("div", "train-locked");
  const list = dsNode("dl");
  rows.forEach(([key, value]) => { const row = dsNode("div"); row.append(dsNode("dt", "", key), dsNode("dd", "", value)); list.append(row); });
  settings.append(dsNode("h6", "", "How this model was taught"), list);
  container.append(settings);
  const admin = dsNode("details", "train-log");
  admin.append(dsNode("summary", "", "Details for your administrator"));
  const fingerprint = dsNode("p", "asset-help");
  fingerprint.append("Fingerprint of the model file: ", dsNode("code", "", payload.model.artifact_sha256 || "not recorded"));
  admin.append(fingerprint);
  container.append(admin);
}

// --- Decisions: trial, approval and going back ----------------------------------------------------
const trDecision = { kind: null, model: null, trigger: null, succeeded: false };

function trDecisionConfig(kind, model) {
  const name = trModelName(model);
  const approved = trState.models.find(candidate => candidate.id === trState.approvedId) || null;
  if (kind === "trial") {
    return {
      title: `Move ${name} to trial?`,
      lead: "This only marks the model as worth a closer look.",
      body: [
        ...(model.evaluation?.available && !model.evaluation.recall ? [dsNode("p", "dataset-note", "Take care: on the Final test pictures this model found none of the boxes people had drawn.")] : []),
        trText("A model in trial is still not in use. Nothing else changes. You can leave it there, or approve it later after you have read its results."),
        trText("A model cannot be moved back from trial to Candidate. If you change your mind, simply do not approve it.")
      ],
      noteLabel: "Add a note (optional)",
      noteHelp: "For example, why this one looks worth trying. It is saved in the history.",
      required: false,
      confirm: "Move to trial",
      path: `/models/${model.id}/stage`,
      done: `${name} is now Ready for trial. It is not in use yet.`
    };
  }
  if (kind === "approve") {
    const body = [trText(trResultSummary(model))];
    if (model.evaluation?.available && !model.evaluation.recall) {
      body.push(dsNode("p", "dataset-note", "Take care: on the Final test pictures this model found none of the boxes people had drawn. Read its results before you rely on it."));
    }
    if (model.evaluation?.available && model.evaluation.test_images < 30) {
      const warning = dsNode("p", "dataset-note", `Take care: this was checked on only ${plural(model.evaluation.test_images, "picture", "pictures")}. That is a small sample, and the scores are not a guarantee about real-world use.`);
      body.push(warning);
    }
    const list = dsNode("ul");
    list.append(dsNode("li", "", `${name} becomes Approved: the model to rely on.`));
    list.append(dsNode("li", "", approved
      ? `${trModelName(approved)}, which is approved now, will be marked Retired. It is not deleted: it stays saved, and “Go back to previous model” brings it back.`
      : "No model is approved yet, so nothing is replaced."));
    list.append(dsNode("li", "", "Approving changes nothing else. Check screens that are already set up keep using the exact model they were set up with, until someone changes them on purpose."));
    list.append(dsNode("li", "", "Your name, the time and your note are written in the history."));
    body.push(list);
    return {
      title: `Approve ${name} for use?`,
      lead: "Please read this before you confirm.",
      body,
      noteLabel: "Why are you approving this model? (required)",
      noteHelp: "One sentence is enough, for example “Found 9 of 10 test boxes; checked with the shift lead.”",
      required: true,
      confirm: "Approve for use",
      path: `/models/${model.id}/approve`,
      done: `${name} is now Approved.`
    };
  }
  const previous = trState.models.find(candidate => candidate.id === model.previous_approved_id) || null;
  const apps = (model.in_use_by_apps || []).map(app => app.name);
  const list = dsNode("ul");
  list.append(dsNode("li", "", `${name}, approved now, will be marked Retired. It is kept saved.`));
  list.append(dsNode("li", "", `${previous ? trModelName(previous) : "The earlier model"} becomes Approved again.`));
  if (apps.length) list.append(dsNode("li", "", `These check screens use ${name} and will stop running until someone points them at another model: ${apps.join(", ")}.`));
  list.append(dsNode("li", "", "Nothing is deleted, and nothing else changes."));
  return {
    title: `Go back to ${previous ? trModelName(previous) : "the previous model"}?`,
    lead: `This replaces ${name} as the approved model.`,
    body: [list],
    noteLabel: "Why are you going back? (required)",
    noteHelp: "For example, what went wrong with the newer model. It is saved in the history.",
    required: true,
    confirm: "Go back to previous model",
    path: `/models/${model.id}/rollback`,
    done: `Went back to ${previous ? trModelName(previous) : "the previous model"}. ${name} is Retired.`
  };
}

function trOpenDecision(kind, model, trigger) {
  const config = trDecisionConfig(kind, model);
  Object.assign(trDecision, { kind, model, trigger, config, succeeded: false });
  trDialogTitle.textContent = config.title;
  trDialogLead.textContent = config.lead;
  trDialogBody.replaceChildren(...config.body);
  trDialogNoteLabel.textContent = config.noteLabel;
  trDialogNoteHelp.textContent = config.noteHelp;
  trDialogNote.value = "";
  trDialogNote.required = config.required;
  trDialogNote.setAttribute("aria-required", String(config.required));
  trDialogError.textContent = "";
  trDialogError.dataset.open = "false";
  trDialogConfirm.textContent = config.confirm;
  trDialogConfirm.disabled = config.required;
  trUpdateNoteCount();
  trDialog.showModal();
  trDialogNote.focus();
}

function trUpdateNoteCount() {
  const length = trDialogNote.value.length;
  trDialogCount.textContent = `${length} of ${TR_NOTE_LIMIT} characters.`;
  if (trDecision.config) trDialogConfirm.disabled = trState.decisionBusy || (trDecision.config.required && !trDialogNote.value.trim());
}

async function trSubmitDecision() {
  const { config, model } = trDecision;
  if (!config || trState.decisionBusy) return;
  const note = trDialogNote.value.trim();
  if (config.required && !note) {
    trDialogError.textContent = "A note is required. Write a sentence saying why, then confirm.";
    trDialogError.dataset.open = "true";
    trDialogNote.focus();
    return;
  }
  trState.decisionBusy = true;
  trDialogConfirm.disabled = true;
  trDialogError.textContent = "";
  trDialogError.dataset.open = "false";
  const projectId = trState.projectId;
  try {
    const body = await api(`/api/projects/${encodeURIComponent(projectId)}${config.path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note })
    });
    trDecision.succeeded = true;
    trDialog.close();
    let message = config.done;
    const stuck = body.apps_still_on_retired_model || [];
    if (trDecision.kind === "approve" && stuck.length) {
      message += ` These check screens still use the retired model and were not changed: ${stuck.map(app => app.name).join(", ")}.`;
    }
    if (trDecision.kind === "rollback" && stuck.length) {
      message += ` These check screens will not run until someone points them at another model: ${stuck.map(app => app.name).join(", ")}.`;
    }
    trState.flash = message;
    trModelsStatus.textContent = message;
    trAnnounce(message);
    trState.evaluations.delete(model.id);
    await trLoadModels(projectId, { focusModelId: body.model.id });
    void refreshProjectCounts();
    void loadCheckScreens(projectId);
  } catch (error) {
    trDialogError.textContent = `Nothing was changed. ${trPlainMessage(error.message)}`;
    trDialogError.dataset.open = "true";
    if (error.status === 409 || error.status === 422) void trLoadModels(projectId);
  } finally {
    trState.decisionBusy = false;
    trUpdateNoteCount();
  }
}

// --- History of decisions --------------------------------------------------------------------------
function trEventText(event, version) {
  const name = `Model ${version}`;
  switch (event.kind) {
    case "candidate_created": return `${name} was taught and added as a Candidate`;
    case "staged": return `${name} moved to trial`;
    case "approved": return `${name} approved for use`;
    case "retired": return `${name} retired`;
    case "rollback_retired": return `${name} taken out of use (went back to the previous model)`;
    case "rollback_restored": return `${name} brought back into use`;
    default: return `${name}: ${String(event.kind).replace(/_/g, " ")}`;
  }
}

async function trLoadHistory(projectId) {
  const models = trState.models.slice(0, TR_HISTORY_MODELS);
  if (!models.length) {
    trHistory.replaceChildren();
    trHistoryStatus.textContent = "Nothing has happened yet. Every step a model goes through will be written down here: who did it, when, and the note they left.";
    return;
  }
  const results = await Promise.allSettled(models.map(model => api(`/api/projects/${encodeURIComponent(projectId)}/models/${encodeURIComponent(model.id)}`)));
  if (trState.projectId !== projectId) return;
  const events = [];
  let failed = 0;
  results.forEach((result, index) => {
    if (result.status !== "fulfilled") { failed += 1; return; }
    result.value.model.events.forEach(event => events.push({ ...event, version: models[index].version }));
  });
  events.sort((a, b) => b.seq - a.seq);
  trHistory.replaceChildren();
  events.slice(0, TR_HISTORY_ROWS).forEach(event => {
    const item = dsNode("li", "history-item");
    item.append(dsNode("span", "history-what", trEventText(event, event.version)));
    const when = dsNode("span", "history-when");
    const time = dsNode("time", "", formatWhen(event.at));
    time.dateTime = event.at;
    when.append(`${trWho(event.by)} · `, time);
    item.append(when);
    // The server's own note for a finished run uses internal wording; say it plainly here.
    const note = event.kind === "candidate_created" ? "It is not in use until a person approves it." : event.note;
    if (note) item.append(dsNode("p", "history-note", `Note: ${note}`));
    trHistory.append(item);
  });
  const hidden = Math.max(0, events.length - TR_HISTORY_ROWS) + Math.max(0, trState.models.length - TR_HISTORY_MODELS);
  trHistoryStatus.textContent = `${plural(Math.min(events.length, TR_HISTORY_ROWS), "entry", "entries")}, newest first.${hidden ? " Older entries are not shown here." : ""}${failed ? " Some entries could not be loaded." : ""}`;
}

trForm.addEventListener("submit", event => { event.preventDefault(); if (!trStartButton.disabled) void trStart(); });
trRecheck.addEventListener("click", () => { if (trState.projectId) { void trLoadReadiness(trState.projectId); void trLoadModels(trState.projectId); } });
trReset.addEventListener("click", trResetSettings);
trCancel.addEventListener("click", () => void trCancelRun());
trSnapshot.addEventListener("change", async () => { trSetError(); trRenderReadiness(); await trLoadGroups(trSelectedSnapshot()); trRenderReadiness(); trUpdateSnapshotHelp(); });
trTool.addEventListener("change", () => { trSetError(); trConfigureSettings(trSelectedTool()); trToolHelp.textContent = TR_TOOLS[trTool.value]?.speed || ""; void trLoadReadiness(trState.projectId); });
TR_SETTINGS.forEach(spec => spec.field.addEventListener("input", () => { trSetError(); trValidateSettings(); }));
trDialogNote.addEventListener("input", trUpdateNoteCount);
trDialogForm.addEventListener("submit", event => { event.preventDefault(); void trSubmitDecision(); });
document.getElementById("decision-cancel").addEventListener("click", () => trDialog.close());
document.getElementById("decision-close").addEventListener("click", () => trDialog.close());
trDialog.addEventListener("close", () => {
  if (trDecision.succeeded) return;
  if (trDecision.trigger && trDecision.trigger.isConnected) trDecision.trigger.focus();
});
window.addEventListener("pagehide", trStopPolling);

// --- end of teaching ---

// --- Check screens (the pages people use to check new pictures) -----------------------------
const appsPanel = document.getElementById("apps-panel");
const appsList = document.getElementById("apps-list");
const appsStatus = document.getElementById("apps-status");
const appsNote = document.getElementById("apps-note");
const appsEmpty = document.getElementById("apps-empty");
const appsEmptyText = document.getElementById("apps-empty-text");
const appsEmptyLink = document.getElementById("apps-empty-link");
const appsHeading = document.getElementById("apps-heading");
const APP_SLUG_PLACEHOLDER = "slug-placeholder";
let appsToken = 0;
let currentApps = null; // null until the list has loaded for the open project
let lastAppsProject = null;

function openScreenUrl(app) {
  const template = appsPanel ? appsPanel.dataset.openUrlTemplate || "" : "";
  return template ? template.replace(APP_SLUG_PLACEHOLDER, encodeURIComponent(app.slug)) : "";
}

function appProblem(app) {
  if (app.status !== "active") return "This screen is switched off, so people cannot use it. Open “Edit” to switch it on.";
  if (!app.model_in_use) return `Its model (Model ${app.model_version.version}) is no longer in use, so this screen will not check pictures. Open “Edit” to choose a model that is.`;
  return "";
}

function renderCheckScreens(project, apps) {
  currentApps = apps;
  renderChecklist(project);
  appsList.replaceChildren();
  appsNote.textContent = apps.length ? plural(apps.length, "screen", "screens") : "";
  const approved = project.approved_model_count || 0;
  appsEmpty.hidden = apps.length > 0;
  if (!apps.length) {
    appsStatus.textContent = "";
    appsEmptyText.textContent = approved
      ? "You have no check screens yet. Set one up so people can check new pictures with your approved model."
      : "You have no check screens yet. You need an approved model first: teach one above, read its results, and approve it. Then you can set up a screen here.";
    const setup = checkScreenUrl(project);
    appsEmptyLink.hidden = !(approved && setup);
    appsEmptyLink.href = setup || "#";
    return;
  }
  appsStatus.textContent = `${plural(apps.length, "check screen", "check screens")} in this project.`;
  const setupBase = checkScreenUrl(project);
  apps.forEach(app => {
    const item = dsNode("li", "app-card");
    item.dataset.appId = app.id;
    const problem = appProblem(app);
    const head = dsNode("div", "app-head");
    const title = dsNode("h4", "app-title", app.name);
    head.append(title, badgeNode(problem ? "failed" : "approved", problem ? "Not working" : "Switched on"));
    item.append(head);
    item.append(dsNode("p", "model-meta", `Uses Model ${app.model_version.version}${app.model_in_use ? "" : " (not in use)"} · web address ${openScreenUrl(app) || `/check/${app.slug}`}`));
    if (problem) item.append(dsNode("p", "dataset-note", problem));
    const actions = dsNode("div", "model-actions");
    const openUrl = openScreenUrl(app);
    if (openUrl && !problem) {
      const open = dsNode("a", "primary link-button", "Open the screen");
      open.href = openUrl;
      open.setAttribute("aria-label", `Open the screen ${app.name}`);
      actions.append(open);
    }
    if (setupBase) {
      const edit = dsNode("a", "secondary link-button", "Edit");
      edit.href = `${setupBase}?app=${encodeURIComponent(app.id)}`;
      edit.setAttribute("aria-label", `Edit the screen ${app.name}`);
      actions.append(edit);
    }
    item.append(actions);
    appsList.append(item);
  });
}

async function loadCheckScreens(projectId) {
  if (!appsPanel) return;
  const project = projects.find(candidate => candidate.id === projectId);
  appsPanel.hidden = !project || project.modality !== "image";
  if (appsPanel.hidden) return;
  const token = ++appsToken;
  if (selectedId !== lastAppsProject) { currentApps = null; lastAppsProject = selectedId; }
  try {
    const body = await api(`/api/projects/${encodeURIComponent(projectId)}/inference/apps`);
    if (token !== appsToken || selectedId !== projectId) return;
    renderCheckScreens(project, body.apps);
  } catch (error) {
    if (token !== appsToken || selectedId !== projectId) return;
    appsList.replaceChildren();
    appsEmpty.hidden = true;
    appsStatus.textContent = `Could not load your check screens. ${error.message}`;
  }
}

// --- Get-started checklist ------------------------------------------------------------
const checklistEl = document.getElementById("checklist");
const checklistProgress = document.getElementById("checklist-progress");
const checklistStart = document.getElementById("checklist-start");
document.getElementById("checklist-create").addEventListener("click", openProjectDialog);
const checklistIntro = document.getElementById("checklist-intro");
const workspaceTitle = document.getElementById("workspace-title");
const workspaceSub = document.getElementById("workspace-sub");
const projectWorkspace = document.getElementById("project-workspace");
workspaceTitle.tabIndex = -1;

function revealWorkspace() {
  projectWorkspace.scrollIntoView({ block: "start" });
  workspaceTitle.focus({ preventScroll: true });
}

function scrollAndFocus(element) {
  element.scrollIntoView({ block: "center" });
  element.focus({ preventScroll: true });
}

// The check-screen builder lives at a per-project address; the page tells us its shape only when it exists.
const CHECK_URL_PLACEHOLDER = "00000000-0000-0000-0000-000000000000";
function checkScreenUrl(project) {
  const template = checklistEl.dataset.checkUrlTemplate || "";
  return template && project ? template.replace(CHECK_URL_PLACEHOLDER, project.id) : "";
}

function checklistSteps(project) {
  const pictures = project.image_count || 0;
  const accepted = project.accepted_boxes || 0;
  const waiting = project.pending_boxes || 0;
  const boxes = accepted + waiting;
  const hasLabels = project.labels.length > 0;
  const snapshots = project.dataset_count || 0;
  const approvedModels = project.approved_model_count || 0;
  const checkUrl = checkScreenUrl(project);
  const workingScreens = (currentApps || []).filter(app => app.status === "active" && app.model_in_use).length;
  const firstAsset = currentAssets[0] || null;
  const firstWaiting = currentAssets.find(asset => asset.pending_boxes) || null;
  const firstUnmarked = currentAssets.find(asset => !asset.accepted_boxes && !asset.pending_boxes) || firstAsset;
  const ready = pictures > 0 && hasLabels;
  return [
    {
      title: "Add pictures",
      state: pictures ? "done" : "todo",
      detail: pictures ? `${plural(pictures, "picture", "pictures")} added.` : "Add the pictures you want to label (JPEG, PNG or WebP).",
      action: { label: pictures ? "Add more pictures" : "Choose pictures", run: () => scrollAndFocus(imageUploadInput) }
    },
    {
      title: "Define labels",
      state: hasLabels ? "done" : "todo",
      detail: hasLabels ? `${plural(project.labels.length, "label", "labels")} defined.` : "Name the things you want to find, such as “knife” or “laptop”.",
      action: { label: hasLabels ? "Edit labels" : "Add labels", run: () => scrollAndFocus(labelNew) }
    },
    {
      title: "Draw or auto-suggest boxes",
      state: boxes ? "done" : ready ? "todo" : "blocked",
      detail: boxes
        ? `${plural(boxes, "box", "boxes")} so far: ${accepted} accepted, ${waiting} waiting for your review.`
        : ready ? "Draw a box around each item yourself, or let the computer suggest boxes for you to check."
          : "Finish steps 1 and 2 first.",
      action: ready && firstAsset ? { label: "Open a picture", run: () => openAnnotation(project.id, firstUnmarked || firstAsset) } : null
    },
    {
      title: "Review the boxes",
      state: boxes && !waiting ? "done" : waiting ? "todo" : "blocked",
      detail: waiting
        ? `${plural(waiting, "suggested box needs", "suggested boxes need")} your review. Nothing counts until you accept it.`
        : boxes ? "Every box has been checked by a person."
          : "Once there are boxes, check each one here. Suggested boxes always need your review.",
      action: waiting && firstWaiting ? { label: "Review suggestions", run: () => openAnnotation(project.id, firstWaiting) } : null
    },
    {
      title: "Make a training set",
      state: snapshots ? "done" : accepted ? "todo" : "blocked",
      detail: snapshots
        ? `${plural(snapshots, "snapshot", "snapshots")} saved. A snapshot never changes, so you can download it whenever you need it.`
        : accepted
          ? `You have ${plural(accepted, "accepted box", "accepted boxes")}. Save a snapshot to package them for a training tool.${waiting ? " Boxes that still need your review are left out." : ""}`
          : "Finish steps 3 and 4 first. You need at least one accepted box.",
      action: snapshots || accepted ? { label: snapshots ? "See snapshots" : "Make a training set", run: () => scrollAndFocus(datasetHeading) } : null
    },
    {
      title: "Teach the computer",
      state: approvedModels ? "done" : snapshots ? "todo" : "blocked",
      detail: approvedModels
        ? "A model has been approved by a person. You can teach it again at any time; a new model is never used until someone approves it."
        : snapshots
          ? "Let the computer learn from a snapshot, read how well it did on pictures it has never seen, then decide whether to approve it. This step ticks only when a person approves a model."
          : "Finish step 5 first. You need a saved snapshot.",
      action: snapshots ? { label: approvedModels ? "See your models" : "Teach the computer", run: () => scrollAndFocus(trHeading) } : null
    },
    checkUrl
      ? {
        title: "Set up the check screen",
        state: workingScreens ? "done" : approvedModels ? "todo" : "blocked",
        detail: workingScreens
          ? `${plural(workingScreens, "check screen is", "check screens are")} switched on. People can open ${workingScreens === 1 ? "it" : "them"} from the “Check screens” list below.`
          : approvedModels ? "Set up a screen where people check what the approved model finds in new pictures, and mark each result right or wrong." : "Finish step 6 first. A person has to approve a model before it can be used.",
        action: workingScreens
          ? { label: "See check screens", run: () => scrollAndFocus(appsHeading) }
          : approvedModels ? { label: "Set up the check screen", href: checkUrl } : null
      }
      : { title: "Set up the check screen", state: "soon", detail: "Coming soon. Not available yet; this step is planned for a later version.", action: null }
  ];
}

const stepBadge = { done: "Done", next: "Do this next", todo: "To do", blocked: "Not yet", soon: "Coming soon" };

function renderChecklist(project) {
  checklistEl.replaceChildren();
  checklistStart.hidden = true;
  const addStep = (number, step) => {
    const item = document.createElement("li");
    item.className = "checklist-step";
    item.dataset.state = step.state;
    if (step.state === "next") item.setAttribute("aria-current", "step");
    const marker = document.createElement("span");
    marker.className = "step-marker";
    marker.textContent = step.state === "done" ? Athena.ICONS.done : number;
    marker.setAttribute("aria-hidden", "true");
    const body = document.createElement("div");
    body.className = "step-body";
    const title = document.createElement("strong");
    title.textContent = step.title;
    const badge = document.createElement("span");
    badge.className = "step-badge";
    badge.textContent = stepBadge[step.state];
    const detail = document.createElement("p");
    detail.textContent = step.detail;
    const heading = document.createElement("div");
    heading.className = "step-title";
    heading.append(title, badge);
    body.append(heading, detail);
    item.append(marker, body);
    if (step.action) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = step.state === "next" ? "primary" : "secondary";
      button.textContent = step.action.label;
      button.setAttribute("aria-label", `${step.action.label} (step ${number}: ${step.title})`);
      if (step.action.href) {
        const link = document.createElement("a");
        link.href = step.action.href;
        link.className = `${step.state === "next" ? "primary" : "secondary"} link-button`;
        link.textContent = step.action.label;
        link.setAttribute("aria-label", `${step.action.label} (step ${number}: ${step.title})`);
        item.append(link);
      } else {
        button.addEventListener("click", step.action.run);
        item.append(button);
      }
    }
    checklistEl.append(item);
  };
  if (!project) {
    checklistProgress.textContent = "";
    checklistIntro.textContent = "Welcome. Here is how labelling works in Athena. The list fills in as you go.";
    checklistStart.hidden = false;
    ["Add pictures", "Define labels", "Draw or auto-suggest boxes", "Review the boxes"].forEach((title, index) =>
      addStep(String(index + 1), { title, state: "blocked", detail: "Create a project first.", action: null }));
    addStep("5", { title: "Make a training set", state: "blocked", detail: "Create a project first.", action: null });
    addStep("6", { title: "Teach the computer", state: "blocked", detail: "Create a project first.", action: null });
    addStep("7", { title: "Set up the check screen", state: checklistEl.dataset.checkUrlTemplate ? "blocked" : "soon", detail: checklistEl.dataset.checkUrlTemplate ? "Create a project first." : "Coming soon. Not available yet; this step is planned for a later version.", action: null });
    return;
  }
  if (project.modality !== "image") {
    checklistProgress.textContent = "";
    checklistIntro.textContent = "This kind of project has no labelling tools yet.";
    addStep("-", { title: "Labelling tools for this kind of file", state: "soon", detail: "Coming soon. You can still keep this project’s name and edit its labels below.", action: null });
    return;
  }
  const steps = checklistSteps(project);
  const nextIndex = steps.findIndex(step => step.state === "todo");
  if (nextIndex >= 0) steps[nextIndex].state = "next";
  const done = steps.filter(step => step.state === "done").length;
  const countable = steps.filter(step => step.state !== "soon").length;
  checklistProgress.textContent = `${done} of ${countable} steps done`;
  const firstSixDone = steps.slice(0, 6).every(step => step.state === "done");
  const lastDone = steps[6] && steps[6].state === "done";
  const keepGoing = "You can keep adding pictures and boxes, save a new snapshot, and teach a new model at any time. A new model is never used until a person approves it.";
  checklistIntro.textContent = firstSixDone
    ? (lastDone ? `Every step is done. ${keepGoing}` : `Steps 1 to 6 are done. ${keepGoing}${checklistEl.dataset.checkUrlTemplate ? " Step 7 is where people check what the approved model finds." : " The last step is coming soon."}`)
    : "Follow these steps in order. The list updates itself as you work.";
  steps.forEach((step, index) => addStep(String(index + 1), step));
}

function renderWorkspace() {
  const project = selectedProject();
  workspaceTitle.textContent = project ? project.name : "No project open";
  workspaceSub.textContent = project
    ? `${displayType(project)} · ${project.task}`
    : "Create a project, or open one from the list above.";
  deleteSelected.hidden = !project;
  labelsPanel.hidden = !project;
  if (!project) {
    imageAssetsPanel.hidden = true;
    datasetPanel.hidden = true;
    trPanel.hidden = true;
    if (appsPanel) appsPanel.hidden = true;
    autolabelPanel.hidden = true;
  } else {
    syncLabelEditor(project);
  }
  renderChecklist(project);
}

updateCustomModalityField();
void loadAutolabelStatus();
loadProjects();
