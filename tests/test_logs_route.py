from pathlib import Path

from web.routes.logs import _tail_lines


def test_tail_lines_returns_latest_lines(tmp_path: Path) -> None:
    log_file = tmp_path / "latest.log"
    log_file.write_text("1\n2\n3\n4\n", encoding="utf-8")
    assert _tail_lines(log_file, 2) == ["3", "4"]
