# T20 — Source time and ordered processing contracts (design record)

Date: 19 November 2026. Status: implemented and locally verified by tests (no real-deployment claim).
Task: T20 in [the vision workflows task list](2026-11-15-vision-workflows-task-list.md); depends on T09
(durable source recordings) and T19 (supervised durable workers for operation executions).

## The problem

An operation execution consumes frames from a recorded source. Everything downstream — crossing
episodes (T25), the event ledger (T23), aggregates over periods and shifts (T27) — needs to agree on
**when** each frame happened and **in what order** it must be processed. Without an explicit time
contract, three separate clocks get silently conflated:

* the moment the camera captured the picture (the recording's own clock),
* the moment the picture's container position says it should be presented (frame timestamps), and
* the moment Athena ingested the file (Athena's clock).

T09 already stores the first and third (the `OperationSourceRecording` row keeps the container's
`creation_time` when present, and `created_at` is the ingestion time). T19 executes operations in
durable workers but its execution step replays recorded human reference events without ever looking
at frame timestamps. T20 defines the middle piece — the ordered frame timeline — and the rules that
make a recorded replay **reproducible**: the same recording bytes always produce the same timeline.

## The time model

Three clocks, kept distinct, each with exactly one meaning:

| Clock | Where it comes from | Meaning | Stored |
|---|---|---|---|
| **Capture time** | the MP4 container's `creation_time` tag (UTC), parsed at import | when the recording session started at the source, *not* per-frame truth | `OperationSourceRecording.captured_at` (null when the container omits the tag — that is normal, not an error) |
| **Frame presentation time** | each video packet's `pts_time` from the recording's own timestamp track | when, relative to the start of the recording, that picture should be shown | computed on demand by this module; never fabricated |
| **Ingestion time** | Athena's clock when the import completed | when Athena took custody of the file; an audit fact about Athena, never a claim about the scene | `OperationSourceRecording.created_at` |

**Wall-clock mapping.** A frame's wall-clock time is
`captured_at + frame pts` — a *derived* value, only meaningful when the container actually carried
`creation_time`. When `captured_at` is null the timeline keeps raw PTS only and every wall-clock
field is explicitly null. The mapping is additive and monotonic, so ordering by PTS is ordering by
wall clock; the mapping never reorders frames.

**Monotonic processing order.** The processing order of a recorded replay is the **PTS order** of the
frame timeline, after discarding non-monotonic noise (see "late frames" below). PTS order — not
container byte order: MP4 stores packets out of order and the demuxer's packet list must be sorted
before it means anything. Within one recording the timeline is therefore totally ordered:
the same digest always yields the same sequence with the same indices.

## Late frames, discontinuities, gaps

For recorded sources these are **properties of the timestamps**, detected by comparing each frame's
PTS to the previous frame's PTS and duration:

* **Frame cadence.** A frame's expected start is the previous frame's `pts + duration` (packet
  durations come from the container; when absent, the demoted fallback is the median inter-frame
  delta of the timeline itself). A frame is **on time** when its PTS equals that expectation within
  `0.5 ms` (timestamps are rational; a healthy constant-rate stream matches exactly).
* **Late frame.** A frame whose PTS is *below* the expectation — the timestamps went backwards.
  For a recorded file this is malformed container data, not a network artefact: the frame is marked
  `late`, excluded from the monotonic processing order (it cannot be processed before frames the
  source already presented), and reported. It is never silently dropped without accounting.
* **Discontinuity / gap.** A frame whose PTS is *above* the expectation by more than a **gap
  threshold** (default **1.0 s**, configurable). A gap means the source itself has missing time:
  frames that should exist between the two neighbours do not. Anything missing but under the
  threshold is treated as ordinary encoder jitter and reported as a count only. A gap is recorded
  with its start, end and length in the timeline's `gaps` list, so downstream consumers (T24 gap
  accounting) can refuse to claim coverage across it.

The threshold is a policy, not a law: the caller passes it, the timeline records which threshold was
used, and different thresholds produce different timelines — deterministically.

## Session boundaries

A **session** is a maximal run of frames with no gap at or above the threshold between them. One
healthy recording is one session. A recording with two camera-stop/start edit points becomes three
sessions. Session rules:

* Sessions are numbered `1..N` in PTS order; every frame belongs to exactly one session.
* A session records its first/last PTS, frame count and (when `captured_at` exists) its first/last
  wall-clock time.
* The first frame of a new session is the first frame after a gap — the frame whose PTS jumps. The
  last frame of the previous session is the frame before it.

This is a *recording-level* definition. Cross-recording segmentation (the same camera's separate
uploads merged into one logical session) is not defined here; it needs source-level identity work
that belongs with T10's live sources.

## Reproducibility

The contract: **same recording digest + same gap threshold → identical timeline.** Concretely:

* the timeline is derived only from the stored bytes via ffprobe (the binary already required by the
  import preflight; if it is missing, timeline building refuses with a plain unavailable message
  rather than guessing),
* the timeline carries the recording's SHA-256 and the threshold used, and
* the timeline serializes canonically (sorted keys, no floats-as-noise — PTS values are rounded to
  microseconds) and hashes deterministically. Two runs over the same file must produce the same
  `timeline_sha256`.

A recording whose frames fail to decode is refused — the import preflight already guarantees whole-
file decodability at import, so a pinned recording that later fails to decode has been corrupted or
truncated, and that is reported as a refusal, not as an empty timeline.

## What the worker does with it

The T19 execution step now builds the pinned recording's frame timeline (after the existing
byte-for-byte digest check) and reports in its outcome: frame count, late-frame count, jitter count,
the gaps (start/end/length), and the sessions (frame range, PTS range, wall-clock range when
available), plus the timeline hash. The honesty rule is unchanged and explicit in the outcome note:
the run **read the recording's timestamps**; it still did not watch pixel content or run a model —
that is T21. The lease/claim/recovery layer is untouched.

## Not covered here (deliberately)

* **Live sources** (T10): wall-clock arrival, network jitter, backpressure and frame dropping are
  different problems; only recorded files have a stable timestamp track to build on.
* **Model inference** (T21): nothing here decodes pictures into detections; the timeline is the
  ordered *input contract* inference will plug into.
* **Time-zone presentation** (T27): site time zones for shift/midnight aggregates.
* **Cross-recording sessions** and camera identity over multiple files.
* **NTP / clock-trust semantics** for `creation_time` (the tag is taken as recorded, not verified
  against an external clock; provenance about the tag's trustworthiness is out of scope).
