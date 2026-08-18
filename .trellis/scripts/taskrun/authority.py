"""Repo-local SQLite authority for Unified Intent Loop v1."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence


SCHEMA_VERSION = 1


class AuthorityError(RuntimeError):
    """The lifecycle authority rejected an operation."""


class OperationConflict(AuthorityError):
    """An operation ID was replayed with different input."""


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def digest(value: object) -> str:
    data = value if isinstance(value, bytes) else canonical_json(value).encode("ascii")
    return sha256(data).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise AuthorityError(detail or f"git {' '.join(args)} failed")
    return result


def git_common_dir(repo_root: Path) -> Path:
    root = Path(repo_root).resolve()
    raw = git(root, "rev-parse", "--git-common-dir").stdout.strip()
    common = Path(raw)
    if not common.is_absolute():
        common = root / common
    common = common.absolute()
    if not common.is_dir():
        raise AuthorityError("Git common dir is unavailable")
    current = Path(common.anchor)
    for part in common.parts[1:]:
        current /= part
        if current.is_symlink():
            raise AuthorityError("Git common dir contains a symlink component")
    return common


def authority_path(repo_root: Path) -> Path:
    return git_common_dir(repo_root) / "trellis" / "harness.sqlite3"


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
  task_id TEXT PRIMARY KEY,
  task_dir_name TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  task_kind TEXT NOT NULL CHECK(task_kind IN ('code','sync','release')),
  prd_path TEXT NOT NULL,
  prd_digest TEXT NOT NULL,
  active_binding_generation INTEGER NOT NULL,
  base_branch TEXT NOT NULL,
  task_branch TEXT NOT NULL,
  worktree_path TEXT,
  work_state TEXT NOT NULL,
  closeout_state TEXT NOT NULL,
  archive_state TEXT NOT NULL,
  verified_candidate_digest TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bindings (
  task_id TEXT NOT NULL REFERENCES tasks(task_id),
  generation INTEGER NOT NULL,
  prd_digest TEXT NOT NULL,
  git_commit TEXT NOT NULL,
  accepted_at TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  PRIMARY KEY(task_id, generation)
);
CREATE TABLE IF NOT EXISTS task_relations (
  task_id TEXT NOT NULL REFERENCES tasks(task_id),
  relation TEXT NOT NULL CHECK(relation IN ('depends_on','part_of','related_to')),
  other_task_id TEXT NOT NULL REFERENCES tasks(task_id),
  PRIMARY KEY(task_id, relation, other_task_id)
);
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL UNIQUE REFERENCES tasks(task_id),
  strategy TEXT NOT NULL CHECK(strategy IN ('loop','single')),
  execution_mode TEXT NOT NULL CHECK(execution_mode IN ('compact','delegated')),
  soft_attempt_limit INTEGER NOT NULL,
  hard_attempt_limit INTEGER NOT NULL,
  stagnation_rounds INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS actions (
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  action_id TEXT NOT NULL,
  binding_generation INTEGER NOT NULL,
  kind TEXT NOT NULL,
  dependencies_json TEXT NOT NULL,
  requirement_ids_json TEXT NOT NULL,
  touches_json TEXT NOT NULL,
  check_ids_json TEXT NOT NULL,
  risk TEXT NOT NULL CHECK(risk IN ('low','medium','high')),
  status TEXT NOT NULL,
  claimed_by TEXT,
  claim_kind TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(run_id, action_id)
);
CREATE TABLE IF NOT EXISTS attempts (
  run_id TEXT NOT NULL,
  action_id TEXT NOT NULL,
  attempt_no INTEGER NOT NULL,
  root_cause_fingerprint TEXT,
  candidate_digest TEXT,
  result_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(run_id, action_id, attempt_no),
  FOREIGN KEY(run_id, action_id) REFERENCES actions(run_id, action_id)
);
CREATE TABLE IF NOT EXISTS findings (
  finding_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  source TEXT NOT NULL,
  severity TEXT NOT NULL,
  category TEXT NOT NULL,
  requirement_ids_json TEXT NOT NULL,
  scope_json TEXT NOT NULL,
  opened_candidate_digest TEXT NOT NULL,
  status TEXT NOT NULL,
  closure_evidence_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS check_results (
  check_result_id INTEGER PRIMARY KEY AUTOINCREMENT,
  operation_id TEXT NOT NULL UNIQUE,
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  action_id TEXT,
  attempt_no INTEGER NOT NULL,
  check_id TEXT NOT NULL,
  input_digest TEXT NOT NULL,
  argv_json TEXT NOT NULL,
  argv_digest TEXT NOT NULL,
  phase TEXT NOT NULL,
  return_code INTEGER NOT NULL,
  output_digest TEXT NOT NULL,
  before_digest TEXT NOT NULL,
  after_digest TEXT NOT NULL,
  passed INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  candidate_digest TEXT NOT NULL,
  review_no INTEGER NOT NULL,
  reviewer_id TEXT NOT NULL,
  findings_json TEXT NOT NULL,
  semantic INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(run_id, candidate_digest, review_no)
);
CREATE TABLE IF NOT EXISTS target_slots (
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  target_id TEXT NOT NULL,
  target_root TEXT NOT NULL,
  state TEXT NOT NULL,
  branch TEXT,
  worktree_path TEXT,
  managed_paths_json TEXT NOT NULL,
  receipt_json TEXT NOT NULL,
  error TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(run_id, target_id)
);
CREATE TABLE IF NOT EXISTS closeout_steps (
  task_id TEXT NOT NULL REFERENCES tasks(task_id),
  partition_id TEXT NOT NULL,
  step_name TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  state TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  input_digest TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(task_id, partition_id, step_name)
);
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT NOT NULL UNIQUE,
  operation_id TEXT NOT NULL UNIQUE,
  task_id TEXT,
  run_id TEXT,
  event_type TEXT NOT NULL,
  input_digest TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  prev_hash TEXT NOT NULL,
  event_hash TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projections (
  name TEXT PRIMARY KEY,
  source_seq INTEGER NOT NULL,
  digest TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS release_bindings (
  release_id TEXT PRIMARY KEY,
  manifest_json TEXT NOT NULL,
  qualified INTEGER NOT NULL,
  commit_oid TEXT,
  tag TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS legacy_records (
  legacy_id TEXT PRIMARY KEY,
  source_path TEXT NOT NULL,
  disposition TEXT NOT NULL,
  digest TEXT NOT NULL,
  summary_json TEXT NOT NULL
);
"""


