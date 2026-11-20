# Writing a recipe

A **recipe** is the operator-facing answer to "What do you want Athena to learn?". It names
the data kind, the annotation style its labels use, and — when the recipe cannot run yet — a
plain-language reason. Everything downstream (the guided project form, annotation
validation, training-job selection) reads the recipe registry, so a new task lands without
modality-specific branches in core code.

The contract is the `TaskRecipe` dataclass in `web/projects/tasks/base.py`; the registry
(and the four built-in recipes) live in `web/projects/tasks/__init__.py`. The JSON API
surface is `GET /api/recipes`.

> **Status.** Recipes exist and are exercised by tests, but there is **no compatibility
> policy for recipe payloads or keys across releases** — a key is "stable" only in the sense
> that it is stored on projects (`Project.recipe`) and URLs. Renaming a published recipe key
> orphans existing projects. No community recipe exists yet (ROADMAP.md M3).

## The fields

```python
from projects.tasks import TaskRecipe, register_recipe
```

`TaskRecipe` is a frozen dataclass; `as_dict()` returns exactly these keys for the API:

| Field | What it is |
| --- | --- |
| `key` | Stable identifier used in URLs and stored on projects. Lower-case letters, digits and `-`, at most 64 characters (`KEY_PATTERN` in the registry enforces it). |
| `title` | Short operator-facing title, e.g. "Recognize objects in pictures". |
| `modality` | The data kind this recipe works on; one of `Project.MODALITY_CHOICES`'s values. |
| `summary` | One or two plain sentences saying what the project will do with the data. |
| `annotation_kind` | Which annotation style this recipe's labels use — a slug shared with the modality plugin (e.g. `"boxes"`, `"clip_labels"`, `"transcripts"`, `"table_target"`). |
| `labels_help` | Plain-language guidance for what the labels mean in this recipe, shown while labeling. |
| `available` | Whether a project of this recipe can be set up and used today. Default `True`. |
| `unavailable_reason` | Why not, in one sentence, when `available` is false. |

The built-ins registered in `web/projects/tasks/__init__.py` are `vision-detection`,
`audio-classification`, `handwriting-ocr` and `tabular-prediction` — read them for the tone.
`DEFAULT_RECIPE_KEY` (`"vision-detection"`) is assumed for image projects created before
recipes existed and for any image project whose caller did not pick one.

### `available=False` placeholders

Unavailable recipes are still listed (the console shows them as "Coming soon"); a project
created with one keeps its name and labels but its upload/teach tools refuse plainly. The
pattern for landing a slice: register a placeholder with `available=False` first, then flip
it (or replace it wholesale) when the plugin and backend ship — the handwriting-OCR module
does exactly this on import (`web/projects/modalities/ocr.py` calls
`register_recipe(HANDWRITING_OCR, replace=True)`).

## Registration

Three ways (all in `web/projects/tasks/__init__.py`; note the settings key is plain
`RECIPES`, not inside `TRAINING`):

```python
# 1. From your own module (what a settings path loads):
from projects.tasks import TaskRecipe, register_recipe
register_recipe(TaskRecipe(
    key="audio-classification",
    title="Recognize sounds or equipment faults",
    modality="audio",
    summary="Teach Athena to tell equipment sounds or unusual faults apart from short recordings.",
    annotation_kind="clip_labels",
    labels_help="One label per sound or fault, e.g. healthy_motor, bearing_fault, belt_slip.",
    available=True,
))

# 2. Via settings (a "module:attribute" string list; the attribute is a TaskRecipe instance):
RECIPES = ["my_package.recipes:MY_RECIPE"]

# 3. In tests or a live process:
from projects.tasks import reload_recipes, unregister_recipe
```

A bad key (wrong characters, too long) raises `ValueError` at registration; a duplicate key
raises unless `replace=True`. The registry loads once and caches — installing a recipe means
restarting the process (`reload_recipes()` exists for tests).

Lookups: `recipes()` (the sorted dict), `get_recipe(key)`, and
`recipe_for_project(project)` — which reads `Project.recipe` (the stored key; the
human-readable text the person typed stays on `Project.task`) and returns `None` when the
project has no recipe or its key is unknown.

## How recipes route to backends

`RECIPE_DEFAULT_BACKENDS` in `web/projects/training_views.py` is the routing table:

```python
RECIPE_DEFAULT_BACKENDS = {"handwriting-ocr": OCR_BACKEND, "audio-classification": "audio-wav-cnn"}
```

The rule, in `_default_backend_for`: a run's backend is the request's own choice, **else the
recipe's entry from this table, else the console default** (`settings.TRAINING`'s
`DEFAULT_BACKEND`). Two consequences for a recipe author:

- **Only recipes whose backend is not the box default need an entry.** A recipe that runs on
  the default backend (today: any image box-detection recipe on
  `torchvision-fasterrcnn`) needs nothing here.
- **A recipe with an entry refuses a request that names a different backend**, in plain
  words (HTTP 400), rather than failing deep inside training. This is how a handwriting-OCR
  project cannot be accidentally run through a box backend.

Whether a project can be taught at all is `teachable_project` in the same file: image
projects always; other modalities when their plugin has an `annotation_kind` or their
recipe is `available`. A recipe with neither gets a 404 and the page's "not available yet"
refusal.

### Annotation dispatch (the rule your recipe participates in)

`annotation_plugin(project)` in `web/projects/annotations.py`: **the recipe's
`annotation_kind` wins when the project has a recipe that names a different kind than the
modality plugin's; the modality plugin is the fallback.** Image projects without a recipe
keep the vision default (boxes), so every existing project behaves exactly as before. If
your recipe asks an image project for something other than boxes, the plugin for that kind
must be registered — see [writing a modality plugin](writing-a-modality-plugin.md) on the
by-kind registration.

## Limits to know before you start

- **A recipe is an offer, not a capability.** `available=True` only means the project can be
  set up, uploaded to, labeled, frozen and taught *if* the modality plugin and the training
  backend it needs are actually installed and available in that deployment. The recipe
  itself carries no availability logic.
- **Keys are forever, practically.** `key` is stored on projects, runs and model versions;
  there is no migration tooling for renaming one.
- **`annotation_kind` must match a plugin's.** Nothing validates the pairing at
  registration time — a recipe naming a kind no plugin registers means labeling requests
  fail at dispatch with the modality's refusal message. Test the pair together (the
  built-ins' tests in `web/projects/tests_modal_foundation.py` and `tests_teach_general.py`
  are the pattern).
- **Tabular note (2026-11-19):** `tabular-prediction` is registered with `available=True`
  and ships a `tabular-sklearn` backend, but it has no entry in `RECIPE_DEFAULT_BACKENDS`;
  run requests on a table project must name `tabular-sklearn` explicitly or they get the
  console default backend, which will fail. If you write a recipe with its own backend, add
  the entry — this is a live example of the gap the table protects against.
