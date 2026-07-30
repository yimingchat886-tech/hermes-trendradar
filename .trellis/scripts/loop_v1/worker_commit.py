"""Isolated worker validation, exact-tree review, and parent child commit."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path
from typing import Any

from .context import (
    FRESHNESS_FIELDS,
    RESULT_FIELDS,
    ContextError,
    FreshnessError,
    _approved_envelope,
    _compare_freshness,
    _digest_json,
    _exact_object,
    _freshness_token,
    _graph_node,
    _insert_committed_operation,
    _latest_context_row,
    _mapping_list,
    _packet_execution_base,
    _packet_source_base,
    _path_matches,
    _repo_path,
    _required_text,
    _text_list,
)
from .ledger import (
    OperationConflict,
    ParentLedger,
    WriterLease,
    _canonical_json,
    _now,
)


class WorkerCommitError(ContextError):
    """Base error for B4 worktree, review, and child commit failures."""


class DirtOverlapError(WorkerCommitError):
    """Raised when canonical user dirt or main drift overlaps child scope."""


class ParentValidationError(WorkerCommitError):
    """Raised when parent Git or check evidence disagrees with a worker result."""


class ReviewError(WorkerCommitError):
    """Raised when exact-tree model review is missing, stale, or blocking."""


class GitStateError(WorkerCommitError):
    """Raised when local Git state is unsafe or requires later reconciliation."""


PARENT_CHECK_TIMEOUT_SECONDS = 300
_INSTALLED_ROOT_ENV = "TRELLIS_LOOP_V1_INSTALLED_ROOT"
_TRELLIS_CLI_ENV = "TRELLIS_CLI"
_FINDING_FIELDS = frozenset({"finding_id", "summary"})
_DISPOSITION_FIELDS = frozenset({"finding_id", "disposition", "rationale"})
_ADVISORY_DISPOSITIONS = frozenset({"accepted", "deferred", "not_applicable"})
_OBSERVATION_KEYS = (
    "actual_touches",
    "base_head",
    "base_tree_id",
    "branch",
    "diff_identity",
    "head",
    "staged_paths",
    "tree_id",
    "untracked_files",
    "worktree",
)


def scan_repository_dirt(repo_root: Path) -> dict[str, object]:
    """Fingerprint dirty path/status metadata without opening file contents."""
    repo = _repository_path(repo_root, "repo_root")
    tracked = _parse_name_status(
        _git(repo, "diff", "--name-status", "-z", "HEAD", "--")
    )
    untracked = [
        {"kind": "untracked", "path": path, "status": "??"}
        for path in _nul_paths(
            _git(repo, "ls-files", "--others", "--exclude-standard", "-z")
        )
    ]
    entries = sorted(
        [*tracked, *untracked],
        key=lambda item: (item["path"], item["kind"], item["status"]),
    )
    return {
        "digest": f"sha256:{_digest_json(entries)}",
        "entries": entries,
        "paths": sorted({item["path"] for item in entries}),
    }


def create_child_worktree(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    operation_id: str,
    child_id: str,
    packet_id: str,
    worktree: Path,
    branch: str,
    integration_worktree: Path | None = None,
    sibling_worktrees: Sequence[Path] = (),
) -> dict[str, object]:
    """Create one parent-owned child worktree from the packet's exact base."""
    operation_id = _required_text(operation_id, "operation_id")
    child_id = _required_text(child_id, "child_id")
    packet_id = _required_text(packet_id, "packet_id")
    branch = _required_text(branch, "branch")
    canonical = ledger.repo_root.resolve()
    target = Path(worktree).resolve()
    protected = [canonical]
    if integration_worktree is not None:
        protected.append(Path(integration_worktree).resolve())
    protected.extend(Path(path).resolve() for path in sibling_worktrees)
    existing = ledger.get_operation(operation_id)
    _assert_worktree_target(
        canonical,
        target,
        protected,
        allow_registered=existing is not None,
    )
    _git(canonical, "check-ref-format", "--branch", branch)

    connection = ledger._connect(read_only=True)
    try:
        runtime = _runtime_state(connection, ledger.run_id)
        packet = _packet(connection, packet_id)
        if packet["child_id"] != child_id:
            raise FreshnessError("worktree child does not own the packet")
        _compare_freshness("worktree", packet["identity"], runtime["token"])
        child = _graph_node(runtime["context"]["graph"], child_id)
        resume_rows = connection.execute(
            """
            SELECT outcome_json FROM operations
            WHERE kind = 'parent_resume' AND phase = 'authority_committed'
              AND epoch = ?
            ORDER BY created_at, operation_id
            """,
            (lease.epoch,),
        ).fetchall()
    finally:
        connection.close()

    source_base = _packet_source_base(packet)
    execution_base = _packet_execution_base(packet)
    dirt = scan_repository_dirt(canonical)
    authorized_dirt_digest = runtime["envelope"]["dirty_path_fingerprint"]
    resume_outcome = None
    if resume_rows:
        resume_outcome = json.loads(resume_rows[-1]["outcome_json"])
        authorized_dirt_digest = resume_outcome["dirt_digest"]
    if dirt["digest"] != authorized_dirt_digest:
        raise DirtOverlapError(
            "canonical dirt fingerprint differs from start or current resume authority"
        )
    main_state = _main_state(canonical, str(source_base["head"]))
    try:
        _assert_main_isolated(main_state, child["touches"])
    except DirtOverlapError as exc:
        if resume_outcome is None:
            raise
        from .acceptance import (
            FinalMergeError,
            _QUALIFICATION_CONFIG_PATH,
            _QUALIFICATION_RECEIPT_DIR,
            _execution_binding_for_recovery,
            _qualification_config_values,
            _qualification_rotation_proof,
            _verify_qualification_receipt_at_commit,
        )

        try:
            anchor_head = str(resume_outcome["main_head"])
            anchor_overlaps = sorted(
                path
                for path in _git_text(
                    canonical,
                    "diff",
                    "--name-only",
                    f"{source_base['head']}..{anchor_head}",
                    "--",
                ).splitlines()
                if path
                and any(_path_matches(path, pattern) for pattern in child["touches"])
            )
            if any(
                path != _QUALIFICATION_CONFIG_PATH
                and not path.startswith(f"{_QUALIFICATION_RECEIPT_DIR}/")
                for path in anchor_overlaps
            ):
                raise FinalMergeError(
                    "resume qualification anchor has unclassified child overlap"
                )
            anchor_path, anchor_digest = _qualification_config_values(
                _git(
                    canonical,
                    "show",
                    f"{anchor_head}:{_QUALIFICATION_CONFIG_PATH}",
                )
            )
            _verify_qualification_receipt_at_commit(
                canonical,
                binding_commit=anchor_head,
                receipt_path=anchor_path,
                receipt_digest=anchor_digest,
            )
            _qualification_rotation_proof(
                canonical,
                publication_head=anchor_head,
                retained_main_head=str(main_state["head"]),
                execution_binding=_execution_binding_for_recovery(ledger),
                allowed_touches=child["touches"],
            )
        except (FinalMergeError, GitStateError) as proof_error:
            raise DirtOverlapError(
                f"retained publication proof rejected replacement worktree: {proof_error}"
            ) from None
    base_tree_id = _git_text(
        canonical, "rev-parse", f"{execution_base['head']}^{{tree}}"
    )
    if base_tree_id != execution_base["tree_id"]:
        raise FreshnessError("packet base tree no longer matches Git")

    input_value = {
        "base_head": execution_base["head"],
        "base_tree_id": base_tree_id,
        "branch": branch,
        "child_id": child_id,
        "dirt_digest": dirt["digest"],
        "freshness": runtime["token"],
        "main_head": main_state["head"],
        "main_drift_paths": main_state["drift_paths"],
        "packet_id": packet_id,
        "protected_worktrees": sorted(str(path) for path in protected),
        "source_base": source_base,
        "worktree": str(target),
    }
    input_fingerprint = _digest_json(input_value)
    if existing is not None:
        return _replay_worktree(
            ledger,
            existing,
            operation_id=operation_id,
            input_fingerprint=input_fingerprint,
            child_id=child_id,
        )

    ledger.prepare_operation(
        lease,
        operation_id=operation_id,
        kind="child_worktree_create",
        input_fingerprint=input_fingerprint,
        intent=input_value,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    _git(
        canonical,
        "worktree",
        "add",
        "-b",
        branch,
        str(target),
        str(execution_base["head"]),
    )
    _assert_same_repository(canonical, target)
    if _git_text(target, "rev-parse", "HEAD") != execution_base["head"]:
        raise GitStateError("created child worktree does not match the packet base")
    if _git_text(target, "symbolic-ref", "--short", "HEAD") != branch:
        raise GitStateError("created child worktree does not own the requested branch")

    outcome = {
        **input_value,
        "dirt_snapshot": dirt,
        "git_operation_id": f"child-worktree:{child_id}",
        "operation_id": operation_id,
        "phase": "worktree_ready",
        "worker_permissions": {
            "can_commit": False,
            "can_mutate_canonical": False,
            "can_mutate_integration": False,
            "can_mutate_siblings": False,
        },
    }
    ledger.advance_operation(
        lease,
        operation_id=operation_id,
        expected_phase="prepared",
        phase="effect_observed",
        output_fingerprint=base_tree_id,
        outcome=outcome,
    )
    _record_git_authority(
        ledger,
        lease,
        operation_id=operation_id,
        git_operation_id=outcome["git_operation_id"],
        outcome=outcome,
        worktree=str(target),
        branch=branch,
        ref_name=f"refs/heads/{branch}",
        expected_old_ref=str(execution_base["head"]),
        commit_id=None,
        tree_id=base_tree_id,
        phase="worktree_ready",
        child_transition=None,
        event_type="child_worktree_recorded",
    )
    return outcome


def observe_worker_candidate(worktree: Path, *, base_head: str) -> dict[str, object]:
    """Observe a candidate through a temporary index without staging real state."""
    repo = _repository_path(worktree, "worktree")
    base_head = _required_text(base_head, "base_head")
    tracked = _nul_paths(_git(repo, "diff", "--name-only", "-z", base_head, "--"))
    untracked = _nul_paths(
        _git(repo, "ls-files", "--others", "--exclude-standard", "-z")
    )
    actual = sorted(set((*tracked, *untracked)))
    staged = _nul_paths(
        _git(repo, "diff", "--cached", "--name-only", "-z", base_head, "--")
    )
    for path in actual:
        _repo_path(path, "candidate path", allow_pattern=False)

    with tempfile.TemporaryDirectory(prefix="loop-v1-index-") as tmp:
        index_path = Path(tmp) / "index"
        env = {**os.environ, "GIT_INDEX_FILE": str(index_path)}
        _git(repo, "read-tree", base_head, env=env)
        if actual:
            _git(repo, "add", "-A", "--", *actual, env=env)
        tree_id = _git_text(repo, "write-tree", env=env)

    diff_bytes = _git(
        repo,
        "diff-tree",
        "--no-commit-id",
        "--binary",
        "-r",
        base_head,
        tree_id,
        "--",
    )
    return {
        "actual_touches": actual,
        "base_head": base_head,
        "base_tree_id": _git_text(repo, "rev-parse", f"{base_head}^{{tree}}"),
        "branch": _git_text(repo, "symbolic-ref", "--short", "HEAD"),
        "diff_identity": f"sha256:{sha256(diff_bytes).hexdigest()}",
        "head": _git_text(repo, "rev-parse", "HEAD"),
        "staged_paths": sorted(staged),
        "tree_id": tree_id,
        "untracked_files": sorted(untracked),
        "worktree": str(repo),
    }


def validate_child_candidate(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    validation_id: str,
    result: Mapping[str, object],
    worktree: Path,
) -> dict[str, object]:
    """Independently validate accepted worker evidence and exact Git state."""
    validation_id = _required_text(validation_id, "validation_id")
    value = _exact_object(result, RESULT_FIELDS, "child result")
    result_digest = _digest_json(value)
    repo = Path(worktree).resolve()
    inputs = _candidate_inputs(ledger, value, repo)
    observation = observe_worker_candidate(repo, base_head=value["base_head"])
    _assert_candidate(value, inputs, observation)
    source_base = _packet_source_base(inputs["packet"])
    execution_base = _packet_execution_base(inputs["packet"])
    main_state = _main_state(ledger.repo_root, str(source_base["head"]))
    _assert_main_isolated(main_state, inputs["packet"]["scope"]["allowed_touches"])

    operation_id = f"candidate-validation:{validation_id}"
    existing = ledger.get_operation(operation_id)
    if existing is not None:
        return _replay_validation(
            existing,
            validation_id=validation_id,
            result_digest=result_digest,
            observation=observation,
            main_state=main_state,
            freshness=inputs["runtime"]["token"],
            allowed_touches=inputs["packet"]["scope"]["allowed_touches"],
            forbidden_touches=inputs["packet"]["scope"]["forbidden_touches"],
        )

    parent_checks = [
        _run_parent_check(repo, command) for command in inputs["packet"]["tests"]
    ]
    failed = [item["command"] for item in parent_checks if item["status"] != "passed"]
    if failed:
        raise ParentValidationError(
            f"parent check failed without accepting candidate: {', '.join(failed)}"
        )
    after_checks = observe_worker_candidate(repo, base_head=value["base_head"])
    if after_checks != observation:
        raise ParentValidationError("parent checks changed the candidate tree or scope")

    input_value = {
        "freshness": inputs["runtime"]["token"],
        "main_state": main_state,
        "observation": observation,
        "parent_checks": parent_checks,
        "result_digest": result_digest,
        "validation_id": validation_id,
        "worktree_operation_id": inputs["worktree"]["operation_id"],
    }
    outcome = {
        "actual_touches": observation["actual_touches"],
        "allowed_touches": sorted(inputs["packet"]["scope"]["allowed_touches"]),
        "branch": observation["branch"],
        "child_id": value["child_id"],
        "coverage": sorted(value["coverage"]),
        "diff_identity": observation["diff_identity"],
        "execution_base": execution_base,
        "freshness": inputs["runtime"]["token"],
        "forbidden_touches": sorted(inputs["packet"]["scope"]["forbidden_touches"]),
        "main_state": main_state,
        "parent_check_digest": _digest_json(parent_checks),
        "parent_checks": parent_checks,
        "result_digest": result_digest,
        "self_check_digest": _digest_json(value["commands"]),
        "source_base": source_base,
        "status": "candidate_validated",
        "tree_id": observation["tree_id"],
        "untracked_files": observation["untracked_files"],
        "validation_id": validation_id,
        "worktree": str(repo),
        "worktree_operation_id": inputs["worktree"]["operation_id"],
    }

    with ledger._write_transaction(lease) as connection:
        current = _candidate_inputs_connection(connection, ledger, value, repo)
        current_observation = observe_worker_candidate(
            repo, base_head=value["base_head"]
        )
        current_main = _main_state(ledger.repo_root, str(source_base["head"]))
        _assert_candidate(value, current, current_observation)
        _assert_main_isolated(
            current_main, current["packet"]["scope"]["allowed_touches"]
        )
        if current_observation != observation or current_main != main_state:
            raise FreshnessError(
                "candidate or canonical Git state changed during validation"
            )
        if connection.execute(
            "SELECT 1 FROM operations WHERE operation_id = ?", (operation_id,)
        ).fetchone():
            raise OperationConflict("candidate validation ID was concurrently reused")
        if current["child_state"] != "result_validated":
            raise FreshnessError("child is not awaiting parent candidate validation")
        now = _now()
        connection.execute(
            """
            INSERT INTO verifications (
                verification_id, run_id, artifact_digest, actor, verdict,
                findings_json, applicability_json, created_at
            ) VALUES (?, ?, ?, 'parent-validator', 'passed', '[]', ?, ?)
            """,
            (
                validation_id,
                ledger.run_id,
                observation["tree_id"],
                _canonical_json(input_value),
                now,
            ),
        )
        updated = connection.execute(
            """
            UPDATE child_operations
            SET state = 'candidate_validated', updated_at = ?
            WHERE child_id = ? AND state = 'result_validated' AND epoch = ?
              AND context_digest = ?
            """,
            (
                now,
                value["child_id"],
                lease.epoch,
                current["runtime"]["token"]["context_digest"],
            ),
        )
        if updated.rowcount != 1:
            raise FreshnessError("child state changed during candidate validation")
        _insert_committed_operation(
            connection,
            ledger,
            operation_id=operation_id,
            kind="candidate_validated",
            epoch=lease.epoch,
            input_fingerprint=_digest_json(input_value),
            outcome=outcome,
            event_type="candidate_validated",
            created_at=now,
        )
    return outcome


def record_precommit_review(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    review_id: str,
    validation_id: str,
    reviewer_identity: str,
    verdict: str,
    required_findings: Sequence[Mapping[str, object]],
    advisory_findings: Sequence[Mapping[str, object]],
    dispositions: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Record one model review bound to the exact validated candidate tree."""
    review_id = _required_text(review_id, "review_id")
    validation_id = _required_text(validation_id, "validation_id")
    reviewer_identity = _required_text(reviewer_identity, "reviewer_identity")
    verdict = _required_text(verdict, "verdict")
    if not reviewer_identity.startswith("model:"):
        raise ReviewError("pre-commit reviewer must use a model identity")
    if verdict not in {"passed", "failed"}:
        raise ReviewError("review verdict must be passed or failed")
    required = _findings(required_findings, "required_findings")
    advisory = _findings(advisory_findings, "advisory_findings")
    finding_ids = [item["finding_id"] for item in (*required, *advisory)]
    if len(finding_ids) != len(set(finding_ids)):
        raise ReviewError("review finding IDs must be unique")
    disposition_rows = _dispositions(dispositions)
    advisory_ids = {item["finding_id"] for item in advisory}
    if {item["finding_id"] for item in disposition_rows} != advisory_ids:
        raise ReviewError("every advisory finding requires exactly one disposition")
    if verdict == "passed" and required:
        raise ReviewError("a review with required findings cannot pass")
    if verdict == "failed" and not required:
        raise ReviewError("a failed review requires at least one required finding")

    validation = _authority_outcome(
        ledger, f"candidate-validation:{validation_id}", "candidate_validated"
    )
    operation_id = f"precommit-review:{review_id}"
    connection = ledger._connect(read_only=True)
    try:
        runtime = _runtime_state(connection, ledger.run_id)
        child_state = _child_state(connection, validation["child_id"])
    finally:
        connection.close()
    _compare_freshness("review", validation["freshness"], runtime["token"])
    surface = reviewer_identity.split(":", 1)[1]
    if surface not in runtime["envelope"]["approved_agent_surfaces"]:
        raise ReviewError("reviewer model surface is outside the approved envelope")
    observation = observe_worker_candidate(
        Path(validation["worktree"]),
        base_head=str(validation["execution_base"]["head"]),
    )
    _assert_review_artifact(validation, observation)
    current_main = _main_state(
        ledger.repo_root, str(validation["source_base"]["head"])
    )
    _assert_main_isolated(current_main, validation["allowed_touches"])

    input_value = {
        "advisory_findings": advisory,
        "dispositions": disposition_rows,
        "required_findings": required,
        "review_id": review_id,
        "reviewer_identity": reviewer_identity,
        "validation_digest": _digest_json(validation),
        "validation_id": validation_id,
        "verdict": verdict,
    }
    outcome = {
        "actual_touches": validation["actual_touches"],
        "allowed_touches": validation["allowed_touches"],
        "advisory_findings": advisory,
        "child_id": validation["child_id"],
        "coverage": validation["coverage"],
        "diff_identity": validation["diff_identity"],
        "dispositions": disposition_rows,
        "execution_base": validation["execution_base"],
        "freshness": validation["freshness"],
        "forbidden_touches": validation["forbidden_touches"],
        "parent_check_digest": validation["parent_check_digest"],
        "required_findings": required,
        "result_digest": validation["result_digest"],
        "review_id": review_id,
        "reviewer_identity": reviewer_identity,
        "self_check_digest": validation["self_check_digest"],
        "source_base": validation["source_base"],
        "status": "reviewed" if verdict == "passed" else "review_blocked",
        "tree_id": validation["tree_id"],
        "validation_id": validation_id,
        "verdict": verdict,
        "worktree": validation["worktree"],
    }
    existing = ledger.get_operation(operation_id)
    if existing is not None:
        return _replay_committed_operation(
            existing,
            kind="precommit_review_recorded",
            input_fingerprint=_digest_json(input_value),
            outcome=outcome,
        )
    if child_state != "candidate_validated":
        raise ReviewError("child is not awaiting exact-tree pre-commit review")

    with ledger._write_transaction(lease) as connection:
        current = _runtime_state(connection, ledger.run_id)
        _compare_freshness("review", validation["freshness"], current["token"])
        current_observation = observe_worker_candidate(
            Path(validation["worktree"]),
            base_head=str(validation["execution_base"]["head"]),
        )
        _assert_review_artifact(validation, current_observation)
        transaction_main = _main_state(
            ledger.repo_root, str(validation["source_base"]["head"])
        )
        _assert_main_isolated(transaction_main, validation["allowed_touches"])
        if transaction_main != current_main:
            raise FreshnessError("canonical Git state changed during review")
        if connection.execute(
            "SELECT 1 FROM operations WHERE operation_id = ?", (operation_id,)
        ).fetchone():
            raise OperationConflict("review ID was concurrently reused")
        now = _now()
        connection.execute(
            """
            INSERT INTO verifications (
                verification_id, run_id, artifact_digest, actor, verdict,
                findings_json, applicability_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review_id,
                ledger.run_id,
                validation["tree_id"],
                reviewer_identity,
                verdict,
                _canonical_json(
                    {
                        "advisory": advisory,
                        "dispositions": disposition_rows,
                        "required": required,
                    }
                ),
                _canonical_json(
                    {
                        "actual_touches": validation["actual_touches"],
                        "allowed_touches": validation["allowed_touches"],
                        "coverage": validation["coverage"],
                        "diff_identity": validation["diff_identity"],
                        "forbidden_touches": validation["forbidden_touches"],
                        "parent_check_digest": validation["parent_check_digest"],
                        "result_digest": validation["result_digest"],
                        "self_check_digest": validation["self_check_digest"],
                        "validation_id": validation_id,
                    }
                ),
                now,
            ),
        )
        updated = connection.execute(
            """
            UPDATE child_operations SET state = ?, updated_at = ?
            WHERE child_id = ? AND state = 'candidate_validated' AND epoch = ?
            """,
            (outcome["status"], now, validation["child_id"], lease.epoch),
        )
        if updated.rowcount != 1:
            raise FreshnessError("child state changed during pre-commit review")
        _insert_committed_operation(
            connection,
            ledger,
            operation_id=operation_id,
            kind="precommit_review_recorded",
            epoch=lease.epoch,
            input_fingerprint=_digest_json(input_value),
            outcome=outcome,
            event_type="precommit_review_recorded",
            created_at=now,
        )
    return outcome


def commit_reviewed_candidate(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    operation_id: str,
    review_id: str,
    message: str,
    author_name: str,
    author_email: str,
) -> dict[str, object]:
    """Stage exact validated scope and create one parent-owned local commit."""
    operation_id = _required_text(operation_id, "operation_id")
    review_id = _required_text(review_id, "review_id")
    message = _required_text(message, "message")
    author_name = _required_text(author_name, "author_name")
    author_email = _required_text(author_email, "author_email")
    if "@" not in author_email:
        raise WorkerCommitError("author_email must contain '@'")
    review = _authority_outcome(
        ledger, f"precommit-review:{review_id}", "precommit_review_recorded"
    )
    if review["verdict"] != "passed" or review["required_findings"]:
        raise ReviewError("child commit requires a green review without findings")
    validation = _authority_outcome(
        ledger,
        f"candidate-validation:{review['validation_id']}",
        "candidate_validated",
    )
    if review["tree_id"] != validation["tree_id"]:
        raise ReviewError("review and validation tree identities disagree")

    connection = ledger._connect(read_only=True)
    try:
        runtime = _runtime_state(connection, ledger.run_id)
        child_state = _child_state(connection, review["child_id"])
        git_row = connection.execute(
            "SELECT * FROM git_operations WHERE git_operation_id = ?",
            (f"child-commit:{review['child_id']}",),
        ).fetchone()
    finally:
        connection.close()
    _compare_freshness("commit", review["freshness"], runtime["token"])
    execution_base = review["execution_base"]
    source_base = review["source_base"]
    observation = observe_worker_candidate(
        Path(review["worktree"]), base_head=str(execution_base["head"])
    )
    main_state = _main_state(ledger.repo_root, str(source_base["head"]))
    _assert_main_isolated(main_state, review["allowed_touches"])

    input_value = {
        "author_email": author_email,
        "author_name": author_name,
        "base_head": execution_base["head"],
        "branch": observation["branch"],
        "child_id": review["child_id"],
        "freshness": review["freshness"],
        "main_state": main_state,
        "message": message,
        "review_digest": _digest_json(review),
        "review_id": review_id,
        "source_base": source_base,
        "tree_id": review["tree_id"],
        "validated_paths": review["actual_touches"],
        "worktree": review["worktree"],
    }
    input_fingerprint = _digest_json(input_value)
    existing = ledger.get_operation(operation_id)
    if git_row is not None:
        return _replay_child_commit(
            ledger,
            dict(git_row),
            existing,
            operation_id=operation_id,
            input_fingerprint=input_fingerprint,
        )
    if existing is not None:
        if (
            existing["kind"] != "child_commit"
            or existing["input_fingerprint"] != input_fingerprint
        ):
            raise OperationConflict("child commit operation ID was reused")
        raise GitStateError(
            f"child commit is unresolved at phase {existing['phase']}; B5 reconciliation required"
        )
    _assert_review_artifact(validation, observation)
    if child_state != "reviewed":
        raise ReviewError("child is not awaiting its parent-owned commit")
    if not review["actual_touches"]:
        raise ParentValidationError("validated child commit scope is empty")

    ledger.prepare_operation(
        lease,
        operation_id=operation_id,
        kind="child_commit",
        input_fingerprint=input_fingerprint,
        intent=input_value,
    )
    repo = Path(review["worktree"])
    before_stage = observe_worker_candidate(
        repo, base_head=str(execution_base["head"])
    )
    _assert_review_artifact(validation, before_stage)
    before_stage_main = _main_state(ledger.repo_root, str(source_base["head"]))
    _assert_main_isolated(before_stage_main, review["allowed_touches"])
    if before_stage_main != main_state:
        raise FreshnessError("canonical Git state changed before child staging")
    _git(repo, "add", "-A", "--", *review["actual_touches"])
    staged_paths = _nul_paths(
        _git(
            repo,
            "diff",
            "--cached",
            "--name-only",
            "-z",
            str(execution_base["head"]),
            "--",
        )
    )
    if sorted(staged_paths) != sorted(review["actual_touches"]):
        raise GitStateError("parent-staged paths differ from validated child scope")
    staged_tree = _git_text(repo, "write-tree")
    if staged_tree != review["tree_id"]:
        raise ReviewError("staged tree differs from the reviewed candidate tree")

    identity_env = {
        **os.environ,
        "GIT_AUTHOR_EMAIL": author_email,
        "GIT_AUTHOR_NAME": author_name,
        "GIT_COMMITTER_EMAIL": author_email,
        "GIT_COMMITTER_NAME": author_name,
    }
    commit_id = _git_text(
        repo,
        "commit-tree",
        staged_tree,
        "-p",
        str(execution_base["head"]),
        "-F",
        "-",
        env=identity_env,
        input_data=f"{message}\n".encode("utf-8"),
    )
    ref_name = f"refs/heads/{observation['branch']}"
    _git(
        repo,
        "update-ref",
        ref_name,
        commit_id,
        str(execution_base["head"]),
    )
    if _git_text(repo, "rev-parse", "HEAD") != commit_id:
        raise GitStateError("child worktree HEAD did not follow the committed branch")
    if _git_text(repo, "rev-parse", f"{commit_id}^{{tree}}") != staged_tree:
        raise GitStateError("committed tree differs from the reviewed tree")
    if (
        _git_text(repo, "rev-parse", f"{commit_id}^")
        != execution_base["head"]
    ):
        raise GitStateError("child commit parent differs from the validated base")
    if _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all"):
        raise GitStateError("child worktree is not clean after parent commit")

    outcome = {
        **input_value,
        "commit_id": commit_id,
        "git_operation_id": f"child-commit:{review['child_id']}",
        "operation_id": operation_id,
        "phase": "committed",
        "ref_name": ref_name,
        "staged_paths": sorted(staged_paths),
    }
    ledger.advance_operation(
        lease,
        operation_id=operation_id,
        expected_phase="prepared",
        phase="effect_observed",
        output_fingerprint=commit_id,
        outcome=outcome,
    )
    _record_git_authority(
        ledger,
        lease,
        operation_id=operation_id,
        git_operation_id=outcome["git_operation_id"],
        outcome=outcome,
        worktree=review["worktree"],
        branch=observation["branch"],
        ref_name=ref_name,
        expected_old_ref=str(execution_base["head"]),
        commit_id=commit_id,
        tree_id=staged_tree,
        phase="committed",
        child_transition=(review["child_id"], "reviewed", "committed"),
        event_type="child_commit_recorded",
    )
    return outcome


def _candidate_inputs(
    ledger: ParentLedger, result: Mapping[str, object], worktree: Path
) -> dict[str, object]:
    connection = ledger._connect(read_only=True)
    try:
        return _candidate_inputs_connection(connection, ledger, result, worktree)
    finally:
        connection.close()


def _candidate_inputs_connection(
    connection: Any,
    ledger: ParentLedger,
    result: Mapping[str, object],
    worktree: Path,
) -> dict[str, object]:
    runtime = _runtime_state(connection, ledger.run_id)
    supplied = {key: result[key] for key in FRESHNESS_FIELDS}
    _compare_freshness("candidate validation", supplied, runtime["token"])
    packet = _packet(connection, result["packet_id"])
    if packet["child_id"] != result["child_id"]:
        raise FreshnessError("candidate child does not own its packet")
    _compare_freshness("candidate packet", supplied, packet["identity"])
    receipt = connection.execute(
        "SELECT * FROM message_receipts WHERE receipt_id = ?", (result["result_id"],)
    ).fetchone()
    if (
        receipt is None
        or receipt["sender"] != result["child_id"]
        or receipt["payload_digest"] != _digest_json(result)
        or receipt["disposition"] != "accepted"
    ):
        raise ParentValidationError("candidate result lacks an exact accepted receipt")
    git_row = connection.execute(
        "SELECT * FROM git_operations WHERE git_operation_id = ?",
        (f"child-worktree:{result['child_id']}",),
    ).fetchone()
    if git_row is None or git_row["phase"] != "worktree_ready":
        raise ParentValidationError("candidate lacks a parent-owned worktree record")
    worktree_outcome = json.loads(git_row["outcome_json"])
    if Path(worktree_outcome["worktree"]).resolve() != worktree.resolve():
        raise ParentValidationError("candidate came from an unregistered worktree")
    return {
        "child_state": _child_state(connection, result["child_id"]),
        "packet": packet,
        "runtime": runtime,
        "worktree": worktree_outcome,
    }


def _assert_candidate(
    result: Mapping[str, object],
    inputs: Mapping[str, object],
    observation: Mapping[str, object],
) -> None:
    packet = inputs["packet"]
    worktree = inputs["worktree"]
    if observation["head"] != _packet_execution_base(packet)["head"]:
        raise ParentValidationError("worker changed HEAD or created a commit")
    if observation["branch"] != worktree["branch"]:
        raise ParentValidationError(
            "worker candidate branch differs from parent record"
        )
    if observation["staged_paths"]:
        raise ParentValidationError("worker staged changes; only the parent may stage")
    actual = sorted(_text_list(result["actual_touches"], "actual_touches"))
    if actual != observation["actual_touches"]:
        raise ParentValidationError("reported touches differ from observed Git changes")
    if result["result_tree_id"] != observation["tree_id"]:
        raise ParentValidationError("reported result tree differs from observed tree")
    if result["diff_identity"] != observation["diff_identity"]:
        raise ParentValidationError("reported diff identity differs from observed diff")
    if result["base_tree_id"] != observation["base_tree_id"]:
        raise FreshnessError("candidate base tree differs from Git")
    allowed = packet["scope"]["allowed_touches"]
    forbidden = packet["scope"]["forbidden_touches"]
    for path in observation["actual_touches"]:
        if not any(_path_matches(path, pattern) for pattern in allowed):
            raise ParentValidationError(f"observed path is outside scope: {path}")
        if any(_path_matches(path, pattern) for pattern in forbidden):
            raise ParentValidationError(f"observed path is forbidden: {path}")
    expected_coverage = sorted(
        item["requirement_id"] for item in packet["requirements"]
    )
    if sorted(result["coverage"]) != expected_coverage:
        raise ParentValidationError(
            "candidate coverage differs from packet requirements"
        )
    _assert_same_repository(Path(worktree["worktree"]), Path(observation["worktree"]))


def _assert_review_artifact(
    validation: Mapping[str, object], observation: Mapping[str, object]
) -> None:
    expected = {
        "actual_touches": validation["actual_touches"],
        "diff_identity": validation["diff_identity"],
        "tree_id": validation["tree_id"],
        "untracked_files": validation["untracked_files"],
        "worktree": validation["worktree"],
    }
    actual = {key: observation[key] for key in expected}
    if actual != expected or observation["staged_paths"]:
        raise ReviewError("candidate changed after exact-tree validation or review")
    if observation["head"] != observation["base_head"]:
        raise ReviewError("candidate HEAD changed before the parent commit")


def _runtime_state(connection: Any, run_id: str) -> dict[str, object]:
    parent = connection.execute(
        "SELECT * FROM parent_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if parent is None or parent["start_gate_ref"] is None:
        raise FreshnessError("worker operation requires an approved start envelope")
    if parent["status"] != "authorized":
        raise FreshnessError(
            f"parent status blocks worker operation: {parent['status']}"
        )
    envelope, _ = _approved_envelope(connection, parent["start_gate_ref"])
    context_row = _latest_context_row(connection)
    if context_row is None:
        raise FreshnessError("worker operation requires canonical context")
    return {
        "context": json.loads(context_row["context_json"]),
        "envelope": envelope,
        "token": _freshness_token(connection, run_id),
    }


def _packet(connection: Any, packet_id: str) -> dict[str, object]:
    row = connection.execute(
        "SELECT packet_json FROM child_packets WHERE packet_id = ?", (packet_id,)
    ).fetchone()
    if row is None:
        raise FreshnessError("worker operation references an unknown packet")
    return json.loads(row["packet_json"])


def _child_state(connection: Any, child_id: str) -> str:
    row = connection.execute(
        "SELECT state FROM child_operations WHERE child_id = ?", (child_id,)
    ).fetchone()
    if row is None:
        raise FreshnessError("worker operation references an unknown child")
    return row["state"]


def _main_state(repo: Path, base_head: str) -> dict[str, object]:
    canonical = _repository_path(repo, "canonical repo")
    head = _git_text(canonical, "rev-parse", "HEAD")
    dirt = scan_repository_dirt(canonical)
    drift_paths = []
    if head != base_head:
        drift_paths = sorted(
            {
                item["path"]
                for item in _parse_name_status(
                    _git(
                        canonical,
                        "diff",
                        "--name-status",
                        "-z",
                        base_head,
                        head,
                        "--",
                    )
                )
            }
        )
    value = {"dirt": dirt, "drift_paths": drift_paths, "head": head}
    value["digest"] = f"sha256:{_digest_json(value)}"
    return value


def _assert_main_isolated(
    main_state: Mapping[str, object], allowed_touches: Sequence[str]
) -> None:
    overlapping = sorted(
        path
        for path in set((*main_state["dirt"]["paths"], *main_state["drift_paths"]))
        if any(_path_matches(path, pattern) for pattern in allowed_touches)
    )
    if overlapping:
        raise DirtOverlapError(
            f"canonical user dirt or main drift overlaps child scope: {', '.join(overlapping)}"
        )


def _run_parent_check(worktree: Path, command: str) -> dict[str, object]:
    command = _required_text(command, "parent check command")
    environment = {
        key: os.environ[key]
        for key in (
            "HOME",
            "LANG",
            "LC_ALL",
            "TMPDIR",
            _INSTALLED_ROOT_ENV,
            _TRELLIS_CLI_ENV,
        )
        if key in os.environ
    }
    cli = environment.get(_TRELLIS_CLI_ENV)
    environment["PATH"] = (
        str(Path(cli).parent) + os.pathsep + os.defpath if cli else os.defpath
    )
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    try:
        result = subprocess.run(
            command,
            cwd=worktree,
            env=environment,
            shell=True,
            executable="/bin/sh",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=PARENT_CHECK_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ParentValidationError(f"parent check timed out: {command}") from exc
    payload = result.stdout + b"\0" + result.stderr
    return {
        "command": command,
        "exit_code": result.returncode,
        "output_digest": f"sha256:{sha256(payload).hexdigest()}",
        "status": "passed" if result.returncode == 0 else "failed",
    }


def _findings(
    value: Sequence[Mapping[str, object]], field: str
) -> list[dict[str, str]]:
    rows = _mapping_list(list(value), field)
    result = []
    seen = set()
    for row in rows:
        item = _exact_object(row, _FINDING_FIELDS, "review finding")
        item["finding_id"] = _required_text(item["finding_id"], "finding_id")
        item["summary"] = _required_text(item["summary"], "summary")
        if item["finding_id"] in seen:
            raise ReviewError(f"duplicate review finding: {item['finding_id']}")
        seen.add(item["finding_id"])
        result.append(item)
    return result


def _dispositions(
    value: Sequence[Mapping[str, object]],
) -> list[dict[str, str]]:
    rows = _mapping_list(list(value), "dispositions")
    result = []
    seen = set()
    for row in rows:
        item = _exact_object(row, _DISPOSITION_FIELDS, "review disposition")
        for field in _DISPOSITION_FIELDS:
            item[field] = _required_text(item[field], field)
        if item["disposition"] not in _ADVISORY_DISPOSITIONS:
            raise ReviewError("advisory disposition is invalid")
        if item["finding_id"] in seen:
            raise ReviewError(f"duplicate review disposition: {item['finding_id']}")
        seen.add(item["finding_id"])
        result.append(item)
    return result


def _authority_outcome(
    ledger: ParentLedger, operation_id: str, kind: str
) -> dict[str, object]:
    operation = ledger.get_operation(operation_id)
    if operation is None:
        raise FreshnessError(f"required authority operation is missing: {operation_id}")
    if operation["kind"] != kind or operation["phase"] != "authority_committed":
        raise FreshnessError(f"authority operation is not current: {operation_id}")
    return operation["outcome"]


def _replay_committed_operation(
    operation: Mapping[str, object],
    *,
    kind: str,
    input_fingerprint: str,
    outcome: Mapping[str, object],
) -> dict[str, object]:
    if (
        operation["kind"] != kind
        or operation["phase"] != "authority_committed"
        or operation["input_fingerprint"] != input_fingerprint
        or operation["outcome"] != outcome
    ):
        raise OperationConflict("stable operation ID was reused with new input")
    return dict(outcome)


def _replay_validation(
    operation: Mapping[str, object],
    *,
    validation_id: str,
    result_digest: str,
    observation: Mapping[str, object],
    main_state: Mapping[str, object],
    freshness: Mapping[str, object],
    allowed_touches: Sequence[str],
    forbidden_touches: Sequence[str],
) -> dict[str, object]:
    if (
        operation["kind"] != "candidate_validated"
        or operation["phase"] != "authority_committed"
    ):
        raise OperationConflict("candidate validation ID belongs to another operation")
    outcome = operation["outcome"]
    expected = {
        "actual_touches": observation["actual_touches"],
        "allowed_touches": sorted(allowed_touches),
        "branch": observation["branch"],
        "diff_identity": observation["diff_identity"],
        "freshness": freshness,
        "forbidden_touches": sorted(forbidden_touches),
        "main_state": main_state,
        "result_digest": result_digest,
        "tree_id": observation["tree_id"],
        "untracked_files": observation["untracked_files"],
        "validation_id": validation_id,
        "worktree": observation["worktree"],
    }
    if any(outcome.get(key) != value for key, value in expected.items()):
        raise OperationConflict(
            "candidate validation replay conflicts with current state"
        )
    return outcome


def _replay_worktree(
    ledger: ParentLedger,
    operation: Mapping[str, object],
    *,
    operation_id: str,
    input_fingerprint: str,
    child_id: str,
) -> dict[str, object]:
    if (
        operation["kind"] != "child_worktree_create"
        or operation["input_fingerprint"] != input_fingerprint
    ):
        raise OperationConflict("worktree operation ID was reused with new input")
    if operation["phase"] != "authority_committed":
        raise GitStateError(
            f"worktree creation is unresolved at phase {operation['phase']}; B5 reconciliation required"
        )
    connection = ledger._connect(read_only=True)
    try:
        row = connection.execute(
            "SELECT * FROM git_operations WHERE git_operation_id = ?",
            (f"child-worktree:{child_id}",),
        ).fetchone()
    finally:
        connection.close()
    if row is None or row["operation_id"] != operation_id:
        raise GitStateError("worktree operation lacks matching Git authority")
    outcome = json.loads(row["outcome_json"])
    _assert_same_repository(ledger.repo_root, Path(outcome["worktree"]))
    if (
        _git_text(Path(outcome["worktree"]), "rev-parse", "HEAD")
        != outcome["base_head"]
        or _git_text(Path(outcome["worktree"]), "symbolic-ref", "--short", "HEAD")
        != outcome["branch"]
    ):
        raise GitStateError("replayed worktree no longer matches durable authority")
    return outcome


def _replay_child_commit(
    ledger: ParentLedger,
    git_row: Mapping[str, object],
    operation: Mapping[str, object] | None,
    *,
    operation_id: str,
    input_fingerprint: str,
) -> dict[str, object]:
    if operation is None:
        raise GitStateError("child commit Git record lacks its authority operation")
    if (
        git_row["operation_id"] != operation_id
        or operation["kind"] != "child_commit"
        or operation["phase"] != "authority_committed"
        or operation["input_fingerprint"] != input_fingerprint
    ):
        raise OperationConflict("child commit replay conflicts with durable authority")
    outcome = json.loads(git_row["outcome_json"])
    repo = Path(outcome["worktree"])
    _assert_same_repository(ledger.repo_root, repo)
    if (
        _git_text(repo, "rev-parse", "HEAD") != outcome["commit_id"]
        or _git_text(repo, "rev-parse", f"{outcome['commit_id']}^{{tree}}")
        != outcome["tree_id"]
    ):
        raise GitStateError("replayed child commit no longer matches Git")
    return outcome


def _record_git_authority(
    ledger: ParentLedger,
    lease: WriterLease,
    *,
    operation_id: str,
    git_operation_id: str,
    outcome: Mapping[str, object],
    worktree: str,
    branch: str,
    ref_name: str,
    expected_old_ref: str,
    commit_id: str | None,
    tree_id: str,
    phase: str,
    child_transition: tuple[str, str, str] | None,
    event_type: str,
) -> None:
    with ledger._write_transaction(lease) as connection:
        operation = connection.execute(
            "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
        ).fetchone()
        if (
            operation is None
            or operation["phase"] != "effect_observed"
            or operation["epoch"] != lease.epoch
            or operation["outcome_json"] != _canonical_json(outcome)
        ):
            raise OperationConflict("Git operation effect is not ready for authority")
        now = _now()
        updated = connection.execute(
            """
            UPDATE operations SET phase = 'authority_committed', updated_at = ?
            WHERE operation_id = ? AND phase = 'effect_observed' AND epoch = ?
            """,
            (now, operation_id, lease.epoch),
        )
        if updated.rowcount != 1:
            raise OperationConflict(
                "Git operation phase changed before authority commit"
            )
        connection.execute(
            """
            INSERT INTO git_operations (
                git_operation_id, run_id, operation_id, worktree, branch,
                ref_name, expected_old_ref, commit_id, tree_id, phase,
                outcome_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                git_operation_id,
                ledger.run_id,
                operation_id,
                worktree,
                branch,
                ref_name,
                expected_old_ref,
                commit_id,
                tree_id,
                phase,
                _canonical_json(outcome),
                now,
            ),
        )
        if child_transition is not None:
            child_id, expected_state, next_state = child_transition
            child = connection.execute(
                """
                UPDATE child_operations SET state = ?, updated_at = ?
                WHERE child_id = ? AND state = ? AND epoch = ?
                """,
                (next_state, now, child_id, expected_state, lease.epoch),
            )
            if child.rowcount != 1:
                raise FreshnessError("child state changed before Git authority commit")
        ledger._insert_event(
            connection,
            operation_id=operation_id,
            event_type=event_type,
            phase="authority_committed",
            epoch=lease.epoch,
            payload=dict(outcome),
            created_at=now,
        )


def _assert_worktree_target(
    canonical: Path,
    target: Path,
    protected: Sequence[Path],
    *,
    allow_registered: bool,
) -> None:
    if target == canonical or canonical in target.parents:
        raise GitStateError("child worktree must be outside the canonical worktree")
    for path in protected:
        if target == path or target in path.parents or path in target.parents:
            raise GitStateError("child worktree overlaps a protected worktree")
    existing = {
        Path(line.removeprefix("worktree ")).resolve()
        for line in _git_text(canonical, "worktree", "list", "--porcelain").splitlines()
        if line.startswith("worktree ")
    }
    if target in existing and not allow_registered:
        raise GitStateError("child worktree path is already registered")


def _assert_same_repository(left: Path, right: Path) -> None:
    if _common_git_dir(left) != _common_git_dir(right):
        raise GitStateError("worktree does not belong to the canonical repository")


def _common_git_dir(repo: Path) -> Path:
    return Path(
        _git_text(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    ).resolve()


def _repository_path(value: Path, field: str) -> Path:
    path = Path(value).resolve()
    if not path.is_dir():
        raise GitStateError(f"{field} is not a directory")
    if _git_text(path, "rev-parse", "--is-inside-work-tree") != "true":
        raise GitStateError(f"{field} is not a Git worktree")
    return path


def _parse_name_status(payload: bytes) -> list[dict[str, str]]:
    tokens = [item.decode("utf-8") for item in payload.split(b"\0") if item]
    result = []
    index = 0
    while index < len(tokens):
        status = tokens[index]
        index += 1
        if index >= len(tokens):
            raise GitStateError("Git name-status output is incomplete")
        path = _repo_path(tokens[index], "Git path", allow_pattern=False)
        index += 1
        if status.startswith(("R", "C")):
            if index >= len(tokens):
                raise GitStateError("Git rename/copy output is incomplete")
            next_path = _repo_path(tokens[index], "Git path", allow_pattern=False)
            index += 1
            result.extend(
                (
                    {"kind": "tracked", "path": path, "status": f"{status}:old"},
                    {
                        "kind": "tracked",
                        "path": next_path,
                        "status": f"{status}:new",
                    },
                )
            )
        else:
            result.append({"kind": "tracked", "path": path, "status": status})
    return result


def _nul_paths(payload: bytes) -> list[str]:
    paths = [item.decode("utf-8") for item in payload.split(b"\0") if item]
    for path in paths:
        _repo_path(path, "Git path", allow_pattern=False)
    return sorted(paths)


def _git_text(
    repo: Path,
    *args: str,
    env: Mapping[str, str] | None = None,
    input_data: bytes | None = None,
) -> str:
    return _git(repo, *args, env=env, input_data=input_data).decode("utf-8").strip()


def _git(
    repo: Path,
    *args: str,
    env: Mapping[str, str] | None = None,
    input_data: bytes | None = None,
) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(Path(repo)), *args],
        env=dict(env) if env is not None else None,
        input=input_data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        command = args[0] if args else "command"
        raise GitStateError(f"git {command} failed with exit code {result.returncode}")
    return result.stdout
