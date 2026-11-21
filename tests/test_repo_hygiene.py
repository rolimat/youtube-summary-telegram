import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent
FORBIDDEN = [
    re.compile(r"\d{8,10}:[A-Za-z0-9_-]{35}"),
    re.compile(r"-100\d{6,}"),
    re.compile(r"@[\w.-]+\.(local|ts\.net)"),
    re.compile("tr" + "ai" + "dingbros", re.IGNORECASE),
]


def tracked_text_files() -> list[Path]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, env=env, capture_output=True, text=True, check=True
    ).stdout
    return [ROOT / name for name in out.splitlines() if (ROOT / name).is_file()]


def test_no_secrets_or_private_values_committed() -> None:
    offenders = []
    for path in tracked_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        offenders += [f"{path.name}: {p.pattern}" for p in FORBIDDEN if p.search(text)]
    assert offenders == []
