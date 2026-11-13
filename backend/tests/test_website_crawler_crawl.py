"""One crawl, end to end over a fake site: what is fetched, what is proposed, and the limits.

The fetcher is real (through a MockTransport). The database is the in-memory FakeCrawlDb.
Nothing here indexes anything: a crawl only proposes changes.
"""

import httpx
import pytest
from crawler_fakes import (
    SITE_HOST,
    FakeCrawlDb,
    FakeSite,
    html_page,
    install,
    make_client,
    make_run,
    page_html,
    public_resolver,
)

from app.db.models.crawler import (
    CHANGE_CHANGED,
    CHANGE_NEW,
    CHANGE_REMOVED,
    RUN_COMPLETED,
    RUN_FAILED,
    UPDATE_PENDING,
    UPDATE_SUPERSEDED,
)
from app.services.website_crawler import crawl as crawl_module
from app.services.website_crawler.diffing import content_hash
from app.services.website_crawler.extract import extract_page
from app.services.website_crawler.urls import MAX_DEPTH, MAX_PAGES

MODULES = ["app.services.website_crawler.crawl"]
ROOT = f"https://{SITE_HOST}/"
DISALLOW_PRIVATE = "User-agent: *\nDisallow: /private\n"


def live_of(title: str, body: str) -> tuple[str, str]:
    """(text, hash) the chatbot would hold for this page: what the crawler computes."""
    extracted = extract_page(page_html(title, body))
    return extracted.text, content_hash(extracted.title, extracted.text)


async def crawl(monkeypatch, db: FakeCrawlDb, site: FakeSite, client=None, **kwargs):
    install(monkeypatch, db, MODULES)
    client = client or make_client(db.client_id)
    run = make_run(client.id)
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    await crawl_module.execute_crawl(
        run,
        client,
        resolver=public_resolver,
        transport=site.transport(),
        sleep=fake_sleep,
        **kwargs,
    )
    return db.finished_runs[-1], sleeps


def fetched(site: FakeSite) -> list[str]:
    return site.fetched_paths()


def changes_for(db: FakeCrawlDb, url_suffix: str):
    """Open changes of one page. The root is new on most tests, so tests look at their page."""
    return [c for c in db.open_changes() if c.page_url.endswith(url_suffix)]


@pytest.fixture
def db():
    return FakeCrawlDb(client_id=make_client().id)


# ---------------------------------------------------------------- discovery


async def test_first_crawl_proposes_each_page_as_new(monkeypatch, db):
    site = FakeSite(
        {
            "/": (200, page_html("Home", "Welcome.", links=[
                "/hours", "/contact?x=1", "/brochure.pdf", "https://other.example/", "/hours/",
            ]), {"content-type": "text/html"}),
            "/hours": (200, page_html("Hours", "Open Monday."), {"content-type": "text/html"}),
            "/contact": (200, page_html("Contact", "Call us."), {"content-type": "text/html"}),
        }
    )  # fmt: skip

    run, _ = await crawl(monkeypatch, db, site)

    assert run["status"] == RUN_COMPLETED
    assert run["pages_fetched"] == 3
    assert run["pages_changed"] == 3
    assert sorted(c.page_url for c in db.open_changes()) == [
        ROOT, f"https://{SITE_HOST}/contact", f"https://{SITE_HOST}/hours",
    ]  # fmt: skip
    assert {c.change_type for c in db.open_changes()} == {CHANGE_NEW}
    # Off-site, file and duplicate links are never requested.
    assert "/brochure.pdf" not in fetched(site)
    assert sorted(fetched(site)) == ["/", "/contact", "/hours", "/robots.txt"]


async def test_a_known_page_is_rechecked_even_when_nothing_links_to_it(monkeypatch, db):
    live_text, live_hash = live_of("Hours", "Open Monday.")
    db.add_page(
        f"https://{SITE_HOST}/hours", live_text=live_text, live_hash=live_hash, title="Hours"
    )
    site = FakeSite({"/": html_page("Home", "Hi."), "/hours": html_page("Hours", "Open Monday.")})

    await crawl(monkeypatch, db, site)

    assert "/hours" in fetched(site)
    # Unchanged content proposes nothing for the known page (the root is new, so it may).
    assert [c for c in db.open_changes() if c.page_url.endswith("/hours")] == []


async def test_depth_stops_at_three_levels_from_the_root(monkeypatch, db):
    site = FakeSite(
        {
            "/": html_page("Home", "Hi.", links=["/d1"]),
            "/d1": html_page("One", "One.", links=["/d2"]),
            "/d2": html_page("Two", "Two.", links=["/d3"]),
            "/d3": html_page("Three", "Three.", links=["/d4"]),
            "/d4": html_page("Four", "Four."),
        }
    )
    assert MAX_DEPTH == 3

    await crawl(monkeypatch, db, site)

    paths = fetched(site)
    assert "/d3" in paths
    assert "/d4" not in paths


