"""Minimal stand-in for FastAPI so app/main.py's wiring can be exercised where FastAPI isn't installed.
It records routes and lets tests call handlers directly. It does NOT behave like real FastAPI
(no validation, no routing, no dependency injection)."""
import types


class HTTPException(Exception):
    def __init__(self, status_code, detail=None):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


class Request:
    def __init__(self, path="/", cookies=None, headers=None, form=None):
        self.cookies = cookies or {}
        self.headers = headers or {}
        self.url = types.SimpleNamespace(path=path)
        self._form = form or {}

    async def form(self):
        return self._form


class APIRouter:
    def __init__(self, prefix=""):
        self.prefix, self.routes = prefix, {}

    def _reg(self, method, path):
        def deco(fn):
            self.routes[(method, self.prefix + path)] = fn
            return fn
        return deco

    def get(self, path, **kw):
        return self._reg("GET", path)

    def post(self, path, **kw):
        return self._reg("POST", path)


class FastAPI:
    def __init__(self, **kw):
        self.routes, self.middleware_fn, self.mounts = {}, None, []

    def middleware(self, kind):
        def deco(fn):
            self.middleware_fn = fn
            return fn
        return deco

    def include_router(self, router):
        self.routes.update(router.routes)

    def add_api_route(self, path, fn, methods=None, **kw):
        for m in methods or ["GET"]:
            self.routes[(m, path)] = fn

    def mount(self, path, app, name=None):
        self.mounts.append(path)
