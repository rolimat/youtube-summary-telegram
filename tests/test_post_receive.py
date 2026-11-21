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
