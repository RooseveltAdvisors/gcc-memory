from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

import typer
import yaml

from . import adapters
from .server import serve_http
from .store import ContextStore, Event
from .ws import serve_ws

app = typer.Typer(add_completion=False, help="gcc-memory - shared file memory for local agents")
branch_app = typer.Typer(help="Manage branches")
ingest_app = typer.Typer(help="Import transcripts from other agents")
metadata_app = typer.Typer(help="Manage metadata segments")
app.add_typer(branch_app, name="branch")
app.add_typer(ingest_app, name="ingest")
app.add_typer(metadata_app, name="metadata")


def _store(ctx: typer.Context) -> ContextStore:
    ctx.ensure_object(dict)
    root = ctx.obj.get("root")
    if root is None:
        root = Path.cwd()
    return ContextStore(root)


def _read_body(message: Optional[str], from_file: Optional[Path]) -> str:
    if message:
        return message.strip()
    if from_file:
        return from_file.read_text(encoding="utf-8").strip()
    if not sys.stdin.isatty():
        data = sys.stdin.read().strip()
        if data:
            return data
    raise typer.BadParameter("Provide --message, --from-file or pipe content into stdin")


def _build_details(body: str, artifacts: List[Path]) -> str:
    chunks = [body]
    for artifact in artifacts:
        text = artifact.read_text(encoding="utf-8")
        lang = artifact.suffix.lstrip(".")
        lang_hint = lang if lang else ""
        truncated = text if len(text) <= 4000 else text[:4000] + "\n... [truncated]"
        chunks.append(f"### Artifact: {artifact}\n```{lang_hint}\n{truncated}\n```")
    return "\n\n".join(chunk for chunk in chunks if chunk.strip())


def _default_agent() -> str:
    return os.environ.get("GCC_MEMORY_AGENT", "agent")


def _ingest_events(ctx: typer.Context, events: List[Event], branch: Optional[str]) -> tuple[int, str]:
    store = _store(ctx)
    target = branch or store.get_active_branch()
    for event in events:
        store.append_event(target, event)
    return len(events), target


@app.callback()
def cli(ctx: typer.Context, root: Path = typer.Option(Path.cwd(), help="Workspace root")) -> None:
    ctx.obj = {"root": root}


@app.command()
def init(
    ctx: typer.Context,
    description: str = typer.Option("", help="Project-wide intent"),
    force: bool = typer.Option(False, help="Overwrite existing .gcc"),
) -> None:
    store = _store(ctx)
    store.init(description=description, force=force)
    typer.echo(f"Initialised gcc-memory at {store.root}")


@branch_app.command("list")
def branch_list(ctx: typer.Context) -> None:
    store = _store(ctx)
    for name in store.list_branches():
        prefix = "*" if name == store.get_active_branch() else "-"
        typer.echo(f"{prefix} {name}")


@branch_app.command("create")
def branch_create(
    ctx: typer.Context,
    name: str,
    parent: Optional[str] = typer.Option(None, help="Parent branch"),
    summary: Optional[str] = typer.Option(None, help="Short intent"),
    activate: bool = typer.Option(False, help="Switch active branch"),
) -> None:
    store = _store(ctx)
    store.create_branch(name, parent=parent, summary=summary, activate=activate)
    typer.echo(f"Created branch {name}")


@branch_app.command("checkout")
def branch_checkout(ctx: typer.Context, name: str) -> None:
    store = _store(ctx)
    store.set_active_branch(name)
    typer.echo(f"Active branch set to {name}")


@branch_app.command("delete")
def branch_delete(ctx: typer.Context, name: str) -> None:
    store = _store(ctx)
    store.delete_branch(name)
    typer.echo(f"Deleted branch {name}")


