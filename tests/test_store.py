import json

import yaml

from gcc_memory import ContextStore, Event


def test_init_log_and_commit(tmp_path):
    store = ContextStore(tmp_path)
    store.init("Sample project", force=False)

    event = Event(agent="codex", channel="shell", summary="Ran tests", details="All passed")
    store.append_event("main", event)
    events = store.recent_events("main", 1)
    assert events
    assert events[-1]["summary"] == "Ran tests"

    stamp = store.commit("main", "Checkpoint", notes="Ready to merge", include_last=1)
    snapshot = store.snapshot(limit=1)
    assert snapshot["branch"] == "main"
    assert "Ready to merge" in snapshot["commit_log"]
    assert stamp


def test_commit_3block_format(tmp_path):
    """Auto-generated commits produce the 3-block format."""
    store = ContextStore(tmp_path)
    store.init("Test project", force=False)

    store.append_event("main", Event(agent="dev", channel="shell", summary="Added auth module"))
    store.append_event("main", Event(agent="dev", channel="shell", summary="Wrote unit tests"))
    store.commit("main", "Auth milestone")

    commit_path = store.branch_path("main") / "commit.md"
    text = commit_path.read_text()
    assert "### Commit: Auth milestone" in text
    assert "**Branch Purpose:**" in text
    assert "**Previous Progress Summary:**" in text
    assert "**This Commit's Contribution:**" in text
    assert "Added auth module" in text
    assert "Wrote unit tests" in text


def test_cumulative_progress(tmp_path):
    """Second commit's progress summary includes first commit's contribution."""
    store = ContextStore(tmp_path)
    store.init("Cumulative test", force=False)

    store.append_event("main", Event(agent="a1", channel="sh", summary="Built parser"))
    store.commit("main", "First milestone")

    store.append_event("main", Event(agent="a1", channel="sh", summary="Built formatter"))
    store.commit("main", "Second milestone")

    commit_path = store.branch_path("main") / "commit.md"
    text = commit_path.read_text()
    # The second commit should reference the first commit's contribution
    sections = text.split("### Commit:")
    assert len(sections) >= 3  # header + 2 commits
    second_commit = sections[-1]
    assert "Built parser" in second_commit  # cumulative progress carries forward


def test_context_status(tmp_path):
    store = ContextStore(tmp_path)
    store.init("Status test project", force=False)

    status = store.context_status()
    assert "active_branch" in status
    assert status["active_branch"] == "main"
    assert "branches" in status
    assert "main" in status["branches"]
    assert "Purpose" in status["project"]


def test_context_branch(tmp_path):
    store = ContextStore(tmp_path)
    store.init("Branch detail test", force=False)

    store.append_event("main", Event(agent="dev", channel="sh", summary="Initial work"))
    store.commit("main", "Setup complete")

    result = store.context_branch("main")
    assert result["branch"] == "main"
    assert result["commit_count"] >= 1
    assert len(result["commits"]) >= 1


def test_context_commit(tmp_path):
    store = ContextStore(tmp_path)
    store.init("Commit detail test", force=False)

    store.append_event("main", Event(agent="dev", channel="sh", summary="Did work"))
    store.commit("main", "Test commit")

    result = store.context_commit("main", index=0)
    assert result["commit"] is not None
    assert "Test commit" in result["commit"]["header"]

    # Non-existent index
    result = store.context_commit("main", index=999)
    assert result["commit"] is None


def test_context_log(tmp_path):
    store = ContextStore(tmp_path)
    store.init("Log test", force=False)

    for i in range(5):
        store.append_event("main", Event(agent="dev", channel="sh", summary=f"Event {i}"))

    result = store.context_log("main", offset=0, limit=3)
    assert result["total"] == 5
    assert len(result["events"]) == 3
    assert result["offset"] == 0

    result2 = store.context_log("main", offset=3, limit=3)
    assert len(result2["events"]) == 2  # only 2 remaining


def test_context_metadata(tmp_path):
    store = ContextStore(tmp_path)
    store.init("Metadata test", force=False)

    meta = store.context_metadata()
    assert "version" in meta
    assert meta["description"] == "Metadata test"

    seg = store.context_metadata("description")
    assert seg["segment"] == "description"
    assert seg["data"] == "Metadata test"


