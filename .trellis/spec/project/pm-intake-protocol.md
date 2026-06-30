# PM Intake Protocol

## When To Use

Use PM intake for non-trivial requests before drafting a PRD or implementing staged overlay work.

## Required Output

```md
# PM Intake

## 1. Original Request

<preserve the user's wording or a faithful summary>

## 2. Real Goal

<what the user is trying to achieve>

## 3. Ambiguous Or Risky Wording

| Original | Issue | Proposed Rewrite |
|---|---|---|

## 4. Optimized Requirement

<executable requirement>

## 5. Risk Level

T0 / T1 / T2 / T3 / T4

## 6. Staged Overlay Needed

yes/no + reason

## 7. Oracle Review Budget Needed

yes/no + reason

## 8. User Confirmation Points

- [ ] ...
```

## Rule

The agent may improve unclear user wording, but must show what changed and why before treating the rewritten requirement as final.
