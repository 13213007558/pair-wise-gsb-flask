"""Use a different JSON provider per blueprint.

The legacy ``v1`` blueprint uses the app's default JSON provider, which
serializes ``Decimal`` as a string. The ``v2`` blueprint uses a custom
provider that serializes ``Decimal`` as an object with an amount and
currency, and parses such objects back to ``Decimal``.

Run a live server with::

    flask --app app run

Then compare::

    curl http://localhost:5000/api/v1/price
    curl http://localhost:5000/api/v2/price

Or run this file directly to see a demo using the test client::

    python app.py
"""

import decimal

from flask import Blueprint
from flask import Flask
from flask import request
from flask.json.provider import DefaultJSONProvider


class CurrencyJSONProvider(DefaultJSONProvider):
    """Serialize ``Decimal`` as ``{"amount": ..., "currency": ...}``,
    and parse such objects back to ``Decimal``.
    """

    @classmethod
    def default(cls, o):
        if isinstance(o, decimal.Decimal):
            return {"amount": str(o), "currency": "EUR"}

        return super().default(o)

    def loads(self, s, **kwargs):
        def object_hook(d):
            if set(d) == {"amount", "currency"}:
                return decimal.Decimal(d["amount"])

            return d

        kwargs.setdefault("object_hook", object_hook)
        return super().loads(s, **kwargs)


def make_price_bp(name):
    bp = Blueprint(name, __name__)

    @bp.get("/price")
    def price():
        return {"price": decimal.Decimal("19.99")}

    @bp.post("/echo")
    def echo():
        data = request.get_json()
        price = data["price"]
        return {"type": type(price).__name__, "value": price}

    return bp


app = Flask(__name__)

# The legacy API keeps the app's default provider: Decimal -> string.
app.register_blueprint(make_price_bp("v1"), url_prefix="/api/v1")

# The new API uses its own provider: Decimal -> {"amount", "currency"}.
v2 = make_price_bp("v2")
v2.json_provider_class = CurrencyJSONProvider
app.register_blueprint(v2, url_prefix="/api/v2")


if __name__ == "__main__":
    client = app.test_client()

    for version in ("v1", "v2"):
        response = client.get(f"/api/{version}/price")
        print(f"GET /api/{version}/price -> {response.get_data(as_text=True).strip()}")

    body = '{"price": {"amount": "19.99", "currency": "EUR"}}'

    for version in ("v1", "v2"):
        response = client.post(
            f"/api/{version}/echo", data=body, content_type="application/json"
        )
        print(
            f"POST /api/{version}/echo -> parsed price as"
            f" {response.get_json()['type']!r}"
        )

    # Responses remember their provider even after the request ended.
    rv_v1 = client.get("/api/v1/price")
    rv_v2 = client.get("/api/v2/price")
    print(f"v1 get_json() after request: {rv_v1.get_json()!r}")
    print(f"v2 get_json() after request: {rv_v2.get_json()!r}")