@app.command()
def log(
    ctx: typer.Context,
    message: Optional[str] = typer.Option(None, "--message", "-m", help="Short description"),
    from_file: Optional[Path] = typer.Option(None, help="Load description from file"),
    branch: Optional[str] = typer.Option(None, help="Branch override"),
    tags: List[str] = typer.Option([], help="Tag labels"),
    channel: str = typer.Option("shell", help="Source channel"),
    agent: str = typer.Option(None, help="Agent name (defaults to env GCC_MEMORY_AGENT)"),
    artifact: List[Path] = typer.Option([], help="Attach file contents"),
) -> None:
    store = _store(ctx)
    body = _read_body(message, from_file)
    details = _build_details(body, artifact)
    target_branch = branch or store.get_active_branch()
    event = Event(
        agent=agent or _default_agent(),
        channel=channel,
        tags=tuple(tags),
        summary=body.splitlines()[0] if body else "",
        details=details,
    )
    store.append_event(target_branch, event)
    typer.echo(f"Logged event to {target_branch}")


@app.command()
def commit(
    ctx: typer.Context,
    title: str = typer.Option("Auto context commit", help="Commit title"),
    notes: Optional[str] = typer.Option(None, help="Manual notes"),
    include_last: int = typer.Option(5, help="Events to summarise when notes omitted"),
    branch: Optional[str] = typer.Option(None, help="Branch override"),
    git: bool = typer.Option(False, "--git", help="Also create a Git commit"),
) -> None:
    store = _store(ctx)
    target_branch = branch or store.get_active_branch()
    stamp = store.commit(target_branch, title, notes, include_last=include_last, git_commit=git)
    typer.echo(f"Committed {target_branch} at {stamp}")
    if git:
        typer.echo("Git commit created.")
    typer.echo("Consider: does main.md need updating? Run `gcc-memory update-main` if goals or decisions changed.")


@app.command()
def merge(
    ctx: typer.Context,
    source: str = typer.Argument(..., help="Source branch"),
    target: Optional[str] = typer.Option(None, help="Target branch"),
    git: bool = typer.Option(False, "--git", help="Also create a Git commit"),
) -> None:
    store = _store(ctx)
    dest = target or store.get_active_branch()
    target_ctx = store.merge(source, dest, git_commit=git)
    typer.echo(f"Merged {source} -> {dest}")
    if git:
        typer.echo("Git commit created.")
    # Show target branch context that was retrieved before merge (paper requirement)
    if target_ctx.get("progress_summary"):
        typer.echo(f"Target branch progress: {target_ctx['progress_summary'][:200]}")
    typer.echo("Consider: update Active Decisions in main.md with what was decided. Run `gcc-memory update-main`.")


@app.command()
def context(
    ctx: typer.Context,
    branch: Optional[str] = typer.Option(None, "--branch", "-b", help="Show branch detail"),
    commit_index: Optional[int] = typer.Option(None, "--commit", "-c", help="Show specific commit (0=latest)"),
    log: bool = typer.Option(False, "--log", "-l", help="Show execution log"),
    metadata: Optional[str] = typer.Option(None, "--metadata", "-m", help="Show metadata segment (or 'all')"),
    offset: int = typer.Option(0, help="Pagination offset"),
    limit: int = typer.Option(10, help="Items per page"),
    output: Optional[Path] = typer.Option(None, help="Write JSON snapshot"),
) -> None:
    store = _store(ctx)

    # Determine which mode to use
    if commit_index is not None:
        data = store.context_commit(branch, commit_index)
        if output:
            output.write_text(json.dumps(data, indent=2), encoding="utf-8")
            typer.echo(f"Snapshot saved to {output}")
            return
        c = data.get("commit")
        if c is None:
            typer.echo(f"No commit at index {commit_index}")
        else:
            typer.echo(c.get("header", ""))
            typer.echo(c.get("body", ""))
        return

    if log:
        data = store.context_log(branch, offset=offset, limit=limit)
        if output:
            output.write_text(json.dumps(data, indent=2), encoding="utf-8")
            typer.echo(f"Snapshot saved to {output}")
            return
        typer.echo(f"Log for {data['branch']} ({data['total']} total, offset {data['offset']}):")
        for event in data["events"]:
            typer.echo(f"- {event.get('timestamp')} | {event.get('agent')}: {event.get('summary')}")
        return

    if metadata is not None:
        seg = None if metadata == "all" else metadata
        data = store.context_metadata(seg, branch=branch)
        if output:
            output.write_text(json.dumps(data, indent=2), encoding="utf-8")
            typer.echo(f"Snapshot saved to {output}")
            return
        typer.echo(yaml.dump(data, sort_keys=False).rstrip())
        return

    if branch is not None:
        data = store.context_branch(branch, offset=offset, limit=limit)
        if output:
            output.write_text(json.dumps(data, indent=2), encoding="utf-8")
            typer.echo(f"Snapshot saved to {output}")
            return
        typer.echo(f"Branch: {data['branch']}")
        typer.echo(f"Purpose: {data['purpose']}")
        if data["progress_summary"]:
            typer.echo(f"\nProgress: {data['progress_summary']}")
        typer.echo(f"\nCommits ({data['commit_count']} total):")
        for c in data["commits"]:
            typer.echo(c.get("header", ""))
            body = c.get("body", "")
            if body:
                # Show first 3 lines of body
                preview = "\n".join(body.splitlines()[:3])
                typer.echo(preview)
            typer.echo("")
        return

    # Default: status overview
    data = store.context_status()
    if output:
        output.write_text(json.dumps(data, indent=2), encoding="utf-8")
        typer.echo(f"Snapshot saved to {output}")
        return
    typer.echo(data.get("project", "(no project context)"))
    typer.echo(f"\nActive branch: {data['active_branch']}")
    typer.echo("\nBranches:")
    for b, summary in data.get("branches", {}).items():
        prefix = "*" if b == data["active_branch"] else "-"
        typer.echo(f"  {prefix} {b}: {summary}")


