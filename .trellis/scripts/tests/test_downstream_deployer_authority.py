from __future__ import annotations

import json
import sqlite3
import stat
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from downstream_deployer.authority import (
    AuthorityError,
    AuthorityStore,
    ENROLLED_TARGETS,
    EventConflict,
    ProjectionError,
    TARGET_ORDER,
    default_state_path,
)
from loop_v1.qualification import DEPLOYMENT_TARGETS, digest_json


UTC = timezone.utc


def digest(label: str, *, prefixed: bool = False) -> str:
    value = sha256(label.encode("utf-8")).hexdigest()
    return f"sha256:{value}" if prefixed else value


def at(day: int, hour: int = 0) -> datetime:
    return datetime(2026, 7, day, hour, tzinfo=UTC)


def common_binding(target_id: str) -> dict[str, str]:
    return {
        "adoption_receipt_id": digest(f"adoption:{target_id}", prefixed=True),
        "branch": "main",
        "check_policy_digest": digest("checks"),
        "commit_policy_digest": digest("commit"),
        "deployment_policy_digest": digest("deployment"),
        "effect_digest": digest("effects"),
        "lineage_anchor": "a" * 40,
        "notification_policy_digest": digest("notification"),
        "ownership_digest": digest("ownership"),
        "preservation_digest": digest("preservation"),
        "recovery_policy_digest": digest("recovery"),
        "repository_id": f"repo:{target_id}",
        "target_id": target_id,
    }


def action_binding(
    target_id: str,
    *,
    plan: str = "plan",
    recover: bool = False,
) -> dict[str, str]:
    value = {
        **common_binding(target_id),
        "base_head": "b" * 40,
        "plan_digest": digest(plan),
        "source_receipt_id": digest("source-receipt", prefixed=True),
    }
    if recover:
        value.update(
            {
                "failed_transaction_id": digest(
                    "failed-transaction",
                    prefixed=True,
                ),
                "preimage_digest": digest("preimage"),
            }
        )
    return value


def plan_binding(target_id: str) -> dict[str, str]:
    return {
        **common_binding(target_id),
        "base_head": "b" * 40,
        "source_receipt_id": digest("source-receipt", prefixed=True),
    }


def source_identity() -> dict[str, str]:
    return {
        "commit": "c" * 40,
        "policy_digest": digest("source-policy"),
        "qualification_receipt_id": digest(
            "source-receipt",
            prefixed=True,
        ),
        "status_digest": digest("clean"),
        "task_fingerprint": digest("task-free"),
        "tree": "d" * 40,
    }


def independent(value: bool = True) -> dict[str, bool]:
    return {
        "grant": value,
        "lock": value,
        "recovery": value,
        "root": value,
        "state": value,
    }


def activate_grants(
    store: AuthorityStore,
    *,
    not_before: datetime,
) -> list[str]:
    grant_ids: list[str] = []
    for index, target_id in enumerate(TARGET_ORDER):
        authority_id = digest(
            f"adoption-authority:{target_id}",
            prefixed=True,
        )
        action = action_binding(
            target_id,
            plan=f"adoption-plan:{target_id}",
        )
        authority = store.request_one_off(
            event_id=f"adoption-request:{index}",
            authority_id=authority_id,
            effect="apply",
            binding=action,
            not_before=at(18),
            expires_at=at(19),
            now=at(18),
        )
        store.approve_one_off(
            event_id=f"adoption-approval:{index}",
            authority_id=authority_id,
            request_digest=authority["request_digest"],
            response_identity=f"adoption-response:{index}",
            direct_user_action=True,
            now=at(18),
        )
        consumption_id = f"adoption-consumption:{index}"
        store.consume_one_off(
            event_id=consumption_id,
            authority_id=authority_id,
            effect="apply",
            binding=action,
            now=at(18),
        )
        store.record_receipt(
            event_id=f"adoption-receipt:{index}",
            receipt_id=common_binding(target_id)["adoption_receipt_id"],
            receipt_kind="final",
            status="verified",
            target_id=target_id,
            transaction_id=digest(
                f"adoption-transaction:{target_id}",
                prefixed=True,
            ),
            plan_digest=action["plan_digest"],
            source_receipt_id=action["source_receipt_id"],
            payload_digest=digest(f"adoption-payload:{target_id}"),
            authority_id=authority_id,
            authority_consumption_id=consumption_id,
            now=at(18),
        )
        grant_id = digest(f"grant:{target_id}", prefixed=True)
        grant = store.request_grant(
            event_id=f"grant-request:{index}",
            grant_id=grant_id,
            binding=common_binding(target_id),
            not_before=not_before,
            now=at(18),
        )
        store.capture_grant(
            event_id=f"grant-capture:{index}",
            grant_id=grant_id,
            request_digest=grant["request_digest"],
            response_identity=f"capture-response:{index}",
            direct_user_action=True,
            now=at(18),
        )
        grant_ids.append(grant_id)
    store.activate_cohort(
        event_id="cohort-activation",
        grant_ids=grant_ids,
        common_not_before=not_before,
        response_identity="cohort-response",
        direct_user_action=True,
        now=at(18, 2),
    )
    return grant_ids


