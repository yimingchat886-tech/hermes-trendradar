# Upstream Adoption And Harness Release

The update model is immutable official npm base plus local UIL overlay, never a
Git fork or rebase.

- AVAILABLE captures exact stable `@mindfoldhq/trellis` and matching
  `@mindfoldhq/trellis-core` packages plus complete supported generated trees
  under Git common dir. Identity is content-addressed; refresh does not mutate
  the worktree or activate a monitor.
- ADOPTED requires complete inherit/port/reject/project-owned coverage and an
  immutable qualified release binding candidate inventory, adoption report,
  overlay, check catalog, and semantic proof.
- INSTALLED means one target slot passed plan/apply/verify. Commit, merge,
  cleanup, push, publication, deployment, and activation remain separate.

Active downstream selection and qualification are defined by
downstream-deployer.md and content-addressed manifests under
`.trellis/releases`. Candidate artifacts authorize no adoption or target write.
