from __future__ import annotations

from flask import Blueprint
from flask import Flask
from flask import Response
from flask import ResponsePolicyContext

app = Flask(__name__)
bp = Blueprint("bp", __name__)


@app.after_request
def after_sync(response: Response) -> Response:
    return Response()


@app.after_request
async def after_async(response: Response) -> Response:
    return Response()


@app.before_request
def before_sync() -> None: ...


@app.before_request
async def before_async() -> None: ...


@app.teardown_appcontext
def teardown_sync(exc: BaseException | None) -> None: ...


@app.teardown_appcontext
async def teardown_async(exc: BaseException | None) -> None: ...


@app.response_policy
def policy_sync(ctx: ResponsePolicyContext) -> None:
    ctx.response.headers["Content-Security-Policy"] = "default-src 'self'"


@app.response_policy
async def policy_async(ctx: ResponsePolicyContext) -> None:
    ctx.response.headers["Referrer-Policy"] = "no-referrer"


@bp.response_policy
def bp_policy(ctx: ResponsePolicyContext) -> None: ...


@bp.app_response_policy
def app_policy(ctx: ResponsePolicyContext) -> None: ...
