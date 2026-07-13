"""Tests for zentinull.cli.status — full lifecycle: start/done/fail/freshness/get/print."""

from __future__ import annotations

from pathlib import Path

import zentinull.cli.status as status_mod


def test_record_start_writes_running(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    status_file = data_dir / "status.json"
    monkeypatch.setattr(status_mod, "STATUS_FILE", status_file)

    from zentinull.cli.status import get_status, record_start

    record_start("ingest")
    data = get_status()
    assert data["stages"]["ingest"]["status"] == "running"


def test_record_done_writes_ok(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    status_file = data_dir / "status.json"
    monkeypatch.setattr(status_mod, "STATUS_FILE", status_file)

    from zentinull.cli.status import get_status, record_done, record_start

    record_start("ingest")
    record_done("ingest")
    data = get_status()
    assert data["stages"]["ingest"]["status"] == "ok"
    assert "duration_ms" in data["stages"]["ingest"]


def test_record_done_with_stats(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    status_file = data_dir / "status.json"
    monkeypatch.setattr(status_mod, "STATUS_FILE", status_file)

    from zentinull.cli.status import get_status, record_done, record_start

    record_start("ingest")
    record_done("ingest", rows=100, sources=6)
    data = get_status()
    assert data["stages"]["ingest"]["rows"] == 100
    assert data["stages"]["ingest"]["sources"] == 6


def test_record_done_load_sets_pipeline_time(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    status_file = data_dir / "status.json"
    monkeypatch.setattr(status_mod, "STATUS_FILE", status_file)

    from zentinull.cli.status import get_status, record_done, record_start

    record_start("load")
    record_done("load")
    data = get_status()
    assert "last_full_pipeline" in data


def test_record_fail_writes_error(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    status_file = data_dir / "status.json"
    monkeypatch.setattr(status_mod, "STATUS_FILE", status_file)

    from zentinull.cli.status import get_status, record_fail, record_start

    record_start("ingest")
    record_fail("ingest", "connection refused")
    data = get_status()
    assert data["stages"]["ingest"]["status"] == "fail"
    assert data["stages"]["ingest"]["error"] == "connection refused"


def test_record_freshness_writes(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    status_file = data_dir / "status.json"
    monkeypatch.setattr(status_mod, "STATUS_FILE", status_file)

    from zentinull.cli.status import get_status, record_freshness

    record_freshness("sp", "2026-01-15T10:00:00Z", 500)
    data = get_status()
    assert data["freshness"]["sp"]["row_count"] == 500
    assert data["freshness"]["sp"]["newest_record"] == "2026-01-15T10:00:00Z"


def test_get_status_returns_default_when_empty(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    status_file = data_dir / "status.json"
    monkeypatch.setattr(status_mod, "STATUS_FILE", status_file)

    from zentinull.cli.status import get_status

    data = get_status()
    assert data == {"stages": {}, "freshness": {}}


def test_get_status_returns_data(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    status_file = data_dir / "status.json"
    monkeypatch.setattr(status_mod, "STATUS_FILE", status_file)

    from zentinull.cli.status import get_status, record_done, record_start

    record_start("export")
    record_done("export", total_records=1423)
    data = get_status()
    assert data["stages"]["export"]["status"] == "ok"
    assert data["stages"]["export"]["total_records"] == 1423


def test_print_status_with_stages(monkeypatch, tmp_path: Path, capsys) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    status_file = data_dir / "status.json"
    monkeypatch.setattr(status_mod, "STATUS_FILE", status_file)

    from zentinull.cli.status import print_status, record_done, record_start

    record_start("ingest")
    record_done("ingest", rows=99)
    capsys.readouterr()  # flush any prior output

    print_status()
    captured = capsys.readouterr()
    assert "ingest" in captured.out
    assert "OK" in captured.out


def test_print_status_empty(monkeypatch, tmp_path: Path, capsys) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    status_file = data_dir / "status.json"
    monkeypatch.setattr(status_mod, "STATUS_FILE", status_file)

    from zentinull.cli.status import print_status

    # Ensure no status file exists
    assert not status_file.exists()

    capsys.readouterr()  # flush
    print_status()
    captured = capsys.readouterr()
    assert "No status data" in captured.out
