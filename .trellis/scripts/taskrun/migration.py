"""Deterministic Legacy inventory and one-way UIL-v1 cutover."""

from __future__ import annotations

import json
import fnmatch
import shutil
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Sequence

from prd import resolve_requirement_ids

from .authority import Authority, AuthorityError, SCHEMA_VERSION, authority_path, canonical_json, digest, git, git_common_dir, utc_now
from .release import repository_snapshot, validate_release
from .service import (
    HARD_ATTEMPT_LIMIT,
    SOFT_ATTEMPT_LIMIT,
    STAGNATION_ROUNDS,
    _changed_paths,
    _write_if_changed,
)


CUTOVER_ROOT = Path(".trellis/migration/unified-intent-loop-v1")
BASELINE_RESIDUALS = {
    "08-07-address-pr-11-taskrun-closeout-review-findings",
    "08-07-address-pr-11-taskrun-closeout-review-findings-r1",
    "08-07-simplify-taskrun-child-commit-closeout-and-parent-settlement",
    "08-07-simplify-taskrun-child-commit-closeout-and-parent-settlement-r1",
}
DISABLED_WRITERS = (
    "add-subtask",
    "archive",
    "archive-orphans",
    "archive-recover",
    "complete-child",
    "create",
    "loop-v1",
    "remove-subtask",
    "soft-archive",
    "start",
)


def _tree_digest(path: Path) -> str:
    rows: list[tuple[str, str]] = []
    for item in sorted(path.rglob("*")):
        if item.is_symlink():
            rows.append((item.relative_to(path).as_posix(), f"symlink:{item.readlink()}"))
        elif item.is_file():
            rows.append((item.relative_to(path).as_posix(), sha256(item.read_bytes()).hexdigest()))
    return digest(rows)


def _legacy_disposition(
    name: str,
    task: Mapping[str, object] | None,
    archived: bool,
    candidate: Mapping[str, object],
) -> str:
    if name in BASELINE_RESIDUALS:
        return "sealed_superseded"
    if task is None:
        return "sealed_legacy_unknown"
    status = str(task.get("status") or "unknown")
    if archived or status in {"completed", "cancelled", "archived"}:
        return "sealed_terminal"
    if status == "planning":
        return "sealed_superseded"
    return (
        "sealed_candidate_attachment"
        if candidate["has_candidate"]
        else "sealed_superseded"
    )


def _task_touches(
    task: Mapping[str, object] | None,
    task_path: Path,
    worktree: Path,
) -> list[str]:
    if not task:
        return [task_path.relative_to(worktree).as_posix() + "/**"]
    touches = task.get("touches")
    if not isinstance(touches, list):
        execution = task.get("meta", {})
        execution = execution.get("execution", {}) if isinstance(execution, Mapping) else {}
        envelope = execution.get("start_envelope", {}) if isinstance(execution, Mapping) else {}
        actions = envelope.get("actions", []) if isinstance(envelope, Mapping) else []
        touches = [
            raw
            for action in actions
            if isinstance(action, Mapping)
            for raw in action.get("touches", [])
            if isinstance(raw, str)
        ]
    return [str(raw) for raw in touches if isinstance(raw, str)] or [
        task_path.relative_to(worktree).as_posix() + "/**"
    ]