@app.command()
def export(
    ctx: typer.Context,
    branch: Optional[str] = typer.Option(None, help="Branch to export"),
    limit: int = typer.Option(10, help="Events to include"),
    output: Path = typer.Option(Path("context-export.md"), help="Destination file"),
) -> None:
    store = _store(ctx)
    snap = store.snapshot(branch, limit=limit)
    lines = [
        f"# gcc-memory Export ({snap['branch']})",
        "## main.md",
        snap["main"].strip() or "(empty)",
        "## commit.md",
        snap["commit_log"].strip() or "(empty)",
        "## Recent Events",
    ]
    for event in snap["events"]:
        lines.append(f"- [{event.get('timestamp', '')}] {event.get('agent', '')}: {event.get('summary', '')}")
    output.write_text("\n\n".join(lines) + "\n", encoding="utf-8")
    typer.echo(f"Exported branch {snap['branch']} to {output}")


@app.command()
def serve(
    ctx: typer.Context,
    host: str = typer.Option("127.0.0.1", help="Bind host"),
    port: int = typer.Option(8123, help="Port"),
    branch: Optional[str] = typer.Option(None, help="Default branch for GET /context"),
) -> None:
    store = _store(ctx)
    typer.echo(f"Serving gcc-memory on http://{host}:{port}")
    serve_http(store, host=host, port=port, branch=branch)


@app.command()
def ws(
    ctx: typer.Context,
    host: str = typer.Option("127.0.0.1", help="Bind host"),
    port: int = typer.Option(8765, help="WebSocket port"),
    branch: Optional[str] = typer.Option(None, help="Default branch for WS streams"),
) -> None:
    store = _store(ctx)
    typer.echo(f"Serving WebSocket relay on ws://{host}:{port}/<branch>")
    serve_ws(store, host=host, port=port, branch=branch)


@app.command()
def tail(
    ctx: typer.Context,
    branch: Optional[str] = typer.Option(None, help="Branch to monitor"),
    limit: int = typer.Option(20, help="Initial events"),
) -> None:
    store = _store(ctx)
    target = branch or store.get_active_branch()
    seen = 0
    history = store.recent_events(target, limit)
    for event in history:
        typer.echo(f"[history] {event.get('timestamp')} | {event.get('summary')}")
        seen += 1
    typer.echo("--- Live tail; press Ctrl+C to stop ---")
    log_path = store.branch_path(target) / "log.md"
    try:
        while True:
            text = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
            docs = list(yaml.safe_load_all(text)) if text.strip() else []
            if len(docs) > seen:
                for event in docs[seen:]:
                    typer.echo(f"{event.get('timestamp')} | {event.get('agent')}: {event.get('summary')}")
                seen = len(docs)
            time.sleep(1)
    except KeyboardInterrupt:
        typer.echo("Stopped tail")


