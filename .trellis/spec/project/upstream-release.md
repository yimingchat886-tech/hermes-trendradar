# Upstream Candidate Release And Wrapper

## 1. Scope / Trigger

Use this contract for the canonical harness's pinned upstream candidate and
repository-owned wrapper source. It is a local preparation and verification
boundary, not a source update, release, publication, downstream deployment,
machine installation, timer/service activation, or recurring cycle.

`release_id` is a content identity only. It never selects a version, implies
that a release exists, authorizes a downstream plan, or authorizes any effect.

## 2. Signatures

```python
plan_candidate(
    source_root: Path,
    official_root: Path,
    scratch_root: Path,
    *,
    candidate_output: Path | None = None,
    manifest_path: Path | None = None,
) -> dict[str, object]

verify_candidate(
    source_root: Path,
    official_root: Path,
    candidate_root: Path,
    plan: Mapping[str, object],
    *,
    manifest_path: Path | None = None,
) -> dict[str, object]
```

The local CLI exposes only those operations:

```text
PYTHONPATH=.trellis/scripts python3 -m upstream_release.cli plan --source-root <source> --official-root <pinned-local-dir> --scratch-root <scratch> [--candidate-output <new-scratch-child>] [--manifest-path <source-owned-manifest>]
PYTHONPATH=.trellis/scripts python3 -m upstream_release.cli verify --source-root <source> --official-root <pinned-local-dir> --candidate-root <candidate> --plan-file <local-json> [--manifest-path <source-owned-manifest>]
```

There is deliberately no `apply`, `release`, `publish`, `install`, `activate`,
or `cycle` command.

The versioned wrapper supports only:

```text
PYTHONPATH=.trellis/scripts python3 -m upstream_release.wrapper [--repo-root <root>] version|status|doctor
PYTHONPATH=.trellis/scripts python3 -m upstream_release.wrapper [--repo-root <root>] render --output-dir <new-temp-child>
```

## 3. Contracts

- Source, pinned official input, scratch, and any candidate are explicit,
  absolute local directories. URL-like input, root overlap, symlinks, missing
  paths, or a persisted candidate outside scratch fail before a candidate is
  created.
- Source must be a clean symbolic Git checkout. The planner records Git
  branch/commit/tree plus a file-map snapshot before and after work and rejects
  any difference. Git inspection disables optional locks.
- The manifest is a source-owned overlay file. It is the only ownership source.
  The official pin may contain only declared official bytes. Candidate assembly
  copies the source without `.git`, replaces exact official paths from the pin,
  then replaces exact overlay paths from source. Project and generated maps
  must remain byte-identical to source. The exact deployment adoption
  projection may be absent in both source and candidate when its policy says
  `source_disposition: preserve`; asymmetric presence or any other missing
  manifest entry remains an error.
- The plan records only relative mutation paths and digest identities. It
  contains separate official and overlay identities from the existing portable
  release format, path-redacted source/pin/candidate evidence, separate
  rollback component IDs, and a discard-only rollback strategy. Target-local
  `.trellis/.template-hashes.json` binds the pin/candidate but stays outside
  the portable official payload identity.
- Verification rechecks the plan digest, current source/pin identity, exact
  candidate map, ownership conformance, preservation maps, and release ID.
  It never regenerates a candidate or tolerates drift.
- Candidate output is optional. A successful output is a new child of scratch;
  the plan records only logical artifact identity and `artifact_persisted`, not
  a machine path. Failure removes the private candidate and produces no plan.
- Wrapper `version`, `status`, and `doctor` are stable redacted JSON derived
  from Git-versioned shell/template digests. `render` writes only a new
  candidate below the temporary root and returns no path. It never calls
  `systemctl`, writes a machine location, starts a service/timer, or invokes a
  downstream command. The service template names `doctor` only.
- The wrapper is not a second planner, transaction, authority, recovery,
  source-maintenance, or fleet-cycle implementation. Future installation or
  activation requires a separately authorized task and must preserve this
  boundary.

## 4. Validation And Error Matrix

| Condition | Required result |
|---|---|
| URL-like pin or non-local root | Reject before scratch candidate creation |
| Dirty/detached source or source snapshot drift | Reject; no source or candidate artifact write |
| Manifest overlap, unknown pin file, missing ownership, or symlink | Reject before candidate release identity |
| Adoption projection absent in both source and candidate | Preserve absence; do not materialize project state |
| Adoption projection present on only one side | Reject as project-preservation drift |
| Candidate `.git`, byte drift, path drift, or preservation drift | Reject verification; do not repair it |
| Official/overlay identity mismatch | Reject verification; never infer a release |
| Wrapper output outside temporary root or existing/symlink path | Reject before rendering |
| Any install/activation/service/timer/cycle request | Out of scope; require separate authority |

## 5. Good / Base / Bad Cases

- Good: a clean local source and exact local pin produce a disposable
  candidate whose official ID matches the pin while overlay and preserved owner
  maps remain independently bound.
- Base: planning without `candidate_output` returns only path-redacted evidence
  and removes the private candidate before returning. A missing target-only
  adoption projection stays missing in the private candidate.
- Bad: a URL-like pin, symlink, manifest collision, unknown pin file, dirty
  source, candidate drift, asymmetric adoption projection, or any other missing
  owner entry is rejected without repairing or mutating source.

## 6. Tests Required

- Plan/verify fixture proves exact official and overlay mutation paths,
  source zero-write, path-redacted plan output, separate component identities,
  project/generated preservation, and no candidate `.git`.
- The fixture preserves a missing adoption projection as absence, rejects
  asymmetric presence, and continues to reject every other missing
  project/generated entry.
- Candidate fixture rejects candidate drift, URL-like pin input, symlink input,
  and manifest collision without source mutation.
- Wrapper tests assert deterministic redacted `version`, `status`, and
  `doctor`, temporary-only rendering, no `systemctl` source, and no machine
  activation. Shell syntax is checked with `bash -n`.

## 7. Wrong Vs Correct

### Wrong

Treat a candidate `release_id` as permission to update source, publish, or
start a downstream plan.

### Correct

Use the ID only to bind candidate bytes. Any source update, release,
publication, target transaction, installation, or activation remains a new
explicitly authorized operation outside this package.

Do not create `{}` at a missing target-only adoption path to satisfy the
manifest. Preserve its absence in the candidate; only the downstream deployer
may write that project-owned projection under separate authority.
