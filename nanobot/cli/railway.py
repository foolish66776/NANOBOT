"""Railway startup entrypoint for nanobot gateway."""
from __future__ import annotations

import os
import pathlib
import sys


def gateway_main() -> None:
    config_json = os.environ.get("NANOBOT_CONFIG_JSON", "")
    if not config_json:
        print("ERROR: NANOBOT_CONFIG_JSON not set", flush=True)
        sys.exit(1)

    # Write config.json to /tmp (always writable)
    cfg_dir = pathlib.Path("/tmp/nanobot")
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = cfg_dir / "config.json"
    cfg_path.write_text(config_json)
    print(f"✓ config.json written ({len(config_json)} chars)", flush=True)

    # Workspace: Railway volume or fallback
    workspace = pathlib.Path(os.environ.get("NANOBOT_WORKSPACE_DIR", "/data/workspace"))
    workspace.mkdir(parents=True, exist_ok=True)
    print(f"✓ Workspace: {workspace}", flush=True)

    # Clone workspace from GitHub if empty
    _maybe_clone_workspace(workspace)

    # Start gateway via CLI (reuse existing Typer command)
    print("✓ Avvio nanobot gateway...", flush=True)
    from nanobot.cli.commands import app
    sys.argv = [
        "nanobot", "gateway",
        "--config", str(cfg_path),
        "--workspace", str(workspace),
    ]
    app()


def _maybe_clone_workspace(workspace: pathlib.Path) -> None:
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("NANOBOT_WORKSPACE_REPO", "")
    if not token or not repo:
        return
    if any(workspace.iterdir() if workspace.exists() else []):
        return  # already populated
    import subprocess
    url = f"https://{token}@github.com/{repo}"
    print(f"Volume vuoto — clono {repo}...", flush=True)
    result = subprocess.run(
        ["git", "clone", "--depth=1", url, str(workspace)],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print("✓ Workspace clonato", flush=True)
    else:
        print(f"⚠ Clone fallito: {result.stderr[:200]}", flush=True)
