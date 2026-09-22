"""Two API versions sharing one Flask app, each with its own JSON
representation of ``Decimal``.

The old ``/v1`` API uses the app-level default provider, which
serializes ``Decimal`` as a string. The new ``/v2`` API uses its own
provider, which serializes ``Decimal`` as an object with an amount and
a currency, and parses JSON floats back as ``Decimal``.

Run a demo with the test client:

    $ python app.py

Or serve it and compare both versions:

    $ flask --app app run
    $ curl http://localhost:5000/v1/price
    $ curl http://localhost:5000/v2/price
"""

import decimal

from flask import Blueprint
from flask import Flask
from flask.json.provider import DefaultJSONProvider


class CurrencyJSONProvider(DefaultJSONProvider):
    """Serialize ``Decimal`` as an object with an amount and a currency,
    and parse JSON floats back as ``Decimal``.
    """

    def default(self, o):
        if isinstance(o, decimal.Decimal):
            return {"amount": str(o), "currency": "EUR"}

        return super().default(o)

    def loads(self, s, **kwargs):
        kwargs.setdefault("parse_float", decimal.Decimal)
        return super().loads(s, **kwargs)


def price():
    return {"price": decimal.Decimal("19.99")}


# The old API keeps the app-level default: Decimal is a string.
v1 = Blueprint("v1", __name__)
v1.add_url_rule("/price", view_func=price)

# The new API uses its own provider: Decimal is an object.
v2 = Blueprint("v2", __name__)
v2.json_provider_class = CurrencyJSONProvider
v2.add_url_rule("/price", view_func=price)

app = Flask(__name__)
app.register_blueprint(v1, url_prefix="/v1")
app.register_blueprint(v2, url_prefix="/v2")

if __name__ == "__main__":
    client = app.test_client()

    for version in ("v1", "v2"):
        rv = client.get(f"/{version}/price")
        parsed = rv.get_json()["price"]
        print(f"GET /{version}/price -> {rv.get_data(as_text=True).strip()}")
        print(f"  parsed back as {type(parsed).__name__}: {parsed!r}")
