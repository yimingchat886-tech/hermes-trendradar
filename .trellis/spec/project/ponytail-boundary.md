# Ponytail Boundary

## Purpose

Ponytail constrains staged delivery to the smallest necessary implementation. It can challenge scope, but it cannot silently remove confirmed requirements.

## Blocking Findings

Stop and ask the user before continuing when a PLAN needs:

- new production dependency
- new framework
- architecture boundary change
- new abstraction layer
- broad directory reorganization
- module rewrite
- deletion or replacement of a public API
- expanding a child task into an implicit larger feature

Use this prompt:

```md
# Ponytail Blocking Finding

Finding: <dependency/architecture/abstraction expansion>
Why it blocks: <risk or overbuild>
Minimal path: <smaller option>
Affected requirement: <id or section>

Options:
A. Do not do it; use the minimal path
B. Do it with a smaller scope
C. Do the current PLAN
D. Redesign

Please confirm.
```

## Advisory Findings

Record accept/reject reasoning, but do not block, for:

- shorter equivalent code
- reuse of existing helper
- less boilerplate
- smaller child scope
- duplicated logic that can be merged safely

## Safety Limit

Do not use Ponytail to weaken validation, security, accessibility, data-loss protections, correctness checks, or explicit user requirements.