def _candidate_evidence(
    worktree: Path,
    task: Mapping[str, object] | None,
    task_path: Path,
) -> dict[str, object]:
    touches = _task_touches(task, task_path, worktree)
    changed = [
        raw
        for raw in _changed_paths(worktree)
        if any(fnmatch.fnmatch(raw, pattern) for pattern in touches)
    ]
    untracked: list[tuple[str, str]] = []
    untracked_paths = set(
        filter(
            None,
            git(
                worktree,
                "ls-files",
                "--others",
                "--exclude-standard",
                "-z",
            ).stdout.split("\0"),
        )
    )
    for raw in changed:
        if raw not in untracked_paths:
            continue
        path = worktree / raw
        if path.is_symlink():
            value = f"symlink:{path.readlink()}"
        elif path.is_file():
            value = sha256(path.read_bytes()).hexdigest()
        else:
            continue
        untracked.append((raw, value))
    errors: list[str] = []
    branch = str(task.get("branch") or "") if task else ""
    base_branch = str(task.get("base_branch") or "") if task else ""
    candidate_ref = branch or str(task.get("commit") or "") if task else ""
    if not candidate_ref:
        candidate_ref = "HEAD"
    candidate_result = git(
        worktree,
        "rev-parse",
        f"{candidate_ref}^{{commit}}",
        check=False,
    )
    candidate_commit = candidate_result.stdout.strip() if not candidate_result.returncode else ""
    if not candidate_commit:
        errors.append(f"candidate_ref_unresolved:{candidate_ref}")
    base_result = git(
        worktree,
        "rev-parse",
        f"{base_branch or 'HEAD'}^{{commit}}",
        check=False,
    )
    base_commit = base_result.stdout.strip() if not base_result.returncode else ""
    if not base_commit:
        errors.append(f"base_ref_unresolved:{base_branch or 'HEAD'}")
    reachable: bool | None = None
    if candidate_commit and base_commit:
        reachable = not git(
            worktree,
            "merge-base",
            "--is-ancestor",
            candidate_commit,
            base_commit,
            check=False,
        ).returncode
    unique = reachable is False
    commit_paths: list[str] = []
    if unique:
        commit_paths = [
            raw
            for raw in filter(
                None,
                git(
                    worktree,
                    "diff",
                    "--name-only",
                    "-z",
                    f"{base_commit}...{candidate_commit}",
                    "--",
                ).stdout.split("\0"),
            )
            if any(fnmatch.fnmatch(raw, pattern) for pattern in touches)
        ]
    tracked = [raw for raw in changed if raw not in untracked_paths]
    tracked_diff = (
        git(worktree, "diff", "--binary", "HEAD", "--", *tracked).stdout
        if tracked
        else ""
    )
    commit_diff = (
        git(
            worktree,
            "diff",
            "--binary",
            f"{base_commit}...{candidate_commit}",
            "--",
            *commit_paths,
        ).stdout
        if commit_paths
        else ""
    )
    status = str(task.get("status") or "unknown") if task else "unreadable"
    return {
        "candidate_diff_digest": digest(
            {
                "commit_diff": commit_diff,
                "tracked_diff": tracked_diff,
                "untracked": sorted(untracked),
            }
        ),
        "candidate_reachable_from_base": reachable,
        "candidate_unique": unique,
        "classification_errors": errors,
        "cleanup_result": "retained_read_only",
        "base_branch": base_branch or None,
        "base_commit": base_commit or None,
        "branch": branch or None,
        "candidate_commit": candidate_commit or None,
        "has_candidate": bool(
            unique or changed or (errors and status not in {"completed", "cancelled", "archived"})
        ),
        "head_commit": git(worktree, "rev-parse", "HEAD^{commit}").stdout.strip(),
        "head_tree": git(worktree, "rev-parse", "HEAD^{tree}").stdout.strip(),
        "scoped_commit_paths": sorted(commit_paths),
        "scoped_dirty_paths": sorted(changed),
        "touches": sorted(touches),
    }


