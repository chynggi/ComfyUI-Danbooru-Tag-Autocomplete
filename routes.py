"""HTTP routes serving the tag artifact to the browser."""

from __future__ import annotations

from aiohttp import web
from server import PromptServer

from . import store

PREFIX = "/danbooru-tag-autocomplete"


async def status_route(request: web.Request) -> web.Response:
    current = store.status()
    if current.state == store.STATE_MISSING:
        store.ensure_download()
        current = store.status()
    return web.json_response(
        {"state": current.state, "dataVersion": current.data_version, "error": current.error}
    )


async def db_route(request: web.Request) -> web.Response:
    path = store.artifact_path()
    if not path.exists():
        return web.json_response({"error": "tag database is not available"}, status=404)
    response = web.FileResponse(path)
    response.headers["Content-Type"] = "application/octet-stream"
    response.headers["Cache-Control"] = "no-cache"
    encoding = store.artifact_content_encoding()
    if encoding is not None:
        response.headers["Content-Encoding"] = encoding
    return response


async def custom_route(request: web.Request) -> web.Response:
    return web.json_response(store.custom_payload())


def register_routes(app_routes) -> None:
    app_routes.get(f"{PREFIX}/status")(status_route)
    app_routes.get(f"{PREFIX}/db")(db_route)
    app_routes.get(f"{PREFIX}/custom")(custom_route)


register_routes(PromptServer.instance.routes)