class DownstreamAuthorityTests(unittest.TestCase):
    def test_ah_map_accepts_one_off_authority_but_not_grants(self) -> None:
        self.assertEqual(ENROLLED_TARGETS, (*TARGET_ORDER, "AH-map"))
        self.assertEqual(DEPLOYMENT_TARGETS, ENROLLED_TARGETS)

        with tempfile.TemporaryDirectory() as tmp:
            store = AuthorityStore.initialize(Path(tmp) / "state.sqlite3")
            target_id = ENROLLED_TARGETS[-1]
            authority_id = digest("ah-map-apply", prefixed=True)
            binding = action_binding(target_id, plan="ah-map-plan")
            request = store.request_one_off(
                event_id="ah-map-request",
                authority_id=authority_id,
                effect="apply",
                binding=binding,
                not_before=at(18),
                expires_at=at(19),
                now=at(18),
            )
            store.approve_one_off(
                event_id="ah-map-approval",
                authority_id=authority_id,
                request_digest=request["request_digest"],
                response_identity="ah-map-response",
                direct_user_action=True,
                now=at(18),
            )
            consumption_id = "ah-map-consumption"
            store.consume_one_off(
                event_id=consumption_id,
                authority_id=authority_id,
                effect="apply",
                binding=binding,
                now=at(18),
            )
            receipt = store.record_receipt(
                event_id="ah-map-receipt",
                receipt_id=digest("ah-map-receipt", prefixed=True),
                receipt_kind="final",
                status="verified",
                target_id=target_id,
                transaction_id=digest("ah-map-transaction", prefixed=True),
                plan_digest=binding["plan_digest"],
                source_receipt_id=binding["source_receipt_id"],
                payload_digest=digest("ah-map-payload"),
                authority_id=authority_id,
                authority_consumption_id=consumption_id,
                now=at(18),
            )
            self.assertEqual(receipt["target_id"], target_id)

            with self.assertRaisesRegex(AuthorityError, "GRANT_INVALID"):
                store.request_grant(
                    event_id="ah-map-grant-request",
                    grant_id=digest("ah-map-grant", prefixed=True),
                    binding=common_binding(target_id),
                    not_before=at(19),
                    now=at(18),
                )
            self.assertNotIn(target_id, store.snapshot()["targets"])

    def test_one_off_authorities_are_exact_distinct_and_replay_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.sqlite3"
            store = AuthorityStore.initialize(path)
            authority_id = digest("one-off-apply", prefixed=True)
            binding = action_binding(TARGET_ORDER[0])
            request = store.request_one_off(
                event_id="one-off-request",
                authority_id=authority_id,
                effect="apply",
                binding=binding,
                not_before=at(19),
                expires_at=at(20),
                now=at(18),
            )
            replay = store.request_one_off(
                event_id="one-off-request",
                authority_id=authority_id,
                effect="apply",
                binding=binding,
                not_before=at(19),
                expires_at=at(20),
                now=at(18),
            )
            self.assertEqual(replay, request)
            with self.assertRaises(EventConflict):
                store.request_one_off(
                    event_id="one-off-request",
                    authority_id=authority_id,
                    effect="apply",
                    binding=binding,
                    not_before=at(19),
                    expires_at=at(21),
                    now=at(18),
                )
            with self.assertRaisesRegex(AuthorityError, "DIRECT_USER_REQUIRED"):
                store.approve_one_off(
                    event_id="one-off-approval",
                    authority_id=authority_id,
                    request_digest=request["request_digest"],
                    response_identity="approval-response",
                    direct_user_action=False,
                    now=at(18, 1),
                )
            store.approve_one_off(
                event_id="one-off-approval",
                authority_id=authority_id,
                request_digest=request["request_digest"],
                response_identity="approval-response",
                direct_user_action=True,
                now=at(18, 1),
            )
            envelope = store.consume_one_off(
                event_id="one-off-consumption",
                authority_id=authority_id,
                effect="apply",
                binding=binding,
                now=at(19, 1),
            )
            self.assertEqual(envelope["effect"], "apply")
            self.assertEqual(envelope["authority_kind"], "one_off")

            commit_id = digest("one-off-commit", prefixed=True)
            commit_request = store.request_one_off(
                event_id="commit-request",
                authority_id=commit_id,
                effect="commit",
                binding=binding,
                not_before=at(19, 1),
                expires_at=at(20),
                now=at(19, 1),
            )
            store.approve_one_off(
                event_id="commit-approval",
                authority_id=commit_id,
                request_digest=commit_request["request_digest"],
                response_identity="commit-response",
                direct_user_action=True,
                now=at(19, 1),
            )
            commit = store.consume_one_off(
                event_id="commit-consumption",
                authority_id=commit_id,
                effect="commit",
                binding=binding,
                now=at(19, 2),
            )
            self.assertEqual(
                set(commit),
                {
                    "authority_id",
                    "authority_kind",
                    "base_head",
                    "branch",
                    "enabled",
                    "lineage_anchor",
                    "plan_digest",
                    "source_receipt_id",
                },
            )
            self.assertTrue(commit["enabled"])
            final_receipt = store.record_receipt(
                event_id="commit-final-receipt",
                receipt_id=digest("commit-final-receipt", prefixed=True),
                receipt_kind="final",
                status="verified",
                target_id=TARGET_ORDER[0],
                transaction_id=digest(
                    "commit-transaction",
                    prefixed=True,
                ),
                plan_digest=binding["plan_digest"],
                source_receipt_id=binding["source_receipt_id"],
                payload_digest=digest("commit-final-payload"),
                authority_id=commit_id,
                authority_consumption_id="commit-consumption",
                commit_id="e" * 40,
                now=at(19, 2),
            )
            self.assertEqual(final_receipt["commit_id"], "e" * 40)

            recovery_id = digest("one-off-recovery", prefixed=True)
            recovery_binding = action_binding(
                TARGET_ORDER[0],
                recover=True,
            )
            recovery_request = store.request_one_off(
                event_id="recovery-request",
                authority_id=recovery_id,
                effect="recover",
                binding=recovery_binding,
                not_before=at(19, 2),
                expires_at=at(20),
                now=at(19, 2),
            )
            store.approve_one_off(
                event_id="recovery-approval",
                authority_id=recovery_id,
                request_digest=recovery_request["request_digest"],
                response_identity="recovery-response",
                direct_user_action=True,
                now=at(19, 2),
            )
            recovery = store.consume_one_off(
                event_id="recovery-consumption",
                authority_id=recovery_id,
                effect="recover",
                binding=recovery_binding,
                now=at(19, 3),
            )
            self.assertEqual(recovery["effect"], "recover")
            with self.assertRaisesRegex(AuthorityError, "CLOCK_REGRESSION"):
                store.request_one_off(
                    event_id="backdated-request",
                    authority_id=digest("backdated", prefixed=True),
                    effect="apply",
                    binding=binding,
                    not_before=at(19, 4),
                    expires_at=at(20),
                    now=at(18),
                )
            self.assertEqual(
                stat.S_IMODE(path.stat().st_mode),
                0o600,
            )
            self.assertEqual(
                default_state_path(
                    {"XDG_STATE_HOME": "/state"},
                    home=Path("/unused"),
                ),
                Path("/state/trellis-loop-updater/state.sqlite3"),
            )
            self.assertNotIn(str(tmp), json.dumps(store.snapshot(), sort_keys=True))

            unknown = Path(tmp) / "unknown.sqlite3"
            connection = sqlite3.connect(unknown)
            try:
                connection.execute(
                    "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT)"
                )
                connection.execute(
                    "INSERT INTO schema_meta VALUES ('schema_version', '99')"
                )
                connection.commit()
            finally:
                connection.close()
            with self.assertRaisesRegex(AuthorityError, "SCHEMA_UNSUPPORTED"):
                AuthorityStore.initialize(unknown)
            connection = sqlite3.connect(unknown)
            try:
                tables = {
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT name FROM sqlite_master
                        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                        """
                    )
                }
            finally:
                connection.close()
            self.assertEqual(tables, {"schema_meta"})

    def test_grant_cohort_pause_resume_renewal_and_policy_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AuthorityStore.initialize(Path(tmp) / "state.sqlite3")
            not_before = at(19)
            grant_ids = activate_grants(store, not_before=not_before)
            target_id = TARGET_ORDER[0]
            binding = action_binding(target_id)
            with self.assertRaisesRegex(AuthorityError, "COHORT_INVALID"):
                store.activate_cohort(
                    event_id="reversed-cohort",
                    grant_ids=list(reversed(grant_ids)),
                    common_not_before=not_before,
                    response_identity="reversed-response",
                    direct_user_action=True,
                    now=at(18, 3),
                )

            plan = store.consume_grant(
                event_id="grant-plan",
                grant_id=grant_ids[0],
                effect="plan",
                binding=plan_binding(target_id),
                now=at(19),
            )
            self.assertEqual(plan["effect"], "plan")
            commit = store.consume_grant(
                event_id="grant-commit",
                grant_id=grant_ids[0],
                effect="commit",
                binding=binding,
                now=at(19, 1),
            )
            self.assertEqual(commit["authority_kind"], "grant")
            store.pause_target(
                event_id="manual-pause",
                target_id=target_id,
                grant_id=grant_ids[0],
                lineage_anchor="a" * 40,
                reason="operator review",
                now=at(19, 2),
            )
            with self.assertRaisesRegex(AuthorityError, "GRANT_AUTHORITY_INVALID"):
                store.consume_grant(
                    event_id="paused-apply",
                    grant_id=grant_ids[0],
                    effect="apply",
                    binding=binding,
                    now=at(19, 3),
                )
            store.resume_target(
                event_id="manual-resume",
                target_id=target_id,
                grant_id=grant_ids[0],
                lineage_anchor="a" * 40,
                response_identity="resume-response",
                direct_user_action=True,
                now=at(19, 4),
            )
            drifted = {**binding, "deployment_policy_digest": digest("drift")}
            with self.assertRaisesRegex(AuthorityError, "GRANT_AUTHORITY_INVALID"):
                store.consume_grant(
                    event_id="drifted-apply",
                    grant_id=grant_ids[0],
                    effect="apply",
                    binding=drifted,
                    now=at(19, 5),
                )
            apply = store.consume_grant(
                event_id="grant-apply",
                grant_id=grant_ids[0],
                effect="apply",
                binding=binding,
                now=at(19, 5),
            )
            self.assertEqual(apply["effect"], "apply")

            new_grant = digest("renewed-grant", prefixed=True)
            renewed = store.renew_grant(
                event_id="grant-renewal",
                old_grant_id=grant_ids[0],
                new_grant_id=new_grant,
                not_before=at(25),
                response_identity="renewal-response",
                direct_user_action=True,
                now=at(20),
            )
            self.assertEqual(
                datetime.fromisoformat(renewed["expires_at"].replace("Z", "+00:00"))
                - datetime.fromisoformat(renewed["not_before"].replace("Z", "+00:00")),
                timedelta(days=30),
            )
            state = store.snapshot()
            self.assertEqual(
                state["authorities"][grant_ids[0]]["state"],
                "superseded",
            )
            self.assertEqual(
                state["targets"][target_id]["grant_id"],
                new_grant,
            )
            store.retire_grant(
                event_id="grant-revocation",
                grant_id=new_grant,
                disposition="revoked",
                reason="direct revocation",
                response_identity="revocation-response",
                direct_user_action=True,
                now=at(26),
            )
            self.assertTrue(store.snapshot()["targets"][target_id]["paused"])
            invalidated = store.retire_grant(
                event_id="grant-invalidation",
                grant_id=grant_ids[1],
                disposition="invalidated",
                reason="deployment policy drift",
                now=at(26),
            )
            self.assertEqual(invalidated["state"], "invalidated")

    def test_reminder_catchup_dedup_and_notifier_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            notifier = (
                sys.executable,
                "-c",
                "import sys; sys.stdin.read(); raise SystemExit(7)",
            )
            store = AuthorityStore.initialize(
                Path(tmp) / "state.sqlite3",
                notifier=notifier,
            )
            not_before = at(19)
            grant_ids = activate_grants(store, not_before=not_before)
            expires = not_before + timedelta(days=30)

            first = store.derive_deadlines(now=expires - timedelta(days=7))
            self.assertEqual(len(first), len(TARGET_ORDER))
            catchup = store.derive_deadlines(now=expires)
            self.assertEqual(len(catchup), 2 * len(TARGET_ORDER))
            self.assertEqual(
                {reminder["milestone"] for reminder in catchup},
                {"expiry", "t-1d"},
            )
            self.assertEqual(store.derive_deadlines(now=expires), [])
            state = store.snapshot()
            self.assertTrue(
                all(
                    state["authorities"][grant_id]["state"] == "expired"
                    for grant_id in grant_ids
                )
            )
            self.assertTrue(
                all(
                    delivery["status"] == "failed"
                    for delivery in state["notifier"].values()
                )
            )
            events = store.events()
            positions = {event["event_id"]: event["position"] for event in events}
            for source_event_id, delivery in state["notifier"].items():
                self.assertLess(
                    positions[source_event_id],
                    positions[delivery["event_id"]],
                )

    def test_cycle_retry_order_independence_and_projection_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AuthorityStore.initialize(Path(tmp) / "state.sqlite3")
            source = source_identity()
            source_digest = digest_json(source)
            cycle = store.start_cycle(
                event_id="cycle-start",
                cycle_id="cycle-1",
                source=source,
                now=at(18),
            )
            self.assertEqual(
                [slot["target_id"] for slot in cycle["slots"]],
                list(TARGET_ORDER),
            )
            first = store.record_attempt(
                event_id="attempt-event-1",
                attempt_id="attempt-1",
                cycle_id="cycle-1",
                target_id=TARGET_ORDER[0],
                result="preflight_failed",
                reason_code="LOCK_BUSY",
                plan_digest=digest("cycle-plan"),
                source_receipt_id=source["qualification_receipt_id"],
                source_identity_digest=source_digest,
                target_identity_digest=digest("target"),
                evidence_digest=digest("attempt-1"),
                independence=independent(),
                zero_write_proof=digest("zero-write"),
                now=at(18, 1),
            )
            self.assertEqual(first["slots"][0]["result"], "retry_pending")
            with self.assertRaisesRegex(AuthorityError, "CYCLE_ORDER_INVALID"):
                store.record_cycle_skip(
                    event_id="out-of-order",
                    cycle_id="cycle-1",
                    target_id=TARGET_ORDER[1],
                    result="no_change",
                    reason_code="NO_CHANGE",
                    evidence_digest=digest("skip"),
                    source_identity_digest=source_digest,
                    independence=independent(),
                    now=at(18, 2),
                )
            cycle = store.record_attempt(
                event_id="attempt-event-2",
                attempt_id="attempt-2",
                cycle_id="cycle-1",
                target_id=TARGET_ORDER[0],
                result="preflight_failed",
                reason_code="LOCK_BUSY",
                plan_digest=digest("cycle-plan"),
                source_receipt_id=source["qualification_receipt_id"],
                source_identity_digest=source_digest,
                target_identity_digest=digest("target"),
                evidence_digest=digest("attempt-2"),
                independence=independent(),
                zero_write_proof=digest("zero-write"),
                now=at(18, 2),
            )
            self.assertTrue(cycle["slots"][0]["stable"])
            self.assertEqual(cycle["status"], "non_success")
            for index, target_id in enumerate(TARGET_ORDER[1:], start=1):
                cycle = store.record_cycle_skip(
                    event_id=f"skip-{index}",
                    cycle_id="cycle-1",
                    target_id=target_id,
                    result="no_change",
                    reason_code="NO_CHANGE",
                    evidence_digest=digest(f"skip-{index}"),
                    source_identity_digest=source_digest,
                    independence=independent(),
                    now=at(18, index + 2),
                )
            self.assertEqual(cycle["status"], "completed_non_success")

            blocked = store.start_cycle(
                event_id="cycle-start-2",
                cycle_id="cycle-2",
                source=source,
                now=at(19),
            )
            blocked = store.record_attempt(
                event_id="unknown-event",
                attempt_id="unknown-attempt",
                cycle_id="cycle-2",
                target_id=TARGET_ORDER[0],
                result="unknown_outcome",
                reason_code="RECOVERY_MISMATCH",
                plan_digest=digest("cycle-plan-2"),
                source_receipt_id=source["qualification_receipt_id"],
                source_identity_digest=source_digest,
                target_identity_digest=digest("target-2"),
                evidence_digest=digest("unknown"),
                independence=independent(False),
                now=at(19, 1),
            )
            self.assertTrue(all(slot["stable"] for slot in blocked["slots"]))
            self.assertEqual(
                blocked["slots"][1]["result"],
                "blocked_by_previous",
            )

            store.start_cycle(
                event_id="cycle-start-3",
                cycle_id="cycle-3",
                source=source,
                now=at(20),
            )
            source_blocked = store.block_cycle_source(
                event_id="source-blocked",
                cycle_id="cycle-3",
                observed_source_digest=digest("drifted-source"),
                reason="source identity drifted",
                now=at(20, 1),
            )
            self.assertEqual(source_blocked["status"], "source_blocked")
            self.assertTrue(
                all(
                    slot["result"] == "source_blocked"
                    for slot in source_blocked["slots"]
                )
            )

            initially_blocked = store.block_cycle_source(
                event_id="source-blocked-initial",
                cycle_id="cycle-4",
                observed_source_digest=digest("active-source"),
                reason="SOURCE_ACTIVE_TASK",
                now=at(20, 2),
            )
            self.assertEqual(initially_blocked["status"], "source_blocked")
            self.assertTrue(
                all(
                    slot["stable"] and slot["result"] == "source_blocked"
                    for slot in initially_blocked["slots"]
                )
            )
            self.assertEqual(
                initially_blocked["source"]["reason"],
                "SOURCE_ACTIVE_TASK",
            )

            connection = sqlite3.connect(store.path)
            try:
                connection.execute("UPDATE projections SET payload_json = '{}'")
                connection.commit()
            finally:
                connection.close()
            with self.assertRaises(ProjectionError):
                store.snapshot()
            rebuilt = store.rebuild_projection()
            self.assertEqual(rebuilt["cycles"]["cycle-1"], cycle)


if __name__ == "__main__":
    unittest.main()
