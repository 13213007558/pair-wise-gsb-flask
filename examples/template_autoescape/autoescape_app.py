"""Inspectable migration example for the strict HTML template policy.

The project renders many ``.jinja`` templates, so relying on the file
extension to decide whether to escape is no longer safe. This example
shows the migration in one place:

* :data:`html_app` keeps the historical, extension-based policy and
  represents the application *before* migration.
* :data:`strict_app` switches to :class:`~flask.templating.HTMLAutoEscapePolicy`,
  the strict policy in which every template is escaped unless it is
  explicitly configured as plain text.
* :data:`mail_app` renders plain-text email with
  :class:`~flask.templating.TextAutoEscapePolicy`. Text mode is an
  explicit configuration choice, never something a template name can
  trigger.

Each application owns its Jinja environment, so the three configurations
coexist in one process without interfering with each other. No global
Jinja setting is changed.
"""

from __future__ import annotations

import os

from flask import Flask
from flask import render_template
from flask import render_template_string
from flask import stream_template
from flask.templating import HTMLAutoEscapePolicy
from flask.templating import TextAutoEscapePolicy

# Fixed input containing every character that matters for escaping:
# quotes, angle brackets, an ampersand entity and an apostrophe.
SAMPLE_INPUT = '<b id="greet">&amp; Co \'26</b>'

TEMPLATE_FOLDER = os.path.join(os.path.dirname(__file__), "templates")


def _create_app(name: str, policy: str | object) -> Flask:
    app = Flask(name, template_folder=TEMPLATE_FOLDER)
    app.config["JINJA_AUTOESCAPE_POLICY"] = policy
    return app


# Before migration: only .html/.htm/.xml/.xhtml/.svg are escaped.
html_app = _create_app("legacy_html_app", "selective")

# After migration: strict HTML. A .jinja name can no longer opt out.
strict_app = _create_app("strict_html_app", HTMLAutoEscapePolicy())

# Plain-text output must declare itself; the template name alone never
# disables escaping.
mail_app = _create_app("plain_text_mail_app", TextAutoEscapePolicy())


def render_page(app: Flask, template_name: str) -> str:
    """Render one page template with the fixed sample input."""
    with app.app_context():
        return render_template(
            template_name,
            name=SAMPLE_INPUT,
            title=SAMPLE_INPUT,
            snippet=SAMPLE_INPUT,
            included=SAMPLE_INPUT,
        )


def render_from_string(app: Flask) -> str:
    """Render a source string, which has no file name at all."""
    with app.app_context():
        return render_template_string("<p>{{ name }}</p>", name=SAMPLE_INPUT)


def stream_page(app: Flask) -> list[str]:
    """Stream a page and return every chunk as the client receives it."""
    with app.app_context():
        response = stream_template(
            "page.jinja",
            name=SAMPLE_INPUT,
            title=SAMPLE_INPUT,
            snippet=SAMPLE_INPUT,
            included=SAMPLE_INPUT,
        )
        return list(response)


def render_email(app: Flask) -> str:
    """Render the plain-text email with the same fixed input."""
    with app.app_context():
        return render_template("email/mail.txt", name=SAMPLE_INPUT)


if __name__ == "__main__":
    print("legacy .jinja output:\n", render_page(html_app, "page.jinja"))
    print("strict .jinja output:\n", render_page(strict_app, "page.jinja"))
    print("plain-text email:\n", render_email(mail_app))
