# RFC-0000: <short imperative title>

| Field | Value |
|---|---|
| Status | `draft` \| `in-review` \| `accepted` \| `implemented` \| `rejected` \| `superseded-by RFC-XXXX` |
| Author | |
| Created | YYYY-MM-DD |
| Updated | YYYY-MM-DD |
| Tracking issue | #NNN |

> **Written for an implementing agent, not only for humans.**
> Anything you leave implicit will be invented. Name real file paths, real type
> names, real commands. Prefer a concrete example over a description of one.
> This document is the artifact that is iterated on — the code is the output.
> When the code and this document disagree, this document is wrong: fix it here first.

## 1. Context and scope

What exists today, what hurts, why now. Evidence, not vibes: error rates, support
tickets, the specific commit or incident that triggered this. State what part of the
system this touches and, explicitly, what it does not touch.

## 2. Goals / Non-goals

**Goals**
- Observable, checkable outcomes. "P95 latency below 200 ms", not "make it faster".

**Non-goals**
- What is deliberately out of scope. This is the section that kills scope creep,
  and the section an agent will otherwise happily ignore. Be generous here.

## 3. Design

### 3.1 Overview
The shape of the solution in a few sentences, plus a diagram if there is more than
one moving part (mermaid, kept in this file).

### 3.2 Interfaces
Public API surface: signatures, routes, CLI flags, events. Real names, real types.
Include an example request and an example response.

### 3.3 Data model and storage
Schemas, migrations, retention, ownership. Note what is authoritative and what is
derived.

### 3.4 Affected code
Explicit paths, so an implementer does not have to guess:

| Path | Change |
|---|---|
| `src/...` | new / modified / deleted — what and why |

### 3.5 Constraints and invariants
Things that must remain true regardless of implementation. Backwards-compatibility
promises, ordering guarantees, idempotency, limits. Each one is a candidate test.

## 4. Alternatives considered

For each: what it was, why it was rejected. An alternative with no stated reason for
rejection reads as "not considered" and will be re-proposed later.

## 5. Cross-cutting concerns

Security and trust boundaries · privacy and personal data · observability (what is
logged, what is measured, what alerts) · performance budget · cost · failure modes
and what happens on each. Drop the lines that genuinely do not apply, and say so.

## 6. Acceptance criteria

Numbered, each one independently checkable. These become the tests — they are written
before the implementation.

1. **Given** … **when** … **then** … → `test_...`
2. …

Checklist for the PR that implements this RFC:

- [ ] Acceptance criteria above are covered by tests
- [ ] Visual change (UI, layout, styling, charts, generated images/PDFs) → before/after
      screenshots embedded in the PR body, same viewport, zoom and theme in both.
      Delete this line if the change has no visual surface.

## 7. Test plan

Which levels (unit / integration / e2e), what is deliberately not tested and why,
what data or fixtures are needed, how it is run (`<command>`).

## 8. Rollout and migration

Order of deployment, feature flag, backfill, how to roll back, who is affected during
the transition.

## 9. Open questions

Unresolved items blocking `accepted`. Each gets an owner. An empty section is a valid
and welcome outcome.

## 10. Changelog

| Date | Change |
|---|---|
| YYYY-MM-DD | Created |
