"""Fetch a URL into raw/ for ingest.

Usage: .venv/Scripts/python -I scripts/fetch.py <url> [--name NAME]

- arXiv (abs/pdf/html links): downloads the PDF to raw/papers/, names it from arXiv metadata.
- Direct PDF links: saved to raw/papers/.
- Other pages: HTML converted to Markdown via pandoc, saved to raw/web/.
Prints a JSON line: {"path": ..., "kind": ..., "title": ..., "authors": [...], "year": ...}
Never overwrites an existing file.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UA = {"User-Agent": "Mozilla/5.0 (personal-knowledge-fetcher)"}


def get(url: str) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read(), r.headers.get("Content-Type", "")


def slug(s: str, n: int = 6) -> str:
    words = re.findall(r"[a-z0-9]+", s.lower())
    return "-".join(words[:n]) or "untitled"


def unique(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(2, 100):
        p = path.with_name(f"{path.stem}-{i}{path.suffix}")
        if not p.exists():
            return p
    raise SystemExit(f"too many collisions for {path}")


def arxiv(aid: str, name: str | None):
    xml, _ = get(f"http://export.arxiv.org/api/query?id_list={aid}")
    ns = {"a": "http://www.w3.org/2005/Atom"}
    e = ET.fromstring(xml).find("a:entry", ns)
    title = " ".join(e.find("a:title", ns).text.split())
    authors = [a.find("a:name", ns).text for a in e.findall("a:author", ns)]
    year = e.find("a:published", ns).text[:4]
    last = authors[0].split()[-1] if authors else "anon"
    fname = name or f"{year}-{slug(last, 1)}-{slug(title)}"
    pdf, _ = get(f"https://arxiv.org/pdf/{aid}")
    out = unique(ROOT / "raw" / "papers" / f"{fname}.pdf")
    out.write_bytes(pdf)
    return {"path": str(out.relative_to(ROOT)), "kind": "paper", "title": title,
            "authors": authors, "year": year, "url": f"https://arxiv.org/abs/{aid}"}


def main():
    args = sys.argv[1:]
    if not args:
        raise SystemExit(__doc__)
    url = args[0]
    name = args[args.index("--name") + 1] if "--name" in args else None

    m = re.search(r"arxiv\.org/(?:abs|pdf|html)/([0-9]{4}\.[0-9]{4,5})", url)
    if m:
        print(json.dumps(arxiv(m.group(1), name), ensure_ascii=False))
        return

    data, ctype = get(url)
    if "pdf" in ctype or url.lower().endswith(".pdf"):
        out = unique(ROOT / "raw" / "papers" / f"{name or slug(Path(url).stem)}.pdf")
        out.write_bytes(data)
        print(json.dumps({"path": str(out.relative_to(ROOT)), "kind": "paper", "url": url}))
        return

    html = data.decode("utf-8", errors="replace")
    t = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    title = " ".join(t.group(1).split()) if t else ""
    md = subprocess.run(["pandoc", "-f", "html", "-t", "gfm-raw_html", "--wrap=none"],
                        input=html.encode("utf-8"), capture_output=True, check=True).stdout.decode("utf-8")
    out = unique(ROOT / "raw" / "web" / f"{name or slug(title or url)}.md")
    out.write_text(f"<!-- source: {url} -->\n\n{md}", encoding="utf-8")
    print(json.dumps({"path": str(out.relative_to(ROOT)), "kind": "web", "title": title, "url": url},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
