import os
import shutil
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).parent.parent / "scripts" / "ops" / "post-receive"
ZEROS = "0" * 40
ONES = "1" * 40

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not available")


def run_hook(tmp_path: Path, stdin: str, *, force: bool = False) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    (tmp_path / "app").mkdir(exist_ok=True)
    env.update(YST_HOME=str(tmp_path), POST_RECEIVE_DRY_RUN="1")
    if force:
        env["POST_RECEIVE_FORCE"] = "1"
    result = subprocess.run(
        ["bash", str(HOOK)], input=stdin, env=env, capture_output=True, text=True, check=True
    )
    return result.stdout


def test_main_update_deploys(tmp_path: Path) -> None:
    out = run_hook(tmp_path, f"{ZEROS} {ONES} refs/heads/main\n")
    assert "[DRY RUN] docker compose" in out
    assert "up -d --build" in out
    assert "docker image prune -f --filter label=com.docker.compose.project=" in out
    assert "==> Deploy complete!" in out


def test_other_branch_does_not_deploy(tmp_path: Path) -> None:
    out = run_hook(tmp_path, f"{ZEROS} {ONES} refs/heads/other\n")
    assert "No deploy" in out
    assert "refs/heads/other" in out
    assert "DRY RUN" not in out


def test_main_deletion_does_not_deploy(tmp_path: Path) -> None:
    out = run_hook(tmp_path, f"{ONES} {ZEROS} refs/heads/main\n")
    assert "No deploy" in out
    assert "DRY RUN" not in out


def test_force_deploys_without_refs(tmp_path: Path) -> None:
    out = run_hook(tmp_path, "", force=True)
    assert "up -d --build" in out


def test_prepares_dirs_and_takes_lock(tmp_path: Path) -> None:
    out = run_hook(tmp_path, "", force=True)
    assert f"[DRY RUN] mkdir -p {tmp_path}/app {tmp_path}/state" in out
    assert f"[DRY RUN] chmod 700 {tmp_path}/secrets" in out
    assert "[DRY RUN] flock" in out


def test_home_derived_from_hook_location(tmp_path: Path) -> None:
    hooks = tmp_path / "repo.git" / "hooks"
    hooks.mkdir(parents=True)
    (tmp_path / "app").mkdir()
    copy = hooks / "post-receive"
    copy.write_text(HOOK.read_text())
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "YST_"))}
    env["POST_RECEIVE_DRY_RUN"] = "1"
    result = subprocess.run(
        ["bash", str(copy)],
        input="",
        env={**env, "POST_RECEIVE_FORCE": "1"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert f"--git-dir={tmp_path.resolve()}/repo.git" in result.stdout
