#!/usr/bin/env python3
"""Static validation of the four mobile task documents; writes 文件校驗報告.json and SHA256SUMS.txt.

Usage: python3 文件校驗.py [--write]
Runs only on the Markdown in this directory; needs no network and no other repository.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOCS = {
    "master": "Yuki-手機端總任務書-v1.3.md",
    "tasks": "手機端提交級任務書.md",
    "matrix": "跨端介面與驗收矩陣.md",
    "platform": "平台調研與分發裁決.md",
}
REPOS = {"Yuki", "Yuki-Desktop", "Yuki-Mobile"}
ACTIVE_REPO = "Yuki-Mobile"  # v1.2 scope: every active card writes only to Yuki-Mobile
RETIRED = {"BE-001", "DESK-01"}  # v1.1 IDs replaced by QA-002 / QA-003, named only in change notes
ID_RE = re.compile(r"\b(?:REPO|MOB|QA|BE|DESK|PUSH|DEV|EXT)-\d{2,3}\b")
SCEN_RE = re.compile(r"\bA(\d{2})\b")
CONDITIONAL = {  # active card -> alternatives, any one of which satisfies the condition
    "DEV-008": {"DEV-002", "DEV-003"},
}
DEFERRED_CONDITIONAL = {"DEV-004": {"DEV-001", "DEV-003"}}
RELEASES = {  # candidate, forbidden IDs, forbidden prefixes (deferred cards are always forbidden)
    "R-A": ("MOB-008", {"MOB-009", "MOB-010", "MOB-011", "MOB-012", "MOB-013"}, ("DEV", "EXT", "PUSH")),
    "R-I": ("MOB-013", {f"MOB-00{i}" for i in range(2, 9)}, ("DEV", "EXT", "PUSH")),
    "R-D2": ("DEV-006", set(), ("MOB", "EXT", "PUSH")),
    "R-DI": ("DEV-007", {"DEV-002", "DEV-006", "DEV-009"}, ("MOB", "EXT", "PUSH")),
}
# Recorded by hand from the 2026-11-15 review session; the validator does not re-run these.
EXECUTION_EVIDENCE = {
    "execution_evidence_2026_11_15": {
        "repository_state": {
            "Yuki": {"local_head": "08164f6732c14fca0396eb3411f8a82d6762aa8c", "remote_main": "08164f6732c14fca0396eb3411f8a82d6762aa8c", "method": "git rev-parse HEAD; git ls-remote origin refs/heads/main"},
            "Yuki-Desktop": {"local_head": "a5687a66cf74485c94394e1008ddfbc0ebd926a2", "remote_main": "a5687a66cf74485c94394e1008ddfbc0ebd926a2"},
            "Mika": {"pinned": "10b5712e30d6d10ace588bac08a1aad0fc6057ce", "remote_main": "eb10b349f86654125e0db5325714e410b5431054", "note": "main moved; this round read only the pinned commit"},
            "Poki": {"pinned": "2743a6b62bdb346a9bb1c1ee51c37ae0c5cd61d0", "remote_main": "2743a6b62bdb346a9bb1c1ee51c37ae0c5cd61d0"},
        },
        "openapi_export": {
            "command": "UV_PROJECT_ENVIRONMENT=<outside-repo> uv sync --frozen --python 3.12; <venv>/bin/python -c 'from yuki.client_api.openapi import export_openapi; export_openapi(Path(OUT))' (same function as `yuki client openapi --output`)",
            "result_sha256": "14fd970b86a2ef393e1abec1aaae3aa56961304875af35454b000b2e8b98c101",
            "desktop_contract_sha256": "14fd970b86a2ef393e1abec1aaae3aa56961304875af35454b000b2e8b98c101",
            "desktop_contract_source_commit": "df133fbcb638c508041554a083c42c9aaee390b1",
            "identical": True,
            "api_revision": 12,
            "paths": 56,
        },
        "backend_tests": {
            "command": "<venv>/bin/python -m pytest -p no:cacheprovider -q -m 'not native_worker' tests/integration/test_client_continuation.py tests/integration/test_client_person_identity.py tests/integration/test_desktop_work_scenario_fixture.py tests/unit/test_desktop_fault_fixture.py",
            "collected": 31,
            "passed": 31,
            "exit_code": 0,
            "duration_seconds": 57.77,
            "environment": "macOS, Python 3.12, uv --frozen --extra dev; venv and basetemp outside the repository",
        },
        "upstream_reads": {
            "method": "git init + git fetch --depth 1 origin <pinned sha> into a scratch directory; read only, no script or build executed",
            "Mika_paths": ["README.md", "app/build.gradle.kts", "app/src/main/AndroidManifest.xml", "app/src/main/res/xml/accessibility_service.xml", "app/src/main/java/com/wangzan101/mika/cap/phone/shell/ShellService.kt", "app/src/main/java/com/wangzan101/mika/cap/phone/a11y/", "app/src/main/java/com/wangzan101/mika/takeover/Takeover.kt", "NOTICE", "licenses/THIRD_PARTY_NOTICES.md"],
            "Poki_paths": ["README.md", "docs/verification.md", "docs/ios-core.md", "docs/physical-iphone.md", "native/ios-app/project.yml", "native/ios-app/project-external.yml", "THIRD_PARTY_NOTICES.md", "native/LICENSE.idb", "LICENSE"],
        },
        "not_executed": [
            "Android or iOS build",
            "Yuki-Desktop unit or Playwright tests",
            "any real-device or simulator run",
            "re-reading Google Play or Apple policy pages (v1.0 reading kept, marked provisional)",
        ],
        "runtime_tests": {f"A{i:02d}": "not_run" for i in range(1, 21)},
        "repository_changes": "CORRECTED in v1.3: uv sync installed Yuki in editable mode and hatch_build.py compiled gitignored native.o and vec1.dylib into Yuki src/yuki/_native/ (created 2026-11-15 20:59 local); git status did not show them. Removed during the v1.3 revision. Yuki-Desktop: no writes. The untracked src/qq_ai_bot/ in Yuki predates this round (mtime 2026-11-03)",
        "branches_created": [],
        "commits_created": [],
    },
    "v1_2_revision": {
        "scope_change": "only Yuki-Mobile is written; BE-001 -> QA-002, DESK-01 -> QA-003, DEV-008 became a black-box test in Yuki-Mobile, new REPO-01 and DEV-009; DEV-001/004/005 and PUSH-01..03 deferred",
        "feasibility_reads": [
            "Yuki tests/support/soul_reference_server.py:76-90,170-255,284,320-343 (container, scripted model, client/agents routers, DesktopFaultFixture.attach)",
            "Yuki tests/support/desktop_reference_server.py:112-211 (publish_artifact, seed_work)",
            "Yuki tests/support/person_merge.py:11 (merge_persons)",
            "Yuki LICENSE and Yuki-Desktop LICENSE: AGPL-3.0-only",
        ],
        "new_runtime_execution": "none; the v1.1 execution evidence above is unchanged",
        "validator_negative_tests": {
            "active card depends on deferred card": "failed as expected",
            "active card assigned to Yuki": "failed as expected",
            "release closure table differs from computed closure": "failed as expected",
            "link to superseded master": "failed as expected",
            "undefined dependency, dependency cycle, broken anchor, Android core depending on iOS (v1.1 run)": "failed as expected",
        },
        "yuki_and_desktop_git_status_after_revision": "unchanged",
    },
    "v1_3_revision": {
        "source": "review comments on v1.2, each re-checked against the pinned Yuki source before editing",
        "fixes": [
            "QA-003 no longer waits for a phone card: before/phone/after phases, phone phase self-verified with the reference client; A01 is judged only on MOB-008/MOB-013",
            "A01 arms work-scenario-artifact before the desktop sends the message",
            "A08 uses a new QA-002 route __test/qq-group-work; owner input accepted, nonowner rejected, delivery stays in the original group; real QQ group run is separate evidence",
            "A09 split: revoked device -> GET /me 401; superuser demotion -> GET /me 200 with is_superuser false and restricted cache cleared",
            "QA-001 adds a fixed-hostname, publicly trusted HTTPS test entrance reachable on Wi-Fi and cellular; MOB-002/MOB-009 verify /meta on a real device on both networks with release trust settings",
            "MOB-008 split into /1 (milestone M1: Android A01 on a debug build) and /2 (release candidate A01..A12)",
        ],
        "source_reads": [
            "Yuki tests/support/desktop_fault_fixture.py:317-334 (work-scenario records after_event_id at arm time)",
            "Yuki tests/support/desktop_reference_server.py:76-110 (seed_qq_entry creates a private-chat event only)",
            "Yuki tests/support/soul_reference_server.py:356-360,422-429 (__test/qq-entry, __test/superuser)",
            "Yuki tests/integration/test_client_continuation.py:236-330 and test_current_material_main_contract.py:80-120 (group entry via enabled_groups_csv, message:group:normal, processor.handle)",
            "Yuki src/yuki/client_api/identity.py:465-492 (/me projects is_superuser from the current person)",
            "Yuki src/yuki/container.py:574 (container exposes processor)",
        ],
        "not_verified": "HTTPS test entrance options (Tailscale Serve, own domain + tunnel) and the iOS single-active-VPN limit were not tried on a device",
        "new_runtime_execution": "none",
        "yuki_worktree_cleanup": "removed src/yuki/_native/ (native.o, vec1.dylib) created by the v1.1 uv sync; afterwards find -newermt <session start> outside .git is empty and git status --porcelain shows only the pre-existing src/qq_ai_bot/",
        "test_root_rule": "YUKI_BACKEND_TEST_ROOT is now a git-archive export outside the worktree; checks use git status --porcelain --ignored",
        "yuki_and_desktop_git_status_after_revision": "unchanged (tracked and untracked; ignored build outputs removed)",
    },
    "v1_3_follow_up": {
        "date": "2026-11-17 America/Los_Angeles",
        "scope": "A01 handoff facts, A08 and A09 fixtures, Android M1, signed-candidate network evidence, and the public GitHub repository decision",
        "source_reads": [
            "Yuki src/yuki/client_api/prompts.py:162-184, works.py:421-428 and identity.py:328-380 (trigger receipt, device and issuing binding)",
            "Yuki tests/support/soul_reference_server.py:422-429 and tests/integration/test_client_person_identity.py:389-400 (demotion and source disable)",
        ],
        "new_runtime_execution": "none; the document and source checks do not execute the mobile acceptance cases",
    },
}
CARD_FIELDS = ("**倉庫：**", "**可重跑驗證：**", "**具名產物：**", "**完成門檻：**")


def slug(heading: str) -> str:
    text = heading.strip().lower()
    kept = "".join(
        ch
        for ch in text
        if ch in "-_ " or unicodedata.category(ch)[0] in {"L", "N", "M"}
    )
    return kept.replace(" ", "-")


def expand(cell: str) -> list[int]:
    out: list[int] = []
    for a, b in re.findall(r"A(\d{2})～A(\d{2})", cell):
        out += range(int(a), int(b) + 1)
    out += [int(x) for x in SCEN_RE.findall(re.sub(r"A\d{2}～A\d{2}", "", cell))]
    return sorted(set(out))


def main() -> int:
    errors: list[str] = []
    text = {k: (ROOT / v).read_text(encoding="utf-8") for k, v in DOCS.items()}

    # 1. Task table
    tasks_text = text["tasks"]
    active_part = tasks_text[tasks_text.index("### 任務總表"):tasks_text.index("### 必要前置閉包")]
    deferred_part = tasks_text[tasks_text.index("## 暫緩卡"):]
    deferred_head = deferred_part[: deferred_part.index("\n### ")]
    deferred: dict[str, dict] = {}
    for line in deferred_head.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 5 and ID_RE.fullmatch(cells[0]):
            deferred[cells[0]] = {"needs_repo_change": cells[1], "prerequisites_when_enabled": sorted(set(ID_RE.findall(cells[3]))), "scenarios": expand(cells[4])}
    table: dict[str, dict] = {}
    for line in active_part.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 5 and ID_RE.fullmatch(cells[0]):
            tid, repo, intent, deps, acc = cells
            if tid in table:
                errors.append(f"duplicate task row {tid}")
            direct = sorted(set(ID_RE.findall(deps.split("條件")[0])))
            table[tid] = {
                "repo": repo,
                "direct": direct,
                "conditional": sorted(CONDITIONAL.get(tid, ())),
                "scenarios": expand(acc),
                "acceptance": acc,
            }
            if repo != ACTIVE_REPO:
                errors.append(f"{tid}: active card must belong to {ACTIVE_REPO}, found {repo!r}")
    ids = set(table)
    for tid, row in table.items():
        for dep in row["direct"] + row["conditional"]:
            if dep in deferred:
                errors.append(f"{tid}: active card depends on deferred card {dep}")
            elif dep not in ids:
                errors.append(f"{tid}: undefined dependency {dep}")
    for tid, row in deferred.items():
        for dep in row["prerequisites_when_enabled"]:
            if dep not in ids and dep not in deferred:
                errors.append(f"{tid}: deferred card names undefined prerequisite {dep}")
    if ids & set(deferred):
        errors.append(f"cards both active and deferred: {sorted(ids & set(deferred))}")
    for tid, alts in CONDITIONAL.items():
        cell_line = next(l for l in active_part.splitlines() if l.startswith(f"| {tid} |"))
        if "條件" not in cell_line or not alts <= set(ID_RE.findall(cell_line)):
            errors.append(f"{tid}: conditional prerequisites not stated in table")

    # 2. Card sections
    cards = re.findall(r"^## ((?:REPO|MOB|QA|BE|DESK|PUSH|DEV|EXT)-\d{2,3})：", tasks_text, re.M)
    if sorted(cards) != sorted(ids) or len(cards) != len(set(cards)):
        errors.append(f"card sections {sorted(set(cards) ^ ids)} do not match the task table")
    deferred_cards = re.findall(r"^### ((?:MOB|PUSH|DEV)-\d{2,3})：", deferred_part, re.M)
    if sorted(deferred_cards) != sorted(deferred):
        errors.append(f"deferred sections {sorted(set(deferred_cards) ^ set(deferred))} do not match the deferred table")
    sections = re.split(r"^## ", tasks_text[: tasks_text.index("## 暫緩卡")], flags=re.M)
    for sec in sections:
        m = re.match(r"((?:REPO|MOB|QA|BE|DESK|PUSH|DEV|EXT)-\d{2,3})：", sec)
        if not m:
            continue
        tid = m.group(1)
        for field in CARD_FIELDS:
            if field not in sec:
                errors.append(f"{tid}: missing field {field}")
        if "**直接前置：**" not in sec and "**條件前置：**" not in sec:
            errors.append(f"{tid}: missing prerequisite field")
        if "失敗情境" not in sec:
            errors.append(f"{tid}: missing failure scenarios")
        repo_line = next((l for l in sec.splitlines() if l.startswith("**倉庫：**")), "")
        if not repo_line.startswith(f"**倉庫：** {ACTIVE_REPO}"):
            errors.append(f"{tid}: card repository line does not start with {ACTIVE_REPO}")
        stated = set(ID_RE.findall(next((l for l in sec.splitlines() if "**直接前置：**" in l), "")))
        stated -= {tid}
        if table.get(tid):
            if "**直接前置：**" in sec and not set(table[tid]["direct"]) <= stated:
                errors.append(f"{tid}: card direct prerequisites {sorted(stated)} differ from table {table[tid]['direct']}")

    # 3. Mandatory graph acyclic
    state: dict[str, int] = {}
    order: list[str] = []

    def visit(n: str, stack: list[str]) -> None:
        if state.get(n) == 1:
            errors.append("cycle: " + " -> ".join(stack + [n]))
            return
        if state.get(n) == 2:
            return
        state[n] = 1
        for d in table[n]["direct"]:
            if d in table:  # undefined IDs are reported above
                visit(d, stack + [n])
        state[n] = 2
        order.append(n)

    for n in sorted(ids):
        visit(n, [])

    def closure(n: str) -> set[str]:
        seen: set[str] = set()
        todo = [n]
        while todo:
            x = todo.pop()
            for d in table[x]["direct"]:
                if d in table and d not in seen:
                    seen.add(d)
                    todo.append(d)
        return seen

    releases = {}
    for name, (candidate, forbidden_ids, forbidden_prefixes) in RELEASES.items():
        c = closure(candidate)
        bad = sorted(x for x in c if x in forbidden_ids or x in deferred or x.split("-")[0] in forbidden_prefixes)
        if bad:
            errors.append(f"{name}: forbidden prerequisites {bad}")
        conds = sorted(x for x in c | {candidate} if x in CONDITIONAL)
        releases[name] = {
            "candidate": candidate,
            "mandatory_closure": sorted(c),
            "conditional_cards_in_closure": {x: sorted(CONDITIONAL[x]) for x in conds},
            "forbidden_found": bad,
        }
    # Closure table in the task document must equal the computed closures for base releases.
    for name, (candidate, _, _) in RELEASES.items():
        row = next(l for l in tasks_text.splitlines() if l.startswith(f"| {name} "))
        cell = row.split("|")[3]
        stated: set[str] = set()
        for pre, a, b in re.findall(r"([A-Z]+)-(\d{3})～(\d{3})", cell):
            stated |= {f"{pre}-{i:03d}" for i in range(int(a), int(b) + 1)}
        stated |= set(ID_RE.findall(re.sub(r"[A-Z]+-\d{3}～\d{3}", "", cell)))
        if stated != closure(candidate):
            errors.append(f"{name}: closure table {sorted(stated)} != computed {sorted(closure(candidate))}")

    # 4. Scenarios
    headings = {int(x) for x in re.findall(r"^### A(\d{2}) ", text["matrix"], re.M)}
    if headings != set(range(1, 21)):
        errors.append(f"scenario headings {sorted(headings)} are not A01..A20")
    used = {s for row in list(table.values()) + list(deferred.values()) for s in row["scenarios"]}
    if not used <= headings:
        errors.append(f"task acceptance references undefined scenarios {sorted(used - headings)}")
    if headings - used:
        errors.append(f"scenarios without a responsible card {sorted(headings - used)}")
    scenario_map = {
        f"A{s:02d}": {
            "active": sorted(t for t, r in table.items() if s in r["scenarios"]),
            "deferred": sorted(t for t, r in deferred.items() if s in r["scenarios"]),
        }
        for s in sorted(headings)
    }
    contracts = sorted(set(re.findall(r"^\| (C\d{2}) ", text["matrix"], re.M)))
    if contracts != [f"C{i:02d}" for i in range(1, 15)]:
        errors.append(f"contract rows {contracts} are not C01..C14")

    # 5. Cross-document IDs, links, fences
    files = {}
    for key, name in DOCS.items():
        body = text[key]
        info = {"bytes": len(body.encode("utf-8"))}
        unknown = sorted(set(ID_RE.findall(body)) - ids - set(deferred) - RETIRED)
        if any(f"v1.{n}.md" in body for n in (0, 1, 2)):
            errors.append(f"{name}: links to a superseded master document")
        if unknown:
            errors.append(f"{name}: references undefined task IDs {unknown}")
        fences = body.count("```")
        info["fences"] = "balanced" if fences % 2 == 0 else "unbalanced"
        if fences % 2:
            errors.append(f"{name}: unbalanced code fences")
        links = re.findall(r"\]\(([^)\s]+)\)", body)
        local = [l for l in links if not re.match(r"[a-z]+://", l)]
        bad_links = []
        for link in local:
            path, _, anchor = link.partition("#")
            target = ROOT / path if path else ROOT / name
            if not target.exists():
                bad_links.append(link)
                continue
            if anchor:
                heads = re.findall(r"^#{1,6} (.+)$", target.read_text(encoding="utf-8"), re.M)
                if anchor not in {slug(h) for h in heads}:
                    bad_links.append(link)
        info["local_links"] = len(local)
        info["local_links_valid"] = not bad_links
        if bad_links:
            errors.append(f"{name}: broken local links {bad_links}")
        refs_used = set(re.findall(r"\]\[([A-Za-z0-9-]+)\]", body)) | set(
            re.findall(r"(?<![\]\w])\[([A-Z][A-Za-z0-9-]*\d?)\](?![(\[:])", body)
        )
        refs_def = set(re.findall(r"^\[([A-Za-z0-9-]+)\]: ", body, re.M))
        missing = sorted(r for r in refs_used if r not in refs_def and re.fullmatch(r"[A-Z][A-Z0-9]*-?[A-Z0-9]*", r))
        info["reference_links_defined"] = not missing
        if missing:
            errors.append(f"{name}: undefined reference links {missing}")
        info["sha256"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
        files[name] = info

    # 6. Shared facts that must agree across documents
    facts = {
        "yuki_commit": "08164f6732c14fca0396eb3411f8a82d6762aa8c",
        "desktop_commit": "a5687a66cf74485c94394e1008ddfbc0ebd926a2",
        "mika_commit": "10b5712e30d6d10ace588bac08a1aad0fc6057ce",
        "poki_commit": "2743a6b62bdb346a9bb1c1ee51c37ae0c5cd61d0",
        "openapi_sha_prefix": "14fd970b",
    }
    for key, value in facts.items():
        where = [n for k, n in DOCS.items() if value[:8] in text[k]]
        if key in {"yuki_commit", "desktop_commit"} and len(where) < 3:
            errors.append(f"{key} appears in only {where}")

    report = {
        "document_version": "v1.3",
        "scope": "only Yuki-Mobile is written; Yuki and Yuki-Desktop are pinned read-only dependencies",
        "document_date": "2026-11-17 America/Los_Angeles",
        "validator": "python3 文件校驗.py --write",
        "document_static_validation": "passed" if not errors else "failed",
        "errors": errors,
        "task_count": len(ids),
        "tasks": table,
        "scenario_count": len(headings),
        "scenario_to_tasks": scenario_map,
        "contract_rows": contracts,
        "dependency_ids": "all_defined" if not any("undefined dependency" in e for e in errors) else "missing",
        "mandatory_dependency_graph": "acyclic" if not any(e.startswith("cycle") for e in errors) else "cyclic",
        "topological_order": order,
        "conditional_prerequisites": {k: sorted(v) for k, v in CONDITIONAL.items()},
        "deferred_cards": deferred,
        "deferred_conditional_prerequisites": {k: sorted(v) for k, v in DEFERRED_CONDITIONAL.items()},
        "retired_ids": sorted(RETIRED),
        "releases": releases,
        "independent_releases": {
            "android_core_without_ios_push_or_operator": not releases["R-A"]["forbidden_found"],
            "ios_core_without_android_push_or_operator": not releases["R-I"]["forbidden_found"],
        },
        "files": files,
        "fixed_sources": facts,
    }
    return_code = 0 if not errors else 1
    if "--write" in sys.argv:
        report.update(EXECUTION_EVIDENCE)
        (ROOT / "文件校驗報告.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        names = list(DOCS.values()) + ["文件校驗.py", "文件校驗報告.json"]
        (ROOT / "SHA256SUMS.txt").write_text(
            "".join(
                f"{hashlib.sha256((ROOT / n).read_bytes()).hexdigest()}  {n}\n" for n in names
            ),
            encoding="utf-8",
        )
    print(json.dumps({"static_validation": report["document_static_validation"], "errors": errors,
                      "tasks": len(ids), "deferred": len(deferred), "scenarios": len(headings)}, ensure_ascii=False, indent=1))
    return return_code


if __name__ == "__main__":
    sys.exit(main())
