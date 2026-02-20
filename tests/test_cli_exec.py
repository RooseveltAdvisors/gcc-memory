import sys

from typer.testing import CliRunner

from gcc_memory.cli import app
from gcc_memory.store import ContextStore


def test_exec_command_records_output(tmp_path):
    runner = CliRunner()
    root = tmp_path
    result = runner.invoke(app, ["--root", str(root), "init", "--description", "demo"])
    assert result.exit_code == 0

    cmd = [sys.executable, "-c", "print('hello from exec')"]
    result = runner.invoke(app, ["--root", str(root), "exec", *cmd])
    assert result.exit_code == 0

    store = ContextStore(root)
    events = store.recent_events("main", 1)
    assert events
    record = events[-1]
    assert "hello from exec" in record["body"]
    assert record["tags"] == ["exec"]


def test_export_command(tmp_path):
    runner = CliRunner()
    root = tmp_path
    result = runner.invoke(app, ["--root", str(root), "init", "--description", "demo"])
    assert result.exit_code == 0

    result = runner.invoke(app, ["--root", str(root), "log", "--message", "Did work"])
    assert result.exit_code == 0

    output = root / "bundle.md"
    result = runner.invoke(app, ["--root", str(root), "export", "--output", str(output)])
    assert result.exit_code == 0
    text = output.read_text(encoding="utf-8")
    assert "# gcc-memory Export" in text
    assert "Did work" in text
