"""生成 raw/MANIFEST.md：raw/ 下每个原始文件的出处链接、大小和校验值。

raw/ 里的原文（PDF、网页抓取、视频转写稿）只存在本机，不进 git、不推送到远端；
远端只保留这份清单，丢失时可以按链接重新抓取，并用 sha256 核对是否是同一份。
出处取自 wiki/sources/*.md 的 frontmatter（raw 字段指向该文件，url 字段是出处）。

用法: .venv\\Scripts\\python -I scripts\\raw_manifest.py          # 重新生成
      .venv\\Scripts\\python -I scripts\\raw_manifest.py --check  # 只检查是否过期（过期时退出码 1）
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw"
OUT = RAW / "MANIFEST.md"


def sources_by_raw() -> dict[str, dict]:
    import yaml

    out = {}
    for p in sorted((ROOT / "wiki" / "sources").glob("*.md")):
        text = p.read_text(encoding="utf-8")
        m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
        if not m:
            continue
        try:
            fm = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            continue
        if fm.get("raw"):
            out[str(fm["raw"]).replace("\\", "/")] = {"url": fm.get("url") or "", "page": p.stem,
                                                     "title": str(fm.get("title") or "")}
    return out


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def build() -> str:
    src = sources_by_raw()
    rows = []
    for f in sorted(RAW.rglob("*")):
        if not f.is_file() or f.name in (".gitkeep", "MANIFEST.md"):
            continue
        rel = f.relative_to(ROOT).as_posix()
        s = src.get(rel, {})
        sha = hashlib.sha256(f.read_bytes()).hexdigest()[:16]
        url = s.get("url") or "（无出处，见 log）"
        page = f"[[{s['page']}]]" if s.get("page") else "（无 source 页）"
        rows.append(f"| `{rel}` | {human(f.stat().st_size)} | `{sha}` | {url} | {page} |")
    head = ["# raw 清单", "",
            "<!-- 由 scripts/raw_manifest.py 生成，不要手改。raw/ 原文只在本机，不进 git；丢失时按链接重新抓取，用 sha256 核对。 -->", "",
            f"共 {len(rows)} 个文件。", "",
            "| 文件 | 大小 | sha256（前 16 位） | 出处 | source 页 |", "|---|---|---|---|---|"]
    return "\n".join(head + rows) + "\n"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    text = build()
    old = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
    if "--check" in sys.argv:
        print("raw/MANIFEST.md 已是最新" if old == text else "raw/MANIFEST.md 已过期，运行 scripts/raw_manifest.py 更新")
        sys.exit(0 if old == text else 1)
    if old != text:
        OUT.write_text(text, encoding="utf-8")
    print(f"raw/MANIFEST.md：{text.count(chr(10) + '| `')} 个文件")


if __name__ == "__main__":
    main()