async def test_a_crawl_fetches_at_most_thirty_pages(monkeypatch, db):
    pages = {"/": html_page("Home", "Hi.", links=[f"/p{i}" for i in range(40)])}
    pages.update({f"/p{i}": html_page(f"Page {i}", f"Text {i}.") for i in range(40)})
    site = FakeSite(pages)

    run, _ = await crawl(monkeypatch, db, site)

    assert run["pages_fetched"] == MAX_PAGES == 30
    assert len([p for p in fetched(site) if p != "/robots.txt"]) == MAX_PAGES


async def test_excluded_pages_are_not_fetched(monkeypatch, db):
    db.add_page(f"https://{SITE_HOST}/old", included=False)
    site = FakeSite(
        {
            "/": html_page("Home", "Hi.", links=["/old"]),
            "/old": html_page("Old", "Old text."),
        }
    )

    await crawl(monkeypatch, db, site)

    assert "/old" not in fetched(site)
    assert db.page_by_url(f"https://{SITE_HOST}/old").live_content_hash is None


# ---------------------------------------------------------------- robots.txt


async def test_robots_disallowed_paths_are_not_fetched(monkeypatch, db):
    site = FakeSite(
        {
            "/robots.txt": (200, DISALLOW_PRIVATE, {"content-type": "text/plain"}),
            "/": html_page("Home", "Hi.", links=["/private/notes", "/hours"]),
            "/private/notes": html_page("Notes", "Secret."),
            "/hours": html_page("Hours", "Open."),
        }
    )  # fmt: skip

    await crawl(monkeypatch, db, site)

    assert "/private/notes" not in fetched(site)
    assert "/hours" in fetched(site)


async def test_robots_5xx_stops_the_crawl(monkeypatch, db):
    site = FakeSite(
        {
            "/robots.txt": (503, "maintenance", {"content-type": "text/plain"}),
            "/": html_page("Home", "Hi."),
        }
    )

    run, _ = await crawl(monkeypatch, db, site)

    assert run["status"] == RUN_FAILED
    assert "robots.txt" in run["error"]
    assert fetched(site) == ["/robots.txt"]
    assert db.open_changes() == []


async def test_robots_unreachable_stops_the_crawl(monkeypatch, db):
    site = FakeSite({"/": html_page("Home", "Hi.")})
    base = site.transport()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            raise httpx.ConnectError("refused")
        return base.handle_request(request)

    install(monkeypatch, db, MODULES)
    client = make_client(db.client_id)
    run = make_run(client.id)

    async def no_sleep(_seconds):
        return None

    await crawl_module.execute_crawl(
        run,
        client,
        resolver=public_resolver,
        transport=httpx.MockTransport(handler),
        sleep=no_sleep,
    )
    assert db.finished_runs[-1]["status"] == RUN_FAILED
    assert fetched(site) == []


async def test_robots_4xx_means_the_site_can_be_crawled(monkeypatch, db):
    site = FakeSite({"/": html_page("Home", "Hi.")})  # robots.txt is 404

    run, _ = await crawl(monkeypatch, db, site)

    assert run["status"] == RUN_COMPLETED
    assert "/" in fetched(site)


async def test_crawl_delay_from_robots_is_the_pause_between_requests(monkeypatch, db):
    site = FakeSite(
        {
            "/robots.txt": (200, "User-agent: *\nCrawl-delay: 3\n", {"content-type": "text/plain"}),
            "/": html_page("Home", "Hi.", links=["/a", "/b"]),
            "/a": html_page("A", "A."),
            "/b": html_page("B", "B."),
        }
    )

    _, sleeps = await crawl(monkeypatch, db, site)

    assert sleeps == [3.0, 3.0]


async def test_without_crawl_delay_the_pause_is_one_second(monkeypatch, db):
    site = FakeSite({"/": html_page("Home", "Hi.", links=["/a"]), "/a": html_page("A", "A.")})

    _, sleeps = await crawl(monkeypatch, db, site)

    assert sleeps == [1.0]


# ---------------------------------------------------------------- changes


