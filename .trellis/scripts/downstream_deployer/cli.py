"""Source-qualified downstream install/update CLI.

Install or update is separate from target-local activation and later
installed_runtime use, which does not depend on the canonical source checkout.
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path

from common.io import write_bytes_atomic
from loop_v1.qualification import digest_json, load_overlay_manifest

from . import planning
from .authority import AuthorityStore, TARGET_ORDER
from .planning import plan_target
from .transaction import (
    apply_transaction,
    recover_transaction,
    verify_transaction,
)


COMMANDS = (
    "apply",
    "cohort-activate",
    "cycle",
    "grant-capture",
    "grant-renew",
    "grant-request",
    "grant-revoke",
    "grant-supersede",
    "inbox",
    "one-off-approve",
    "one-off-request",
    "pause",
    "plan",
    "recover",
    "resume",
    "single-target",
    "status",
    "verify",
)
_STORE_OPTIONS = {"notifier", "notifier_timeout"}
_INDEPENDENT = {
    "grant": True,
    "lock": True,
    "recovery": True,
    "root": True,
    "state": True,
}


class CliInputError(ValueError):
    """Raised when CLI input is missing, malformed, or ambiguous."""


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliInputError(message)


def _expect(
    value: Mapping[str, object],
    required: set[str],
    optional: set[str] | None = None,
    *,
    label: str = "input",
) -> None:
    allowed = required | (optional or set())
    if set(value) != required | (set(value) & (optional or set())):
        missing = sorted(required - set(value))
        unknown = sorted(set(value) - allowed)
        raise CliInputError(
            f"{label} keys invalid; missing={missing}, unknown={unknown}"
        )


def _real_file(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise CliInputError(f"{label} must be a file path")
    path = Path(value).expanduser().absolute()
    for candidate in (path, *path.parents):
        if candidate.exists() and candidate.is_symlink():
            raise CliInputError(f"{label} cannot contain a symlink")
    if not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
        raise CliInputError(f"{label} must be a file no larger than 4 MiB")
    return path


def _load_object(value: object, label: str) -> dict[str, object]:
    path = _real_file(value, label)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CliInputError(f"{label} must contain UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise CliInputError(f"{label} must contain one JSON object")
    return payload


def _commands(value: object) -> tuple[tuple[str, ...], ...]:
    if not isinstance(value, list) or not value:
        raise CliInputError("verification_commands must be a non-empty array")
    commands: list[tuple[str, ...]] = []
    for command in value:
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(part, str) or not part for part in command)
        ):
            raise CliInputError(
                "each verification command must be a non-empty string array"
            )
        commands.append(tuple(command))
    return tuple(commands)


def _write_new_json(
    value: object,
    payload: Mapping[str, object],
    protected: Sequence[Path],
) -> None:
    if not isinstance(value, str) or not value:
        raise CliInputError("plan_output must be a file path")
    path = Path(value).expanduser().absolute()
    if any(
        candidate.exists() and candidate.is_symlink()
        for candidate in (path, *path.parents)
    ):
        raise CliInputError("plan_output cannot contain a symlink")
    parent = path.parent.resolve()
    if (
        path.exists()
        or path.is_symlink()
        or any(
            parent == root or parent.is_relative_to(root) or root.is_relative_to(parent)
            for root in protected
        )
    ):
        raise CliInputError("plan_output must be new and disjoint")
    parent.mkdir(parents=True, exist_ok=True)
    write_bytes_atomic(
        path,
        (
            json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8"),
    )


def _store(
    payload: Mapping[str, object],
    *,
    initialize: bool,
    protected: Sequence[Path] = (),
) -> AuthorityStore:
    path = payload.get("state_path")
    if not isinstance(path, str) or not path:
        raise CliInputError("state_path must be a file path")
    state_path = Path(path).expanduser().absolute()
    if any(
        candidate.exists() and candidate.is_symlink()
        for candidate in (state_path, *state_path.parents)
    ):
        raise CliInputError("state_path cannot contain a symlink")
    resolved_state = state_path.resolve()
    if any(
        resolved_state == root.resolve()
        or resolved_state.is_relative_to(root.resolve())
        or root.resolve().is_relative_to(resolved_state)
        for root in protected
    ):
        raise CliInputError("state_path must be disjoint from protected roots")
    notifier = payload.get("notifier")
    if notifier is not None and (
        not isinstance(notifier, list)
        or not notifier
        or any(not isinstance(part, str) or not part for part in notifier)
    ):
        raise CliInputError("notifier must be a non-empty string array")
    timeout = payload.get("notifier_timeout", 10)
    if type(timeout) is not int:
        raise CliInputError("notifier_timeout must be an integer")
    kwargs = {
        "notifier": notifier,
        "notifier_timeout": timeout,
    }
    if initialize:
        return AuthorityStore.initialize(state_path, **kwargs)
    return AuthorityStore(state_path, **kwargs)


_STATE_SPECS: dict[
    str,
    tuple[str, set[str], bool],
] = {
    "cohort-activate": (
        "activate_cohort",
        {
            "common_not_before",
            "event_id",
            "grant_ids",
            "response_identity",
        },
        True,
    ),
    "grant-capture": (
        "capture_grant",
        {"event_id", "grant_id", "request_digest", "response_identity"},
        True,
    ),
    "grant-renew": (
        "renew_grant",
        {
            "event_id",
            "new_grant_id",
            "not_before",
            "old_grant_id",
            "response_identity",
        },
        True,
    ),
    "grant-request": (
        "request_grant",
        {"binding", "event_id", "grant_id", "not_before"},
        False,
    ),
    "grant-supersede": (
        "renew_grant",
        {
            "event_id",
            "new_grant_id",
            "not_before",
            "old_grant_id",
            "response_identity",
        },
        True,
    ),
    "one-off-approve": (
        "approve_one_off",
        {
            "authority_id",
            "event_id",
            "request_digest",
            "response_identity",
        },
        True,
    ),
    "one-off-request": (
        "request_one_off",
        {
            "authority_id",
            "binding",
            "effect",
            "event_id",
            "expires_at",
            "not_before",
        },
        False,
    ),
    "pause": (
        "pause_target",
        {"event_id", "grant_id", "lineage_anchor", "reason", "target_id"},
        False,
    ),
    "resume": (
        "resume_target",
        {
            "event_id",
            "grant_id",
            "lineage_anchor",
            "response_identity",
            "target_id",
        },
        True,
    ),
}


def _state_command(
    command: str,
    payload: Mapping[str, object],
) -> tuple[dict[str, object], int]:
    if command == "grant-revoke":
        required = {
            "event_id",
            "grant_id",
            "now",
            "reason",
            "response_identity",
            "state_path",
        }
        _expect(payload, required, _STORE_OPTIONS)
        store = _store(payload, initialize=True)
        result = store.retire_grant(
            event_id=str(payload["event_id"]),
            grant_id=str(payload["grant_id"]),
            disposition="revoked",
            reason=str(payload["reason"]),
            response_identity=str(payload["response_identity"]),
            direct_user_action=True,
            now=payload["now"],
        )
        return result, 0
    method_name, fields, direct = _STATE_SPECS[command]
    required = fields | {"now", "state_path"}
    _expect(payload, required, _STORE_OPTIONS)
    store = _store(payload, initialize=True)
    kwargs = {field: payload[field] for field in fields}
    kwargs["now"] = payload["now"]
    if direct:
        kwargs["direct_user_action"] = True
    result = getattr(store, method_name)(**kwargs)
    return result, 0


def _plan(payload: Mapping[str, object]) -> tuple[dict[str, object], int]:
    required = {
        "candidate_output",
        "manifest_path",
        "plan_output",
        "scratch_root",
        "source_root",
        "target_id",
        "target_root",
        "verification_commands",
    }
    _expect(payload, required)
    source = Path(str(payload["source_root"])).resolve()
    target = Path(str(payload["target_root"])).resolve()
    candidate = Path(str(payload["candidate_output"])).absolute()
    try:
        result = plan_target(
            source,
            target,
            str(payload["target_id"]),
            manifest_path=Path(str(payload["manifest_path"])),
            scratch_root=Path(str(payload["scratch_root"])),
            candidate_output=candidate,
            verification_commands=_commands(payload["verification_commands"]),
        )
        _write_new_json(
            payload["plan_output"],
            result,
            (source, target),
        )
    except BaseException:
        if candidate.exists() and candidate.is_dir() and not candidate.is_symlink():
            shutil.rmtree(candidate, ignore_errors=True)
        raise
    return result, 0


def _authority_spec(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise CliInputError(f"{label} must be an object")
    result = dict(value)
    _expect(
        result,
        {"authority_id", "binding", "event_id", "kind"},
        label=label,
    )
    if result["kind"] not in {"grant", "one_off"}:
        raise CliInputError(f"{label}.kind must be grant or one_off")
    if not isinstance(result["binding"], Mapping):
        raise CliInputError(f"{label}.binding must be an object")
    return result


def _consume(
    store: AuthorityStore,
    spec: Mapping[str, object],
    effect: str,
    now: object,
) -> dict[str, object]:
    kwargs = {
        "event_id": str(spec["event_id"]),
        "effect": effect,
        "binding": spec["binding"],
        "now": now,
    }
    if spec["kind"] == "one_off":
        return store.consume_one_off(
            authority_id=str(spec["authority_id"]),
            **kwargs,
        )
    return store.consume_grant(
        grant_id=str(spec["authority_id"]),
        **kwargs,
    )


def _record_result_receipt(
    store: AuthorityStore,
    *,
    event_id: str,
    result: Mapping[str, object],
    plan: Mapping[str, object],
    authority: Mapping[str, object],
    now: object,
) -> dict[str, object]:
    if result.get("status") == "succeeded":
        receipt = result["final_receipt"]
        assert isinstance(receipt, Mapping)
        receipt_id = str(receipt["receipt_id"])
        receipt_kind = "final"
        status = "verified"
        commit = result.get("commit")
        commit_id = str(commit["commit_id"]) if isinstance(commit, Mapping) else None
        payload_digest = digest_json(receipt)
    else:
        receipt_id = "sha256:" + digest_json(result)
        receipt_kind = "recovery"
        status = str(result["status"])
        commit_id = None
        payload_digest = digest_json(result)
    return store.record_receipt(
        event_id=event_id,
        receipt_id=receipt_id,
        receipt_kind=receipt_kind,
        status=status,
        target_id=str(result["target_id"]),
        transaction_id=str(result["transaction_id"]),
        plan_digest=str(plan["plan_digest"]),
        source_receipt_id=str(plan["source"]["qualification_receipt_id"]),
        payload_digest=payload_digest,
        authority_id=str(authority["authority_id"]),
        authority_consumption_id=str(authority["event_id"]),
        commit_id=commit_id,
        now=now,
    )


def _apply(payload: Mapping[str, object]) -> tuple[dict[str, object], int]:
    required = {
        "authority",
        "candidate_root",
        "now",
        "plan_path",
        "receipt_event_id",
        "recovery_root",
        "source_root",
        "state_path",
        "target_root",
        "verification_commands",
    }
    _expect(payload, required, _STORE_OPTIONS | {"commit_authority"})
    plan = _load_object(payload["plan_path"], "plan_path")
    source = Path(str(payload["source_root"]))
    target = Path(str(payload["target_root"]))
    candidate = Path(str(payload["candidate_root"]))
    recovery = Path(str(payload["recovery_root"]))
    store = _store(
        payload,
        initialize=True,
        protected=(source, target, candidate, recovery),
    )
    authority = _authority_spec(payload["authority"], "authority")
    _consume(store, authority, "apply", payload["now"])
    commit_envelope = None
    if payload.get("commit_authority") is not None:
        commit_spec = _authority_spec(
            payload["commit_authority"],
            "commit_authority",
        )
        commit_envelope = _consume(
            store,
            commit_spec,
            "commit",
            payload["now"],
        )
    result = apply_transaction(
        source,
        target,
        candidate,
        plan,
        verification_commands=_commands(payload["verification_commands"]),
        recovery_root=recovery,
        local_commit_authority=commit_envelope,
    )
    authority_receipt = _record_result_receipt(
        store,
        event_id=str(payload["receipt_event_id"]),
        result=result,
        plan=plan,
        authority=authority,
        now=payload["now"],
    )
    value = {
        "authority_receipt": authority_receipt,
        "transaction": result,
    }
    return value, 0 if result["status"] == "succeeded" else 1


def _verify(payload: Mapping[str, object]) -> tuple[dict[str, object], int]:
    required = {
        "plan_path",
        "receipt_path",
        "source_root",
        "target_root",
        "verification_commands",
    }
    _expect(payload, required)
    result = verify_transaction(
        Path(str(payload["source_root"])),
        Path(str(payload["target_root"])),
        _load_object(payload["plan_path"], "plan_path"),
        _load_object(payload["receipt_path"], "receipt_path"),
        verification_commands=_commands(payload["verification_commands"]),
    )
    return result, 0


def _recover(payload: Mapping[str, object]) -> tuple[dict[str, object], int]:
    required = {
        "authority",
        "now",
        "receipt_event_id",
        "recovery_root",
        "state_path",
        "target_root",
        "transaction_id",
    }
    _expect(payload, required, _STORE_OPTIONS)
    target = Path(str(payload["target_root"]))
    recovery = Path(str(payload["recovery_root"]))
    store = _store(
        payload,
        initialize=True,
        protected=(target, recovery),
    )
    authority = _authority_spec(payload["authority"], "authority")
    if authority["kind"] != "one_off":
        raise CliInputError("recover requires one_off authority")
    envelope = _consume(store, authority, "recover", payload["now"])
    result = recover_transaction(
        target,
        recovery,
        str(payload["transaction_id"]),
        envelope,
        authority["binding"],
    )
    receipt = store.record_receipt(
        event_id=str(payload["receipt_event_id"]),
        receipt_id=str(result["receipt_id"]),
        receipt_kind="recovery",
        status="recovered",
        target_id=str(result["target_id"]),
        transaction_id=str(result["transaction_id"]),
        plan_digest=str(authority["binding"]["plan_digest"]),
        source_receipt_id=str(authority["binding"]["source_receipt_id"]),
        payload_digest=digest_json(result),
        authority_id=str(authority["authority_id"]),
        authority_consumption_id=str(authority["event_id"]),
        now=payload["now"],
    )
    return {"authority_receipt": receipt, "recovery": result}, 0


def _single_target(payload: Mapping[str, object]) -> tuple[dict[str, object], int]:
    """Delegate one exact target operation to the established core handler."""
    _expect(payload, {"operation", "request"}, label="single-target input")
    operation = payload["operation"]
    request = payload["request"]
    if not isinstance(operation, str) or operation not in {
        "plan",
        "apply",
        "verify",
        "recover",
    }:
        raise CliInputError("single-target operation must be plan, apply, verify, or recover")
    if not isinstance(request, Mapping):
        raise CliInputError("single-target request must be an object")
    handlers = {
        "plan": _plan,
        "apply": _apply,
        "verify": _verify,
        "recover": _recover,
    }
    result, exit_code = handlers[operation](dict(request))
    return {"operation": operation, "result": result}, exit_code


def _source_identity(source: Path, manifest: Path) -> dict[str, str]:
    manifest_input = manifest.expanduser().absolute()
    if not manifest_input.is_relative_to(source):
        raise planning.PlanError("MANIFEST_INVALID")
    planning._assert_no_symlink(
        source,
        manifest_input.relative_to(source),
        "manifest path",
    )
    manifest_file = manifest_input.resolve()
    if not manifest_file.is_file():
        raise planning.PlanError("MANIFEST_INVALID")
    identity = planning._source_identity(
        source,
        manifest_file,
        load_overlay_manifest(manifest_file),
    )
    return {
        "commit": str(identity["commit"]),
        "policy_digest": str(identity["manifest_digest"]),
        "qualification_receipt_id": str(identity["qualification_receipt_id"]),
        "status_digest": digest_json(
            {
                "branch": identity["branch"],
                "owner_payloads": identity["owner_payloads"],
            }
        ),
        "task_fingerprint": digest_json({"source_task_gate": "ignored"}),
        "tree": str(identity["tree"]),
    }


def _event_id(cycle_id: str, *parts: object) -> str:
    return "cycle:" + digest_json([cycle_id, *parts])


def _reason_code(error: BaseException) -> str:
    value = str(error).split(":", 1)[0]
    return value if value and value.replace("_", "").isalnum() else type(error).__name__


def _cycle_skip(
    store: AuthorityStore,
    *,
    cycle_id: str,
    source_digest: str,
    target_id: str,
    result: str,
    reason: str,
    now: object,
) -> dict[str, object]:
    return store.record_cycle_skip(
        event_id=_event_id(cycle_id, target_id, result),
        cycle_id=cycle_id,
        target_id=target_id,
        result=result,
        reason_code=reason,
        evidence_digest=digest_json({"reason_code": reason}),
        source_identity_digest=source_digest,
        independence=_INDEPENDENT,
        now=now,
    )


def _cycle(payload: Mapping[str, object]) -> tuple[dict[str, object], int]:
    required = {
        "cycle_id",
        "manifest_path",
        "now",
        "scratch_root",
        "source_root",
        "state_path",
        "targets",
    }
    _expect(payload, required, _STORE_OPTIONS)
    raw_targets = payload["targets"]
    if not isinstance(raw_targets, list) or len(raw_targets) != len(TARGET_ORDER):
        raise CliInputError("cycle targets must contain exactly five entries")
    targets: list[dict[str, object]] = []
    target_fields = {
        "binding",
        "grant_id",
        "recovery_root",
        "target_id",
        "target_root",
        "verification_commands",
    }
    for raw in raw_targets:
        if not isinstance(raw, Mapping):
            raise CliInputError("cycle target must be an object")
        item = dict(raw)
        _expect(item, target_fields, label="cycle target")
        if not isinstance(item["binding"], Mapping):
            raise CliInputError("cycle target binding must be an object")
        targets.append(item)
    if [item["target_id"] for item in targets] != list(TARGET_ORDER):
        raise CliInputError("cycle targets must use the frozen order")

    source = Path(str(payload["source_root"])).resolve()
    manifest = Path(str(payload["manifest_path"])).absolute()
    scratch = Path(str(payload["scratch_root"])).resolve()
    cycle_id = str(payload["cycle_id"])
    now = payload["now"]
    protected = [source, scratch]
    for item in targets:
        protected.extend(
            (
                Path(str(item["target_root"])),
                Path(str(item["recovery_root"])),
            )
        )
    store = _store(payload, initialize=True, protected=protected)
    try:
        frozen = _source_identity(source, manifest)
    except Exception as exc:
        reason = _reason_code(exc)
        cycle = store.block_cycle_source(
            event_id=_event_id(cycle_id, "source-blocked"),
            cycle_id=cycle_id,
            observed_source_digest=digest_json({"reason_code": reason}),
            reason=reason,
            now=now,
        )
        return cycle, 1
    source_digest = digest_json(frozen)
    cycle = store.start_cycle(
        event_id=_event_id(cycle_id, "start"),
        cycle_id=cycle_id,
        source=frozen,
        now=now,
    )
    if cycle["status"] != "running":
        return cycle, 0 if cycle["status"] == "completed" else 1

    for index, item in enumerate(targets):
        cycle = store.snapshot()["cycles"][cycle_id]
        slot = cycle["slots"][index]
        if slot["stable"]:
            continue
        try:
            observed_source = _source_identity(source, manifest)
        except Exception as exc:
            store.block_cycle_source(
                event_id=_event_id(cycle_id, "source-blocked"),
                cycle_id=cycle_id,
                observed_source_digest=digest_json({"reason_code": _reason_code(exc)}),
                reason=_reason_code(exc),
                now=now,
            )
            break
        if observed_source != frozen:
            store.block_cycle_source(
                event_id=_event_id(cycle_id, "source-drift"),
                cycle_id=cycle_id,
                observed_source_digest=digest_json(observed_source),
                reason="SOURCE_IDENTITY_DRIFT",
                now=now,
            )
            break

        target_id = str(item["target_id"])
        grant_id = str(item["grant_id"])
        state = store.snapshot()
        grant = state["authorities"].get(grant_id)
        target_state = state["targets"].get(target_id)
        if not isinstance(grant, Mapping) or grant.get("state") != "active":
            _cycle_skip(
                store,
                cycle_id=cycle_id,
                source_digest=source_digest,
                target_id=target_id,
                result="expired",
                reason="GRANT_INACTIVE",
                now=now,
            )
            continue
        if isinstance(target_state, Mapping) and target_state.get("paused") is True:
            _cycle_skip(
                store,
                cycle_id=cycle_id,
                source_digest=source_digest,
                target_id=target_id,
                result="paused",
                reason="TARGET_PAUSED",
                now=now,
            )
            continue
        if dict(item["binding"]) != grant.get("binding"):
            store.retire_grant(
                event_id=_event_id(cycle_id, target_id, "invalidate"),
                grant_id=grant_id,
                disposition="invalidated",
                reason="POLICY_DRIFT",
                now=now,
            )
            _cycle_skip(
                store,
                cycle_id=cycle_id,
                source_digest=source_digest,
                target_id=target_id,
                result="ineligible",
                reason="POLICY_DRIFT",
                now=now,
            )
            continue

        target = Path(str(item["target_root"])).resolve()
        checks = _commands(item["verification_commands"])
        plan: dict[str, object] | None = None
        target_before: dict[str, object] | None = None
        transaction_result: dict[str, object] | None = None
        active_attempt = 1
        try:
            target_before = planning._repo_identity(target, "TARGET")
            plan_binding = {
                **dict(item["binding"]),
                "base_head": target_before["head"],
                "source_receipt_id": frozen["qualification_receipt_id"],
            }
            store.consume_grant(
                event_id=_event_id(cycle_id, target_id, "plan"),
                grant_id=grant_id,
                effect="plan",
                binding=plan_binding,
                now=now,
            )
            with tempfile.TemporaryDirectory(
                prefix=f"trellis-cycle-{index}-",
                dir=scratch,
            ) as temporary:
                candidate = Path(temporary) / "candidate"
                plan = plan_target(
                    source,
                    target,
                    target_id,
                    manifest_path=manifest,
                    scratch_root=scratch,
                    candidate_output=candidate,
                    verification_commands=checks,
                )
                if not plan["candidate"]["predicted_mutations"]:
                    _cycle_skip(
                        store,
                        cycle_id=cycle_id,
                        source_digest=source_digest,
                        target_id=target_id,
                        result="no_change",
                        reason="NO_CHANGE",
                        now=now,
                    )
                    continue
                action_binding = {
                    **dict(item["binding"]),
                    "base_head": plan["target"]["head"],
                    "plan_digest": plan["plan_digest"],
                    "source_receipt_id": plan["source"]["qualification_receipt_id"],
                }
                for attempt in (1, 2):
                    active_attempt = attempt
                    apply_event = _event_id(
                        cycle_id,
                        target_id,
                        "apply",
                        attempt,
                    )
                    store.consume_grant(
                        event_id=apply_event,
                        grant_id=grant_id,
                        effect="apply",
                        binding=action_binding,
                        now=now,
                    )
                    commit_envelope = store.consume_grant(
                        event_id=_event_id(
                            cycle_id,
                            target_id,
                            "commit",
                            attempt,
                        ),
                        grant_id=grant_id,
                        effect="commit",
                        binding=action_binding,
                        now=now,
                    )
                    try:
                        transaction_result = apply_transaction(
                            source,
                            target,
                            candidate,
                            plan,
                            verification_commands=checks,
                            recovery_root=Path(str(item["recovery_root"])),
                            local_commit_authority=commit_envelope,
                        )
                    except Exception as exc:
                        zero_write: str | None = None
                        try:
                            if (
                                planning._repo_identity(target, "TARGET")
                                == target_before
                            ):
                                zero_write = digest_json(target_before)
                        except Exception:
                            pass
                        cycle = store.record_attempt(
                            event_id=_event_id(
                                cycle_id,
                                target_id,
                                "attempt",
                                attempt,
                            ),
                            attempt_id=_event_id(
                                cycle_id,
                                target_id,
                                "attempt-id",
                                attempt,
                            ),
                            cycle_id=cycle_id,
                            target_id=target_id,
                            result="preflight_failed",
                            reason_code=_reason_code(exc),
                            plan_digest=str(plan["plan_digest"]),
                            source_receipt_id=str(
                                plan["source"]["qualification_receipt_id"]
                            ),
                            source_identity_digest=source_digest,
                            target_identity_digest=digest_json(plan["target"]),
                            evidence_digest=digest_json(
                                {"reason_code": _reason_code(exc)}
                            ),
                            independence=_INDEPENDENT,
                            zero_write_proof=zero_write,
                            now=now,
                        )
                        current_slot = cycle["slots"][index]
                        if current_slot["result"] == "retry_pending":
                            continue
                        break
                    receipt = _record_result_receipt(
                        store,
                        event_id=_event_id(
                            cycle_id,
                            target_id,
                            "receipt",
                            attempt,
                        ),
                        result=transaction_result,
                        plan=plan,
                        authority={
                            "authority_id": grant_id,
                            "event_id": apply_event,
                        },
                        now=now,
                    )
                    status = str(transaction_result["status"])
                    store.record_attempt(
                        event_id=_event_id(
                            cycle_id,
                            target_id,
                            "attempt",
                            attempt,
                        ),
                        attempt_id=_event_id(
                            cycle_id,
                            target_id,
                            "attempt-id",
                            attempt,
                        ),
                        cycle_id=cycle_id,
                        target_id=target_id,
                        result=status,
                        reason_code=(
                            "OK"
                            if status == "succeeded"
                            else str(transaction_result["reason_code"])
                        ),
                        plan_digest=str(plan["plan_digest"]),
                        source_receipt_id=str(
                            plan["source"]["qualification_receipt_id"]
                        ),
                        source_identity_digest=source_digest,
                        target_identity_digest=digest_json(plan["target"]),
                        evidence_digest=digest_json(receipt),
                        independence={
                            **_INDEPENDENT,
                            "recovery": status != "unknown_outcome",
                        },
                        now=now,
                    )
                    break
        except Exception as exc:
            code = _reason_code(exc)
            if code.startswith("SOURCE_"):
                store.block_cycle_source(
                    event_id=_event_id(cycle_id, "source-error", index),
                    cycle_id=cycle_id,
                    observed_source_digest=digest_json({"reason_code": code}),
                    reason=code,
                    now=now,
                )
                break
            if plan is None:
                _cycle_skip(
                    store,
                    cycle_id=cycle_id,
                    source_digest=source_digest,
                    target_id=target_id,
                    result="ineligible",
                    reason=code,
                    now=now,
                )
                continue

            zero_write: str | None = None
            if transaction_result is None and target_before is not None:
                try:
                    if planning._repo_identity(target, "TARGET") == target_before:
                        zero_write = digest_json(target_before)
                except Exception:
                    pass
            status = (
                str(transaction_result["status"])
                if transaction_result is not None
                else ""
            )
            fallback_result = (
                "recovered"
                if status == "recovered"
                else "preflight_failed"
                if zero_write is not None
                else "unknown_outcome"
            )
            store.record_attempt(
                event_id=_event_id(
                    cycle_id,
                    target_id,
                    "fallback",
                    active_attempt,
                ),
                attempt_id=_event_id(
                    cycle_id,
                    target_id,
                    "fallback-attempt",
                    active_attempt,
                ),
                cycle_id=cycle_id,
                target_id=target_id,
                result=fallback_result,
                reason_code=code,
                plan_digest=str(plan["plan_digest"]),
                source_receipt_id=str(plan["source"]["qualification_receipt_id"]),
                source_identity_digest=source_digest,
                target_identity_digest=digest_json(plan["target"]),
                evidence_digest=digest_json(
                    {
                        "reason_code": code,
                        "transaction_status": status or None,
                    }
                ),
                independence={
                    **_INDEPENDENT,
                    "recovery": fallback_result != "unknown_outcome",
                },
                zero_write_proof=zero_write,
                now=now,
            )

    store.derive_deadlines(now=now)
    cycle = store.snapshot()["cycles"][cycle_id]
    return cycle, 0 if cycle["status"] == "completed" else 1


def _status(
    command: str,
    payload: Mapping[str, object],
) -> tuple[dict[str, object], int]:
    _expect(payload, {"state_path"})
    state = _store(payload, initialize=False).snapshot()
    return (state if command == "status" else {"events": state["inbox"]}), 0


def _dispatch(
    command: str,
    payload: Mapping[str, object],
) -> tuple[dict[str, object], int]:
    if command in _STATE_SPECS or command == "grant-revoke":
        return _state_command(command, payload)
    if command == "plan":
        return _plan(payload)
    if command == "apply":
        return _apply(payload)
    if command == "verify":
        return _verify(payload)
    if command == "recover":
        return _recover(payload)
    if command == "single-target":
        return _single_target(payload)
    if command == "cycle":
        return _cycle(payload)
    if command in {"status", "inbox"}:
        return _status(command, payload)
    raise CliInputError(f"unsupported command: {command}")


def _parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("--input", required=True, dest="input_path")
    return parser


def _emit(payload: Mapping[str, object]) -> None:
    print(
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    command = "unknown"
    try:
        args = _parser().parse_args(argv)
        command = args.command
        result, exit_code = _dispatch(
            command,
            _load_object(args.input_path, "input"),
        )
        if exit_code == 0:
            _emit(
                {
                    "command": command,
                    "result": result,
                    "schema_version": 1,
                    "status": "ok",
                }
            )
        else:
            status = result.get("status")
            transaction = result.get("transaction")
            if status is None and isinstance(transaction, Mapping):
                status = transaction.get("status")
            _emit(
                {
                    "command": command,
                    "error": {
                        "code": (
                            f"{command}_{status or 'non_success'}".upper().replace(
                                "-", "_"
                            )
                        ),
                        "detail": "sha256:" + digest_json(result),
                    },
                    "schema_version": 1,
                    "status": "error",
                }
            )
        return exit_code
    except CliInputError as exc:
        _emit(
            {
                "command": command,
                "error": {
                    "code": "CLI_INPUT_INVALID",
                    "detail": "sha256:" + sha256(str(exc).encode("utf-8")).hexdigest(),
                },
                "schema_version": 1,
                "status": "error",
            }
        )
        return 2
    except Exception as exc:
        _emit(
            {
                "command": command,
                "error": {
                    "code": _reason_code(exc),
                    "detail": "sha256:" + sha256(str(exc).encode("utf-8")).hexdigest(),
                },
                "schema_version": 1,
                "status": "error",
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
