"""早报的本地数据：各项目待我决定、过去 24 小时的动态、需要我处理的会话、分诊异常。

只读，确定性；输出 Markdown 给 daily-brief skill 使用（Notion 部分由 skill 通过 Notion MCP 查询）。

用法: .venv\\Scripts\\python -I scripts\\brief_data.py [--hours 24] [--no-remote]
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hub_state as hs  # noqa: E402
from sessions import collect  # noqa: E402

ROOT = hs.ROOT


def recent_lines(lines: list[str], since: float) -> list[str]:
    out = []
    for ln in lines:
        m = re.match(r"^- (\d{4}-\d\d-\d\d \d\d:\d\d) (.*)$", ln)
        if m and time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M")) >= since:
            out.append(f"- {m.group(1)[5:]} {m.group(2)}")
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    hours = float(sys.argv[sys.argv.index("--hours") + 1]) if "--hours" in sys.argv else 24
    since = time.time() - hours * 3600
    known = sorted(p.stem for p in (ROOT / "projects").glob("*.md"))
    stated = sorted(p.stem for p in hs.STATE.glob("*.md")) if hs.STATE.exists() else []
    print(f"# 早报数据（过去 {hours:g} 小时，生成于 {time.strftime('%Y-%m-%d %H:%M')}）\n")

    print("## 各项目")
    for project in known + [p for p in stated if p not in known]:
        st = hs.load_state(project)
        pending = [re.sub(r"\s*\| 会话 [^|]*|\s*\| msg \S+$", "", ln) for ln in st["sections"].get("待我决定", [])]
        open_tags = set(re.findall(r"#[\w.-]+-\d+", "\n".join(pending)))
        # 动态里提到、但已不在「待我决定」里的标签，注明已处理，避免被当成仍待决
        dyn = [re.sub(r"(#[\w.-]+-\d+)", lambda m: m.group(1) if m.group(1) in open_tags else f"{m.group(1)}（已处理）", ln)
               for ln in recent_lines(st["sections"].get("今日动态", []), since)]
        quiet = recent_lines(st["sections"].get("记录", []), since)
        if not (pending or dyn) and project not in known:
            continue
        print(f"\n### {project}{'' if project in known else '（非正式项目）'}")
        print("待我决定：" + ("\n" + "\n".join(pending) if pending else "无"))
        print("动态：" + ("\n" + "\n".join(dyn) if dyn else "无"))
        if quiet:
            print(f"（另有 {len(quiet)} 条静默记录）")

    print("\n## 需要我处理的会话")
    rows = collect(days=hours / 24, remote="--no-remote" not in sys.argv)
    attention = [r for r in rows if r.get("live") and (r.get("state") in ("blocked", "waiting") or r.get("waiting_for"))]
    for r in attention:
        print(f"- {r.get('project')} · {r.get('name', '')} [{r.get('kind', '')} {r.get('bg_id', '')}] · {r.get('state')}"
              f"{'（' + r['waiting_for'] + '）' if r.get('waiting_for') else ''} · {r.get('cwd', '')}")
    if not attention:
        print("无")
    unreachable = [r for r in rows if str(r.get("state", "")).startswith("⚠")]
    for r in unreachable:
        print(f"- {r['project']}：{r['state']}")

    err = ROOT / "events" / "triage_errors.log"
    if err.exists():
        n = sum(1 for ln in err.read_text(encoding="utf-8").splitlines()
                if re.match(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d ", ln)
                and time.mktime(time.strptime(ln[:19], "%Y-%m-%d %H:%M:%S")) >= since)
        if n:
            print(f"\n## 分诊异常\n过去 {hours:g} 小时分诊失败 {n} 次（已兜底发原始通知），详见 events/triage_errors.log")

    import incident
    incs = [(p, m) for p, m in incident.all_incidents()
            if m.get("status") in (*incident.OPEN_STATES, "needs-user") or str(m.get("closed", "")) >= time.strftime("%Y-%m-%d %H:%M", time.localtime(since))]
    if incs:
        print("\n## 故障单（ops/INCIDENTS.md）")
        for p, m in incs:
            print(f"- {m.get('id')} [{m.get('status')}] {m.get('source')} · {m.get('title')} · ×{m.get('count', 1)} · 处理者 {m.get('owner') or '无'}")


if __name__ == "__main__":
    main()
