#!/usr/bin/env python3
"""Daily literature alert for PLM/PPI/proximity-labeling papers."""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
import yaml


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "queries.yaml"
STATE_PATH = ROOT / "data" / "seen_articles.json"


@dataclass
class Paper:
    title: str
    source: str
    date: str = ""
    authors: list[str] = field(default_factory=list)
    abstract: str = ""
    url: str = ""
    doi: str = ""
    identifier: str = ""
    score: int = 0
    reasons: list[str] = field(default_factory=list)

    def key(self) -> str:
        if self.doi:
            return "doi:" + self.doi.lower().strip()
        if self.identifier:
            return f"{self.source.lower()}:{self.identifier.lower().strip()}"
        clean = normalize_text(self.title)
        return "title:" + clean[:180]


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def clean_title(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().rstrip(".")


def parse_date(value: str) -> dt.date | None:
    if not value:
        return None
    value = value[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y %b %d", "%Y"):
        try:
            parsed = dt.datetime.strptime(value, fmt)
            return parsed.date()
        except ValueError:
            continue
    return None


def request_json(url: str, *, params: dict[str, Any] | None = None, timeout: int = 30) -> Any:
    response = requests.get(url, params=params, timeout=timeout, headers={"User-Agent": "plm-ppi-literature-alert/1.0"})
    response.raise_for_status()
    return response.json()


def request_text(url: str, *, params: dict[str, Any] | None = None, timeout: int = 30) -> str:
    response = requests.get(url, params=params, timeout=timeout, headers={"User-Agent": "plm-ppi-literature-alert/1.0"})
    response.raise_for_status()
    return response.text


def fetch_pubmed(config: dict[str, Any], days_back: int) -> list[Paper]:
    papers: list[Paper] = []
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    today = dt.date.today()
    mindate = today - dt.timedelta(days=days_back)

    for query in config["sources"]["pubmed"]["queries"]:
        params = {
            "db": "pubmed",
            "term": query,
            "retmode": "json",
            "retmax": 30,
            "sort": "pub+date",
            "datetype": "pdat",
            "mindate": mindate.isoformat(),
            "maxdate": today.isoformat(),
        }
        try:
            data = request_json(f"{base}/esearch.fcgi", params=params)
            ids = data.get("esearchresult", {}).get("idlist", [])
            if not ids:
                continue
            summary = request_json(
                f"{base}/esummary.fcgi",
                params={"db": "pubmed", "id": ",".join(ids), "retmode": "json"},
            )
        except Exception as exc:
            print(f"PubMed query failed: {query} ({exc})", file=sys.stderr)
            continue

        result = summary.get("result", {})
        for pmid in ids:
            item = result.get(pmid, {})
            title = clean_title(item.get("title", ""))
            if not title:
                continue
            authors = [a.get("name", "") for a in item.get("authors", [])[:6] if a.get("name")]
            article_ids = item.get("articleids", [])
            doi = ""
            for article_id in article_ids:
                if article_id.get("idtype") == "doi":
                    doi = article_id.get("value", "")
                    break
            url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
            papers.append(
                Paper(
                    title=title,
                    source="PubMed",
                    date=item.get("pubdate", ""),
                    authors=authors,
                    url=url,
                    doi=doi,
                    identifier=pmid,
                )
            )
        time.sleep(0.35)

    return papers


def fetch_arxiv(config: dict[str, Any], days_back: int) -> list[Paper]:
    papers: list[Paper] = []
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_back)

    for query in config["sources"]["arxiv"]["queries"]:
        params = {
            "search_query": query,
            "start": 0,
            "max_results": 30,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        try:
            text = request_text("https://export.arxiv.org/api/query", params=params)
            root = ET.fromstring(text)
        except Exception as exc:
            print(f"arXiv query failed: {query} ({exc})", file=sys.stderr)
            continue

        for entry in root.findall("atom:entry", ns):
            published_text = entry.findtext("atom:published", default="", namespaces=ns)
            try:
                published = dt.datetime.fromisoformat(published_text.replace("Z", "+00:00"))
            except ValueError:
                published = None
            if published and published < cutoff:
                continue
            title = clean_title(entry.findtext("atom:title", default="", namespaces=ns))
            if not title:
                continue
            url = entry.findtext("atom:id", default="", namespaces=ns)
            authors = [a.findtext("atom:name", default="", namespaces=ns) for a in entry.findall("atom:author", ns)]
            abstract = clean_title(entry.findtext("atom:summary", default="", namespaces=ns))
            identifier = url.rsplit("/", 1)[-1] if url else title
            papers.append(
                Paper(
                    title=title,
                    source="arXiv",
                    date=published.date().isoformat() if published else "",
                    authors=[a for a in authors[:6] if a],
                    abstract=abstract,
                    url=url,
                    identifier=identifier,
                )
            )
        time.sleep(3.1)

    return papers


def fetch_openalex(config: dict[str, Any], days_back: int) -> list[Paper]:
    papers: list[Paper] = []
    cutoff = dt.date.today() - dt.timedelta(days=days_back)

    for query in config["sources"]["openalex"]["queries"]:
        params = {
            "search": query,
            "filter": f"from_publication_date:{cutoff.isoformat()}",
            "sort": "publication_date:desc",
            "per-page": 25,
        }
        try:
            data = request_json("https://api.openalex.org/works", params=params)
        except Exception as exc:
            print(f"OpenAlex query failed: {query} ({exc})", file=sys.stderr)
            continue

        for item in data.get("results", []):
            title = clean_title(item.get("title", ""))
            if not title:
                continue
            authorships = item.get("authorships", [])
            authors = []
            for authorship in authorships[:6]:
                name = authorship.get("author", {}).get("display_name")
                if name:
                    authors.append(name)
            doi = (item.get("doi") or "").replace("https://doi.org/", "")
            url = item.get("doi") or item.get("id") or ""
            abstract = inverted_index_to_text(item.get("abstract_inverted_index"))
            papers.append(
                Paper(
                    title=title,
                    source="OpenAlex",
                    date=item.get("publication_date", ""),
                    authors=authors,
                    abstract=abstract,
                    url=url,
                    doi=doi,
                    identifier=item.get("id", ""),
                )
            )
        time.sleep(0.5)

    return papers


def inverted_index_to_text(index: dict[str, list[int]] | None) -> str:
    if not index:
        return ""
    words: list[tuple[int, str]] = []
    for word, positions in index.items():
        for position in positions:
            words.append((position, word))
    return " ".join(word for _, word in sorted(words))


def fetch_biorxiv_like(server: str, enabled: bool, days_back: int) -> list[Paper]:
    if not enabled:
        return []

    papers: list[Paper] = []
    today = dt.date.today()
    start = today - dt.timedelta(days=days_back)
    cursor = 0
    max_pages = 5

    for _ in range(max_pages):
        url = f"https://api.biorxiv.org/details/{server}/{start.isoformat()}/{today.isoformat()}/{cursor}"
        try:
            data = request_json(url)
        except Exception as exc:
            print(f"{server} query failed: {exc}", file=sys.stderr)
            break
        collection = data.get("collection", [])
        if not collection:
            break
        for item in collection:
            title = clean_title(item.get("title", ""))
            if not title:
                continue
            doi = item.get("doi", "")
            papers.append(
                Paper(
                    title=title,
                    source=server,
                    date=item.get("date", ""),
                    authors=[a.strip() for a in item.get("authors", "").split(";")[:6] if a.strip()],
                    abstract=clean_title(item.get("abstract", "")),
                    url=f"https://doi.org/{doi}" if doi else "",
                    doi=doi,
                    identifier=doi or title,
                )
            )
        cursor += len(collection)
        if len(collection) < 100:
            break
        time.sleep(0.5)

    return papers


def score_paper(paper: Paper, config: dict[str, Any]) -> Paper:
    weights = config.get("score_weights", {})
    positive_weight = int(weights.get("positive_keyword", 1))
    phrase_weight = int(weights.get("high_priority_phrase", 3))
    title_multiplier = int(weights.get("title_match_multiplier", 2))
    negative_weight = int(weights.get("negative_keyword", -3))

    title = normalize_text(paper.title)
    body = normalize_text(" ".join([paper.title, paper.abstract]))
    score = 0
    reasons: list[str] = []

    for keyword in config.get("positive_keywords", []):
        key = normalize_text(keyword)
        if key and key in body:
            add = positive_weight * (title_multiplier if key in title else 1)
            score += add
            reasons.append(keyword)

    for phrase in config.get("high_priority_phrases", []):
        key = normalize_text(phrase)
        if key and key in body:
            add = phrase_weight * (title_multiplier if key in title else 1)
            score += add
            reasons.append(f"high priority: {phrase}")

    for keyword in config.get("negative_keywords", []):
        key = normalize_text(keyword)
        if key and key in body:
            score += negative_weight
            reasons.append(f"excluded-ish: {keyword}")

    paper.score = score
    paper.reasons = sorted(set(reasons), key=str.lower)[:10]
    return paper


def deduplicate(papers: list[Paper]) -> list[Paper]:
    by_key: dict[str, Paper] = {}
    for paper in papers:
        key = paper.key()
        existing = by_key.get(key)
        if not existing or paper.score > existing.score:
            by_key[key] = paper
    return list(by_key.values())


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"seen": {}}
    with STATE_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with STATE_PATH.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)
        handle.write("\n")