@app.command("exec", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def exec_command(
    ctx: typer.Context,
    agent: Optional[str] = typer.Option(None, help="Agent label"),
    channel: str = typer.Option("shell", help="Channel label"),
    tags: List[str] = typer.Option([], help="Tag labels"),
    branch: Optional[str] = typer.Option(None, help="Branch override"),
    summary: Optional[str] = typer.Option(None, help="Custom summary override"),
) -> None:
    command = list(ctx.args)
    if not command:
        raise typer.BadParameter("Provide a command to execute")
    quoted = " ".join(shlex.quote(part) for part in command)
    result = subprocess.run(command, capture_output=True, text=True)
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()

    def format_block(title: str, content: str) -> str:
        if not content:
            return ""
        snippet = content if len(content) <= 8000 else content[:8000] + "\n... [truncated]"
        return f"### {title}\n```\n{snippet}\n```"

    details_parts = [f"$ {quoted}", f"exit: {result.returncode}"]
    if stdout:
        details_parts.append(format_block("stdout", stdout))
    if stderr:
        details_parts.append(format_block("stderr", stderr))
    details = "\n\n".join(details_parts)
    summary_text = summary or (stdout.splitlines()[0] if stdout else f"{command[0]} (exit {result.returncode})")
    store = _store(ctx)
    target = branch or store.get_active_branch()
    event = Event(
        agent=agent or _default_agent(),
        channel=channel,
        tags=tuple(tags) or ("exec",),
        summary=summary_text,
        details=details,
    )
    store.append_event(target, event)
    typer.echo(f"Logged command output to {target}")
    raise typer.Exit(result.returncode)


@metadata_app.command("update")
def metadata_update(
    ctx: typer.Context,
    segment: Optional[str] = typer.Option(None, "--segment", "-s", help="Segment to update (file_structure, dependencies, env_config)"),
    branch: Optional[str] = typer.Option(None, "--branch", "-b", help="Write to branch metadata instead of root"),
) -> None:
    store = _store(ctx)
    if branch:
        store.update_branch_metadata(branch, segment)
    else:
        store.update_metadata(segment)
    label = segment or "all segments"
    target = f"branch {branch}" if branch else "root"
    typer.echo(f"Updated {target} metadata: {label}")


@app.command("update-main")
def update_main(
    ctx: typer.Context,
    section: str = typer.Option(..., "--section", "-s", help="Section heading to update (e.g. 'Active Decisions')"),
    content: str = typer.Option(..., "--content", "-c", help="New content for the section"),
) -> None:
    """Update a section in the curated portion of main.md."""
    store = _store(ctx)
    store.update_main_section(section, content)
    typer.echo(f"Updated main.md section: {section}")


@ingest_app.command("codex")
def ingest_codex(
    ctx: typer.Context,
    path: Path,
    branch: Optional[str] = typer.Option(None, help="Branch override"),
) -> None:
    events = adapters.parse_codex(path)
    count, target = _ingest_events(ctx, events, branch)
    typer.echo(f"Imported {count} Codex events into {target}")


@ingest_app.command("claude")
def ingest_claude(
    ctx: typer.Context,
    path: Path,
    branch: Optional[str] = typer.Option(None, help="Branch override"),
) -> None:
    events = adapters.parse_claude(path)
    count, target = _ingest_events(ctx, events, branch)
    typer.echo(f"Imported {count} Claude events into {target}")


@ingest_app.command("opencode")
def ingest_opencode(
    ctx: typer.Context,
    path: Path,
    branch: Optional[str] = typer.Option(None, help="Branch override"),
) -> None:
    events = adapters.parse_opencode(path)
    count, target = _ingest_events(ctx, events, branch)
    typer.echo(f"Imported {count} OpenCode events into {target}")