def test_main_md_has_marker(tmp_path):
    """init() seeds main.md with the auto-update marker."""
    store = ContextStore(tmp_path)
    store.init("Marker test", force=False)

    main_path = store.root / "main.md"
    text = main_path.read_text()
    assert "<!-- AUTO-UPDATED BELOW" in text
    assert "## Purpose" in text
    assert "## Active Decisions" in text
    assert "## Pending Questions" in text


def test_merge_origin_tags(tmp_path):
    """Merged events get origin tags in their summary."""
    store = ContextStore(tmp_path)
    store.init("Merge test", force=False)
    store.create_branch("feature", parent="main", summary="Feature work")

    store.append_event("feature", Event(agent="dev", channel="sh", summary="Added feature X"))
    store.merge("feature", "main")

    events = list(store.iter_events("main"))
    merged = [e for e in events if "merge" in e.get("tags", [])]
    assert len(merged) >= 1
    assert merged[0]["summary"].startswith("[from feature]")

    # Merge commit should have structured blocks
    commit_path = store.branch_path("main") / "commit.md"
    text = commit_path.read_text()
    assert "Merge feature -> main" in text
    assert "**Branch Purpose:**" in text


def test_update_metadata_file_structure(tmp_path):
    """update_metadata populates file_structure segment."""
    store = ContextStore(tmp_path)
    store.init("Meta enrichment test", force=False)

    # Create a dummy file in the workspace
    (tmp_path / "hello.py").write_text("print('hi')")

    store.update_metadata("file_structure")
    meta = store.context_metadata("file_structure")
    assert "hello.py" in meta["data"]


def test_update_metadata_dependencies_pyproject(tmp_path):
    """update_metadata parses pyproject.toml dependencies."""
    store = ContextStore(tmp_path)
    store.init("Deps test", force=False)

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "test"\ndependencies = ["requests", "pyyaml"]\n'
    )

    store.update_metadata("dependencies")
    meta = store.context_metadata("dependencies")
    assert "python" in meta["data"]
    assert "requests" in meta["data"]["python"]["requires"]


def test_event_ota_fields(tmp_path):
    """Event OTA fields (observation, thought, action) are persisted when set."""
    store = ContextStore(tmp_path)
    store.init("OTA test", force=False)

    event = Event(
        agent="dev",
        channel="sh",
        summary="OTA test",
        observation="Tests failing",
        thought="Need to fix import",
        action="Edited store.py",
    )
    store.append_event("main", event)

    events = store.recent_events("main", 1)
    assert events[-1]["observation"] == "Tests failing"
    assert events[-1]["thought"] == "Need to fix import"
    assert events[-1]["action"] == "Edited store.py"


def test_event_ota_fields_absent(tmp_path):
    """Events without OTA fields work normally (backward compat)."""
    store = ContextStore(tmp_path)
    store.init("No OTA test", force=False)

    event = Event(agent="dev", channel="sh", summary="Plain event")
    store.append_event("main", event)

    events = store.recent_events("main", 1)
    assert "observation" not in events[-1]
    assert "thought" not in events[-1]
    assert "action" not in events[-1]


def test_update_branch_metadata(tmp_path):
    """update_branch_metadata writes file_structure to branch metadata."""
    store = ContextStore(tmp_path)
    store.init("Branch meta test", force=False)

    (tmp_path / "app.py").write_text("print('app')")
    store.update_branch_metadata("main", "file_structure")

    meta = store.context_metadata("file_structure", branch="main")
    assert "app.py" in meta["data"]


def test_context_metadata_branch(tmp_path):
    """context_metadata with branch reads branch-level metadata."""
    store = ContextStore(tmp_path)
    store.init("Branch ctx test", force=False)

    # Branch metadata has name/summary from creation
    meta = store.context_metadata(branch="main")
    assert meta.get("name") == "main"


def test_scan_file_structure_excludes_images(tmp_path):
    """Binary/image files are excluded from file structure scan."""
    store = ContextStore(tmp_path)
    store.init("Image filter test", force=False)

    (tmp_path / "app.py").write_text("code")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG")
    (tmp_path / "icon.svg").write_text("<svg/>")
    (tmp_path / "font.woff2").write_bytes(b"\x00")
    (tmp_path / "data.csv").write_text("a,b,c")

    store.update_metadata("file_structure")
    meta = store.context_metadata("file_structure")
    files = meta["data"]
    assert "app.py" in files
    assert "data.csv" in files
    assert "logo.png" not in files
    assert "icon.svg" not in files
    assert "font.woff2" not in files


