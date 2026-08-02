"""The route census: what the *real* application actually exposes.

Every other API test in this suite builds its own throwaway ``FastAPI()`` and
mounts the one router it cares about, with ``current_user`` overridden. That is
the right shape for testing a router's behaviour, but it shares a blind spot:
a router that ``main.create_app`` never includes still passes all of its own
tests, because those tests supply the app themselves. The annotations router was
in exactly that state — fully written, fully tested, and unreachable over HTTP.

So this module asserts the two things only the assembled app can answer:

1. **Every endpoint a slice ships is mounted**, checked against the routers'
   own route tables rather than a hand-written list of paths, so a new endpoint
   is covered the moment it is written and this test cannot go stale.
2. **Every ``/api`` route requires authentication**, with the handful of
   deliberately public ones named explicitly. A new endpoint that forgets
   ``current_user`` fails here rather than in production, and adding one to the
   allow-list is a line a reviewer has to see.
"""

from __future__ import annotations

import os
import tempfile

# Set before anything under ``pharos`` is imported. ``pharos.main`` builds an
# application at module scope, so importing it opens a database immediately —
# against the developer's real data directory unless it is redirected first, and
# that database is a schema the test process has no business migrating.
os.environ["PHAROS_DATA_DIR"] = tempfile.mkdtemp(prefix="pharos-routes-")
os.environ.setdefault("PHAROS_AUTH_SECRET", "test-secret-test-secret-test-secret")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.routing import APIRoute  # noqa: E402
from pharos.api import (  # noqa: E402
    annotate,
    auth,
    daily,
    daily_vault,
    directions,
    discovery,
    jobs,
    organise,
    papers,
    projects,
    search,
    zotero,
)
from pharos.main import create_app  # noqa: E402

#: Routes that are meant to be reachable without a token, and why.
PUBLIC = {
    ("GET", "/api/health"),  # liveness probe; returns no user data
    ("POST", "/api/auth/register"),  # creating the account that gets a token
    ("POST", "/api/auth/login"),  # exchanging a password for a token
    # A sign-in screen has to know whether to draw a sign-up form before anyone
    # has a token. Returns one boolean and nothing about who is registered.
    ("GET", "/api/auth/status"),
    # Browser redirect from Zotero cannot carry the localStorage Bearer token;
    # the callback is instead bound to a one-use request token plus HttpOnly
    # browser state and never accepts a user id from the request.
    ("GET", "/api/zotero/oauth/callback"),
}

#: Every router the application is supposed to mount. Listing them here rather
#: than importing ``main``'s list is the point: this is the independent statement
#: of intent that ``create_app`` is checked against.
ROUTERS = [
    auth.router,
    papers.router,
    jobs.router,
    search.router,
    organise.router,
    annotate.router,
    projects.router,
    discovery.router,
    directions.router,
    daily_vault.router,
    daily.router,
    zotero.router,
]


def _api_routes(app: FastAPI) -> list[APIRoute]:
    """Every ``APIRoute`` on the app, flattening FastAPI's lazy router wrappers.

    ``app.routes`` does not necessarily contain routes: recent FastAPI versions
    park an included router behind a wrapper object and resolve it on demand, so
    walking the list naively finds only the routes declared on the app itself —
    which would make this whole module silently vacuous.
    """
    found: list[APIRoute] = []
    stack = list(app.routes)
    while stack:
        route = stack.pop()
        if isinstance(route, APIRoute):
            found.append(route)
            continue
        inner = getattr(route, "original_router", None) or getattr(route, "router", None)
        if inner is not None:
            stack.extend(inner.routes)
    return found


def _dependency_names(route: APIRoute) -> set[str]:
    """Names of every dependency in the route's tree, however deeply nested.

    ``current_user`` is usually a direct dependency, but a router-level or
    nested one protects the endpoint just as well, so a shallow check would
    report a false failure the first time somebody factors one out.
    """
    names: set[str] = set()

    def walk(dependant) -> None:
        for sub in dependant.dependencies:
            names.add(getattr(sub.call, "__name__", ""))
            walk(sub)

    walk(route.dependant)
    return names


@pytest.fixture(scope="module")
def app() -> FastAPI:
    """The real application, against the throwaway data directory set above.

    No lifespan is run: nothing here makes a request, and starting the daily
    scheduler for a route census would be a background timer with nothing to do.
    """
    return create_app()


def test_every_router_is_mounted(app: FastAPI) -> None:
    """No slice ships an endpoint the application does not serve.

    Compared path-and-method rather than by router identity, because a router
    can be included under the wrong prefix and still be "included".
    """
    mounted = {(method, r.path) for r in _api_routes(app) for method in r.methods or ()}
    declared = {
        (method, route.path)
        for router in ROUTERS
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in route.methods or ()
    }
    missing = declared - mounted
    assert not missing, f"declared but not mounted in create_app(): {sorted(missing)}"


def test_annotate_endpoints_are_reachable(app: FastAPI) -> None:
    """The specific regression: highlights and notes exist on the app.

    Redundant with the census above, and kept anyway — it names the endpoints
    that were once missing, so the failure message points straight at the cause
    instead of at a set difference.
    """
    paths = {r.path for r in _api_routes(app)}
    for path in (
        "/api/papers/{paper_id}/highlights",
        "/api/highlights/{highlight_id}",
        "/api/papers/{paper_id}/note",
    ):
        assert path in paths, f"{path} is not mounted on the application"


def test_every_api_route_requires_authentication(app: FastAPI) -> None:
    """An endpoint with no ``current_user`` is an unowned view of somebody's data.

    This is the check that catches the route nobody remembered. Every per-user
    slice — search, collections, tags, highlights, notes — depends on the owner
    id arriving from the token, so an endpoint that never resolves a user is not
    merely unauthenticated: it has no owner to scope by at all.
    """
    unprotected = sorted(
        (method, route.path)
        for route in _api_routes(app)
        for method in route.methods or ()
        if route.path.startswith("/api")
        and (method, route.path) not in PUBLIC
        and not {"current_user", "current_user_optional"} & _dependency_names(route)
    )
    assert not unprotected, f"/api routes with no authenticated user: {unprotected}"
