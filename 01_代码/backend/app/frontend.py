"""Serve the optional production React build from the same local API origin."""
from pathlib import Path, PurePosixPath

from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        # StaticFiles.get_path() uses os.path.normpath, hence backslashes on Windows.
        path = path.replace("\\", "/").lstrip("/")
        if path.split("/", 1)[0] in {"api", "health"}:
            raise HTTPException(status_code=404)
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            # Real asset failures stay 404; client routes receive the app shell.
            if exc.status_code == 404 and not PurePosixPath(path).suffix and scope["method"] in {"GET", "HEAD"}:
                return await super().get_response("index.html", scope)
            raise
