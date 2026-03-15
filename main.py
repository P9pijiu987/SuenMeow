from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from bot.logging_utils import configure_logging
from bot.settings import AppPaths, load_settings
from bot.trigger_engine import TriggerEngine
from db.repositories import Database
from web.main import create_app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SuenMeow service entrypoint")
    parser.add_argument(
        "command",
        choices=("run-once", "worker", "web", "init-db", "debug-topics"),
        help="Execution mode",
    )
    parser.add_argument(
        "--root",
        default=str(Path(__file__).resolve().parent),
        help="Project root directory",
    )
    parser.add_argument(
        "--topic-id",
        dest="topic_ids",
        action="append",
        type=int,
        help="Specific topic id to debug. Can be provided multiple times.",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=2,
        help="Number of topics to debug when no explicit topic ids are provided.",
    )
    return parser


async def run_once(root: Path) -> None:
    paths = AppPaths.from_root(root)
    settings = load_settings(paths)
    database = Database(paths.database_path)
    database.initialize()
    engine = TriggerEngine(settings=settings, database=database)
    await engine.run_once()


async def run_worker(root: Path) -> None:
    paths = AppPaths.from_root(root)
    settings = load_settings(paths)
    database = Database(paths.database_path)
    database.initialize()
    engine = TriggerEngine(settings=settings, database=database)
    await engine.run_forever()


async def debug_topics(root: Path, topic_ids: list[int] | None, count: int) -> None:
    paths = AppPaths.from_root(root)
    settings = load_settings(paths)
    database = Database(paths.database_path)
    database.initialize()
    engine = TriggerEngine(settings=settings, database=database)
    await engine.debug_topics(topic_ids=topic_ids, count=count)


def main() -> None:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    configure_logging(root / "logs")

    if args.command == "web":
        app = create_app(root)
        import uvicorn

        uvicorn.run(app, host=app.state.settings.webui.host, port=app.state.settings.webui.port)
        return

    if args.command == "init-db":
        paths = AppPaths.from_root(root)
        database = Database(paths.database_path)
        database.initialize()
        return

    if args.command == "run-once":
        asyncio.run(run_once(root))
        return

    if args.command == "worker":
        asyncio.run(run_worker(root))
        return

    if args.command == "debug-topics":
        asyncio.run(debug_topics(root, args.topic_ids, args.count))
        return


if __name__ == "__main__":
    main()
