# NovelForge Domain Context

## Story Graph

- **Canonical fact**: A story fact that has crossed the project's commit and
  acceptance boundary. It is the source for what has happened in the story.
- **Story Graph node**: A queryable story-world concept. A node may describe a
  canonical fact, a planning concept, or recorded evidence; its status and
  provenance must make that distinction explicit.
- **Semantic edge**: A typed assertion about how two nodes relate, such as
  `advances`, `resolves`, `involves`, or `happens_at`. An unlabeled relation is
  not sufficient domain language.
- **Provenance**: The recorded reason a node or edge exists, including the
  authoritative source and the relevant chapter, fact, commit, or run when
  available.

## Narrative concepts

- **PlotThread**: A named line of narrative pursuit or conflict that can span
  multiple chapters and can be advanced or resolved. A PlotThread reference
  identifies a named line; it does not by itself assert that a new canonical
  event happened. A lifecycle stage is established only by an explicit typed
  StoryFact action for that PlotThread; an association in the same fact is not
  progress evidence.
- **Foreshadow**: A planted narrative promise or unresolved hook. Its lifecycle
  is `planted`, `advanced`, optionally `deferred`, and `resolved` when those
  states are explicitly recorded.
- **Structured entity reference**: A typed reference such as
  `{type: "PlotThread", id: "identity-investigation"}`. The type is part of
  the meaning; an untyped string must not be promoted into a different entity
  type by guesswork.
- **Planning overlay**: Author or AI intent about future story state. It is
  useful for workflow decisions but is not Canon until the existing commit
  boundary accepts the resulting story facts.

## Explainability boundary

Recorded evidence can explain why a node or edge is visible. Missing evidence
must remain missing; the interface must not infer Canon, knowledge, lifecycle
progress, or AI context from prose alone.