async def test_changed_text_is_proposed_as_changed_with_word_counts(monkeypatch, db):
    live_text, live_hash = live_of("Hours", "Open Monday to Friday.")
    db.add_page(ROOT, live_text=live_text, live_hash=live_hash, title="Hours")
    site = FakeSite({"/": html_page("Hours", "Open Monday to Saturday.")})

    run, _ = await crawl(monkeypatch, db, site)

    (change,) = db.open_changes()
    assert change.change_type == CHANGE_CHANGED
    assert (change.words_added, change.words_removed) == (1, 1)
    assert run["pages_changed"] == 1


async def test_unchanged_text_proposes_nothing(monkeypatch, db):
    live_text, live_hash = live_of("Hours", "Open Monday.")
    db.add_page(ROOT, live_text=live_text, live_hash=live_hash, title="Hours")
    site = FakeSite({"/": html_page("Hours", "Open Monday.")})

    run, _ = await crawl(monkeypatch, db, site)

    assert db.open_changes() == []
    assert run["pages_changed"] == 0


async def test_a_live_page_that_disappears_is_proposed_for_removal(monkeypatch, db):
    live_text, live_hash = live_of("Hours", "Open Monday.")
    page = db.add_page(f"https://{SITE_HOST}/hours", live_text=live_text, live_hash=live_hash)
    site = FakeSite(
        {
            "/": html_page("Home", "Hi.", links=["/hours"]),
            "/hours": (404, "gone", {"content-type": "text/html"}),
        }
    )

    await crawl(monkeypatch, db, site)

    (change,) = changes_for(db, "/hours")
    assert change.change_type == CHANGE_REMOVED
    assert change.page_id == page.id
    assert change.words_removed == 3  # live text "Hours / Open Monday.", counted in words
    assert db.pages[page.id].live_content_hash == live_hash  # nothing removed until approved


async def test_a_page_gone_with_410_is_treated_as_removed(monkeypatch, db):
    live_text, live_hash = live_of("Hours", "Open Monday.")
    db.add_page(f"https://{SITE_HOST}/hours", live_text=live_text, live_hash=live_hash)
    site = FakeSite({"/": html_page("Home", "Hi.", links=["/hours"]), "/hours": (410, "", {})})

    await crawl(monkeypatch, db, site)

    assert [c.change_type for c in changes_for(db, "/hours")] == [CHANGE_REMOVED]


async def test_a_missing_page_that_was_never_live_proposes_nothing(monkeypatch, db):
    site = FakeSite({"/": html_page("Home", "Hi.", links=["/gone"]), "/gone": (404, "", {})})

    run, _ = await crawl(monkeypatch, db, site)

    assert changes_for(db, "/gone") == []
    assert run["pages_failed"] == 0


async def test_a_server_error_on_a_live_page_is_not_a_removal(monkeypatch, db):
    live_text, live_hash = live_of("Hours", "Open Monday.")
    db.add_page(f"https://{SITE_HOST}/hours", live_text=live_text, live_hash=live_hash)
    site = FakeSite({"/": html_page("Home", "Hi.", links=["/hours"]), "/hours": (500, "oops", {})})

    run, _ = await crawl(monkeypatch, db, site)

    assert changes_for(db, "/hours") == []
    assert run["pages_failed"] == 1
    assert db.page_by_url(f"https://{SITE_HOST}/hours").last_error == "HTTP 500"


async def test_a_rejected_version_is_not_proposed_again(monkeypatch, db):
    _, rejected_hash = live_of("Hours", "Open Monday to Saturday.")
    live_text, live_hash = live_of("Hours", "Open Monday to Friday.")
    db.add_page(ROOT, live_text=live_text, live_hash=live_hash, rejected_hash=rejected_hash)
    site = FakeSite({"/": html_page("Hours", "Open Monday to Saturday.")})

    await crawl(monkeypatch, db, site)

    assert db.open_changes() == []


async def test_a_different_version_after_a_rejection_is_proposed(monkeypatch, db):
    _, rejected_hash = live_of("Hours", "Open Monday to Saturday.")
    live_text, live_hash = live_of("Hours", "Open Monday to Friday.")
    db.add_page(ROOT, live_text=live_text, live_hash=live_hash, rejected_hash=rejected_hash)
    site = FakeSite({"/": html_page("Hours", "Open Monday to Sunday.")})

    await crawl(monkeypatch, db, site)

    assert [c.change_type for c in db.open_changes()] == [CHANGE_CHANGED]


async def test_an_open_change_is_superseded_when_the_page_is_back_to_live(monkeypatch, db):
    live_text, live_hash = live_of("Hours", "Open Monday.")
    page = db.add_page(ROOT, live_text=live_text, live_hash=live_hash, title="Hours")
    stale = await db.insert_pending_change(
        client_id=db.client_id,
        page_id=page.id,
        crawl_run_id=None,
        change_type=CHANGE_CHANGED,
        proposed_title="Hours",
        proposed_text="Open Sunday.",
        proposed_hash="c" * 64,
        words_added=2,
        words_removed=2,
    )
    site = FakeSite({"/": html_page("Hours", "Open Monday.")})

    await crawl(monkeypatch, db, site)

    assert db.pending[stale].status == UPDATE_SUPERSEDED
    assert db.open_changes() == []