def legacy_inventory(repo_root: Path, *, bootstrap_task_dir: str | None = None) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    worktrees = [
        Path(line.removeprefix("worktree ")).resolve()
        for line in git(root, "worktree", "list", "--porcelain").stdout.splitlines()
        if line.startswith("worktree ")
    ]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for worktree in sorted(set(worktrees)):
        tasks_root = worktree / ".trellis/tasks"
        if not tasks_root.is_dir():
            continue
        candidates = [
            path for path in tasks_root.iterdir() if path.is_dir() and path.name != "archive"
        ]
        archive = tasks_root / "archive"
        if archive.is_dir():
            candidates.extend(
                path
                for month in archive.iterdir()
                if month.is_dir()
                for path in month.iterdir()
                if path.is_dir()
            )
        for path in sorted(candidates):
            if path.name == bootstrap_task_dir:
                continue
            relative = path.relative_to(worktree).as_posix()
            task_path = path / "task.json"
            try:
                task = (
                    json.loads(task_path.read_text(encoding="utf-8"))
                    if task_path.is_file()
                    else None
                )
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                task = None
            candidate = _candidate_evidence(worktree, task, path)
            variant = {
                **candidate,
                "archived": "archive" in path.relative_to(tasks_root).parts,
                "digest": _tree_digest(path),
                "disposition": _legacy_disposition(
                    path.name,
                    task,
                    "archive" in path.relative_to(tasks_root).parts,
                    candidate,
                ),
                "path": relative,
                "status": str(task.get("status")) if task else "unreadable",
                "worktree": str(worktree),
            }
            grouped.setdefault(path.name, []).append(variant)
    entries = []
    for variants_value in grouped.values():
        variants = sorted(
            variants_value,
            key=lambda item: (item["digest"], item["path"], item["worktree"]),
        )
        statuses = sorted({item["status"] for item in variants})
        dispositions = {item.pop("disposition") for item in variants}
        disposition = (
            "sealed_terminal"
            if "sealed_terminal" in dispositions
            else (
                "sealed_candidate_attachment"
                if "sealed_candidate_attachment" in dispositions
                else sorted(dispositions)[0]
            )
        )
        paths = sorted({item["path"] for item in variants})
        path = next((raw for raw in paths if "/archive/" not in raw), paths[0])
        entries.append(
            {
                "digest": digest(variants),
                "disposition": disposition,
                "path": path,
                "status": statuses[0] if len(statuses) == 1 else "divergent",
                "variants": variants,
            }
        )
    entries.sort(key=lambda item: item["path"])
    evidence_files = []
    for worktree in sorted(set(worktrees)):
        candidates = [
            worktree / ".trellis/.current-task",
            worktree / "BOARD.md",
            *sorted((worktree / ".trellis/tasks").glob("**/*.jsonl")),
            *sorted((worktree / ".trellis/tasks").glob("**/stage-report.md")),
            *sorted((worktree / ".trellis/tasks").glob("**/taskrun-start.json")),
            *sorted(
                (worktree / ".trellis/spec/project/receipts").glob("**/*.json")
            ),
        ]
        for path in candidates:
            if path.is_file():
                evidence_files.append(
                    {
                        "digest": sha256(path.read_bytes()).hexdigest(),
                        "path": path.relative_to(worktree).as_posix(),
                        "worktree": str(worktree),
                    }
                )
    runtime = [
        {"digest": sha256(path.read_bytes()).hexdigest(), "path": str(path)}
        for path in _runtime_authorities(root)
    ]
    surfaces = {
        "branches": sorted(
            line
            for line in git(
                root,
                "for-each-ref",
                "--format=%(refname) %(objectname)",
                "refs/heads",
            ).stdout.splitlines()
            if line
        ),
        "evidence_files": sorted(
            evidence_files, key=lambda item: (item["path"], item["worktree"])
        ),
        "runtime_authorities": runtime,
        "classification_errors": sorted(
            {
                error
                for entry in entries
                for variant in entry["variants"]
                for error in variant["classification_errors"]
            }
        ),
        "worktree_registry_digest": digest(
            git(root, "worktree", "list", "--porcelain").stdout
        ),
    }
    return {
        "baseline_commit": git(root, "rev-parse", "HEAD^{commit}").stdout.strip(),
        "entries": entries,
        "schema": "uil-v1-legacy-inventory",
        "surfaces": surfaces,
    }


def migration_plan(repo_root: Path, *, bootstrap_task_dir: str) -> dict[str, Any]:
    inventory = legacy_inventory(repo_root, bootstrap_task_dir=bootstrap_task_dir)
    return {
        "bootstrap_task_dir": bootstrap_task_dir,
        "inventory_digest": digest(inventory),
        "legacy_count": len(inventory["entries"]),
        "phases": ["backup", "initialize", "seal", "project", "mark"],
        "schema": "uil-v1-cutover-plan",
    }


def _runtime_authorities(repo_root: Path) -> list[Path]:
    roots: set[Path] = set()
    lines = git(repo_root, "worktree", "list", "--porcelain").stdout.splitlines()
    for line in lines:
        if line.startswith("worktree "):
            roots.add(Path(line.removeprefix("worktree ")).resolve())
    databases: set[Path] = set()
    for root in roots:
        runtime = root / ".trellis/.runtime/taskrun/runs"
        if runtime.is_dir():
            databases.update(
                path for path in runtime.glob("*/authority.sqlite3*") if path.is_file()
            )
    return sorted(databases)


