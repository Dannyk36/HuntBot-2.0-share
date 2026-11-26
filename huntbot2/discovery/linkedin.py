from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin

from huntbot2.discovery.models import JobRecord
from huntbot2.discovery.normalize import (
    normalize_company,
    normalize_location,
    normalize_title,
    normalize_url,
)


class _CardParser(HTMLParser):
    def __init__(self, source_url: str):
        super().__init__()
        self.source_url = source_url
        self.cards: list[dict[str, str]] = []
        self._in_card = False
        self._depth = 0
        self._field: str | None = None
        self._current: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        attr_map = {k: (v or "") for k, v in attrs}
        classes = set(attr_map.get("class", "").split())

        if tag == "div" and "job-card" in classes and not self._in_card:
            self._in_card = True
            self._depth = 1
            self._current = {}
            return

        if self._in_card and tag == "div":
            self._depth += 1

        if not self._in_card or self._current is None:
            return

        if tag == "a" and "job-link" in classes:
            href = attr_map.get("href", "")
            self._current["url"] = urljoin(self.source_url, href)
            self._field = "title"
        elif tag in {"h3", "span", "div", "time"}:
            if "title" in classes:
                self._field = "title"
            elif "company" in classes:
                self._field = "company"
            elif "location" in classes:
                self._field = "location"
            elif "posted" in classes:
                self._field = "posted_at"

    def handle_endtag(self, tag: str):
        if self._in_card and tag == "div":
            self._depth -= 1
            if self._depth == 0:
                if self._current:
                    self.cards.append(self._current)
                self._current = None
                self._in_card = False
        self._field = None

    def handle_data(self, data: str):
        if not self._in_card or self._current is None or self._field is None:
            return
        text = unescape(data).strip()
        if not text:
            return
        prior = self._current.get(self._field, "")
        self._current[self._field] = (f"{prior} {text}" if prior else text).strip()


def discover_from_search_html(html: str, source_url: str) -> list[JobRecord]:
    parser = _CardParser(source_url=source_url)
    parser.feed(html)
    jobs: list[JobRecord] = []
    for card in parser.cards:
        title = normalize_title(card.get("title", ""))
        url = normalize_url(card.get("url", ""))
        if not title or not url:
            continue
        jobs.append(
            JobRecord(
                source="linkedin",
                title=title,
                company=normalize_company(card.get("company", "")),
                location=normalize_location(card.get("location", "")),
                url=url,
                posted_at=card.get("posted_at"),
                raw_snapshot=str(card),
            )
        )
    return jobs