def render_email(papers: list[Paper], config: dict[str, Any], days_back: int) -> tuple[str, str]:
    today = dt.date.today().isoformat()
    title = config.get("alert", {}).get("title", "Daily Literature Alert")
    subject = f"{title} - {today}"

    if not papers:
        plain = f"No new papers passed the relevance threshold in the last {days_back} days."
        html_body = f"<p>{html.escape(plain)}</p>"
        return subject, wrap_html(title, html_body)

    rows = []
    plain_lines = [subject, ""]
    for idx, paper in enumerate(papers, 1):
        authors = ", ".join(paper.authors[:4])
        if len(paper.authors) > 4:
            authors += " et al."
        reasons = ", ".join(paper.reasons[:6]) or "keyword match"
        abstract = paper.abstract[:700] + ("..." if len(paper.abstract) > 700 else "")
        url_html = f'<a href="{html.escape(paper.url)}">link</a>' if paper.url else ""
        rows.append(
            f"""
            <div class="paper">
              <div class="rank">#{idx} · score {paper.score} · {html.escape(paper.source)} · {html.escape(paper.date)}</div>
              <h2>{html.escape(paper.title)}</h2>
              <p class="authors">{html.escape(authors)}</p>
              <p><strong>Why relevant:</strong> {html.escape(reasons)}</p>
              <p>{html.escape(abstract)}</p>
              <p>{url_html}</p>
            </div>
            """
        )
        plain_lines.extend(
            [
                f"{idx}. {paper.title}",
                f"   Source: {paper.source}; date: {paper.date}; score: {paper.score}",
                f"   Why relevant: {reasons}",
                f"   Link: {paper.url}",
                "",
            ]
        )

    context = config.get("alert", {}).get("project_context", "")
    html_body = f"""
    <p class="context">{html.escape(context)}</p>
    <p>Found {len(papers)} new relevant papers from the last {days_back} days.</p>
    {''.join(rows)}
    """
    return subject, wrap_html(title, html_body)