def _backup_legacy(repo_root: Path) -> tuple[Path, dict[str, Any]]:
    root = Path(repo_root).resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = git_common_dir(root) / "trellis/backups/uil-v1" / stamp
    suffix = 0
    while backup.exists():
        suffix += 1
        backup = backup.with_name(f"{stamp}-{suffix}")
    backup.mkdir(parents=True, mode=0o700)
    rows = []
    for index, source in enumerate(_runtime_authorities(root), start=1):
        target = backup / f"legacy-authority-{index:03d}-{source.name}"
        shutil.copy2(source, target)
        rows.append(
            {
                "digest": sha256(target.read_bytes()).hexdigest(),
                "source": str(source),
                "target": target.name,
            }
        )
    manifest = {"files": rows, "schema": "uil-v1-byte-backup"}
    _write_if_changed(backup / "backup-manifest.json", (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())
    return backup, manifest


def _import_bootstrap(authority: Authority, bootstrap: Path) -> str:
    task = json.loads((bootstrap / "task.json").read_text(encoding="utf-8"))
    prd_path = bootstrap / "prd.md"
    prd = prd_path.read_text(encoding="utf-8")
    task_id = str(task["id"])
    task_dir_name = bootstrap.name
    prd_digest = sha256(prd.encode()).hexdigest()
    accepted_commit = git(
        authority.repo_root,
        "log", "-1", "--format=%H", "--", prd_path.relative_to(authority.repo_root).as_posix(),
    ).stdout.strip()
    requirements = resolve_requirement_ids(authority.repo_root, prd, default_owner="codex")
    now = utc_now()
    authority.execute(
        "INSERT OR IGNORE INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            task_id, task_dir_name, task["title"], "code",
            prd_path.relative_to(authority.repo_root).as_posix(), prd_digest, 1,
            task.get("base_branch") or "main", task.get("branch") or "codex/unified-intent-loop-v1",
            task.get("worktree_path"), "running", "not_started", "open", None, now, now,
        ),
    )
    authority.execute(
        "INSERT OR IGNORE INTO bindings VALUES(?,?,?,?,?,?)",
        (
            task_id, 1, prd_digest, accepted_commit, now,
            canonical_json({"base_commit": accepted_commit, "requirement_ids": requirements}),
        ),
    )
    run_id = f"run-{digest({'task_id': task_id})}"
    authority.execute(
        "INSERT OR IGNORE INTO runs VALUES(?,?,?,?,?,?,?,?,?)",
        (run_id, task_id, "loop", "delegated", SOFT_ATTEMPT_LIMIT, HARD_ATTEMPT_LIMIT, STAGNATION_ROUNDS, now, now),
    )
    start = bootstrap / "taskrun-start.json"
    actions = json.loads(start.read_text(encoding="utf-8"))["actions"] if start.is_file() else []
    for action in actions:
        authority.execute(
            "INSERT OR IGNORE INTO actions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id, action["action_id"], 1,
                action["action_id"].split("-", 2)[-1].split("-", 1)[0],
                canonical_json(action["dependencies"]), canonical_json(action["requirement_ids"]),
                canonical_json(action["touches"]), canonical_json(action["checks"]),
                "high" if action["action_id"] in {"ACT-10-authority", "ACT-40-closeout", "ACT-70-hard-cutover"} else "medium",
                "pending", None, None, now, now,
            ),
        )
    authority.record_event(
        f"migration:bootstrap:{task_id}:{prd_digest}",
        "bootstrap_imported",
        {"binding_generation": 1, "legacy_taskrun_imported": False},
        task_id=task_id,
        run_id=run_id,
    )
    return task_id