def test_commit_filter_excludes_observation_thought(tmp_path):
    """Auto-commit blocks exclude observation/thought tagged events."""
    store = ContextStore(tmp_path)
    store.init("Commit filter test", force=False)

    # Add observation and thought events (should be filtered out)
    store.append_event("main", Event(
        agent="claude", channel="claude-hook",
        tags=("observation", "prompt"), summary="User asked about auth",
        observation="User asked about auth",
    ))
    store.append_event("main", Event(
        agent="claude", channel="claude-hook",
        tags=("thought", "response"), summary="I should implement JWT",
        thought="I should implement JWT",
    ))
    # Add action events (should be included)
    store.append_event("main", Event(
        agent="claude", channel="claude-hook",
        tags=("bash",), summary="Ran pytest", action="Ran pytest",
    ))
    store.append_event("main", Event(
        agent="claude", channel="claude-hook",
        tags=("write",), summary="write: auth.py", action="write: auth.py",
    ))

    store.commit("main", "Filtered commit")
    commit_path = store.branch_path("main") / "commit.md"
    text = commit_path.read_text()

    # Action events should appear in the contribution
    assert "Ran pytest" in text
    assert "write: auth.py" in text
    # Observation/thought events should NOT appear in the contribution
    assert "User asked about auth" not in text
    assert "I should implement JWT" not in text


def test_update_main_section_existing(tmp_path):
    """update_main_section replaces an existing section."""
    store = ContextStore(tmp_path)
    store.init("Main section test", force=False)

    store.update_main_section("Active Decisions", "- Using JWT for auth\n- PostgreSQL for storage")
    main_text = (store.root / "main.md").read_text()
    assert "- Using JWT for auth" in main_text
    assert "- PostgreSQL for storage" in main_text
    # Old placeholder should be gone
    assert "(none yet)" not in main_text.split("Active Decisions")[1].split("##")[0]


def test_update_main_section_new(tmp_path):
    """update_main_section appends a new section if not found."""
    store = ContextStore(tmp_path)
    store.init("New section test", force=False)

    store.update_main_section("Architecture Notes", "Event-driven with file-based storage")
    main_text = (store.root / "main.md").read_text()
    assert "## Architecture Notes" in main_text
    assert "Event-driven with file-based storage" in main_text
    # Auto-update marker should still be intact
    assert "<!-- AUTO-UPDATED BELOW" in main_text


def test_update_main_section_preserves_marker(tmp_path):
    """update_main_section keeps auto-updated section intact."""
    store = ContextStore(tmp_path)
    store.init("Marker preserve test", force=False)

    store.update_main_section("Purpose", "Build a memory system for AI agents")
    main_text = (store.root / "main.md").read_text()
    assert "<!-- AUTO-UPDATED BELOW" in main_text
    assert "## Status" in main_text  # auto-updated section preserved


def test_branch_creates_initial_commit_entry(tmp_path):
    """Creating a branch writes initial commit.md with Branch Purpose."""
    store = ContextStore(tmp_path)
    store.init("Branch init test", force=False)

    store.create_branch("experiment", summary="Test async endpoints")
    commit_path = store.branch_path("experiment") / "commit.md"
    text = commit_path.read_text()
    assert "Branch Purpose" in text
    assert "Test async endpoints" in text
    assert "Branch created" in text


def test_merge_returns_target_context(tmp_path):
    """merge() returns context_branch result for target (auto-CONTEXT)."""
    store = ContextStore(tmp_path)
    store.init("Merge context test", force=False)

    store.create_branch("feature", parent="main", summary="Add feature X")
    store.append_event("feature", Event(
        agent="claude", channel="test", summary="Implemented feature X",
    ))
    result = store.merge("feature", "main")
    # Should return target branch context dict
    assert result["branch"] == "main"
    assert "purpose" in result


def test_init_has_milestones_and_todo(tmp_path):
    """init() creates main.md with Milestones and To-Do sections."""
    store = ContextStore(tmp_path)
    store.init("Milestone test", force=False)

    main_text = (store.root / "main.md").read_text()
    assert "## Milestones" in main_text
    assert "## To-Do" in main_text
