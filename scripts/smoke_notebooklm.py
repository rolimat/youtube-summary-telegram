import argparse
import asyncio
import os
import sys
from pathlib import Path

from notebooklm import NotebookLMClient

from youtube_summary.config import load_app_config
from youtube_summary.message import parse_answer
from youtube_summary.summarizer import NotebookLMSummarizer, reset_conversation


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"missing environment variable {name}")
    return value


def _report(count: int, answer: str) -> None:
    summary = parse_answer(answer)
    print(f"--- ask #{count} raw ---\n{answer}")
    print(f"summary ({len(summary.text)} chars): {summary.text}")
    print(f"key points: {summary.key_points} json_parsed={bool(summary.key_points)}")


async def _extra_asks(storage: Path, notebook_id: str, prompts: list[str]) -> None:
    async with NotebookLMClient.from_storage(str(storage)) as client:
        for count, prompt in enumerate(prompts, start=2):
            await reset_conversation(client, notebook_id)
            _report(count, (await client.chat.ask(notebook_id, prompt)).answer)


async def _run(url: str, prompts: list[str], storage: Path, notebook_id: str) -> None:
    summarizer = NotebookLMSummarizer(storage, notebook_id)
    print(f"storage mtime before: {storage.stat().st_mtime}")
    await summarizer.ping()
    print(f"storage mtime after:  {storage.stat().st_mtime}")
    _report(1, await summarizer.summarize(url, prompts[0]))
    if prompts[1:]:
        await _extra_asks(storage, notebook_id, prompts[1:])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--prompt-file", type=Path, action="append", default=[])
    args = parser.parse_args()
    storage = Path(_env("NOTEBOOKLM_STORAGE_STATE_PATH"))
    config = load_app_config(Path(_env("CONFIG_DIR")) / "config.yml")
    prompts = [f.read_text() for f in args.prompt_file] or [config.prompt]
    asyncio.run(_run(args.url, prompts, storage, config.notebook_id))


if __name__ == "__main__":
    main()
