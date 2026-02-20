from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence
import shutil
import textwrap

import yaml

from .utils import atomic_write, file_lock, iso_now


@dataclass(slots=True)
class Event:
    agent: str
    channel: str
    tags: Sequence[str] = field(default_factory=tuple)
    summary: str = ""
    details: str = ""
    payload: Dict[str, Any] | None = None
    timestamp: str | None = None
    observation: str | None = None
    thought: str | None = None
    action: str | None = None

    def as_record(self, branch: str) -> Dict[str, Any]:
        stamp = self.timestamp or iso_now()
        record: Dict[str, Any] = {
            "kind": "event",
            "timestamp": stamp,
            "agent": self.agent,
            "channel": self.channel,
            "tags": list(self.tags),
            "summary": self.summary,
            "body": self.details.strip(),
            "branch": branch,
        }
        if self.payload:
            record["payload"] = self.payload
        if self.observation:
            record["observation"] = self.observation
        if self.thought:
            record["thought"] = self.thought
        if self.action:
            record["action"] = self.action
        return record


class ContextStore:
    def __init__(self, workspace: str | Path):
        self.workspace = Path(workspace)
        self.root = self.workspace / ".gcc"
        self.branches_dir = self.root / "branches"
        self.lock_dir = self.root / ".locks"

    # ------------------------------------------------------------------
    # Initialization & Metadata helpers
    # ------------------------------------------------------------------
    def init(self, description: str = "", force: bool = False) -> None:
        if self.root.exists():
            if not force:
                raise RuntimeError("gcc-memory already initialised; use --force to reinit")
            shutil.rmtree(self.root)
        self.branches_dir.mkdir(parents=True, exist_ok=True)
        self.lock_dir.mkdir(parents=True, exist_ok=True)

        metadata = {
            "version": "0.1.0",
            "created_at": iso_now(),
            "updated_at": iso_now(),
            "default_branch": "main",
            "active_branch": "main",
            "branches": {},
            "description": description or "Shared agent memory",
        }
        self._write_root_metadata(metadata)

        main_md = textwrap.dedent(
            f"""
            # Project Context

            ## Purpose
            {description or 'Describe your long-lived goals here.'}

            ## Milestones
            - (none yet)

            ## To-Do
            - (none yet)

            ## Active Decisions
            - (none yet)

            ## Pending Questions
            - (none yet)

            <!-- AUTO-UPDATED BELOW - Do not edit below this line -->
            ## Status
            - Active branch: main
            - Branches: main

            ## Recent Highlights
            (no events yet)
            """
        ).strip() + "\n"
        atomic_write(self.root / "main.md", main_md)

        self.create_branch("main", parent=None, summary="Default working branch", activate=True)
        self.update_branch_metadata("main")
        self.update_metadata()

    def _root_metadata_path(self) -> Path:
        return self.root / "metadata.yaml"

    def _load_root_metadata(self) -> Dict[str, Any]:
        path = self._root_metadata_path()
        if not path.exists():
            return {}
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    def _write_root_metadata(self, data: Dict[str, Any]) -> None:
        data["updated_at"] = iso_now()
        atomic_write(self._root_metadata_path(), yaml.safe_dump(data, sort_keys=False))

    # ------------------------------------------------------------------
    # Branch management
    # ------------------------------------------------------------------
    def list_branches(self) -> List[str]:
        if not self.branches_dir.exists():
            return []
        return sorted(p.name for p in self.branches_dir.iterdir() if p.is_dir())

    def branch_path(self, name: str) -> Path:
        return self.branches_dir / name

    def branch_metadata_path(self, name: str) -> Path:
        return self.branch_path(name) / "metadata.yaml"

    def _load_branch_metadata(self, name: str) -> Dict[str, Any]:
        path = self.branch_metadata_path(name)
        if not path.exists():
            return {}
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    def _write_branch_metadata(self, name: str, data: Dict[str, Any]) -> None:
        data["updated_at"] = iso_now()
        atomic_write(self.branch_metadata_path(name), yaml.safe_dump(data, sort_keys=False))

    def create_branch(
        self,
        name: str,
        *,
        parent: str | None = None,
        summary: str | None = None,
        activate: bool = False,
    ) -> None:
        branch_dir = self.branch_path(name)
        if branch_dir.exists():
            raise RuntimeError(f"Branch '{name}' already exists")

        branch_dir.mkdir(parents=True, exist_ok=True)
        # Paper: "creates an empty log.md to track new OTA cycles"
        log_path = branch_dir / "log.md"
        log_path.touch()
        # Paper: "initializes a new commit.md, prompting the agent to write
        # an explanation of the branch's purpose and motivation"
        commit_path = branch_dir / "commit.md"
        purpose_text = summary or "(branch purpose to be defined)"
        initial_entry = (
            f"### Commit: Branch created ({iso_now()} | {name})\n\n"
            f"**Branch Purpose:** {purpose_text}\n\n"
            f"**Previous Progress Summary:** Initial branch — no prior progress.\n\n"
            f"**This Commit's Contribution:**\n"
            f"Branch created to {purpose_text.lower().rstrip('.')}.\n\n"
        )
        atomic_write(commit_path, initial_entry)

        branch_meta = {
            "name": name,
            "parent": parent,
            "created_at": iso_now(),
            "summary": summary or "",
            "commit_count": 0,
            "last_commit_at": None,
        }
        self._write_branch_metadata(name, branch_meta)

        root_meta = self._load_root_metadata()
        root_meta.setdefault("branches", {})[name] = {
            "created_at": branch_meta["created_at"],
            "parent": parent,
            "summary": summary or "",
        }
        if activate or not root_meta.get("active_branch"):
            root_meta["active_branch"] = name
        self._write_root_metadata(root_meta)

    def delete_branch(self, name: str) -> None:
        if name == "main":
            raise RuntimeError("Cannot delete main branch")
        branch_dir = self.branch_path(name)
        if branch_dir.exists():
            shutil.rmtree(branch_dir)
        root_meta = self._load_root_metadata()
        root_meta.get("branches", {}).pop(name, None)
        if root_meta.get("active_branch") == name:
            root_meta["active_branch"] = "main"
        self._write_root_metadata(root_meta)

    def get_active_branch(self) -> str:
        meta = self._load_root_metadata()
        return meta.get("active_branch") or meta.get("default_branch", "main")

    def set_active_branch(self, name: str) -> None:
        if name not in self.list_branches():
            raise RuntimeError(f"Unknown branch '{name}'")
        meta = self._load_root_metadata()
        meta["active_branch"] = name
        self._write_root_metadata(meta)

    # ------------------------------------------------------------------
    # Logging helpers
    # ------------------------------------------------------------------
    def _branch_log_path(self, name: str) -> Path:
        return self.branch_path(name) / "log.md"

    def _branch_commit_path(self, name: str) -> Path:
        return self.branch_path(name) / "commit.md"

    def append_event(self, branch: str, event: Event) -> None:
        if branch not in self.list_branches():
            raise RuntimeError(f"Unknown branch '{branch}'")
        record = event.as_record(branch)
        self._write_log_record(branch, record)

    def _write_log_record(self, branch: str, record: Dict[str, Any]) -> None:
        log_path = self._branch_log_path(branch)
        lock_path = self.lock_dir / f"{branch}.log.lock"
        record.setdefault("timestamp", iso_now())
        with file_lock(lock_path):
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write("---\n")
                yaml.safe_dump(record, handle, sort_keys=False)
        branch_meta = self._load_branch_metadata(branch)
        branch_meta["last_event_at"] = record["timestamp"]
        self._write_branch_metadata(branch, branch_meta)

    def recent_events(self, branch: str, limit: int = 5) -> List[Dict[str, Any]]:
        events = list(self.iter_events(branch))
        return events[-limit:]

    def iter_events(self, branch: str) -> Iterable[Dict[str, Any]]:
        log_path = self._branch_log_path(branch)
        if not log_path.exists():
            return []
        text = log_path.read_text(encoding="utf-8")
        if not text.strip():
            return []
        return list(yaml.safe_load_all(text))

    # ------------------------------------------------------------------
    # Commit & merge helpers
    # ------------------------------------------------------------------
    def commit(
        self,
        branch: str,
        title: str,
        notes: str | None = None,
        *,
        include_last: int = 5,
        git_commit: bool = False,
    ) -> str:
        if branch not in self.list_branches():
            raise RuntimeError(f"Unknown branch '{branch}'")
        timestamp = iso_now()
        if notes is not None:
            body = notes.strip()
        else:
            body = self._build_commit_blocks(branch, include_last)
        entry = f"### Commit: {title} ({timestamp} | {branch})\n\n{body}\n\n"
        commit_path = self._branch_commit_path(branch)
        lock_path = self.lock_dir / f"{branch}.commit.lock"
        with file_lock(lock_path):
            with commit_path.open("a", encoding="utf-8") as handle:
                handle.write(entry)
        meta = self._load_branch_metadata(branch)
        meta["commit_count"] = int(meta.get("commit_count") or 0) + 1
        meta["last_commit_at"] = timestamp
        self._write_branch_metadata(branch, meta)

        if git_commit:
            self._git_commit(title)

        return timestamp

    def _git_commit(self, message: str) -> bool:
        """Create a Git commit in the workspace with all staged + unstaged changes."""
        try:
            subprocess.run(
                ["git", "add", "-A"],
                cwd=self.workspace, capture_output=True, timeout=30,
            )
            result = subprocess.run(
                ["git", "commit", "-m", message, "--allow-empty"],
                cwd=self.workspace, capture_output=True, timeout=30,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    _HOOK_CHANNELS = frozenset({"claude-hook", "codex-hook"})

    def _build_commit_blocks(self, branch: str, include_last: int) -> str:
        branch_meta = self._load_branch_metadata(branch)
        purpose = branch_meta.get("summary") or "General working branch"

        last_blocks = self._get_last_commit_blocks(branch)
        progress = self._synthesize_progress(last_blocks, branch, include_last)

        # Fetch extra events and filter to action-only hook events
        all_events = self.recent_events(branch, include_last * 3)
        action_events: List[Dict[str, Any]] = []
        for ev in all_events:
            tags = ev.get("tags", [])
            ch = ev.get("channel", "")
            # Skip observation/thought events; only include actions
            if "observation" in tags or "thought" in tags:
                continue
            # For hook channels, always include; for others, include all
            if ch in self._HOOK_CHANNELS or ch not in self._HOOK_CHANNELS:
                action_events.append(ev)
        events = action_events[-include_last:]

        if events:
            contribution_lines = []
            for event in events:
                agent = event.get("agent", "agent")
                summary = event.get("summary") or "(no summary)"
                contribution_lines.append(f"- {agent}: {summary}")
            contribution = "\n".join(contribution_lines)
        else:
            contribution = "No events since last commit."

        return (
            f"**Branch Purpose:** {purpose}\n\n"
            f"**Previous Progress Summary:** {progress}\n\n"
            f"**This Commit's Contribution:**\n{contribution}"
        )

    def _get_last_commit_blocks(self, branch: str) -> Dict[str, str]:
        commit_path = self._branch_commit_path(branch)
        if not commit_path.exists():
            return {}
        text = commit_path.read_text(encoding="utf-8")
        if not text.strip():
            return {}
        # Parse the last commit entry (split on ### Commit:)
        sections = text.split("### Commit:")
        if len(sections) < 2:
            return {}
        last_section = sections[-1]
        blocks: Dict[str, str] = {}
        for key in ("Branch Purpose", "Previous Progress Summary", "This Commit's Contribution"):
            marker = f"**{key}:**"
            idx = last_section.find(marker)
            if idx == -1:
                continue
            start = idx + len(marker)
            # Find the next block marker or end
            next_idx = len(last_section)
            for other_key in ("Branch Purpose", "Previous Progress Summary", "This Commit's Contribution"):
                if other_key == key:
                    continue
                other_marker = f"**{other_key}:**"
                pos = last_section.find(other_marker, start)
                if pos != -1 and pos < next_idx:
                    next_idx = pos
            blocks[key] = last_section[start:next_idx].strip()
        return blocks

    def _synthesize_progress(
        self, last_blocks: Dict[str, str], branch: str, include_last: int
    ) -> str:
        prev_progress = last_blocks.get("Previous Progress Summary", "")
        prev_contribution = last_blocks.get("This Commit's Contribution", "")

        parts: List[str] = []
        if prev_progress:
            parts.append(prev_progress)
        if prev_contribution:
            parts.append(prev_contribution)

        if not parts:
            return "Initial commit — no prior progress."

        combined = " ".join(parts)
        # Truncate to keep progress summaries from growing unbounded
        max_len = 1500
        if len(combined) > max_len:
            combined = combined[:max_len].rsplit(" ", 1)[0] + " ..."
        return combined

    def merge(self, source: str, target: str, *, git_commit: bool = False) -> Dict[str, Any]:
        """Merge source branch into target.

        Per the GCC paper, CONTEXT is called on the target branch before
        merging so the agent sees its historical summaries and planning rationale.

        Returns the target branch context that was retrieved before merge.
        """
        if source == target:
            raise RuntimeError("Source and target branches match")
        if source not in self.list_branches() or target not in self.list_branches():
            raise RuntimeError("Unknown branch in merge")

        # Paper: "Before merging, the controller automatically calls CONTEXT
        # on the target branch to surface its historical summaries."
        target_context = self.context_branch(target)

        events = list(self.iter_events(source))
        for event in events:
            carried = dict(event)
            carried.setdefault("metadata", {})
            carried.setdefault("tags", [])
            carried["tags"].append("merge")
            carried["metadata"]["merged_from"] = source
            # Add origin tag for traceability
            original_summary = carried.get("summary", "")
            carried["summary"] = f"[from {source}] {original_summary}"
            self._write_log_record(target, carried)

        # Build a merge commit with structured blocks
        source_meta = self._load_branch_metadata(source)
        source_purpose = source_meta.get("summary") or source
        recent_source = self.recent_events(source, 5)
        contribution_lines = [f"Merged branch **{source}** ({source_purpose}):"]
        for event in recent_source:
            agent = event.get("agent", "agent")
            summary = event.get("summary") or "(no summary)"
            contribution_lines.append(f"- {agent}: {summary}")

        merge_body = (
            f"**Branch Purpose:** Integrate work from {source}\n\n"
            f"**Previous Progress Summary:** See prior commits on {target}.\n\n"
            f"**This Commit's Contribution:**\n" + "\n".join(contribution_lines)
        )
        self.commit(
            target, f"Merge {source} -> {target}", merge_body,
            include_last=0, git_commit=git_commit,
        )
        return target_context

    # ------------------------------------------------------------------
    # Snapshot helpers
    # ------------------------------------------------------------------
    def snapshot(self, branch: str | None = None, limit: int = 10) -> Dict[str, Any]:
        branch = branch or self.get_active_branch()
        meta = self._load_root_metadata()
        main_text = ""
        main_path = self.root / "main.md"
        if main_path.exists():
            main_text = main_path.read_text(encoding="utf-8")
        branch_meta = self._load_branch_metadata(branch)
        commits = ""
        commit_path = self._branch_commit_path(branch)
        if commit_path.exists():
            commits = commit_path.read_text(encoding="utf-8")
        events = self.recent_events(branch, limit)
        return {
            "root": meta,
            "branch": branch,
            "branch_meta": branch_meta,
            "main": main_text,
            "commit_log": commits,
            "events": events,
        }

    # ------------------------------------------------------------------
    # Multi-level CONTEXT retrieval (paper-aligned)
    # ------------------------------------------------------------------
    def context_status(self) -> Dict[str, Any]:
        """Bare CONTEXT — project overview like ``git status``."""
        meta = self._load_root_metadata()
        main_path = self.root / "main.md"
        main_text = main_path.read_text(encoding="utf-8") if main_path.exists() else ""
        # Extract the curated section (above auto-update marker)
        marker = "<!-- AUTO-UPDATED BELOW"
        curated = main_text.split(marker)[0].strip() if marker in main_text else main_text.strip()

        branches = self.list_branches()
        active = self.get_active_branch()
        branch_summaries: Dict[str, str] = {}
        for b in branches:
            last = self._get_last_commit_blocks(b)
            progress = last.get("This Commit's Contribution", "")
            if not progress:
                bm = self._load_branch_metadata(b)
                progress = bm.get("summary") or "(no commits yet)"
            # First line only
            branch_summaries[b] = progress.strip().splitlines()[0] if progress.strip() else "(no activity)"

        return {
            "project": curated,
            "active_branch": active,
            "branches": branch_summaries,
            "description": meta.get("description", ""),
        }

    def context_branch(
        self, branch: str | None = None, offset: int = 0, limit: int = 10
    ) -> Dict[str, Any]:
        """Branch detail — purpose, progress, and recent commits."""
        branch = branch or self.get_active_branch()
        if branch not in self.list_branches():
            raise RuntimeError(f"Unknown branch '{branch}'")
        branch_meta = self._load_branch_metadata(branch)
        last_blocks = self._get_last_commit_blocks(branch)
        progress = last_blocks.get("Previous Progress Summary", "")

        commits = self._parse_commits(branch)
        total = len(commits)
        page = commits[offset : offset + limit]

        return {
            "branch": branch,
            "purpose": branch_meta.get("summary") or "",
            "progress_summary": progress,
            "commit_count": total,
            "offset": offset,
            "commits": page,
        }

    def context_commit(self, branch: str | None = None, index: int = 0) -> Dict[str, Any]:
        """Full 3-block content of a specific commit (0 = latest)."""
        branch = branch or self.get_active_branch()
        commits = self._parse_commits(branch)
        if not commits:
            return {"branch": branch, "index": index, "commit": None}
        # index 0 = latest
        idx = len(commits) - 1 - index
        if idx < 0 or idx >= len(commits):
            return {"branch": branch, "index": index, "commit": None}
        return {"branch": branch, "index": index, "commit": commits[idx]}

    def context_log(
        self, branch: str | None = None, offset: int = 0, limit: int = 20
    ) -> Dict[str, Any]:
        """Paginated execution trace from log.md."""
        branch = branch or self.get_active_branch()
        events = list(self.iter_events(branch))
        total = len(events)
        page = events[offset : offset + limit]
        return {
            "branch": branch,
            "total": total,
            "offset": offset,
            "events": page,
        }

    def context_metadata(
        self, segment: str | None = None, branch: str | None = None
    ) -> Dict[str, Any]:
        """Return metadata, optionally filtered to a single segment.

        When *branch* is given, reads from the branch-level metadata.yaml
        instead of the root metadata.
        """
        if branch:
            if branch not in self.list_branches():
                raise RuntimeError(f"Unknown branch '{branch}'")
            meta = self._load_branch_metadata(branch)
        else:
            meta = self._load_root_metadata()
        if segment:
            return {"segment": segment, "data": meta.get(segment, {})}
        return meta

    def update_main_section(self, section: str, content: str) -> None:
        """Update a section in the curated portion of main.md (above the marker).

        If the section exists, replaces its content. Otherwise appends it
        just before the auto-update marker.
        """
        main_path = self.root / "main.md"
        if not main_path.exists():
            raise RuntimeError("main.md does not exist — run init first")

        text = main_path.read_text(encoding="utf-8")
        marker = "<!-- AUTO-UPDATED BELOW"

        if marker in text:
            curated, auto = text.split(marker, 1)
            auto = marker + auto
        else:
            curated = text
            auto = ""

        heading = f"## {section}"
        lines = curated.split("\n")
        start_idx = None
        end_idx = None
        for i, line in enumerate(lines):
            if line.strip() == heading:
                start_idx = i
            elif start_idx is not None and line.startswith("## ") and i > start_idx:
                end_idx = i
                break

        new_block = f"{heading}\n{content.strip()}\n"

        if start_idx is not None:
            if end_idx is None:
                end_idx = len(lines)
            lines[start_idx:end_idx] = [new_block]
            curated = "\n".join(lines)
        else:
            # Append before marker
            curated = curated.rstrip() + "\n\n" + new_block + "\n"

        atomic_write(main_path, curated + auto)

    def _parse_commits(self, branch: str) -> List[Dict[str, str]]:
        """Parse commit.md into a list of commit dicts."""
        commit_path = self._branch_commit_path(branch)
        if not commit_path.exists():
            return []
        text = commit_path.read_text(encoding="utf-8")
        if not text.strip():
            return []
        parts = text.split("### Commit:")
        commits: List[Dict[str, str]] = []
        for part in parts[1:]:
            lines = part.strip()
            # Extract title from first line: "title (timestamp | branch)"
            first_line = lines.splitlines()[0] if lines else ""
            body = "\n".join(lines.splitlines()[1:]).strip() if lines else ""
            commits.append({"header": f"### Commit:{first_line}", "body": body})
        # Also handle legacy format "### timestamp | branch | title"
        if not commits:
            parts = text.split("### ")
            for part in parts[1:]:
                lines = part.strip()
                first_line = lines.splitlines()[0] if lines else ""
                body = "\n".join(lines.splitlines()[1:]).strip() if lines else ""
                commits.append({"header": f"### {first_line}", "body": body})
        return commits

    # ------------------------------------------------------------------
    # Metadata enrichment
    # ------------------------------------------------------------------
    def update_metadata(self, segment: str | None = None) -> None:
        """Refresh metadata segments (file_structure, dependencies, env_config)."""
        meta = self._load_root_metadata()
        segments_to_update = [segment] if segment else ["file_structure", "dependencies", "env_config"]

        if "file_structure" in segments_to_update:
            meta["file_structure"] = self._scan_file_structure()
        if "dependencies" in segments_to_update:
            meta["dependencies"] = self._scan_dependencies()
        if "env_config" in segments_to_update:
            meta["env_config"] = self._scan_env_config()

        self._write_root_metadata(meta)

    def update_branch_metadata(self, branch: str, segment: str | None = None) -> None:
        """Refresh metadata segments on a per-branch metadata.yaml."""
        if branch not in self.list_branches():
            raise RuntimeError(f"Unknown branch '{branch}'")
        meta = self._load_branch_metadata(branch)
        segments_to_update = [segment] if segment else ["file_structure", "dependencies", "env_config"]

        if "file_structure" in segments_to_update:
            meta["file_structure"] = self._scan_file_structure()
        if "dependencies" in segments_to_update:
            meta["dependencies"] = self._scan_dependencies()
        if "env_config" in segments_to_update:
            meta["env_config"] = self._scan_env_config()

        self._write_branch_metadata(branch, meta)

    _IGNORE_DIRS = frozenset({
        ".gcc", ".git", "node_modules", ".venv", "venv", "__pycache__",
        ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
        ".next", ".nuxt", "target", ".tox", "egg-info",
    })

    _IGNORE_EXTENSIONS = frozenset({
        ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp", ".bmp",
        ".tiff", ".mp4", ".mp3", ".wav", ".zip", ".tar", ".gz", ".woff",
        ".woff2", ".ttf", ".eot",
    })

    def _scan_file_structure(self, max_files: int = 200) -> List[str]:
        tree: List[str] = []
        count = 0
        for item in sorted(self.workspace.rglob("*")):
            if count >= max_files:
                tree.append("... (truncated)")
                break
            rel = item.relative_to(self.workspace)
            if any(part in self._IGNORE_DIRS for part in rel.parts):
                continue
            if item.is_file():
                if item.suffix.lower() in self._IGNORE_EXTENSIONS:
                    continue
                tree.append(str(rel))
                count += 1
        return tree

    def _scan_dependencies(self) -> Dict[str, Any]:
        deps: Dict[str, Any] = {}
        pyproject = self.workspace / "pyproject.toml"
        if pyproject.exists():
            try:
                import tomllib
            except ModuleNotFoundError:
                import tomli as tomllib  # type: ignore[no-redef]
            try:
                data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
                project = data.get("project", {})
                deps["python"] = {
                    "requires": project.get("dependencies", []),
                    "dev": (
                        data.get("tool", {})
                        .get("uv", {})
                        .get("dev-dependencies", project.get("optional-dependencies", {}).get("dev", []))
                    ),
                }
            except Exception:
                pass
        pkg_json = self.workspace / "package.json"
        if pkg_json.exists():
            try:
                data = json.loads(pkg_json.read_text(encoding="utf-8"))
                deps["node"] = {
                    "dependencies": data.get("dependencies", {}),
                    "devDependencies": data.get("devDependencies", {}),
                }
            except Exception:
                pass
        return deps

    def _scan_env_config(self) -> List[str]:
        env_vars: List[str] = []
        for name in (".env.example", ".env.sample", ".env.template"):
            env_file = self.workspace / name
            if env_file.exists():
                for line in env_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        var_name = line.split("=", 1)[0].strip()
                        env_vars.append(var_name)
                break
        return env_vars
