import hashlib
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent
FORBIDDEN_NAME_SHA256 = "fb4b12fd940110e5e369c476e87dfdc0f5481ed0923ce351bfbd442609b459ca"
PRIVATE_PATHS = ("local/", "scripts/server.env", "scripts/private/")
FORBIDDEN = [
    re.compile(r"\d{8,10}:[A-Za-z0-9_-]{35}"),
    re.compile(r"-100\d{6,}"),
    re.compile(r"@[\w.-]+\.(local|ts\.net)"),
]


def _git(*args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    result = subprocess.run(["git", *args], cwd=ROOT, env=env, capture_output=True, text=True)
    return result.stdout.strip()


def is_private_copy(origin_url: str) -> bool:
    return origin_url.removesuffix(".git").endswith("-private")


def scanned(names: list[str], private_copy: bool) -> list[str]:
    return [n for n in names if not (private_copy and n.startswith(PRIVATE_PATHS))]


def tracked_text_files() -> list[Path]:
    names = _git("ls-files").splitlines()
    private_copy = is_private_copy(_git("remote", "get-url", "origin"))
    return [ROOT / n for n in scanned(names, private_copy) if (ROOT / n).is_file()]


def has_forbidden_name(text: str) -> bool:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return any(hashlib.sha256(t.encode()).hexdigest() == FORBIDDEN_NAME_SHA256 for t in tokens)


def test_no_secrets_or_private_values_committed() -> None:
    offenders = []
    for path in tracked_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if has_forbidden_name(text):
            offenders.append(f"{path.name}: forbidden name")
        offenders += [f"{path.name}: {p.pattern}" for p in FORBIDDEN if p.search(text)]
    assert offenders == []


def test_private_paths_are_scanned_in_the_public_repo() -> None:
    names = ["src/app.py", "local/config/config.yml", "scripts/server.env"]
    assert scanned(names, private_copy=False) == names
    assert scanned(names, private_copy=True) == ["src/app.py"]


def test_private_copy_is_detected_by_origin_url() -> None:
    assert is_private_copy("https://github.com/o/youtube-summary-telegram-private.git")
    assert not is_private_copy("https://github.com/o/youtube-summary-telegram.git")
    assert not is_private_copy("")
