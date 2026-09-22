# Blueprint JSON Providers

Demonstrates how a blueprint can use its own JSON provider while the
application keeps its default provider.

The legacy `v1` API serializes `Decimal` as a string. The `v2` API
uses a custom provider that serializes `Decimal` as an object with an
amount and currency, and parses such objects back to `Decimal`. Both
coexist in the same Flask app.

Run the demo with the test client:

```shell
python app.py
```

Or start a development server and compare the two versions:

```shell
flask --app app run
curl http://localhost:5000/api/v1/price
curl http://localhost:5000/api/v2/price
```
