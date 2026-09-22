import decimal

from app import app


def test_v1_uses_app_default_provider():
    rv = app.test_client().get("/v1/price")
    assert rv.get_json() == {"price": "19.99"}


def test_v2_uses_blueprint_provider():
    rv = app.test_client().get("/v2/price")
    assert rv.get_json() == {"price": {"amount": "19.99", "currency": "EUR"}}


def test_interleaved_requests_do_not_share_providers():
    client = app.test_client()
    responses = []

    for _ in range(3):
        responses.append((client.get("/v2/price"), dict))
        responses.append((client.get("/v1/price"), str))

    # Parsing the responses in reverse order still uses the provider
    # that created each response.
    for rv, type_ in reversed(responses):
        assert isinstance(rv.get_json()["price"], type_)


def test_v2_parses_floats_as_decimal():
    rv = app.test_client().get("/v2/price")
    assert isinstance(rv.get_json()["price"]["amount"], str)

    data = app.test_client().get("/v2/price").get_json()
    assert data["price"]["currency"] == "EUR"
    assert decimal.Decimal(data["price"]["amount"]) == decimal.Decimal("19.99")