async def test_the_same_new_text_is_not_proposed_twice(monkeypatch, db):
    site = FakeSite({"/": html_page("Hours", "Open Monday.")})

    await crawl(monkeypatch, db, site)
    await crawl(monkeypatch, db, site)

    assert len(db.open_changes()) == 1
    assert db.open_changes()[0].status == UPDATE_PENDING


async def test_a_redirect_off_the_site_is_a_failure_with_no_change(monkeypatch, db):
    site = FakeSite(
        {
            "/": html_page("Home", "Hi.", links=["/hours"]),
            "/hours": (302, "", {"location": "https://evil.example/hours"}),
        }
    )

    run, _ = await crawl(monkeypatch, db, site)

    assert changes_for(db, "/hours") == []
    assert run["pages_failed"] == 1
    assert db.page_by_url(f"https://{SITE_HOST}/hours").last_error == "REDIRECT_BLOCKED"


async def test_a_non_html_response_is_skipped_without_a_change(monkeypatch, db):
    site = FakeSite(
        {
            "/": html_page("Home", "Hi.", links=["/feed"]),
            "/feed": (200, "<rss></rss>", {"content-type": "application/rss+xml"}),
        }
    )

    run, _ = await crawl(monkeypatch, db, site)

    assert [c.page_url for c in db.open_changes()] == [ROOT]
    assert db.page_by_url(f"https://{SITE_HOST}/feed").last_error == "Not an HTML page"
    assert run["pages_failed"] == 0


async def test_a_page_with_no_readable_text_is_a_failure_not_a_change(monkeypatch, db):
    site = FakeSite(
        {
            "/": (
                200,
                "<html><body><script>x()</script></body></html>",
                {"content-type": "text/html"},
            )
        }
    )

    run, _ = await crawl(monkeypatch, db, site)

    assert db.open_changes() == []
    assert run["pages_failed"] == 1


async def test_a_crash_during_the_crawl_closes_the_run_as_failed(monkeypatch, db):
    site = FakeSite({"/": html_page("Home", "Hi.")})

    async def broken(*_args, **_kwargs):
        raise RuntimeError("database went away")

    db.ensure_crawled_page = broken  # shadows the fake's method when crawl() installs it
    run, _ = await crawl(monkeypatch, db, site)

    assert run["status"] == RUN_FAILED
    assert run["error"] == "Crawl stopped: RuntimeError"


# ---------------------------------------------------------------- the scheduler's gate


async def test_the_scheduler_skips_a_client_that_is_already_crawling(monkeypatch):
    from app.db.models.crawler import CrawlAlreadyRunning
    from app.services.website_crawler import scheduler

    busy, free = make_client(), make_client()
    started: list = []

    async def due(*, interval_days):
        return [busy.id, free.id]

    async def get_client(client_id):
        return busy if client_id == busy.id else free

    async def begin_run(client, *, trigger, requested_by):
        if client is busy:
            raise CrawlAlreadyRunning()
        return make_run(client.id, trigger)

    async def execute(run, client, **_kwargs):
        started.append(client.id)

    monkeypatch.setattr(scheduler, "clients_due_for_crawl", due)
    monkeypatch.setattr(scheduler, "get_client", get_client)
    monkeypatch.setattr(scheduler, "begin_run", begin_run)
    monkeypatch.setattr(scheduler, "execute_crawl", execute)

    ran = await scheduler.run_due_crawls(resolver=public_resolver, transport=None)

    assert ran == 1
    assert started == [free.id]


async def test_only_the_advisory_lock_holder_is_the_leader():
    from app.services.website_crawler.scheduler import try_become_leader

    class Cursor:
        def __init__(self, value):
            self.value = value

        async def fetchone(self):
            return (self.value,)

    class Conn:
        def __init__(self, value):
            self.value = value

        async def execute(self, *_args):
            return Cursor(self.value)

    assert await try_become_leader(Conn(True)) is True
    assert await try_become_leader(Conn(False)) is False


async def test_a_client_without_a_website_cannot_start_a_crawl():
    from app.services.website_crawler.crawl import CrawlNotConfigured, begin_run

    with pytest.raises(CrawlNotConfigured):
        await begin_run(make_client(website_url=None), trigger="manual", requested_by=None)
