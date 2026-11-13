"""Main text of a page, and the links on it.

Boilerplate is removed by tag (scripts, navigation, headers, footers, forms) and by class or id
for cookie and consent banners. The page's main or article element is preferred when there is
one. The heuristics miss some noise, so the admin reads the extracted text before it reaches
the chatbot.
"""

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Tag

MAX_TEXT_BYTES = 200 * 1024

_DROP_TAGS = ("script", "style", "noscript", "template", "svg", "iframe", "form", "button")
_CHROME_TAGS = ("nav", "header", "footer", "aside", "select", "option")
_NOISE_ATTRIBUTE = re.compile(r"cookie|consent|gdpr|breadcrumb|skip-link", re.IGNORECASE)


@dataclass(frozen=True)
class ExtractedPage:
    title: str
    text: str
    truncated: bool
    links: list[str] = field(default_factory=list)


def _attribute_text(tag: Tag) -> str:
    classes = tag.get("class") or []
    if isinstance(classes, str):
        classes = [classes]
    return " ".join([*classes, tag.get("id") or ""])


def extract_page(html: str) -> ExtractedPage:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""

    # Links are read before anything is removed: navigation is how the crawler finds pages.
    links = [a["href"] for a in soup.find_all("a", href=True) if isinstance(a["href"], str)]

    for name in (*_DROP_TAGS, *_CHROME_TAGS):
        for tag in soup.find_all(name):
            tag.decompose()
    for tag in soup.find_all(True):
        if _NOISE_ATTRIBUTE.search(_attribute_text(tag)):
            tag.decompose()

    root = soup.find("main") or soup.find("article") or soup.body or soup
    lines = [line.strip() for line in root.get_text("\n").splitlines()]
    text = "\n".join(line for line in lines if line)

    encoded = text.encode("utf-8")
    truncated = len(encoded) > MAX_TEXT_BYTES
    if truncated:
        text = encoded[:MAX_TEXT_BYTES].decode("utf-8", errors="ignore")
    return ExtractedPage(title=" ".join(title.split()), text=text, truncated=truncated, links=links)
