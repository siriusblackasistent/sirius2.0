#!/usr/bin/env python3
"""Small JSON bridge between Sirius and the hdrezka virtual environment."""

import argparse
import asyncio
import json
import os
import sys

from hdrezka import HDRezkaClient




def _credentials() -> tuple[str, str]:
    login = os.environ.get("REZKA_LOGIN")
    password = os.environ.get("REZKA_PASSWORD")
    if login and password:
        return login, password

    from sirius_settings import get_secret
    login = get_secret("rezka_play.login")
    password = get_secret("rezka_play.password")
    if not login or not password:
        raise RuntimeError("HDRezka credentials are not configured in core.py")
    return login, password



async def _popular(limit: int) -> list[dict]:
    login, password = _credentials()
    async with HDRezkaClient() as client:
        await client.login(login, password)
        items = await client.page().get_page(filter="popular")
        return _serialize_items(items, limit)


async def _search(query: str, limit: int) -> list[dict]:
    login, password = _credentials()
    async with HDRezkaClient() as client:
        await client.login(login, password)
        # hdrezka 5.2.0 Search has a broken paginator; parse its URL as a normal page.
        search_url = client.search(query).page
        items = await client.page(search_url).get_page()
        return _serialize_items(items, limit)


def _content_type(url: str) -> str:
    path = url.lower()
    if "/series/" in path:
        return "series"
    if "/films/" in path:
        return "film"
    if "/cartoons/" in path:
        return "cartoon"
    if "/animation/" in path or "/anime/" in path:
        return "anime"
    return "video"


def _serialize_items(items, limit: int) -> list[dict]:
    return [
        {
            "name": item.name,
            "year": item.info.year,
            "country": item.info.country,
            "genre": item.info.genre,
            "type": _content_type(item.url),
            "url": item.url,
        }
        for item in items[:limit]
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    popular = subparsers.add_parser("popular")
    popular.add_argument("--limit", type=int, default=5, choices=range(1, 21))
    search = subparsers.add_parser("search")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=5, choices=range(1, 21))
    args = parser.parse_args()

    try:
        if args.command == "popular":
            result = asyncio.run(_popular(args.limit))
        else:
            result = asyncio.run(_search(args.query, args.limit))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(f"HDRezka error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
