"""轮询 subscriptions.yaml 里的订阅源：抓取 → 去重 → 关键词预筛。不调用模型，不消耗额度。

输出 feeds/candidates/YYYY-MM-DD.json（供 daily-digest 打分），并更新 feeds/state.json（已见条目）。
某个源第一次出现时，只把最近 7 天内的条目当作新内容，避免第一次运行就把历史文章全推过来。

用法: .venv\\Scripts\\python -I scripts\\poll_feeds.py [--dry-run]
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
FEEDS = ROOT / "feeds"
STATE = FEEDS / "state.json"
UA = {"User-Agent": "Mozilla/5.0 (personal feed reader)"}
NEW_SOURCE_WINDOW = timedelta(days=7)
STATE_TTL = timedelta(days=90)
NS = {"atom": "http://www.w3.org/2005/Atom", "dc": "http://purl.org/dc/elements/1.1/",
      "arxiv": "http://arxiv.org/schemas/atom", "sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def get(url: str, accept_json: bool = False):
    req = urllib.request.Request(url, headers={**UA, **({"Accept": "application/json"} if accept_json else {})})
    with urllib.request.urlopen(req, timeout=40) as r:
        data = r.read()
    return json.loads(data) if accept_json else data


def clean(html: str, limit: int = 700) -> str:
    text = re.sub(r"<[^>]+>", " ", unescape(html or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip()
    for f in (lambda x: parsedate_to_datetime(x), lambda x: datetime.fromisoformat(x.replace("Z", "+00:00"))):
        try:
            d = f(s)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError, IndexError):
            continue
    return None


def compile_keywords(kws: list[str]):
    pats = []
    for k in kws:
        k = k.lower().strip()
        # 短词要求整词匹配（避免 slo 命中 slow）；长词只要求词首对齐（prefix cach 命中 caching）
        pats.append((k, re.compile(r"\b" + re.escape(k) + (r"\b" if len(k) <= 4 else ""), re.I)))
    return pats


# ---------------- 各类源 ----------------
def fetch_arxiv(cfg) -> list[dict]:
    cats = "+".join(cfg["categories"])
    root = ET.fromstring(get(f"https://rss.arxiv.org/rss/{cats}"))
    out = []
    for it in root.iter("item"):
        kind = (it.findtext("arxiv:announce_type", "", NS) or "").strip()
        if kind not in ("new", "cross"):
            continue
        link = (it.findtext("link") or "").strip()
        aid = link.rstrip("/").split("/")[-1]
        desc = it.findtext("description") or ""
        abstract = desc.split("Abstract:", 1)[-1]
        out.append({"id": f"arxiv:{aid}", "title": clean(it.findtext("title"), 300), "url": link,
                    "date": parse_date(it.findtext("pubDate")), "summary": clean(abstract, 1200),
                    "authors": clean(it.findtext("dc:creator", "", NS), 200),
                    "categories": [c.text for c in it.findall("category") if c.text]})
    return out


def fetch_rss(src) -> list[dict]:
    root = ET.fromstring(get(src["url"]))
    out = []
    for it in root.iter("item"):
        link = (it.findtext("link") or "").strip()
        out.append({"id": link, "title": clean(it.findtext("title"), 300), "url": link,
                    "date": parse_date(it.findtext("pubDate") or it.findtext("dc:date", None, NS)),
                    "summary": clean(it.findtext("description"), 600)})
    for it in root.iter("{http://www.w3.org/2005/Atom}entry"):
        ln = it.find("atom:link", NS)
        link = ln.get("href") if ln is not None else ""
        out.append({"id": link, "title": clean(it.findtext("atom:title", "", NS), 300), "url": link,
                    "date": parse_date(it.findtext("atom:published", None, NS) or it.findtext("atom:updated", None, NS)),
                    "summary": clean(it.findtext("atom:summary", "", NS) or it.findtext("atom:content", "", NS), 600)})
    return out


def fetch_sitemap(src) -> list[dict]:
    root = ET.fromstring(get(src["url"]))
    pat = re.compile(src.get("match", "."))
    out = []
    for u in root.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}url"):
        loc = (u.findtext("sm:loc", "", NS) or "").strip()
        if not pat.search(loc):
            continue
        m = re.search(r"(\d{4}-\d{2}-\d{2})-([^/]+)", loc)
        d = parse_date(u.findtext("sm:lastmod", None, NS)) or (
            datetime.fromisoformat(m.group(1)).replace(tzinfo=timezone.utc) if m else None)
        title = m.group(2).replace("-", " ") if m else loc
        out.append({"id": loc.replace("://www.", "://"), "title": title, "url": loc, "date": d,
                    "summary": "（sitemap 发现的新文章，标题由 URL 推断）"})
    return out


def fetch_hf(src) -> list[dict]:
    data = get(f"https://huggingface.co/api/models?author={src['author']}&sort=createdAt&direction=-1&limit=20",
               accept_json=True)
    return [{"id": f"hf:{m['id']}", "title": m["id"], "url": f"https://huggingface.co/{m['id']}",
             "date": parse_date(m.get("createdAt")),
             "summary": f"Hugging Face 新模型；pipeline={m.get('pipeline_tag')}；likes={m.get('likes')}；"
                        f"downloads={m.get('downloads')}；tags={', '.join((m.get('tags') or [])[:8])}"}
            for m in data]


def fetch_github(src) -> list[dict]:
    data = get(f"https://api.github.com/orgs/{src['org']}/repos?sort=created&direction=desc&per_page=15",
               accept_json=True)
    return [{"id": f"gh:{r['full_name']}", "title": r["full_name"], "url": r["html_url"],
             "date": parse_date(r.get("created_at")),
             "summary": f"GitHub 新仓库：{r.get('description') or '（无描述）'}；stars={r.get('stargazers_count')}；"
                        f"language={r.get('language')}"}
            for r in data if not r.get("fork")]


def enrich_from_page(it: dict):
    try:
        html = get(it["url"]).decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return
    t = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html) or re.search(r"<title[^>]*>(.*?)</title>", html, re.S)
    d = re.search(r'<meta[^>]+(?:name="description"|property="og:description")[^>]+content="([^"]+)"', html)
    if t:
        it["title"] = clean(t.group(1), 300).removesuffix(" | LMSYS Org").strip()
    if d:
        it["summary"] = clean(d.group(1), 600)


FETCHERS = {"rss": fetch_rss, "sitemap": fetch_sitemap, "hf_models": fetch_hf, "github_repos": fetch_github}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    dry = "--dry-run" in sys.argv
    cfg = yaml.safe_load((ROOT / "subscriptions.yaml").read_text(encoding="utf-8"))
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"seen": {}, "sources": []}
    seen, known_sources = state["seen"], set(state.get("sources", []))
    now = datetime.now(timezone.utc)
    today = date.today().isoformat()
    candidates, errors, stats = [], [], {}

    def accept(items, source, importance, first_time, extra=None):
        n = 0
        for it in items:
            if not it.get("id") or it["id"] in seen:
                continue
            seen[it["id"]] = today
            if first_time and (it["date"] is None or now - it["date"] > NEW_SOURCE_WINDOW):
                continue
            it.update({"source": source, "importance": importance, **(extra or {})})
            it["date"] = it["date"].date().isoformat() if it.get("date") else None
            candidates.append(it)
            n += 1
        stats[source] = n

    # arXiv：关键词预筛后按命中数取前 max_candidates 条
    ax = cfg.get("arxiv")
    if ax:
        try:
            pats = compile_keywords(ax.get("keywords", []))
            weak = compile_keywords(ax.get("weak_keywords", []))
            scored = []
            for it in fetch_arxiv(ax):
                text = f"{it['title']} {it['summary']}"
                hits = [k for k, p in pats if p.search(text)]
                if hits:   # 弱关键词只在已有强命中时追加，用于排序
                    hits += [k for k, p in weak if p.search(text)]
                if hits:
                    it["hits"] = hits
                    scored.append(it)
                elif it["id"] not in seen:
                    seen[it["id"]] = today       # 没命中的也记为已见，避免第二天重复判断
            scored.sort(key=lambda x: -len(x["hits"]))
            accept(scored[: ax.get("max_candidates", 60)], "arXiv", "normal", first_time=False)
        except Exception as e:  # noqa: BLE001
            errors.append(f"arXiv: {type(e).__name__}: {e}")

    for src in cfg.get("feeds", []):
        name = src["name"]
        try:
            items = FETCHERS[src["type"]](src)
            before = len(candidates)
            accept(items, name, src.get("importance", "normal"), first_time=name not in known_sources)
            known_sources.add(name)
            if src["type"] == "sitemap":          # sitemap 只有 URL：为新文章补抓网页标题和描述
                for it in candidates[before:]:
                    enrich_from_page(it)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {type(e).__name__}: {e}")

    cutoff = (now - STATE_TTL).date().isoformat()
    state = {"seen": {k: v for k, v in seen.items() if v >= cutoff}, "sources": sorted(known_sources)}
    out = {"date": today, "generated_at": now.isoformat(timespec="seconds"), "stats": stats,
           "errors": errors, "max_arxiv_in_digest": (ax or {}).get("max_in_digest", 10), "items": candidates}

    if not dry:
        (FEEDS / "candidates").mkdir(parents=True, exist_ok=True)
        (FEEDS / "candidates" / f"{today}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        STATE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"date": today, "total": len(candidates), "stats": stats, "errors": errors,
                      "file": None if dry else f"feeds/candidates/{today}.json"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
