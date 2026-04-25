"""GitManager — sincronizzazione git del vault con GitHub (per Obsidian)."""

from __future__ import annotations

import asyncio
import os
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from loguru import logger


class GitError(Exception):
    pass


class GitManager:
    """
    Gestisce git operations sul vault.
    Subprocess git — no GitPython.
    SSH key iniettata via GIT_SSH_COMMAND per ogni chiamata.
    """

    def __init__(
        self,
        repo_path: Path,
        remote_url: str,
        ssh_key_content: str,
        author_name: str,
        author_email: str,
        notify_callback=None,
    ) -> None:
        self.repo_path = repo_path
        self.remote_url = remote_url
        self.author_name = author_name
        self.author_email = author_email
        self._notify = notify_callback
        self._ssh_key_file: Optional[tempfile.NamedTemporaryFile] = None
        self._ssh_key_path: Optional[str] = None
        self._setup_ssh_key(ssh_key_content)

    # ------------------------------------------------------------------
    # SSH key lifecycle
    # ------------------------------------------------------------------

    def _setup_ssh_key(self, content: str) -> None:
        """Scrive SSH key privata in tempfile chmod 600."""
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False)
        f.write(content)
        if not content.endswith("\n"):
            f.write("\n")
        f.flush()
        f.close()
        os.chmod(f.name, stat.S_IRUSR | stat.S_IWUSR)
        self._ssh_key_path = f.name
        logger.debug("GitManager: SSH key written to {}", f.name)

    def teardown(self) -> None:
        """Rimuove il file SSH key temporaneo."""
        if self._ssh_key_path and Path(self._ssh_key_path).exists():
            Path(self._ssh_key_path).unlink(missing_ok=True)
            logger.debug("GitManager: SSH key removed")

    # ------------------------------------------------------------------
    # Public async API
    # ------------------------------------------------------------------

    async def pull(self) -> None:
        """git pull origin main — all'avvio e prima di ogni read."""
        try:
            await self._run(["git", "pull", "origin", "main"])
            logger.info("GitManager: pull OK")
        except GitError as e:
            logger.warning("GitManager: pull failed — {}", e)

    async def commit_and_push(self, message: str, files: list[str]) -> None:
        """
        git add <files> → git commit → git push origin main.
        Commit message format: "wiki: <tipo> | <titolo>"
        """
        try:
            await self._run(["git", "add", "--"] + files)
            # Check if there's anything to commit
            result = await self._run_output(["git", "status", "--porcelain"])
            if not result.strip():
                logger.debug("GitManager: nothing to commit for {}", files)
                return
            env_extra = {
                "GIT_AUTHOR_NAME": self.author_name,
                "GIT_AUTHOR_EMAIL": self.author_email,
                "GIT_COMMITTER_NAME": self.author_name,
                "GIT_COMMITTER_EMAIL": self.author_email,
            }
            await self._run(["git", "commit", "-m", message], env_extra=env_extra)
            await self._push_with_retry()
            logger.info("GitManager: committed and pushed — {}", message)
        except GitError as e:
            logger.error("GitManager: commit_and_push failed — {}", e)
            raise

    async def pull_with_rebase(self) -> None:
        """
        git pull --rebase origin main.
        Fallback se push fallisce per divergenza.
        Se rebase fallisce → notifica Alessandro.
        """
        try:
            await self._run(["git", "pull", "--rebase", "origin", "main"])
            logger.info("GitManager: pull --rebase OK")
        except GitError as e:
            logger.error("GitManager: pull --rebase failed — {}", e)
            if self._notify:
                await self._notify(
                    f"⚠️ Conflitto git non risolvibile automaticamente.\n"
                    f"Errore: {e}\n"
                    "Risolvi manualmente in Obsidian e dimmi quando è fatto."
                )
            raise

    # ------------------------------------------------------------------
    # Git repo init
    # ------------------------------------------------------------------

    async def ensure_repo_initialized(self) -> None:
        """Inizializza o clona il repo se non esiste ancora."""
        git_dir = self.repo_path / ".git"
        if git_dir.exists():
            return
        if not any(self.repo_path.iterdir()) if self.repo_path.exists() else True:
            # Cartella vuota o inesistente — clona
            self.repo_path.mkdir(parents=True, exist_ok=True)
            try:
                await self._run(
                    ["git", "clone", self.remote_url, "."],
                    cwd=self.repo_path,
                )
                logger.info("GitManager: cloned {} into {}", self.remote_url, self.repo_path)
            except GitError as e:
                logger.error("GitManager: clone failed — {}", e)
        else:
            # Cartella con contenuto ma no .git — init + remote
            await self._run(["git", "init"], cwd=self.repo_path)
            await self._run(
                ["git", "remote", "add", "origin", self.remote_url],
                cwd=self.repo_path,
            )
            logger.info("GitManager: initialized existing dir as git repo")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_env(self, extra: dict | None = None) -> dict:
        ssh_cmd = f"ssh -i {self._ssh_key_path} -o StrictHostKeyChecking=no"
        env = {**os.environ, "GIT_SSH_COMMAND": ssh_cmd}
        if extra:
            env.update(extra)
        return env

    async def _run(
        self,
        cmd: list[str],
        cwd: Path | None = None,
        env_extra: dict | None = None,
    ) -> None:
        loop = asyncio.get_event_loop()
        cwd = cwd or self.repo_path
        env = self._build_env(env_extra)
        await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                cmd,
                cwd=cwd,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            ),
        )

    async def _run_output(
        self,
        cmd: list[str],
        cwd: Path | None = None,
    ) -> str:
        loop = asyncio.get_event_loop()
        cwd = cwd or self.repo_path
        env = self._build_env()
        result = await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                cmd,
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
            ),
        )
        return result.stdout

    async def _push_with_retry(self) -> None:
        """Push con fallback pull --rebase se divergenza."""
        try:
            await self._run(["git", "push", "origin", "main"])
        except subprocess.CalledProcessError as e:
            if "rejected" in (e.stderr or "") or "diverged" in (e.stderr or ""):
                logger.warning("GitManager: push rejected, trying pull --rebase")
                await self.pull_with_rebase()
                await self._run(["git", "push", "origin", "main"])
            else:
                raise GitError(str(e)) from e
        except Exception as e:
            raise GitError(str(e)) from e
