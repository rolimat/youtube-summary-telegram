import argparse
import asyncio
import os
from pathlib import Path

from notebooklm import NotebookLMClient

from youtube_summary.config import load_app_config
from youtube_summary.message import parse_answer
from youtube_summary.summarizer import NotebookLMSummarizer


def _mtime(path: Path) -> float:
    return path.stat().st_mtime


async def _run(url: str, prompts: list[str], storage: Path, notebook_id: str) -> None:
    summarizer = NotebookLMSummarizer(storage, notebook_id)
    print(f"storage mtime before: {_mtime(storage)}")
    await summarizer.ping()
    print(f"storage mtime after:  {_mtime(storage)}")
    async with NotebookLMClient.from_storage(str(storage)) as client:
        for source in await client.sources.list(notebook_id):
            await client.sources.delete(notebook_id, source.id)
        await client.sources.add_url(notebook_id, url, wait=True)
        for count, prompt in enumerate(prompts, start=1):
            answer = (await client.chat.ask(notebook_id, prompt)).answer
            summary = parse_answer(answer)
            print(f"--- ask #{count} raw ---\n{answer}")
            print(f"summary ({len(summary.text)} chars): {summary.text}")
            print(f"key points: {summary.key_points} json_parsed={bool(summary.key_points)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--prompt-file", type=Path, action="append", default=[])
    args = parser.parse_args()
    config = load_app_config(Path(os.environ["CONFIG_DIR"]) / "config.yml")
    prompts = [f.read_text() for f in args.prompt_file] or [config.prompt]
    storage = Path(os.environ["NOTEBOOKLM_STORAGE_STATE_PATH"])
    asyncio.run(_run(args.url, prompts, storage, config.notebook_id))


if __name__ == "__main__":
    main()
