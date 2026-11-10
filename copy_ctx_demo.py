"""Boundaries of flask.copy_current_request_context (Flask 3.2.0.dev, this repo).

Run: python copy_ctx_demo.py
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from flask import Flask, copy_current_request_context, current_app, g, jsonify, request, session

app = Flask(__name__)
app.secret_key = "demo-key"

results: dict = {}


def underlying(proxy):
    """The real object a LocalProxy points at, not the proxy itself."""
    return proxy._get_current_object()


# ---------------------------------------------------------------- scenario 1
# Two independent user requests, forced to interleave with a barrier.
barrier = threading.Barrier(2)


@app.route("/whoami")
def whoami():
    me = request.args["u"]
    session["user"] = me          # touch session -> own session object
    g.user = me                   # touch g -> own g object
    barrier.wait(timeout=10)      # both requests are now mid-flight
    return jsonify(
        user_in_session=session.get("user"),
        user_in_g=g.user,
        session_obj=id(underlying(session)),
        g_obj=id(underlying(g)),
        request_obj=id(underlying(request)),
    )


def scenario_isolation():
    clients = [app.test_client(), app.test_client()]
    out = {}

    def hit(client, user):
        out[user] = client.get(f"/whoami?u={user}").get_json()

    threads = [threading.Thread(target=hit, args=(c, u)) for c, u in zip(clients, ["alice", "bob"])]
    for t in threads: t.start()
    for t in threads: t.join()

    a, b = out["alice"], out["bob"]
    print("\n== 1. two independent requests, interleaved ==")
    print("alice sees:", a["user_in_session"], a["user_in_g"])
    print("bob   sees:", b["user_in_session"], b["user_in_g"])
    assert a["user_in_session"] == "alice" and b["user_in_session"] == "bob"
    assert a["session_obj"] != b["session_obj"], "sessions must be distinct objects"
    assert a["g_obj"] != b["g_obj"], "g must be distinct objects"
    assert a["request_obj"] != b["request_obj"], "requests must be distinct objects"
    print("PASS: fully isolated - distinct request/session/g objects, correct data")


# ---------------------------------------------------------------- scenario 2
# Copied context of ONE request: what is shared, what is not.
response_sent = threading.Event()
child_done = threading.Event()


@app.route("/parent")
def parent():
    session["user"] = "alice"     # parent loads + mutates session
    session["counter"] = 1
    g.user = "alice"

    results["parent"] = dict(
        session_obj=id(underlying(session)),
        g_obj=id(underlying(g)),
        request_obj=id(underlying(request)),
        app_obj=id(underlying(current_app)),
    )

    @copy_current_request_context
    def child_task():
        # Wait until the parent's response (and its Set-Cookie) is gone.
        response_sent.wait(timeout=10)
        s = underlying(session)
        results["child"] = dict(
            session_obj=id(s),
            g_obj=id(underlying(g)),
            request_obj=id(underlying(request)),
            app_obj=id(underlying(current_app)),
            session_data=dict(s),          # does it see the parent's mutation?
            g_user=g.get("user"),          # copied ctx gets a FRESH g
            request_path=request.path,
        )
        s["child_write"] = "too-late"      # mutate the shared session object
        results["child"]["after_write"] = dict(s)
        child_done.set()

    threading.Thread(target=child_task).start()
    return "parent response"


def scenario_copy():
    client = app.test_client()
    resp = client.get("/parent")
    print("\n== 2. copied context of one request ==")
    print("Set-Cookie sent:", "session=" in resp.headers.get("Set-Cookie", ""))
    response_sent.set()                   # response is gone; let the child read
    assert child_done.wait(timeout=10)

    p, c = results["parent"], results["child"]
    print("session obj  parent/child:", p["session_obj"], c["session_obj"])
    print("request obj  parent/child:", p["request_obj"], c["request_obj"])
    print("app obj      parent/child:", p["app_obj"], c["app_obj"])
    print("g obj        parent/child:", p["g_obj"], c["g_obj"])
    print("child reads session:", c["session_data"])
    print("child reads g.user:", c["g_user"])

    assert c["session_obj"] == p["session_obj"], "session object is SHARED"
    assert c["request_obj"] == p["request_obj"], "request object is SHARED"
    assert c["app_obj"] == p["app_obj"], "app object is SHARED"
    assert c["g_obj"] != p["g_obj"], "g is NOT shared - fresh namespace per context"
    assert c["session_data"] == {"user": "alice", "counter": 1}
    assert c["g_user"] is None
    print("PASS: request/session/app shared by reference; g is a fresh object")

    # The child's write hit the shared object, but the cookie was already sent.
    follow_up = client.get("/dump").get_json()
    print("session after follow-up request:", follow_up)
    assert "child_write" not in follow_up
    print("PASS: child's session write silently lost (response cookie already sent)")


@app.route("/dump")
def dump():
    return jsonify(dict(session))


@app.route("/login")
def login():
    session["user"] = request.args["u"]
    return "ok"


# ---------------------------------------------------------------- scenario 3
# Copy made BEFORE the session was lazily loaded.
def scenario_lazy_session():
    print("\n== 3. copy before lazy session load ==")
    client = app.test_client()
    client.get("/login?u=alice")  # get a real signed cookie, no threads involved
    cookie = client.get_cookie("session").value

    parent_ctx = app.test_request_context("/lazy", headers={"Cookie": f"session={cookie}"})
    assert parent_ctx._session is None, "session not loaded yet"
    child_ctx = parent_ctx.copy()
    assert child_ctx._session is None, "copy carries the not-yet-loaded state"

    parent_ctx.push()
    try:
        s_parent = underlying(session)          # lazy load happens now, in parent
        s_parent["note"] = "parent-was-here"
    finally:
        parent_ctx.pop()

    child_ctx.push()
    try:
        s_child = underlying(session)           # child lazily loads its OWN object
        child_data = dict(s_child)
    finally:
        child_ctx.pop()

    print("parent session obj:", id(s_parent), "child session obj:", id(s_child))
    print("child session data:", child_data)
    assert s_parent is not s_child, "each context lazily opened its OWN session"
    assert "note" not in child_data, "parent's in-memory mutation invisible to child"
    assert child_data.get("user") == "alice", "both parsed the same cookie independently"
    print("PASS: copy before lazy load -> two independent session objects from one cookie")


# ---------------------------------------------------------------- scenario 4
# Explicit authorization handoff instead of context copying.
REVOKED_USERS: set[str] = set()


@dataclass(frozen=True)  # immutable snapshot - safe to hand to a thread/queue
class Grant:
    user_id: str
    scope: frozenset[str]
    issued_at: float
    expires_at: float


def run_privileged_task(grant: Grant) -> str:
    """Re-verify at EXECUTION time; never trust the snapshot's freshness."""
    now = time.time()
    if now > grant.expires_at:
        return f"REJECTED: grant for {grant.user_id} expired {now - grant.expires_at:.2f}s ago"
    if grant.user_id in REVOKED_USERS:
        return f"REJECTED: grant for {grant.user_id} was revoked"
    return f"EXECUTED as {grant.user_id} with {sorted(grant.scope)}"