class Authority:
    """Thin transactional access to the one shared lifecycle database."""

    def __init__(self, repo_root: Path, *, create: bool = False) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.path = authority_path(self.repo_root)
        if not create and not self.path.is_file():
            raise AuthorityError("Unified Intent Loop authority is not initialized")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.path.parent.is_symlink():
            raise AuthorityError("Authority directory must not be a symlink")
        self.connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA busy_timeout=5000")
        if create:
            self._initialize()
        os.chmod(self.path, 0o600)

    def _initialize(self) -> None:
        self.connection.executescript("BEGIN IMMEDIATE;\n" + SCHEMA + "\nCOMMIT;")
        row = self.connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        if row and int(row["value"]) != SCHEMA_VERSION:
            raise AuthorityError("Unsupported authority schema version")
        with self.transaction():
            self.connection.execute(
                "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version',?)",
                (str(SCHEMA_VERSION),),
            )

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "Authority":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        else:
            self.connection.execute("COMMIT")

    def one(self, sql: str, values: Sequence[object] = ()) -> sqlite3.Row | None:
        return self.connection.execute(sql, tuple(values)).fetchone()

    def all(self, sql: str, values: Sequence[object] = ()) -> list[sqlite3.Row]:
        return list(self.connection.execute(sql, tuple(values)).fetchall())

    def execute(self, sql: str, values: Sequence[object] = ()) -> sqlite3.Cursor:
        return self.connection.execute(sql, tuple(values))

    def record_event(
        self,
        operation_id: str,
        event_type: str,
        payload: Mapping[str, object],
        *,
        task_id: str | None = None,
        run_id: str | None = None,
        operation_input: object | None = None,
    ) -> dict[str, Any]:
        if self.connection.in_transaction:
            return self._record_event(
                operation_id,
                event_type,
                payload,
                task_id=task_id,
                run_id=run_id,
                operation_input=operation_input,
            )
        with self.transaction():
            return self._record_event(
                operation_id,
                event_type,
                payload,
                task_id=task_id,
                run_id=run_id,
                operation_input=operation_input,
            )

    def _record_event(
        self,
        operation_id: str,
        event_type: str,
        payload: Mapping[str, object],
        *,
        task_id: str | None,
        run_id: str | None,
        operation_input: object | None,
    ) -> dict[str, Any]:
        event_input = operation_input if operation_input is not None else payload
        input_digest = digest(event_input)
        replay = self.replay_event(operation_id, event_type, event_input)
        if replay is not None:
            return replay
        previous = self.one("SELECT event_hash FROM events ORDER BY seq DESC LIMIT 1")
        prev_hash = previous["event_hash"] if previous else "0" * 64
        created_at = utc_now()
        body = {
            "created_at": created_at,
            "event_type": event_type,
            "input_digest": input_digest,
            "operation_id": operation_id,
            "payload": dict(payload),
            "prev_hash": prev_hash,
            "run_id": run_id,
            "task_id": task_id,
        }
        event_hash = digest(body)
        event_id = f"evt-{event_hash}"
        self.execute(
            """INSERT INTO events(
                 event_id,operation_id,task_id,run_id,event_type,input_digest,
                 payload_json,prev_hash,event_hash,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                event_id,
                operation_id,
                task_id,
                run_id,
                event_type,
                input_digest,
                canonical_json(payload),
                prev_hash,
                event_hash,
                created_at,
            ),
        )
        return dict(payload)

    def replay_event(
        self,
        operation_id: str,
        event_type: str,
        operation_input: object,
    ) -> dict[str, Any] | None:
        existing = self.one(
            "SELECT input_digest,payload_json,event_type FROM events WHERE operation_id=?",
            (operation_id,),
        )
        if not existing:
            return None
        if existing["input_digest"] != digest(operation_input) or existing["event_type"] != event_type:
            raise OperationConflict("operation replay input changed")
        return json.loads(existing["payload_json"])

    def verify_event_chain(self) -> None:
        previous = "0" * 64
        for row in self.all("SELECT * FROM events ORDER BY seq"):
            body = {
                "created_at": row["created_at"],
                "event_type": row["event_type"],
                "input_digest": row["input_digest"],
                "operation_id": row["operation_id"],
                "payload": json.loads(row["payload_json"]),
                "prev_hash": row["prev_hash"],
                "run_id": row["run_id"],
                "task_id": row["task_id"],
            }
            if row["prev_hash"] != previous or row["event_hash"] != digest(body):
                raise AuthorityError("authority event chain is invalid")
            previous = row["event_hash"]

    def backup(self, destination: Path) -> Path:
        target = Path(destination)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if target.exists():
            raise AuthorityError("backup destination already exists")
        copy = sqlite3.connect(target)
        try:
            self.connection.backup(copy)
        finally:
            copy.close()
        os.chmod(target, 0o600)
        return target

    def task_snapshot(self, task_id: str) -> dict[str, Any]:
        task = self.one("SELECT * FROM tasks WHERE task_id=?", (task_id,))
        if not task:
            raise AuthorityError(f"Unknown task: {task_id}")
        run = self.one("SELECT * FROM runs WHERE task_id=?", (task_id,))
        actions = self.all(
            "SELECT * FROM actions WHERE run_id=? ORDER BY action_id",
            (run["run_id"],),
        ) if run else []
        return {
            "task": dict(task),
            "run": dict(run) if run else None,
            "actions": [dict(row) for row in actions],
        }
