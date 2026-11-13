"""Website crawler, pure parts: URL rules, website address, robots.txt, extraction, hashing, diffs.

No network and no database. The fetch and crawl tests are in test_website_crawler_crawl.py.
"""

import importlib.util
from pathlib import Path

import pytest

from app.db.models.client import normalize_website_url
from app.services.website_crawler.diffing import content_hash, normalize, word_diff
from app.services.website_crawler.extract import MAX_TEXT_BYTES, extract_page
from app.services.website_crawler.robots import (
    ALLOW_ALL,
    DENY_ALL,
    MAX_CRAWL_DELAY_SECONDS,
    MIN_DELAY_SECONDS,
    policy_from_response,
)
from app.services.website_crawler.urls import canonical_url, in_scope, same_site

# ---------------------------------------------------------------- canonical URLs


@pytest.mark.parametrize(
    ("raw", "base", "expected"),
    [
        ("https://Clinic.example/Hours/", None, "https://clinic.example/Hours"),
        ("https://clinic.example/hours?utm=1#top", None, "https://clinic.example/hours"),
        ("https://clinic.example", None, "https://clinic.example/"),
        ("https://clinic.example/", None, "https://clinic.example/"),
        ("https://clinic.example//a//b/", None, "https://clinic.example/a/b"),
        ("/contact", "https://clinic.example/hours", "https://clinic.example/contact"),
        ("../team/", "https://clinic.example/a/b", "https://clinic.example/team"),
        ("https://clinic.example:443/x", None, "https://clinic.example/x"),
    ],
)
def test_links_become_one_canonical_form(raw, base, expected):
    assert canonical_url(raw, base) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "http://clinic.example/hours",  # not https
        "mailto:office@clinic.example",
        "javascript:void(0)",
        "https://user:pass@clinic.example/",  # credentials
        "https://clinic.example:8443/",  # other port
        "https://clinic.example/brochure.pdf",  # a file, not a page
        "https://clinic.example/logo.PNG",
        "https://clinic.example/styles.css",
        "",
        "https://clinic.example/" + "a" * 2100,  # over the URL length limit
    ],
)
def test_links_the_crawler_must_not_follow_are_refused(raw):
    assert canonical_url(raw) is None


def test_the_site_host_and_its_www_counterpart_are_in_scope():
    assert same_site("clinic.example", "clinic.example")
    assert same_site("www.clinic.example", "clinic.example")
    assert same_site("clinic.example", "www.clinic.example")


@pytest.mark.parametrize("host", ["blog.clinic.example", "clinic.example.evil.test", "other.test"])
def test_other_hosts_are_out_of_scope(host):
    assert not same_site(host, "clinic.example")
    assert not in_scope(f"https://{host}/x", "clinic.example")


# ---------------------------------------------------------------- website address (admin input)


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("clinic.example", "https://clinic.example"),
        ("https://Clinic.Example/", "https://clinic.example"),
        ("https://www.clinic.example", "https://www.clinic.example"),
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_website_address_is_stored_as_the_site_origin(typed, expected):
    assert normalize_website_url(typed) == expected


@pytest.mark.parametrize(
    "typed",
    [
        "http://clinic.example",
        "https://clinic.example/contact",
        "https://clinic.example?x=1",
        "https://clinic.example:8443",
        "https://user@clinic.example",
        "https://localhost",
        "https://",
        "https://clinic.example:notaport",
    ],
)
def test_website_address_refusals(typed):
    with pytest.raises(ValueError):
        normalize_website_url(typed)