def _import_legacy_continuation(
    authority: Authority,
    entry: Mapping[str, object],
) -> str:
    variants = entry.get("variants")
    if not isinstance(variants, list) or not variants:
        raise AuthorityError(f"Legacy continuation has no readable variant: {entry['path']}")
    readable = [
        variant
        for variant in variants
        if variant.get("status") != "unreadable"
        and (Path(str(variant["worktree"])) / str(entry["path"]) / "task.json").is_file()
        and (Path(str(variant["worktree"])) / str(entry["path"]) / "prd.md").is_file()
    ]
    if not readable:
        raise AuthorityError(f"Legacy continuation has no readable variant: {entry['path']}")
    selected = sorted(
        readable,
        key=lambda variant: (
            variant.get("status") != "running",
            str(variant["worktree"]),
        ),
    )[0]
    source_root = Path(str(selected["worktree"]))
    source = source_root / str(entry["path"])
    task = json.loads((source / "task.json").read_text(encoding="utf-8"))
    prd = (source / "prd.md").read_text(encoding="utf-8")
    task_id = str(task["id"])
    target = authority.repo_root / str(entry["path"])
    _write_if_changed(target / "prd.md", prd.encode())
    requirements = resolve_requirement_ids(authority.repo_root, prd, default_owner="codex")
    prd_digest = sha256(prd.encode()).hexdigest()
    now = utc_now()
    accepted_commit = git(source_root, "rev-parse", "HEAD^{commit}").stdout.strip()
    authority.execute(
        "INSERT OR IGNORE INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            task_id,
            target.name,
            task.get("title") or task_id,
            "code",
            (target / "prd.md").relative_to(authority.repo_root).as_posix(),
            prd_digest,
            1,
            task.get("base_branch") or "main",
            task.get("branch") or f"codex/{task_id}",
            None,
            "human_blocked",
            "not_started",
            "open",
            None,
            now,
            now,
        ),
    )
    authority.execute(
        "INSERT OR IGNORE INTO bindings VALUES(?,?,?,?,?,?)",
        (
            task_id,
            1,
            prd_digest,
            accepted_commit,
            now,
            canonical_json(
                {
                    "base_commit": accepted_commit,
                    "legacy_candidate": {
                        key: selected[key]
                        for key in (
                            "candidate_diff_digest",
                            "head_commit",
                            "head_tree",
                        )
                    },
                    "requirement_ids": requirements,
                    "source": "legacy_continuation",
                }
            ),
        ),
    )
    run_id = f"run-{digest({'task_id': task_id})}"
    authority.execute(
        "INSERT OR IGNORE INTO runs VALUES(?,?,?,?,?,?,?,?,?)",
        (
            run_id,
            task_id,
            "loop",
            "delegated",
            SOFT_ATTEMPT_LIMIT,
            HARD_ATTEMPT_LIMIT,
            STAGNATION_ROUNDS,
            now,
            now,
        ),
    )
    authority.execute(
        "INSERT OR IGNORE INTO actions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            run_id,
            "imported_candidate",
            1,
            "recovery",
            "[]",
            canonical_json(requirements),
            canonical_json(task.get("touches") or ["**"]),
            canonical_json(["trellis.unittest.focused", "trellis.diff.check"]),
            "high",
            "pending",
            None,
            None,
            now,
            now,
        ),
    )
    authority.record_event(
        f"migration:continuation:{task_id}:{prd_digest}",
        "legacy_continuation_imported",
        {
            "candidate": {
                key: selected[key]
                for key in (
                    "candidate_diff_digest",
                    "head_commit",
                    "head_tree",
                )
            },
            "source_path": entry["path"],
        },
        task_id=task_id,
        run_id=run_id,
    )
    return task_id


