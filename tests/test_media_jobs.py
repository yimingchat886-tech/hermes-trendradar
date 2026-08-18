from __future__ import annotations

import json
import shutil
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import hermes_benchmark.media_jobs as media_jobs


NOW = datetime(2026, 8, 18, 12, tzinfo=timezone.utc)


def test_all_sources_agent_handoff_replay_and_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profile, calls = profile_fixture(tmp_path)
    direct_calls: list[str] = []

    def download_direct(url: str, output: Path, _timeout: int, _max_bytes: int) -> None:
        direct_calls.append(url)
        output.write_bytes(b"direct-video")

    monkeypatch.setattr(media_jobs, "_download_direct", download_direct)
    request = write_request(
        tmp_path,
        "agent-job",
        [
            {"source_id": "douyin", "url": "https://www.douyin.com/video/1?token=secret"},
            {"source_id": "youtube", "url": "https://youtu.be/2"},
            {"source_id": "bilibili", "url": "https://www.bilibili.com/video/BV3"},
            {"source_id": "direct", "url": "https://media.example/4.webm"},
            {"source_id": "xhs", "url": "https://www.xiaohongshu.com/explore/5"},
        ],
    )

    result = media_jobs.fetch(profile, request, now=NOW)

    assert result["status"] == "partial"
    assert result["succeeded"] == 4
    assert result["failed"] == 1
    assert result["completed_at"] == "2026-08-18T12:00:00Z"
    assert result["expires_at"] == "2026-08-25T12:00:00Z"
    rows = read_manifest(result)
    assert {row["platform"] for row in rows} == {"douyin", "youtube", "bilibili", "direct", "unsupported"}
    unsupported = next(row for row in rows if row["platform"] == "unsupported")
    assert (unsupported["error_code"], unsupported["retryable"]) == ("unsupported_source", False)
    assert all(valid_media_row(row) for row in rows if row["download_status"] == "succeeded")
    assert next(row for row in rows if row["platform"] == "direct")["local_media_path"].endswith(".webm")
    assert stat.S_IMODE(Path(result["manifest_ref"]).parent.stat().st_mode) == 0o700
    assert all(
        stat.S_IMODE(Path(row["local_media_path"]).stat().st_mode) == 0o600
        for row in rows
        if row["download_status"] == "succeeded"
    )
    assert not list(Path(result["manifest_ref"]).parent.rglob("*.tmp*"))
    assert "secret" not in "".join(path.read_text() for path in Path(result["manifest_ref"]).parent.glob("logs/*"))

    adopted = tmp_path / "hyperframes-project" / "public" / "media"
    adopted.mkdir(parents=True)
    for row in rows:
        if row["download_status"] == "succeeded":
            shutil.copy2(row["local_media_path"], adopted / Path(row["local_media_path"]).name)
    assert len(list(adopted.iterdir())) == 4

    calls_before = calls.read_text().splitlines()
    replay = media_jobs.fetch(profile, request, now=NOW + timedelta(hours=1))
    retry = media_jobs.retry(profile, "agent-job", now=NOW + timedelta(hours=2))
    assert replay == result
    assert retry == result
    assert calls.read_text().splitlines() == calls_before
    assert direct_calls == ["https://media.example/4.webm"]

    first = next(row for row in rows if row["platform"] == "youtube")
    Path(first["local_media_path"]).write_bytes(b"corrupt")
    with pytest.raises(media_jobs.MediaJobError, match="missing or corrupt"):
        media_jobs.status(profile, "agent-job")
    repaired = media_jobs.fetch(profile, request, now=NOW + timedelta(hours=3))
    assert repaired["status"] == "partial"
    assert repaired["completed_at"] == "2026-08-18T15:00:00Z"
    assert len(calls.read_text().splitlines()) == len(calls_before) + 1
    assert valid_media_row(next(row for row in read_manifest(repaired) if row["platform"] == "youtube"))

    Path(repaired["manifest_ref"]).unlink()
    restored = media_jobs.fetch(profile, request, now=NOW + timedelta(hours=4))
    assert Path(restored["manifest_ref"]).is_file()
    assert len(calls.read_text().splitlines()) == len(calls_before) + 1


