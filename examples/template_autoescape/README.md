# Strict HTML Autoescaping Migration Example

This project renders many `.jinja` templates. Flask's historical policy
only autoescapes names ending in `.html`, `.htm`, `.xml`, `.xhtml` or
`.svg`, so a `.jinja` template — or any name coming from user input — is
rendered without escaping. This example shows how to migrate to an
explicit, inspectable autoescape policy without touching Jinja globally.

## The three policies

Policies are selected per application with the
`JINJA_AUTOESCAPE_POLICY` config key. Each application keeps its own
Jinja environment, so the configurations coexist in one process.

| Config value                     | Class                      | Behavior                                                        |
| -------------------------------- | -------------------------- | --------------------------------------------------------------- |
| `"selective"` (Flask default)    | `SelectiveAutoEscapePolicy`| Historical: escape recognized HTML extensions only.             |
| `"html"` or `HTMLAutoEscapePolicy()` | `HTMLAutoEscapePolicy` | Strict: escape everything except explicitly listed text extensions (default `.txt`, `.text`). |
| `"text"` or `TextAutoEscapePolicy()` | `TextAutoEscapePolicy` | Never escape; for plain-text email and similar output.          |

A custom callable `(template_name: str | None) -> bool`, an
`AutoEscapePolicy` subclass, or an instance is accepted as well.

## What the example demonstrates

- `autoescape_app.py` builds three apps: `html_app` (before migration),
  `strict_app` (after migration) and `mail_app` (plain-text email).
- `templates/page.jinja` extends `base.jinja`, includes
  `_partials.jinja`, and imports macros from `_widgets.jinja`. The
  policy chosen for the page applies across all of them.
- The same fixed input (quotes, angle brackets, an `&amp;` entity, an
  apostrophe) is rendered before and after migration so the output
  difference is visible.
- `Markup(...)` and the `|safe` filter still render raw HTML under the
  strict policy. They are the application's explicit trust signal, not a
  failure of autoescaping.
- A user-controlled template name cannot switch escaping off under the
  strict policy; plain text requires the explicit `"text"` policy or an
  entry in `HTMLAutoEscapePolicy(non_html_extensions=...)`.
- Streaming output equals normal rendering regardless of how the
  iterator is chunked.

## Run it

```bash
python autoescape_app.py
python -m pytest tests/ -v
```