def scenario_authz():
    print("\n== 4. explicit grant handoff, re-verified at execution ==")
    now = time.time()
    valid = Grant("alice", frozenset({"report:run"}), now, now + 60)
    expiring = Grant("bob", frozenset({"report:run"}), now, now + 0.2)
    revoked = Grant("carol", frozenset({"report:run"}), now, now + 60)
    REVOKED_USERS.add("carol")

    outcomes = {}
    def work(name, grant, delay):
        time.sleep(delay)
        outcomes[name] = run_privileged_task(grant)

    threads = [
        threading.Thread(target=work, args=("valid", valid, 0)),
        threading.Thread(target=work, args=("expired", expiring, 0.4)),  # runs after TTL
        threading.Thread(target=work, args=("revoked", revoked, 0)),
    ]
    for t in threads: t.start()
    for t in threads: t.join()

    for k, v in outcomes.items(): print(f"  {k}: {v}")
    assert outcomes["valid"].startswith("EXECUTED")
    assert outcomes["expired"].startswith("REJECTED: grant for bob expired")
    assert outcomes["revoked"].startswith("REJECTED: grant for carol was revoked")
    print("PASS: expired and revoked grants rejected at execution time")


if __name__ == "__main__":
    print("proxy id is constant, underlying object is what matters:")
    print("  id(session proxy) ==", id(session), "(same object for the whole process)")
    scenario_isolation()
    scenario_copy()
    scenario_lazy_session()
    scenario_authz()
    print("\nALL CHECKS PASSED")