def test_interrupted_job_resumes_and_same_job_is_single_writer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profile, _calls = profile_fixture(tmp_path)
    request = write_request(tmp_path, "resume-job", [{"url": "https://media.example/video.mp4"}])

    def interrupt(*_args: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(media_jobs, "_download_direct", interrupt)
    with pytest.raises(KeyboardInterrupt):
        media_jobs.fetch(profile, request, now=NOW)
    assert media_jobs.status(profile, "resume-job")["status"] == "running"

    job_dir = Path(profile["run_root"]) / "resume-job"
    with media_jobs._job_lock(job_dir):
        with pytest.raises(media_jobs.MediaJobError, match="active writer") as locked:
            media_jobs.retry(profile, "resume-job", now=NOW)
        assert locked.value.code == "job_locked"

    monkeypatch.setattr(
        media_jobs,
        "_download_direct",
        lambda _url, output, _timeout, _max_bytes: output.write_bytes(b"resumed-video"),
    )
    resumed = media_jobs.retry(profile, "resume-job", now=NOW + timedelta(minutes=1))
    assert resumed["status"] == "succeeded"
    assert valid_media_row(read_manifest(resumed)[0])


def test_cleanup_obeys_exact_seven_day_boundary_and_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profile, _calls = profile_fixture(tmp_path)
    monkeypatch.setattr(
        media_jobs,
        "_download_direct",
        lambda _url, output, _timeout, _max_bytes: output.write_bytes(b"video"),
    )
    request = write_request(tmp_path, "cleanup-job", [{"url": "https://media.example/video.mp4"}])
    result = media_jobs.fetch(profile, request, now=NOW)
    job_dir = Path(result["manifest_ref"]).parent

    assert media_jobs.cleanup_expired(profile, now=NOW + timedelta(days=7) - timedelta(microseconds=1))["deleted"] == 0
    with pytest.raises(media_jobs.MediaJobError) as expired:
        media_jobs.fetch(profile, request, now=NOW + timedelta(days=7))
    assert expired.value.code == "job_expired"
    with media_jobs._job_lock(job_dir):
        locked = media_jobs.cleanup_expired(profile, now=NOW + timedelta(days=7))
        assert (locked["deleted"], locked["locked"]) == (0, 1)
    deleted = media_jobs.cleanup_expired(profile, now=NOW + timedelta(days=7))
    assert deleted["deleted_job_ids"] == ["cleanup-job"]
    assert not job_dir.exists()
    assert media_jobs.cleanup_expired(profile, now=NOW + timedelta(days=8))["deleted"] == 0


def test_profile_healthcheck_and_boundaries_fail_closed(tmp_path: Path) -> None:
    profile, _calls = profile_fixture(tmp_path)
    checked = media_jobs.healthcheck(profile)
    assert checked["status"] == "succeeded"
    assert set(checked["checks"]) == {"direct", "douyin", "youtube", "bilibili"}

    missing_path = tmp_path / "missing-profile.json"
    missing_path.write_text(
        json.dumps({"schema_version": "2.0", "run_root": str(tmp_path / "runs-missing"), "backends": {}}),
        encoding="utf-8",
    )
    missing = media_jobs.load_media_profile(missing_path, repo_root=tmp_path / "repo")
    result = media_jobs.fetch(
        missing,
        write_request(tmp_path, "missing-job", [{"url": "https://youtu.be/missing"}]),
        now=NOW,
    )
    assert read_manifest(result)[0]["error_code"] == "backend_unavailable"

    repo = tmp_path / "repo"
    repo.mkdir()
    unsafe_profile = tmp_path / "unsafe-profile.json"
    unsafe_profile.write_text(
        json.dumps({"schema_version": "2.0", "run_root": str(repo / "media"), "backends": {}}), encoding="utf-8"
    )
    with pytest.raises(media_jobs.MediaJobError, match="overlap"):
        media_jobs.load_media_profile(unsafe_profile, repo_root=repo)
    assert media_jobs._classify_source("https://evilyoutube.com/watch/1", "") == "unsupported"
    assert media_jobs._classify_source("http://127.0.0.1/internal", "youtube") == "unsupported"


def test_cli_emits_one_json_envelope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _profile, _calls = profile_fixture(tmp_path)
    monkeypatch.setattr(
        media_jobs,
        "_download_direct",
        lambda _url, output, _timeout, _max_bytes: output.write_bytes(b"video"),
    )
    request = write_request(tmp_path, "cli-job", [{"url": "https://media.example/video.mp4"}])

    exit_code = media_jobs.main(["--profile", str(tmp_path / "profile.json"), "fetch", "--request", str(request)])
    output = capsys.readouterr()

    assert exit_code == 0
    assert output.err == ""
    assert len(output.out.splitlines()) == 1
    assert json.loads(output.out)["status"] == "succeeded"


def test_direct_download_pins_each_redirect_to_its_validated_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolver_calls: list[tuple[str, int]] = []
    addresses = {"first.example": "8.8.8.8", "second.example": "1.1.1.1"}

    def resolve(host: str, port: int, *, type: int) -> list[tuple[object, ...]]:
        resolver_calls.append((host, port))
        return [(media_jobs.socket.AF_INET, type, 6, "", (addresses[host], port))]

    responses = [
        FakeHTTPResponse(302, {"Location": "http://second.example/video.mp4"}),
        FakeHTTPResponse(200, {"Content-Type": "video/mp4", "Content-Length": "5"}, [b"video", b""]),
    ]
    connections: list[tuple[str, str, int]] = []

    class FakeConnection:
        def __init__(self, host: str, address: str, port: int, *, timeout: int):
            connections.append((host, address, port))

        def request(self, _method: str, _target: str, *, headers: dict[str, str]) -> None:
            assert headers["User-Agent"] == "TrendRadarMedia/2.0"

        def getresponse(self) -> FakeHTTPResponse:
            return responses.pop(0)

        def close(self) -> None:
            pass

    monkeypatch.setattr(media_jobs.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(media_jobs, "_PinnedHTTPConnection", FakeConnection)
    output = tmp_path / "video.mp4"

    media_jobs._download_direct("http://first.example/start", output, timeout=5, max_bytes=100)

    assert output.read_bytes() == b"video"
    assert resolver_calls == [("first.example", 80), ("second.example", 80)]
    assert connections == [("first.example", "8.8.8.8", 80), ("second.example", "1.1.1.1", 80)]


def test_pinned_https_connection_preserves_tls_hostname(monkeypatch: pytest.MonkeyPatch) -> None:
    raw_socket = object()
    wrapped_socket = object()
    observed: dict[str, object] = {}

    class FakeContext:
        def wrap_socket(self, sock: object, *, server_hostname: str) -> object:
            observed.update(sock=sock, server_hostname=server_hostname)
            return wrapped_socket

    monkeypatch.setattr(media_jobs, "_connect_pinned", lambda address, port, timeout: raw_socket)
    connection = media_jobs._PinnedHTTPSConnection(
        "media.example", "8.8.8.8", 443, timeout=5, context=FakeContext()
    )

    connection.connect()

    assert connection.host == "media.example"
    assert connection.sock is wrapped_socket
    assert observed == {"sock": raw_socket, "server_hostname": "media.example"}


class FakeHTTPResponse:
    def __init__(self, status: int, headers: dict[str, str], chunks: list[bytes] | None = None):
        self.status = status
        self.headers = headers
        self.chunks = list(chunks or [])

    def getheader(self, name: str, default: str | None = None) -> str | None:
        return self.headers.get(name, default)

    def read(self, _size: int) -> bytes:
        return self.chunks.pop(0) if self.chunks else b""

    def close(self) -> None:
        pass


def profile_fixture(tmp_path: Path) -> tuple[dict[str, object], Path]:
    adapter = tmp_path / "adapter.py"
    calls = tmp_path / "adapter-calls.txt"
    adapter.write_text(
        """from pathlib import Path
import sys
if sys.argv[1] == '--probe':
    print('fixture-adapter 2.0')
else:
    url, output, calls = sys.argv[1:]
    with Path(calls).open('a', encoding='utf-8') as handle:
        handle.write(url + '\\n')
    print(url, file=sys.stderr)
    Path(output).write_bytes(b'external-video')
""",
        encoding="utf-8",
    )
    backend = {
        "argv": [sys.executable, str(adapter), "{url}", "{output}", str(calls)],
        "probe_argv": [sys.executable, str(adapter), "--probe"],
    }
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "run_root": str(tmp_path / "runs"),
                "timeout_seconds": 10,
                "max_bytes": 1024,
                "backends": {platform: backend for platform in ("douyin", "youtube", "bilibili")},
            }
        ),
        encoding="utf-8",
    )
    return media_jobs.load_media_profile(profile_path, repo_root=tmp_path / "repo"), calls


def write_request(tmp_path: Path, job_id: str, sources: list[dict[str, str]]) -> Path:
    path = tmp_path / f"{job_id}-request.json"
    path.write_text(json.dumps({"schema_version": "2.0", "job_id": job_id, "sources": sources}), encoding="utf-8")
    return path


def read_manifest(envelope: dict[str, object]) -> list[dict[str, object]]:
    return [json.loads(line) for line in Path(str(envelope["manifest_ref"])).read_text().splitlines()]


def valid_media_row(row: dict[str, object]) -> bool:
    path = Path(str(row["local_media_path"]))
    return (
        path.is_file()
        and path.stat().st_size == row["media_size_bytes"]
        and media_jobs._sha256_file(path) == row["media_hash"]
    )
