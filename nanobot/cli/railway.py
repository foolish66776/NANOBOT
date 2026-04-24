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

    # Workspace: Railway volume if writable, altrimenti /tmp
    workspace = _resolve_workspace()
    print(f"✓ Workspace: {workspace}", flush=True)

    _maybe_clone_workspace(workspace)

    print("✓ Avvio nanobot gateway...", flush=True)
    from nanobot.cli.commands import app
    sys.argv = [
        "nanobot", "gateway",
        "--config", str(cfg_path),
        "--workspace", str(workspace),
    ]
    app()


def _resolve_workspace() -> pathlib.Path:
    preferred = pathlib.Path(os.environ.get("NANOBOT_WORKSPACE_DIR", "/tmp/workspace"))
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        test_file = preferred / ".write_test"
        test_file.write_text("ok")
        test_file.unlink()
        return preferred
    except OSError:
        print(f"⚠ {preferred} non scrivibile, uso /tmp/workspace", flush=True)
        fallback = pathlib.Path("/tmp/workspace")
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def _maybe_clone_workspace(workspace: pathlib.Path) -> None:
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("NANOBOT_WORKSPACE_REPO", "")
    if not token or not repo:
        return
    if any(workspace.iterdir() if workspace.exists() else []):
        return
    import subprocess
    url = f"https://{token}@github.com/{repo}"
    print(f"Volume vuoto — clono {repo}...", flush=True)
    result = subprocess.run(
        ["git", "clone", "--depth=1", "--branch=master", url, str(workspace)],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print("✓ Workspace clonato", flush=True)
    else:
        print(f"⚠ Clone fallito: {result.stderr[:200]}", flush=True)