def wrap_html(title: str, body: str) -> str:
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif; color: #17202a; line-height: 1.45; }}
    .container {{ max-width: 820px; margin: 0 auto; padding: 24px; }}
    h1 {{ font-size: 24px; margin-bottom: 8px; }}
    h2 {{ font-size: 18px; margin: 6px 0; }}
    .paper {{ border-top: 1px solid #d9e2ec; padding: 18px 0; }}
    .rank {{ color: #52616b; font-size: 13px; }}
    .authors {{ color: #52616b; }}
    .context {{ background: #f5f7fa; padding: 12px; border-left: 4px solid #2f80ed; }}
    a {{ color: #1b66c9; }}
  </style>
</head>
<body>
  <div class="container">
    <h1>{html.escape(title)}</h1>
    {body}
  </div>
</body>
</html>"""


def send_with_resend(subject: str, html_body: str) -> None:
    api_key = os.environ.get("RESEND_API_KEY")
    email_from = os.environ.get("EMAIL_FROM")
    email_to = os.environ.get("EMAIL_TO")
    if not api_key or not email_from or not email_to:
        raise RuntimeError("Missing RESEND_API_KEY, EMAIL_FROM, or EMAIL_TO.")

    payload = {
        "from": email_from,
        "to": [addr.strip() for addr in email_to.split(",") if addr.strip()],
        "subject": subject,
        "html": html_body,
    }
    response = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        data=json.dumps(payload),
        timeout=30,
    )
    if response.status_code >= 300:
        raise RuntimeError(f"Resend failed: {response.status_code} {response.text}")


def collect_papers(config: dict[str, Any], days_back: int) -> list[Paper]:
    papers: list[Paper] = []
    papers.extend(fetch_pubmed(config, days_back))
    papers.extend(fetch_arxiv(config, days_back))
    papers.extend(fetch_openalex(config, days_back))
    papers.extend(fetch_biorxiv_like("biorxiv", config["sources"].get("biorxiv", {}).get("enabled", True), days_back))
    papers.extend(fetch_biorxiv_like("medrxiv", config["sources"].get("medrxiv", {}).get("enabled", True), days_back))
    return papers


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days-back", type=int, default=int(os.environ.get("DAYS_BACK", "7")))
    parser.add_argument("--min-score", type=int, default=int(os.environ.get("MIN_SCORE", "4")))
    parser.add_argument("--max-results", type=int, default=int(os.environ.get("MAX_RESULTS", "25")))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config = load_config()
    state = load_state()
    seen = state.setdefault("seen", {})

    papers = collect_papers(config, args.days_back)
    scored = [score_paper(paper, config) for paper in papers]
    deduped = deduplicate(scored)
    eligible = [paper for paper in deduped if paper.score >= args.min_score and paper.key() not in seen]
    eligible.sort(key=lambda paper: (paper.score, paper.date), reverse=True)
    selected = eligible[: args.max_results]

    subject, html_body = render_email(selected, config, args.days_back)

    print(f"Collected {len(papers)} papers; {len(deduped)} after dedupe; {len(eligible)} new eligible; sending {len(selected)}.")
    print(subject)
    for paper in selected:
        print(f"- [{paper.score}] {paper.title} ({paper.source}) {paper.url}")

    if not args.dry_run:
        send_with_resend(subject, html_body)
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        for paper in selected:
            seen[paper.key()] = {
                "title": paper.title,
                "source": paper.source,
                "date": paper.date,
                "url": paper.url,
                "sent_at": now,
                "score": paper.score,
            }
        save_state(state)
    else:
        preview = ROOT / "daily_literature_alert_preview.html"
        preview.write_text(html_body, encoding="utf-8")
        print(f"Dry run only. Preview written to {preview}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

