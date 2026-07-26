"""Production web-client hosting for the assembled Pharos application.

The development workflow keeps Vite and FastAPI separate.  Production is
deliberately different: the compiled React application lives in the same
immutable image as the API, so a release and its rollback can never mix
incompatible frontend and backend versions.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from starlette.staticfiles import StaticFiles


class ImmutableStaticFiles(StaticFiles):
    """Serve Vite's content-hashed assets with an immutable cache policy."""

    async def get_response(self, path: str, scope: dict) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code < 400:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def mount_web_app(app: FastAPI, web_dir: Path) -> None:
    """Mount a compiled Vite app after all API and documentation routes.

    Only extensionless browser-navigation paths fall back to ``index.html``.
    Missing API routes and missing files retain real 404 responses instead of
    being disguised as HTML, which keeps clients and monitoring honest.
    """

    root = web_dir.expanduser().resolve()
    index = root / "index.html"
    assets = root / "assets"
    if not index.is_file() or not assets.is_dir():
        raise RuntimeError(f"PHAROS_WEB_DIR must contain a Vite production build: {root}")

    app.mount("/assets", ImmutableStaticFiles(directory=assets), name="web-assets")

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
    async def serve_web_app(path: str = "") -> Response:
        # Existing /api, /docs, /redoc, and /openapi.json routes were mounted
        # before this catch-all and win normally. Unknown paths in those
        # namespaces must remain 404s rather than opening the React shell.
        first_segment = path.partition("/")[0]
        if first_segment in {"api", "docs", "redoc"} or path == "openapi.json":
            raise HTTPException(status_code=404, detail="Not Found")

        candidate = (root / path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Not Found") from exc

        if candidate.is_file():
            return FileResponse(
                candidate,
                headers={"Cache-Control": "public, max-age=3600"},
            )

        # A missing filename (JS, CSS, icon, source map, and so on) is a real
        # missing asset. Only extensionless paths are possible SPA navigation.
        if Path(path).suffix:
            raise HTTPException(status_code=404, detail="Not Found")

        return FileResponse(
            index,
            media_type="text/html",
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )
