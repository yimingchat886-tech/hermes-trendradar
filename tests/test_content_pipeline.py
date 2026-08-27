from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_benchmark.content_pipeline import (
    ContentPipelineError,
    _read_media_manifest,
    build_mediacrawler_command,
    deterministic_run_id,
    execute_content_pipeline,
    load_pipeline_profile,
    request_digest,
    resolve_content_pipeline_root,
    run_real_content_pipeline,
    validate_request,
    validate_pipeline_profile,
)


def _request(scope: dict, *, copy_mode: str | None = None) -> dict:
    value = {
        "schema_version": "hermes-content-request.v1",
        "target": {"platform": "douyin", "account_id": "acct_1"},
        "scope": scope,
    }
    if copy_mode is not None:
        value["copy_mode"] = copy_mode
    return value


def _profile(root: Path) -> SimpleNamespace:
    return SimpleNamespace(root={"content_pipeline_root": str(root)})


def _error_code(callable_, *args, **kwargs) -> str:
    with pytest.raises(ContentPipelineError) as error:
        callable_(*args, **kwargs)
    return error.value.code


def test_request_is_strict_and_defaults_copy_mode() -> None:
    request = _request({"content_ids": ["c-1"], "max_items": 2})
    parsed = validate_request(request)
    assert parsed["copy_mode"] == "original"
    assert parsed["scope"] == {
        "content_ids": ["c-1"],
        "published_since": None,
        "max_items": 2,
        "all_visible": False,
    }
    assert validate_request(_request({"content_ids": ["c-1"]}, copy_mode="optimized"))["copy_mode"] == "optimized"

    assert _error_code(validate_request, {**request, "extra": True}) == "content_request_extra_field"
    assert _error_code(
        validate_request,
        _request({"content_ids": ["c-1"]})
        | {"target": {"platform": "douyin", "account_id": "a", "extra": 1}},
    ) == "content_request_target"
    assert _error_code(validate_request, _request({"content_ids": ["c-1"], "extra": 1})) == "content_request_scope"


@pytest.mark.parametrize(
    "scope",
    [
        {},
        {"content_ids": []},
        {"all_visible": False},
        {"all_visible": "true"},
        {"all_visible": True, "max_items": 1},
    ],
)
def test_scope_requires_an_effective_selector(scope: dict) -> None:
    code = _error_code(validate_request, _request(scope))
    assert code in {
        "content_request_scope",
        "content_request_scope_selector_required",
        "content_request_scope_selector_conflict",
    }


@pytest.mark.parametrize("max_items", [0, -1, True, 1.0, "1"])
def test_max_items_is_optional_but_positive_integer_when_present(max_items) -> None:
    assert _error_code(validate_request, _request({"content_ids": ["c"], "max_items": max_items})) == "content_request_max_items"
    parsed = validate_request(_request({"published_since": "2026-01-01T00:00:00Z"}))
    assert parsed["scope"]["max_items"] is None


def test_scope_sentinels_are_fixed_and_digest_equivalent() -> None:
    sparse = _request({"max_items": 3})
    full = _request(
        {
            "content_ids": [],
            "published_since": None,
            "max_items": 3,
            "all_visible": False,
        }
    )
    expected_scope = {
        "content_ids": [],
        "published_since": None,
        "max_items": 3,
        "all_visible": False,
    }
    assert validate_request(full)["scope"] == expected_scope
    assert request_digest(sparse) == request_digest(full)
    assert deterministic_run_id(sparse) == deterministic_run_id(full)
    assert _error_code(
        validate_request,
        _request(
            {
                "content_ids": [],
                "published_since": None,
                "max_items": None,
                "all_visible": False,
            }
        ),
    ) == "content_request_scope_selector_required"


def test_digest_and_run_id_are_canonical_and_deterministic() -> None:
    first = _request({"max_items": 3, "content_ids": ["c"]})
    second = {
        "scope": {"content_ids": ["c"], "max_items": 3},
        "target": {"account_id": "acct_1", "platform": "douyin"},
        "schema_version": "hermes-content-request.v1",
        "copy_mode": "original",
    }
    assert request_digest(first) == request_digest(second)
    assert deterministic_run_id(first) == deterministic_run_id(second)
    assert request_digest(first) != request_digest(_request({"content_ids": ["other"]}))


