"""Characterization tests for the autoescape policy migration example.

The same fixed input is rendered before and after migration, through
normal rendering, source strings and streaming, and in plain-text mode.
Streaming output is reassembled with different chunk groupings and must
always equal the non-streamed output.
"""

from __future__ import annotations

import importlib.util
import os
import sys

from markupsafe import Markup

# Load the example under a unique module name. The example directory is
# added to sys.path only for the import and removed again afterwards, so
# the module never shadows other modules (such as a generic ``app``) for
# the rest of the test session.
_EXAMPLE_DIR = os.path.dirname(os.path.dirname(__file__))
_SPEC_PATH = os.path.join(_EXAMPLE_DIR, "autoescape_app.py")
_SPEC = importlib.util.spec_from_file_location(
    "template_autoescape_example", _SPEC_PATH
)
assert _SPEC is not None and _SPEC.loader is not None
example = importlib.util.module_from_spec(_SPEC)
sys.path.insert(0, _EXAMPLE_DIR)
try:
    _SPEC.loader.exec_module(example)
finally:
    sys.path.remove(_EXAMPLE_DIR)

ESCAPED = "&lt;b id=&#34;greet&#34;&gt;&amp;amp; Co &#39;26&lt;/b&gt;"
RAW = example.SAMPLE_INPUT


def test_legacy_extension_policy_leaks_jinja_templates():
    """Before migration the .jinja extension is not recognized as HTML."""
    output = example.render_page(example.html_app, "page.jinja")
    assert RAW in output
    assert ESCAPED not in output
    assert example.html_app.select_jinja_autoescape("page.jinja") is False


def test_strict_policy_escapes_every_html_template():
    output = example.render_page(example.strict_app, "page.jinja")
    # Every interpolated value is escaped, including in the extended
    # base template, the include and the imported macro. The only raw
    # copy comes from the explicit |safe macro, checked separately.
    assert output.count(ESCAPED) == 5
    assert output.count(RAW) == 1


def test_strict_policy_cannot_be_disabled_by_template_name():
    # User-controlled names with non-HTML extensions stay escaped unless
    # the extension is explicitly configured as plain text.
    policy = example.strict_app.jinja_autoescape_policy
    assert policy("anything-from-user.jinja") is True
    assert policy("anything-from-user.unknown") is True
    assert policy(None) is True
    # Only the configured plain-text extension opts out.
    assert policy("email/mail.txt") is False


def test_markup_and_safe_remain_explicit_trust():
    output = example.render_page(example.strict_app, "page.jinja")
    # The |safe macro output is the single place raw HTML survives.
    assert output.count(RAW) == 1
    assert '<span class="trusted">' + RAW + "</span>" in output


def test_markup_value_is_trusted_under_strict_policy():
    from flask import render_template_string

    with example.strict_app.app_context():
        output = render_template_string("{{ v }}", v=Markup(RAW))
    assert output == RAW


def test_source_string_uses_same_default_protection():
    assert example.render_from_string(example.strict_app) == f"<p>{ESCAPED}</p>"
    assert example.render_from_string(example.html_app) == f"<p>{ESCAPED}</p>"


def test_plain_text_mail_is_escaped_only_when_configured():
    mail = example.render_email(example.mail_app)
    assert RAW in mail
    assert ESCAPED not in mail
    # The strict app explicitly lists .txt as plain text.
    assert example.strict_app.select_jinja_autoescape("email/mail.txt") is False
    # But the text app switches off escaping even for HTML names.
    assert example.mail_app.select_jinja_autoescape("page.html") is False


def test_streaming_matches_normal_rendering_for_any_chunking():
    full = example.render_page(example.strict_app, "page.jinja")
    chunks = example.stream_page(example.strict_app)
    assert "".join(chunks) == full

    for size in (1, 2, 3, 5, 100):
        grouped: list[str] = []
        buffer = ""
        for chunk in chunks:
            buffer += str(chunk)
            while len(buffer) >= size:
                grouped.append(buffer[:size])
                buffer = buffer[size:]
        grouped.append(buffer)
        assert "".join(grouped) == full


def test_apps_do_not_affect_each_other_in_one_process():
    assert (
        example.html_app.jinja_env.autoescape
        is not example.strict_app.jinja_env.autoescape
    )
    assert (
        example.strict_app.jinja_env.autoescape
        is not example.mail_app.jinja_env.autoescape
    )
    legacy = example.render_page(example.html_app, "page.jinja")
    strict = example.render_page(example.strict_app, "page.jinja")
    assert RAW in legacy and ESCAPED not in legacy
    # Strict output escapes everywhere except the single |safe span.
    assert strict.count(ESCAPED) == 5 and strict.count(RAW) == 1
    # Re-rendering through the first app still uses its own policy.
    assert RAW in example.render_page(example.html_app, "page.jinja")
