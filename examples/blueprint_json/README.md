# Blueprint JSON Provider Example

Two API versions share one Flask app, each with its own JSON
representation of ``Decimal``:

-   ``/v1/price`` uses the app-level default provider, which
    serializes ``Decimal`` as a string: ``{"price": "19.99"}``.
-   ``/v2/price`` uses its own provider, which serializes
    ``Decimal`` as an object with an amount and a currency:
    ``{"price": {"amount": "19.99", "currency": "EUR"}}``.

A blueprint sets ``json_provider_class`` to use its own provider for
its requests. The provider is selected based on the blueprint matched
for each request, including for ``jsonify``, dict and list return values,
``request.get_json``, error responses, and parsing the response with
``response.get_json`` after the request has ended. Anything not matched
to that blueprint keeps using the app-level provider.

Run the demo with the test client:

    $ python app.py

Or serve it and compare both versions:

    $ flask --app app run
    $ curl http://localhost:5000/v1/price
    $ curl http://localhost:5000/v2/price

Run the tests:

    $ pytest test_app.py
