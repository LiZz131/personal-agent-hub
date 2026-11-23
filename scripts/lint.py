"""Mechanical health checks for the wiki. Read-only: never modifies files.

Usage: python -I scripts/lint.py [--json]
Checks: broken links, orphans, index coverage, frontmatter, raw paths,
stale projects, my_level calibration queue, suspicious log entries.
Semantic checks (unsourced claims, contradictions) are left to the LLM.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WIKI = ROOT / "wiki"
PROJECTS = ROOT / "projects"
STALE_DAYS = 14
LEVELS = {"unknown", "新手", "会用", "熟练", "精通"}
STATUSES = {"to-learn", "learning", "learned", "archived"}
QUEUE = ROOT / "learning" / "queue.md"
REQUIRED = {
    "source": ["title", "type", "year", "url", "raw", "tags", "status", "added"],
    "concept": ["title", "type", "tags", "my_level", "sources", "updated"],
    "entity": ["title", "type", "kind", "tags", "my_level", "sources", "updated"],
    "project": ["title", "type", "location", "status", "updated"],
}
SPECIAL = {"index", "log"}

FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
CODE_RE = re.compile(r"```.*?```|`[^`\n]*`", re.S)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def load(path: Path):
    text = path.read_text(encoding="utf-8")
    fm, body, err = {}, text, None
    m = FM_RE.match(text)
    if m:
        try:
            fm = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError as e:
            err = f"YAML 解析失败: {e.__class__.__name__}"
        body = text[m.end():]
    links = {l.strip() for l in LINK_RE.findall(CODE_RE.sub("", body))}
    return fm, links, err


def category(path: Path) -> str:
    rel = path.relative_to(ROOT).parts
    if rel[0] == "projects":
        return "project"
    return {"sources": "source", "concepts": "concept", "entities": "entity"}.get(rel[1], "special")


def as_date(v):
    if isinstance(v, dt.date):
        return v
    if isinstance(v, str) and DATE_RE.match(v):
        return dt.date.fromisoformat(v)
    return None


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    pages = {}
    for p in list(WIKI.rglob("*.md")) + list(PROJECTS.glob("*.md")):
        pages[p.stem] = p
    data = {name: load(p) for name, p in pages.items()}
    issues = {k: [] for k in ["broken_links", "orphans", "index_missing", "index_stale",
                              "frontmatter", "raw_missing", "stale_projects",
                              "calibration", "suspicious", "learning_state"]}

    inbound = {name: set() for name in pages}
    for name, (fm, links, err) in data.items():
        for l in links:
            if l in pages:
                if l != name:
                    inbound[l].add(name)
            else:
                issues["broken_links"].append({"page": name, "target": l})

    index_links = data.get("index", ({}, set(), None))[1]
    today = dt.date.today()
    for name, p in pages.items():
        if name in SPECIAL:
            continue
        cat = category(p)
        fm, links, err = data[name]
        rel = str(p.relative_to(ROOT)).replace("\\", "/")
        if cat != "project" and name not in index_links:
            issues["index_missing"].append(rel)
        if not (inbound[name] - SPECIAL) and cat != "project":
            issues["orphans"].append(rel)
        if err:
            issues["frontmatter"].append({"page": rel, "problem": err})
            continue
        for field in REQUIRED.get(cat, []):
            if fm.get(field) in (None, "", []):
                issues["frontmatter"].append({"page": rel, "problem": f"缺字段 {field}"})
        for field in ("added", "updated"):
            if field in fm and fm[field] not in (None, "") and as_date(fm[field]) is None:
                issues["frontmatter"].append({"page": rel, "problem": f"{field} 不是 YYYY-MM-DD"})
        if cat == "source":
            st = str(fm.get("status") or "")
            if st not in STATUSES:
                issues["learning_state"].append({"page": name, "problem": f"status 非法值 {st!r}（应为 {sorted(STATUSES)}）"})
            if st == "learned" and not (fm.get("learned") and fm.get("learned_via")):
                issues["learning_state"].append({"page": name, "problem": "learned 但缺 learned / learned_via"})
        if cat == "source" and fm.get("raw") and not (ROOT / fm["raw"]).exists():
            issues["raw_missing"].append({"page": rel, "raw": fm["raw"]})
        if cat in ("concept", "entity"):
            lvl = str(fm.get("my_level") or "unknown")
            sug = fm.get("my_level_suggested")
            if lvl not in LEVELS:
                issues["frontmatter"].append({"page": rel, "problem": f"my_level 非法值 {lvl}"})
            if lvl == "unknown" or (sug and str(sug) != lvl):
                issues["calibration"].append({"page": name, "my_level": lvl, "suggested": sug})
        if cat == "project" and fm.get("status") == "active":
            d = as_date(fm.get("updated"))
            if d and (today - d).days > STALE_DAYS:
                issues["stale_projects"].append({"page": rel, "days": (today - d).days})

    for l in index_links:
        if l not in pages:
            issues["index_stale"].append(l)

    # 学习状态：来源页 status 与 learning/queue.md 的一致性（状态以 frontmatter 为准）
    if QUEUE.exists():
        active, done, sec = set(), set(), None
        for ln in QUEUE.read_text(encoding="utf-8").splitlines():
            if ln.startswith("## "):
                sec = "done" if "已完成" in ln else "active"
                continue
            if sec and ln.lstrip().startswith("- "):
                links = LINK_RE.findall(ln)   # 每行第一个链接是条目本身，其余是「前置」
                if links:
                    (done if sec == "done" else active).add(links[0].strip())
        for l in sorted(active | done):
            if l not in pages:
                issues["learning_state"].append({"page": l, "problem": "队列中的条目没有对应页面（尚未入库？）"})
        for name, p in pages.items():
            if category(p) != "source":
                continue
            st = str(data[name][0].get("status") or "")
            if st in ("to-learn", "learning") and name not in active:
                issues["learning_state"].append({"page": name, "problem": f"status={st} 但不在待学清单的「待排序」或「队列」中"})
            if st == "learned" and name in active:
                issues["learning_state"].append({"page": name, "problem": "已 learned 但仍在待学队列中（应移到「已完成」）"})
            if st == "archived" and name in active:
                issues["learning_state"].append({"page": name, "problem": "已 archived 但仍在待学队列中"})

    log = WIKI / "log.md"
    if log.exists():
        issues["suspicious"] = [ln.strip() for ln in log.read_text(encoding="utf-8").splitlines()
                                if "[suspicious]" in ln]

    if "--json" in sys.argv:
        print(json.dumps(issues, ensure_ascii=False, indent=1))
        return
    total = sum(len(v) for k, v in issues.items() if k != "calibration")
    print(f"# Mechanical lint — {len(pages)} pages, {total} issues, "
          f"{len(issues['calibration'])} pages to calibrate\n")
    for k, v in issues.items():
        print(f"## {k} ({len(v)})")
        for item in v:
            print(f"- {item}")
        print()


if __name__ == "__main__":
    main()