def test_profile_root_must_be_absolute_external_and_non_symlink(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "runs"
    assert resolve_content_pipeline_root(_profile(root), repo) == root
    assert resolve_content_pipeline_root({"content_pipeline_root": str(root / "direct")}, repo) == root / "direct"

    assert _error_code(resolve_content_pipeline_root, _profile(Path("runs")), repo) == "content_pipeline_root_not_absolute"
    assert _error_code(resolve_content_pipeline_root, _profile(repo), repo) == "content_pipeline_root_inside_repo"
    assert _error_code(resolve_content_pipeline_root, SimpleNamespace(root={}), repo) == "content_pipeline_root_missing"

    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    assert _error_code(resolve_content_pipeline_root, _profile(link), repo) == "content_pipeline_path_boundary"


def test_execute_mixed_run_keeps_canonical_media_and_cleans_temp(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "runs"
    profile = _profile(root)
    request = _request({"all_visible": True})
    rows = [
        {
            "platform": "douyin",
            "account_id": "acct_1",
            "content_id": "newer",
            "platform_content_id": "p-newer",
            "published_at": "2026-01-02T00:00:00Z",
            "title": "Newer",
            "media_path": "newer.mp4",
            "raw_source_url": "https://example.test/newer",
        },
        {
            "platform": "douyin",
            "account_id": "acct_1",
            "content_id": "broken",
            "platform_content_id": "p-broken",
            "published_at": "2026-01-01T00:00:00Z",
            "title": "Broken",
            "media_path": "broken.mp4",
            "source_url": "https://example.test/broken",
        },
        {"platform": "douyin", "account_id": "other", "content_id": "ignored"},
    ]
    seen_temp: list[Path] = []

    def fetch(row: dict) -> bytes:
        if row["content_id"] == "broken":
            raise RuntimeError("fetch failed at /secret/path")
        return b"canonical-media"

    def transcribe(path: Path) -> dict:
        seen_temp.append(path)
        assert path.name == "newer.mp4"
        assert path.read_bytes() == b"canonical-media"
        return {"text": "transcript text"}

    receipt = execute_content_pipeline(profile, request, lambda _target, _scope: rows, fetch, transcribe, repo_root=repo)
    assert receipt["status"] == "partial"
    assert [item["content_id"] for item in receipt["items"]] == ["newer", "broken"]
    assert receipt["items"][0]["status"] == "completed"
    assert receipt["items"][1]["error_code"] == "item_failed"
    assert str(root) not in json.dumps(receipt)
    assert all(ref.startswith("file:") and str(root) not in ref for ref in receipt["artifact_refs"] + receipt["items"][0]["artifact_refs"])
    run_dir = root / receipt["run_id"]
    canonical = run_dir / "newer" / "media" / "original.mp4"
    assert canonical.read_bytes() == b"canonical-media"
    assert (run_dir / "newer" / "transcript.original.md").read_text(encoding="utf-8") == "transcript text"
    assert seen_temp and not seen_temp[0].exists()
    assert not list((run_dir / ".transcription-tmp").glob("*"))


def test_execute_missing_content_is_blocked_and_sorted_without_default_cap(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    request = _request({"content_ids": ["missing", "old", "new"]})
    rows = [
        {"platform": "douyin", "account_id": "acct_1", "content_id": "old", "published_at": "2026-01-01T00:00:00Z", "url": "https://example.test/old"},
        {"platform": "douyin", "account_id": "acct_1", "content_id": "new", "published_at": "2026-01-02T00:00:00Z", "url": "https://example.test/new"},
    ]
    receipt = execute_content_pipeline(
        _profile(tmp_path / "runs"),
        request,
        lambda _target, _scope: rows,
        lambda _row: b"media",
        lambda _path: {"text": "text"},
        repo_root=repo,
    )
    assert receipt["status"] == "partial"
    assert [item["content_id"] for item in receipt["items"]] == ["missing", "old", "new"]
    assert receipt["items"][0]["error_code"] == "content_not_found"


def test_execute_replay_verifies_hashes_and_tamper_conflicts(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "runs"
    profile = _profile(root)
    request = _request({"content_ids": ["one"]})
    calls = {"collector": 0, "fetcher": 0, "transcriber": 0}

    def collect(_target, _scope):
        calls["collector"] += 1
        return [{"platform": "douyin", "account_id": "acct_1", "content_id": "one", "media_path": "one.mp4", "url": "https://example.test/one", "published_at": "2026-01-01T00:00:00Z"}]

    def fetch(_row):
        calls["fetcher"] += 1
        return b"media"

    def transcribe(_path):
        calls["transcriber"] += 1
        return {"text": "text"}

    first = execute_content_pipeline(profile, request, collect, fetch, transcribe, repo_root=repo)
    replay = execute_content_pipeline(
        profile,
        request,
        lambda *_: (_ for _ in ()).throw(AssertionError("replay collected")),
        lambda _row: (_ for _ in ()).throw(AssertionError("replay fetched")),
        lambda _path: (_ for _ in ()).throw(AssertionError("replay transcribed")),
        repo_root=repo,
    )
    assert first["status"] == replay["status"] == "success"
    assert replay["replayed"] is True
    assert calls == {"collector": 1, "fetcher": 1, "transcriber": 1}

    media = root / first["run_id"] / "one" / "media" / "original.mp4"
    media.write_bytes(b"tampered")
    assert _error_code(
        execute_content_pipeline,
        profile,
        request,
        collect,
        fetch,
        transcribe,
        repo_root=repo,
    ) == "content_pipeline_artifact_conflict"


def test_execute_replay_resumes_blocked_item(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "runs"
    profile = _profile(root)
    request = _request({"content_ids": ["recover"]})
    calls = {"collector": 0, "fetcher": 0, "transcriber": 0}
    row = {
        "platform": "douyin",
        "account_id": "acct_1",
        "content_id": "recover",
        "url": "https://example.test/recover",
        "published_at": "2026-01-01T00:00:00Z",
    }

    def collect(_target, _scope):
        calls["collector"] += 1
        return [row]

    def fetch(_row):
        calls["fetcher"] += 1
        if calls["fetcher"] == 1:
            raise RuntimeError("first attempt failed")
        return b"media"

    def transcribe(_path):
        calls["transcriber"] += 1
        return {"text": "recovered"}

    first = execute_content_pipeline(profile, request, collect, fetch, transcribe, repo_root=repo)
    second = execute_content_pipeline(profile, request, collect, fetch, transcribe, repo_root=repo)
    assert first["status"] == "blocked"
    assert second["status"] == "success"
    assert second["replayed"] is True
    assert second["items"][0]["status"] == "completed"
    assert calls == {"collector": 2, "fetcher": 2, "transcriber": 1}


@pytest.mark.parametrize("tampered_field", ["status", "summary", "target", "scope", "items"])
def test_execute_replay_receipt_shape_tampering_fails_closed(tmp_path: Path, tampered_field: str) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "runs"
    profile = _profile(root)
    request = _request({"content_ids": ["tamper"]})
    row = {
        "platform": "douyin",
        "account_id": "acct_1",
        "content_id": "tamper",
        "url": "https://example.test/tamper",
        "published_at": "2026-01-01T00:00:00Z",
    }
    receipt = execute_content_pipeline(
        profile,
        request,
        lambda _target, _scope: [row],
        lambda _row: b"media",
        lambda _path: {"text": "text"},
        repo_root=repo,
    )
    receipt_path = root / receipt["run_id"] / "receipt.json"
    tampered = json.loads(receipt_path.read_text(encoding="utf-8"))
    if tampered_field == "status":
        tampered["status"] = "blocked"
    elif tampered_field == "summary":
        tampered["summary"]["completed"] = 0
    elif tampered_field == "target":
        tampered["target"]["account_id"] = "other"
    elif tampered_field == "scope":
        tampered["scope"]["content_ids"] = ["other"]
    else:
        tampered["items"][0]["content_id"] = "other"
    receipt_path.write_text(json.dumps(tampered), encoding="utf-8")
    assert _error_code(
        execute_content_pipeline,
        profile,
        request,
        lambda *_: pytest.fail("tampered replay collected"),
        lambda _row: pytest.fail("tampered replay fetched"),
        lambda _path: pytest.fail("tampered replay transcribed"),
        repo_root=repo,
    ) == "content_pipeline_artifact_conflict"


def test_mediacrawler_epoch_timestamps_are_accepted_by_executor(tmp_path: Path) -> None:
    from hermes_benchmark.collection_runner import mediacrawler_douyin_row

    repo = tmp_path / "repo"
    repo.mkdir()
    adapter_row = mediacrawler_douyin_row(
        {
            "aweme_id": "aweme-epoch",
            "aweme_url": "https://www.douyin.com/video/aweme-epoch",
            "create_time": 1782950400,
            "last_modify_ts": 1782950401,
            "desc": "epoch row",
        },
        {"handle": "acct_1"},
    )
    adapter_row.update(
        {
            "account_id": "acct_1",
            "content_id": "content-douyin-aweme-epoch",
        }
    )
    receipt = execute_content_pipeline(
        _profile(tmp_path / "runs"),
        _request({"content_ids": ["content-douyin-aweme-epoch"]}),
        lambda _target, _scope: [adapter_row],
        lambda _row: b"media",
        lambda _path: {"text": "epoch transcript"},
        repo_root=repo,
    )
    assert receipt["status"] == "success"
    assert receipt["items"][0]["source"]["published_at"] == "2026-07-02T00:00:00Z"


def test_empty_successful_collection_is_no_op_and_replay_safe(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    profile = _profile(tmp_path / "runs")
    request = _request({"all_visible": True})
    calls = {"collector": 0}

    def collect(_target, _scope):
        calls["collector"] += 1
        return []

    first = execute_content_pipeline(profile, request, collect, lambda _row: b"unused", lambda _path: {"text": "unused"}, repo_root=repo)
    replay = execute_content_pipeline(
        profile,
        request,
        lambda *_: pytest.fail("no-op replay collected"),
        lambda _row: pytest.fail("no-op replay fetched"),
        lambda _path: pytest.fail("no-op replay transcribed"),
        repo_root=repo,
    )
    assert first["status"] == replay["status"] == "no_op"
    assert first["summary"] == {"selected": 0, "completed": 0, "blocked": 0}
    assert replay["replayed"] is True
    assert calls == {"collector": 1}


def test_execute_path_media_keeps_source_canonical_and_cleans_temp(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "runs"
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"path-backed-media")
    request = _request({"content_ids": ["path-item"]})
    row = {
        "platform": "douyin",
        "account_id": "acct_1",
        "content_id": "path-item",
        "platform_content_id": "p-path",
        "url": "https://example.test/path",
        "published_at": "2026-01-03T00:00:00Z",
        "crawled_at": "2026-01-04T00:00:00Z",
        "media_path": "source.mp4",
    }
    seen_temp: list[Path] = []

    def transcribe(path: Path) -> dict:
        seen_temp.append(path)
        assert path != source_path
        return {"text": "path transcript"}

    receipt = execute_content_pipeline(
        _profile(root),
        request,
        lambda _target, _scope: [row],
        lambda _row: source_path,
        transcribe,
        repo_root=repo,
    )
    run_dir = root / receipt["run_id"]
    assert receipt["status"] == "success"
    assert source_path.read_bytes() == b"path-backed-media"
    assert (run_dir / "path-item" / "media" / "original.mp4").read_bytes() == b"path-backed-media"
    assert seen_temp and not seen_temp[0].exists()


def test_execute_blocked_state_deletion_fails_closed_on_replay(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "runs"
    request = _request({"content_ids": ["blocked"]})
    row = {
        "platform": "douyin",
        "account_id": "acct_1",
        "content_id": "blocked",
        "url": "https://example.test/blocked",
        "published_at": "2026-01-01T00:00:00Z",
    }
    receipt = execute_content_pipeline(
        _profile(root),
        request,
        lambda _target, _scope: [row],
        lambda _row: (_ for _ in ()).throw(RuntimeError("missing media")),
        lambda _path: {"text": "unused"},
        repo_root=repo,
    )
    assert receipt["status"] == "blocked"
    (root / receipt["run_id"] / "blocked" / "state.json").unlink()
    assert _error_code(
        execute_content_pipeline,
        _profile(root),
        request,
        lambda *_: (_ for _ in ()).throw(AssertionError("replay collected")),
        lambda _row: (_ for _ in ()).throw(AssertionError("replay fetched")),
        lambda _path: (_ for _ in ()).throw(AssertionError("replay transcribed")),
        repo_root=repo,
    ) == "content_pipeline_artifact_conflict"


@pytest.mark.parametrize("missing_field", ["url", "published_at"])
def test_execute_missing_source_fields_are_blocked(tmp_path: Path, missing_field: str) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    row = {
        "platform": "douyin",
        "account_id": "acct_1",
        "content_id": "source-invalid",
        "url": "https://example.test/source-invalid",
        "published_at": "2026-01-01T00:00:00Z",
    }
    row.pop(missing_field)
    receipt = execute_content_pipeline(
        _profile(tmp_path / "runs"),
        _request({"content_ids": ["source-invalid"]}),
        lambda _target, _scope: [row],
        lambda _row: (_ for _ in ()).throw(AssertionError("invalid source fetched")),
        lambda _path: (_ for _ in ()).throw(AssertionError("invalid source transcribed")),
        repo_root=repo,
    )
    assert receipt["status"] == "blocked"
    assert receipt["items"][0]["error_code"] == "source_invalid"
    assert set(receipt["items"][0]["source"]) == {
        "platform",
        "account_id",
        "content_id",
        "platform_content_id",
        "source_id",
        "url",
        "published_at",
    }


def test_source_url_fragment_is_removed_and_sensitive_query_is_not_exposed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    request = _request({"content_ids": ["good-url", "bad-url"]})
    sensitive_url = "https://example.test/bad?Api_Key=do-not-expose#fragment"
    rows = [
        {
            "platform": "douyin",
            "account_id": "acct_1",
            "content_id": "good-url",
            "url": "https://example.test/good?view=1#fragment",
            "published_at": "2026-01-01T00:00:00Z",
        },
        {
            "platform": "douyin",
            "account_id": "acct_1",
            "content_id": "bad-url",
            "url": sensitive_url,
            "published_at": "2026-01-01T00:00:00Z",
        },
    ]
    receipt = execute_content_pipeline(
        _profile(tmp_path / "runs"),
        request,
        lambda _target, _scope: rows,
        lambda _row: b"media",
        lambda _path: {"text": "text"},
        repo_root=repo,
    )
    assert receipt["status"] == "partial"
    assert receipt["items"][0]["source"]["url"] == "https://example.test/good?view=1"
    assert receipt["items"][1]["error_code"] == "source_invalid"
    assert receipt["items"][1]["source"]["url"] == ""
    assert sensitive_url not in json.dumps(receipt)


def test_execute_receipt_symlink_is_a_replay_conflict(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "runs"
    request = _request({"content_ids": ["symlink-receipt"]})
    row = {
        "platform": "douyin",
        "account_id": "acct_1",
        "content_id": "symlink-receipt",
        "url": "https://example.test/symlink-receipt",
        "published_at": "2026-01-01T00:00:00Z",
    }
    receipt = execute_content_pipeline(
        _profile(root),
        request,
        lambda _target, _scope: [row],
        lambda _row: b"media",
        lambda _path: {"text": "text"},
        repo_root=repo,
    )
    receipt_path = root / receipt["run_id"] / "receipt.json"
    target = tmp_path / "receipt-copy.json"
    target.write_text("{}", encoding="utf-8")
    receipt_path.unlink()
    receipt_path.symlink_to(target)
    assert _error_code(
        execute_content_pipeline,
        _profile(root),
        request,
        lambda *_: (_ for _ in ()).throw(AssertionError("symlink replay collected")),
        lambda _row: (_ for _ in ()).throw(AssertionError("symlink replay fetched")),
        lambda _path: (_ for _ in ()).throw(AssertionError("symlink replay transcribed")),
        repo_root=repo,
    ) == "content_pipeline_artifact_conflict"


def test_execute_transcript_tamper_is_a_replay_conflict(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "runs"
    request = _request({"content_ids": ["transcript-tamper"]})
    row = {
        "platform": "douyin",
        "account_id": "acct_1",
        "content_id": "transcript-tamper",
        "url": "https://example.test/transcript-tamper",
        "published_at": "2026-01-01T00:00:00Z",
    }
    receipt = execute_content_pipeline(
        _profile(root),
        request,
        lambda _target, _scope: [row],
        lambda _row: b"media",
        lambda _path: {"text": "text"},
        repo_root=repo,
    )
    (root / receipt["run_id"] / "transcript-tamper" / "transcript.original.md").write_text("tampered", encoding="utf-8")
    assert _error_code(
        execute_content_pipeline,
        _profile(root),
        request,
        lambda *_: [],
        lambda _row: b"unused",
        lambda _path: {"text": "unused"},
        repo_root=repo,
    ) == "content_pipeline_artifact_conflict"


def test_pipeline_profile_has_exact_shape_and_resolves_relative_refs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    wrapper_dir = tmp_path / "wrapper"
    wrapper_dir.mkdir()
    hermes = wrapper_dir / "hermes.json"
    media = wrapper_dir / "media.json"
    hermes.write_text("{}", encoding="utf-8")
    media.write_text("{}", encoding="utf-8")
    wrapper = wrapper_dir / "pipeline.json"
    wrapper.write_text(
        json.dumps(
            {
                "schema_version": "hermes-content-pipeline-profile.v1",
                "content_pipeline_root": str(tmp_path / "external-runs"),
                "hermes_profile_ref": "file:hermes.json",
                "media_profile_ref": "file:media.json",
            }
        ),
        encoding="utf-8",
    )
    parsed = load_pipeline_profile(wrapper, repo_root=repo)
    assert set(parsed) == {
        "schema_version",
        "content_pipeline_root",
        "hermes_profile_ref",
        "media_profile_ref",
    }
    assert parsed["hermes_profile_ref"] == "file:" + str(hermes.resolve())
    assert parsed["media_profile_ref"] == "file:" + str(media.resolve())
    assert Path(parsed["content_pipeline_root"]).is_dir()
    assert _error_code(validate_pipeline_profile, {**parsed, "extra": True}, repo_root=repo) == "content_pipeline_profile_schema"


def test_mediacrawler_command_uses_platform_ids_and_no_creator_cap(tmp_path: Path) -> None:
    account = {"id": "acct", "profile_url": "https://www.douyin.com/user/acct"}
    detail = build_mediacrawler_command(
        account,
        {"content_ids": ["content-douyin-aweme-1", "content-douyin-aweme-2"]},
        python_executable="python",
        output_dir=tmp_path / "raw",
    )
    assert detail[detail.index("--type") + 1] == "detail"
    assert detail[detail.index("--specified_id") + 1] == "aweme-1,aweme-2"
    creator = build_mediacrawler_command(
        account,
        {"content_ids": [], "max_items": 3},
        python_executable="python",
        output_dir=tmp_path / "raw",
    )
    assert creator[creator.index("--type") + 1] == "creator"
    assert "--creator_id" in creator
    assert "--crawler_max_notes_count" not in creator


def test_mediacrawler_command_rejects_unmappable_content_id(tmp_path: Path) -> None:
    code = _error_code(
        build_mediacrawler_command,
        {"id": "acct", "profile_url": "https://www.douyin.com/user/acct"},
        {"content_ids": ["aweme-1"]},
        python_executable="python",
        output_dir=tmp_path / "raw",
    )
    assert code == "content_id_not_collectable"


def test_real_runner_checks_exact_account_before_runtime(monkeypatch, tmp_path: Path) -> None:
    import hermes_benchmark.content_pipeline as module

    repo = tmp_path / "repo"
    repo.mkdir()
    hermes = tmp_path / "hermes.json"
    media = tmp_path / "media.json"
    hermes.write_text("{}", encoding="utf-8")
    media.write_text("{}", encoding="utf-8")
    wrapper = {
        "schema_version": "hermes-content-pipeline-profile.v1",
        "content_pipeline_root": str(tmp_path / "external-runs"),
        "hermes_profile_ref": "file:" + str(hermes),
        "media_profile_ref": "file:" + str(media),
    }
    monkeypatch.setattr(module, "load_profile", lambda _path: object())
    monkeypatch.setattr(module, "enabled_douyin_accounts", lambda _profile: [{"id": "other", "platform": "douyin"}])
    monkeypatch.setattr(module, "resolve_runtime_config", lambda _profile: pytest.fail("runtime started"))
    with pytest.raises(ContentPipelineError) as error:
        run_real_content_pipeline(
            wrapper,
            _request({"content_ids": ["content-douyin-aweme-1"]}),
            repo_root=repo,
        )
    assert error.value.code == "target_not_configured"


def test_media_manifest_hash_mismatch_is_a_boundary_failure(tmp_path: Path) -> None:
    run_root = tmp_path / "media-runs"
    job_dir = run_root / "pipeline-job"
    media_path = job_dir / "media" / "video.mp4"
    media_path.parent.mkdir(parents=True)
    media_path.write_bytes(b"media")
    source_id = "source-test"
    (job_dir / "media-manifest.private.jsonl").write_text(
        json.dumps(
            {
                "source_id": source_id,
                "download_status": "succeeded",
                "local_media_path": str(media_path),
                "media_hash": "sha256:" + "0" * 64,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert _error_code(
        _read_media_manifest,
        {"run_root": run_root},
        "pipeline-job",
        source_id,
    ) == "media_manifest_boundary"


def test_content_pipeline_parser_requires_profile_and_request() -> None:
    from hermes_benchmark import cli

    parser = cli.build_parser()
    with pytest.raises(cli.CliContractError):
        parser.parse_args(["content-pipeline"])
    with pytest.raises(cli.CliContractError):
        parser.parse_args(["content-pipeline", "--profile", "pipeline.json"])


@pytest.mark.parametrize(
    ("result", "expected_ok", "expected_exit"),
    [
        ({"status": "success", "run_id": "run-success"}, True, 0),
        ({"status": "partial", "run_id": "run-partial"}, True, 0),
        ({"status": "no_op", "run_id": "run-no-op"}, True, 0),
        ({"status": "blocked", "run_id": "run-blocked"}, False, 4),
    ],
)
def test_content_pipeline_cli_json_envelopes(
    monkeypatch,
    capsys: pytest.CaptureFixture[str],
    result: dict[str, str],
    expected_ok: bool,
    expected_exit: int,
) -> None:
    from hermes_benchmark import cli

    monkeypatch.setattr(cli, "load_pipeline_profile", lambda _path: {"profile": "stub"})
    monkeypatch.setattr(cli, "load_request_file", lambda _path: {"request": "stub"})
    monkeypatch.setattr(cli, "run_real_content_pipeline", lambda _profile, _request: result)
    exit_code = cli.main(
        [
            "content-pipeline",
            "--profile",
            "pipeline.json",
            "--request",
            "request.json",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == expected_exit
    assert payload["ok"] is expected_ok
    assert payload["command"] == "content-pipeline"
    assert payload["data"]["status"] == result["status"]


def test_content_pipeline_cli_catches_safe_request_error(
    monkeypatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from hermes_benchmark import cli

    monkeypatch.setattr(cli, "load_pipeline_profile", lambda _path: {"profile": "stub"})
    monkeypatch.setattr(cli, "load_request_file", lambda _path: {"request": "stub"})

    def fail(_profile, _request):
        raise ContentPipelineError("content_request_invalid", "/secret/should-not-leak")

    monkeypatch.setattr(cli, "run_real_content_pipeline", fail)
    exit_code = cli.main(
        [
            "content-pipeline",
            "--profile",
            "pipeline.json",
            "--request",
            "request.json",
            "--json",
        ]
    )
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert exit_code == 2
    assert payload["ok"] is False
    assert payload["error"]["code"] == "content_request_invalid"
    assert "/secret/should-not-leak" not in output


def test_content_pipeline_sample_is_strict_json() -> None:
    sample_path = Path(__file__).parents[1] / "profiles" / "examples" / "hermes-content-pipeline.v1.sample.json"
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    assert set(sample) == {
        "schema_version",
        "content_pipeline_root",
        "hermes_profile_ref",
        "media_profile_ref",
    }
    assert sample["schema_version"] == "hermes-content-pipeline-profile.v1"
    assert sample["content_pipeline_root"] == "/home/USER/trendradar-content-pipeline"
    assert sample["hermes_profile_ref"] == "file:hermes.v1.4.douyin.sample.json"
    assert sample["media_profile_ref"] == "file:media.v2.0.sample.json"
