"""robots.txt for the crawler (RFC 9309).

A 4xx answer for robots.txt means no restriction. A 5xx answer, a redirect that is not
followed, or no answer at all means the site cannot be crawled this run.

The text is parsed here and not by urllib.robotparser's own fetcher, because that fetcher
would skip the pinned-address check. The parser only reads the rules.
"""

from dataclasses import dataclass
from urllib.robotparser import RobotFileParser

# The token robots.txt rules are matched against. Generic, and it names the crawler.
USER_AGENT_TOKEN = "WebsiteAssistantCrawler"
USER_AGENT = f"{USER_AGENT_TOKEN}/1.0 (+site content index)"
MIN_DELAY_SECONDS = 1.0
MAX_CRAWL_DELAY_SECONDS = 10.0
MAX_ROBOTS_BYTES = 500 * 1024


@dataclass(frozen=True)
class RobotsPolicy:
    allow_all: bool
    deny_all: bool
    parser: RobotFileParser | None
    crawl_delay: float | None

    def allows(self, url: str) -> bool:
        if self.deny_all:
            return False
        if self.allow_all or self.parser is None:
            return True
        return self.parser.can_fetch(USER_AGENT_TOKEN, url)

    def delay_seconds(self) -> float:
        """Seconds to wait between requests: at least the minimum, at most the cap."""
        requested = self.crawl_delay or 0.0
        return max(MIN_DELAY_SECONDS, min(requested, MAX_CRAWL_DELAY_SECONDS))


DENY_ALL = RobotsPolicy(allow_all=False, deny_all=True, parser=None, crawl_delay=None)
ALLOW_ALL = RobotsPolicy(allow_all=True, deny_all=False, parser=None, crawl_delay=None)


def policy_from_response(status_code: int | None, body: str) -> RobotsPolicy:
    """The policy for one robots.txt answer. status_code None means no answer at all."""
    if status_code is None or status_code >= 500:
        return DENY_ALL
    if 400 <= status_code < 500:
        return ALLOW_ALL
    if not 200 <= status_code < 300:
        # A redirect the fetcher did not follow, or something unexpected: do not guess.
        return DENY_ALL
    parser = RobotFileParser()
    parser.parse(body.splitlines())
    delay = parser.crawl_delay(USER_AGENT_TOKEN)
    return RobotsPolicy(
        allow_all=False,
        deny_all=False,
        parser=parser,
        crawl_delay=float(delay) if delay is not None else None,
    )