def test_migration_0018_follows_0017():
    path = (
        Path(__file__).resolve().parents[1] / "app/db/migrations/versions/0018_website_crawler.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0018", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0018_website_crawler"
    assert module.down_revision == "0017_booking_current_patient"
    assert any("website_url" in statement for statement in module.UPGRADE)


# ---------------------------------------------------------------- robots.txt


RULES = "User-agent: *\nDisallow: /private\nCrawl-delay: 4\n"


def test_robots_200_applies_its_rules():
    policy = policy_from_response(200, RULES)
    assert policy.allows("https://clinic.example/hours")
    assert not policy.allows("https://clinic.example/private/notes")


def test_robots_crawl_delay_is_used_within_the_cap():
    assert policy_from_response(200, RULES).delay_seconds() == 4.0
    assert policy_from_response(200, "User-agent: *\nCrawl-delay: 999\n").delay_seconds() == (
        MAX_CRAWL_DELAY_SECONDS
    )
    assert policy_from_response(200, "User-agent: *\n").delay_seconds() == MIN_DELAY_SECONDS


def test_robots_4xx_means_no_restriction():
    for status in (401, 403, 404, 410):
        policy = policy_from_response(status, "")
        assert policy.allows("https://clinic.example/private/notes")
        assert policy == ALLOW_ALL or policy.allow_all


@pytest.mark.parametrize("status", [500, 503, 599])
def test_robots_5xx_means_do_not_crawl(status):
    policy = policy_from_response(status, "")
    assert policy.deny_all
    assert not policy.allows("https://clinic.example/hours")


def test_robots_with_no_answer_means_do_not_crawl():
    assert policy_from_response(None, "") == DENY_ALL


def test_robots_redirect_not_followed_means_do_not_crawl():
    assert policy_from_response(302, "").deny_all


# ---------------------------------------------------------------- extraction


PAGE = """<!doctype html><html><head><title>  Opening   hours </title>
<script>var tracking = "do not index";</script><style>p{color:red}</style></head>
<body>
<nav><a href="/about">About us</a> <a href="/contact?x=1">Contact</a></nav>
<div class="cookie-banner">We use cookies. Accept?</div>
<div id="gdpr-consent">Consent preferences</div>
<main>
  <h1>Hours</h1>
  <p>Open Monday to Friday, 9 to 5.</p>
  <form><input value="search"><button>Search</button></form>
</main>
<footer>Copyright Clinic. <a href="/team">Team</a></footer>
</body></html>"""


def test_extraction_keeps_the_main_content_and_drops_the_boilerplate():
    page = extract_page(PAGE)
    assert page.title == "Opening hours"
    assert page.text == "Hours\nOpen Monday to Friday, 9 to 5."
    for leftover in ("tracking", "cookies", "Consent", "Copyright", "Search", "About us"):
        assert leftover not in page.text


def test_links_are_read_before_navigation_is_removed():
    page = extract_page(PAGE)
    assert "/about" in page.links
    assert "/contact?x=1" in page.links
    assert "/team" in page.links


def test_page_without_main_uses_the_body():
    page = extract_page("<html><body><p>Walk-ins welcome.</p></body></html>")
    assert page.text == "Walk-ins welcome."


def test_text_is_capped_at_200_kb_and_marked_truncated():
    long_page = "<main><p>" + ("word " * 100_000) + "</p></main>"
    page = extract_page(long_page)
    assert page.truncated
    assert len(page.text.encode("utf-8")) <= MAX_TEXT_BYTES


def test_short_text_is_not_marked_truncated():
    assert extract_page("<main><p>Short.</p></main>").truncated is False


# ---------------------------------------------------------------- hashes and diffs


def test_hash_ignores_spacing_and_capitalization():
    a = content_hash("Hours", "Open   Monday.\n\nClosed Sunday.")
    b = content_hash("  hours ", "open monday. closed sunday.")
    assert a == b
    assert len(a) == 64


def test_hash_changes_when_the_words_change():
    assert content_hash("Hours", "Open Monday.") != content_hash("Hours", "Open Tuesday.")


def test_hash_changes_when_the_title_changes():
    assert content_hash("Hours", "Open Monday.") != content_hash("Opening hours", "Open Monday.")


def test_normalize_collapses_whitespace_and_case():
    assert normalize("  A \n\t B  ") == "a b"


def test_word_diff_counts_added_and_removed_words():
    diff = word_diff("Open Monday to Friday.", "Open Monday to Saturday, 9 to 5.")
    assert diff.words_removed == 1  # Friday.
    assert diff.words_added == 4  # Saturday, 9 to 5.
    assert [s.op for s in diff.segments] == ["equal", "delete", "insert"]
    assert " ".join(s.text for s in diff.segments if s.op != "delete") == (
        "Open Monday to Saturday, 9 to 5."
    )


def test_word_diff_against_nothing_is_all_insertions():
    diff = word_diff("", "New page text here")
    assert (diff.words_added, diff.words_removed) == (4, 0)
    assert [s.op for s in diff.segments] == ["insert"]


def test_word_diff_ignores_whitespace_only_changes():
    diff = word_diff("Open  Monday", "Open\nMonday")
    assert (diff.words_added, diff.words_removed) == (0, 0)
    assert [s.op for s in diff.segments] == ["equal"]
