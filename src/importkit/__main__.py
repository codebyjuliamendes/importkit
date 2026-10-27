"""``python -m importkit`` / ``importkit``: run migrations (optional) and serve."""

from __future__ import annotations

import argparse

import uvicorn

from importkit import migrate
from importkit.config import get_settings


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="importkit")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("migrate", help="apply database migrations (alembic upgrade head)")
    serve = sub.add_parser("serve", help="run the HTTP server")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--reload", action="store_true")
    args = parser.parse_args(argv)
    settings = get_settings()

    if args.command == "migrate":
        migrate.upgrade(settings.database_url)
        return

    uvicorn.run(
        "importkit.app:create_app",
        factory=True,
        host=getattr(args, "host", None) or settings.host,
        port=getattr(args, "port", None) or settings.port,
        reload=bool(getattr(args, "reload", False)),
        proxy_headers=True,
        server_header=False,
        timeout_graceful_shutdown=10,
        log_config=None,
    )


if __name__ == "__main__":
    main()