def apply_cutover(
    repo_root: Path,
    *,
    bootstrap_task_dir: str,
    first_release_id: str,
    continue_legacy: Sequence[str] = (),
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    output = root / CUTOVER_ROOT
    marker_path = output / "cutover-marker.json"
    if marker_path.is_file():
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        if (
            marker.get("bootstrap_task_dir") != bootstrap_task_dir
            or marker.get("first_qualified_release_id") != first_release_id
            or marker.get("continued_legacy") != sorted(set(continue_legacy))
        ):
            raise AuthorityError("Existing cutover marker binds different cutover inputs")
        return marker
    if authority_path(root).is_file():
        with Authority(root) as authority:
            recovered = authority.one(
                """SELECT payload_json FROM events
                   WHERE event_type='cutover_applied' ORDER BY seq DESC LIMIT 1"""
            )
        if recovered:
            outputs = json.loads(recovered["payload_json"])["outputs"]
            marker = outputs["marker"]
            if (
                marker["bootstrap_task_dir"] != bootstrap_task_dir
                or marker["first_qualified_release_id"] != first_release_id
                or marker["continued_legacy"] != sorted(set(continue_legacy))
            ):
                raise AuthorityError("Committed cutover binds different inputs")
            output.mkdir(parents=True, exist_ok=True)
            names = {
                "cutover_report": "cutover-report.json",
                "legacy_inventory": "legacy-inventory.json",
                "marker": "cutover-marker.json",
                "plan": "plan.json",
            }
            for name, value in outputs.items():
                _write_if_changed(
                    output / names[name],
                    (json.dumps(value, indent=2, sort_keys=True) + "\n").encode(),
                )
            return marker
    plan = migration_plan(root, bootstrap_task_dir=bootstrap_task_dir)
    plan_digest = digest(plan)
    inventory = legacy_inventory(root, bootstrap_task_dir=bootstrap_task_dir)
    continuations = [
        entry
        for entry in inventory["entries"]
        if entry["disposition"] == "sealed_candidate_attachment"
    ]
    requested = set(continue_legacy)
    unresolved = [
        entry["path"]
        for entry in continuations
        if entry["path"] not in requested and Path(entry["path"]).name not in requested
    ]
    if unresolved:
        raise AuthorityError(
            "Legacy work needs an explicit continuation disposition: "
            + ", ".join(unresolved)
        )
    release_path = root / ".trellis/releases" / f"{first_release_id}.json"
    if not release_path.is_file() or release_path.is_symlink():
        raise AuthorityError("Cutover first Harness release manifest is unavailable")
    release_manifest = json.loads(release_path.read_text(encoding="utf-8"))
    if validate_release(root, release_manifest) != first_release_id:
        raise AuthorityError("Cutover first Harness release identity changed")
    candidate_digest = repository_snapshot(root)
    backup_path, backup_manifest = _backup_legacy(root)
    with Authority(root, create=True) as authority, authority.transaction():
        authority.execute(
            "INSERT OR IGNORE INTO release_bindings VALUES(?,?,?,?,?,?)",
            (
                first_release_id,
                canonical_json(release_manifest),
                1,
                None,
                None,
                utc_now(),
            ),
        )
        authority.record_event(
            f"release:qualify:{first_release_id}:cutover",
            "release_qualified",
            {"release_id": first_release_id},
            operation_input=release_manifest,
        )
        for entry in inventory["entries"]:
            legacy_id = "legacy-" + digest({"path": entry["path"]})
            authority.execute(
                "INSERT OR IGNORE INTO legacy_records VALUES(?,?,?,?,?)",
                (
                    legacy_id, entry["path"], entry["disposition"], entry["digest"],
                    canonical_json({"status": entry["status"]}),
                ),
            )
        task_id = _import_bootstrap(authority, root / ".trellis/tasks" / bootstrap_task_dir)
        imported = [_import_legacy_continuation(authority, entry) for entry in continuations]
        report = {
            "backup_manifest_digest": digest(backup_manifest),
            "backup_ref": backup_path.relative_to(git_common_dir(root)).as_posix(),
            "dispositions": {
                disposition: sum(
                    1
                    for row in inventory["entries"]
                    if row["disposition"] == disposition
                )
                for disposition in sorted(
                    {row["disposition"] for row in inventory["entries"]}
                )
            },
            "inventory_digest": digest(inventory),
            "continuation_task_ids": imported,
            "schema": "uil-v1-cutover-report",
        }
        marker = {
            "baseline_commit": inventory["baseline_commit"],
            "baseline_tree": git(root, "rev-parse", "HEAD^{tree}").stdout.strip(),
            "bootstrap_task_dir": bootstrap_task_dir,
            "db_schema_version": SCHEMA_VERSION,
            "disabled_writers": list(DISABLED_WRITERS),
            "continued_legacy": sorted(set(continue_legacy)),
            "first_qualified_release_id": first_release_id,
            "full_validation_digest": release_manifest["semantic_qualification"].get(
                "suite_digest", digest(release_manifest["semantic_qualification"])
            ),
            "implementation_candidate_digest": candidate_digest,
            "legacy_inventory_digest": digest(inventory),
            "migration_plan_digest": plan_digest,
            "migration_report_digest": digest(report),
            "retained_reader_version": 1,
            "schema": "unified-intent-loop-v1-cutover-marker",
        }
        outputs = {
            "cutover_report": report,
            "legacy_inventory": inventory,
            "marker": marker,
            "plan": plan,
        }
        authority.record_event(
            f"migration:cutover:{plan_digest}",
            "cutover_applied",
            {
                "continuation_task_ids": imported,
                "inventory_digest": digest(inventory),
                "outputs": outputs,
                "task_id": task_id,
            },
            task_id=task_id,
            operation_input=plan,
        )
    output.mkdir(parents=True, exist_ok=True)
    _write_if_changed(output / "plan.json", (json.dumps(plan, indent=2, sort_keys=True) + "\n").encode())
    _write_if_changed(output / "legacy-inventory.json", (json.dumps(inventory, indent=2, sort_keys=True) + "\n").encode())
    _write_if_changed(output / "cutover-report.json", (json.dumps(report, indent=2, sort_keys=True) + "\n").encode())
    _write_if_changed(marker_path, (json.dumps(marker, indent=2, sort_keys=True) + "\n").encode())
    return marker


def legacy_records(repo_root: Path) -> list[dict[str, Any]]:
    with Authority(repo_root) as authority:
        return [dict(row) for row in authority.all("SELECT * FROM legacy_records ORDER BY source_path")]


def reject_partial_rollback(repo_root: Path) -> None:
    marker = Path(repo_root) / CUTOVER_ROOT / "cutover-marker.json"
    if marker.is_file():
        raise AuthorityError("Partial rollback is disabled after cutover; revert the whole cutover and restore its DB backup")
