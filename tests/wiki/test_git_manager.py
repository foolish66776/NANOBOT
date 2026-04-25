"""Tests for GitManager — mock subprocess, commit format, SSH env injection."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


FAKE_KEY = "-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n-----END OPENSSH PRIVATE KEY-----\n"


def _make_manager(tmp_path: Path, notify=None):
    from nanobot.business.wiki.git_manager import GitManager
    return GitManager(
        repo_path=tmp_path,
        remote_url="git@github.com:test/wiki.git",
        ssh_key_content=FAKE_KEY,
        author_name="nanobot",
        author_email="nanobot@test.it",
        notify_callback=notify,
    )


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# SSH key file
# ---------------------------------------------------------------------------

def test_ssh_key_written_to_tempfile(tmp_path):
    mgr = _make_manager(tmp_path)
    assert mgr._ssh_key_path is not None
    key_file = Path(mgr._ssh_key_path)
    assert key_file.exists()
    assert oct(key_file.stat().st_mode)[-3:] == "600"
    mgr.teardown()
    assert not key_file.exists()


def test_ssh_command_injected_in_env(tmp_path):
    mgr = _make_manager(tmp_path)
    env = mgr._build_env()
    assert "GIT_SSH_COMMAND" in env
    assert mgr._ssh_key_path in env["GIT_SSH_COMMAND"]
    assert "StrictHostKeyChecking=no" in env["GIT_SSH_COMMAND"]
    mgr.teardown()


# ---------------------------------------------------------------------------
# commit_and_push — commit message format
# ---------------------------------------------------------------------------

def test_commit_and_push_message_format(tmp_path):
    mgr = _make_manager(tmp_path)
    calls = []

    async def fake_run(cmd, cwd=None, env_extra=None):
        calls.append(cmd)

    async def fake_run_output(cmd, cwd=None):
        # Simulate dirty working tree for the porcelain check
        if "status" in cmd:
            return "M beliefs/test.md"
        return ""

    async def fake_push():
        pass

    mgr._run = fake_run
    mgr._run_output = fake_run_output
    mgr._push_with_retry = fake_push

    _run(mgr.commit_and_push("wiki: beliefs | beliefs/test.md", ["beliefs/test.md"]))

    commit_call = next((c for c in calls if "commit" in c), None)
    assert commit_call is not None
    assert "wiki: beliefs | beliefs/test.md" in commit_call
    mgr.teardown()


# ---------------------------------------------------------------------------
# pull_with_rebase — notifica se fallisce
# ---------------------------------------------------------------------------

def test_pull_with_rebase_notifies_on_failure(tmp_path):
    notified = []

    async def notify(msg):
        notified.append(msg)

    mgr = _make_manager(tmp_path, notify=notify)
    from nanobot.business.wiki.git_manager import GitError

    async def failing_run(cmd, cwd=None, env_extra=None):
        if "rebase" in cmd:
            raise GitError("merge conflict")

    mgr._run = failing_run

    with pytest.raises(GitError):
        _run(mgr.pull_with_rebase())

    assert len(notified) == 1
    assert "Conflitto" in notified[0]
    mgr.teardown()


# ---------------------------------------------------------------------------
# Integration: write_page triggers commit_and_push
# ---------------------------------------------------------------------------

def test_write_page_triggers_git_commit(tmp_path):
    from nanobot.business.wiki.vault import VaultManager
    from nanobot.business.wiki.git_manager import GitManager

    committed = []

    mgr = _make_manager(tmp_path)

    async def fake_commit(message, files):
        committed.append((message, files))

    mgr.commit_and_push = fake_commit

    vault = VaultManager(tmp_path, git_manager=mgr)
    vault.create_page(
        path="beliefs/test-git.md",
        page_type="belief",
        title="Test git",
        body="# Test\n\nContenuto.",
        confidence="high",
        status="active",
    )

    # commit_and_push called asynchronously via ensure_future / run_until_complete
    # Give event loop a tick
    _run(asyncio.sleep(0))

    assert len(committed) >= 1
    msg, files = committed[0]
    assert "beliefs" in msg
    assert "beliefs/test-git.md" in files
    mgr.teardown()
