#!/usr/bin/env python3
"""SDLC gate check for pull requests (run by .github/workflows/sdlc-gates.yml). No AI.

Make this a REQUIRED status check in branch protection. It fails the PR when:
  1. an intent/spec/plan moves through a human gate (intent accepted|rejected, spec approved,
     plan accepted) without signed_off_by + signed_off_at, or the status change was committed
     by a bot (the agent may draft; only a human signs);
  2. the gates are out of order (spec approved before the intent is accepted, plan accepted
     before the spec is approved, an approved spec still has "Decision: pending");
  3. two change folders share a number;
  4. the PR changes code but is not linked to a change whose plan is accepted/building/done
     (unless it has the trivial label, or gates.requirePlanForCode is false).

Usage: gate_check.py --base <ref> [--head HEAD] [--branch NAME] [--labels a,b] [--body-file F]
Writes a markdown summary to stdout (and $GITHUB_STEP_SUMMARY), and `risk`, `changes`
to $GITHUB_OUTPUT. Exit 1 on any violation. Standard library only.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys

KINDS = ("intent", "spec", "plan")
GATE_STATUS = {"intent": {"accepted", "rejected"}, "spec": {"approved"}, "plan": {"accepted"}}
GATE_ID = {"intent": "G1", "spec": "G2", "plan": "G3"}
BOT_RX = re.compile(r"(\[bot\]|github-actions|noreply@anthropic\.com|^claude\b)", re.I)
NON_CODE_DEFAULT = [
    "sdlc/**", "docs/**", "lessons/**", "evals/**", "ops/**", "releases/**", ".github/**", ".claude/**",
    "*.md", "**/*.md", "LICENSE", ".gitignore",
]


def git(*args: str) -> str:
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def front(text: str) -> dict:
    m = re.match(r"^---\s*\n(.*?)\n---", text or "", flags=re.DOTALL)
    out = {}
    if m:
        for line in m.group(1).splitlines():
            km = re.match(r"^([A-Za-z_]+):\s*(.*?)\s*(#.*)?$", line)
            if km:
                out[km.group(1)] = km.group(2).strip().strip('"').strip("'")
    return out


def signing_bots(base: str, head: str, path: str, gated: set) -> list:
    """Authors/committers of the commits in this PR that ADDED a gated status line to `path`.
    Drafting commits by the bot (status: draft/review) are fine; only the signing commit matters.
    Both author and committer are checked, so `git commit --author=...` can't disguise a bot."""
    shas = git("log", "--format=%H", f"{base}..{head}", "-G", "^status:", "--", path).split()
    out = []
    for sha in shas:
        diff = git("show", "--format=", sha, "--", path)
        if not re.search(r"(?m)^\+status:\s*(" + "|".join(sorted(gated)) + r")\b", diff):
            continue
        who = git("show", "-s", "--format=%an <%ae>|%cn <%ce>", sha).strip()
        if any(BOT_RX.search(x.strip()) for x in who.split("|")):
            out.append(who)
    return out


def show(ref: str, path: str) -> str:
    return git("show", f"{ref}:{path}")


def match(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, p) or fnmatch.fnmatch("/" + path, "*/" + p.lstrip("*/")) for p in patterns)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--branch", default="")
    ap.add_argument("--labels", default="")
    ap.add_argument("--body-file", default="")
    ap.add_argument("--config", default=".claude/sdlc.json")
    a = ap.parse_args()

    try:
        cfg = json.load(open(a.config))
    except Exception:
        cfg = {}
    gates = cfg.get("gates") or {}
    require_plan = gates.get("requirePlanForCode", True)
    trivial_label = gates.get("trivialLabel", "sdlc:trivial")
    non_code = gates.get("nonCodePaths") or NON_CODE_DEFAULT
    labels = {l.strip() for l in a.labels.split(",") if l.strip()}
    body = open(a.body_file).read() if a.body_file and os.path.exists(a.body_file) else ""

    base = git("merge-base", a.base, a.head).strip() or a.base
    changed = [l for l in git("diff", "--name-only", f"{base}..{a.head}").splitlines() if l]
    errors, notes, risk, linked = [], [], "routine", set()

    # 3. unique numbers
    nums = {}
    for d in sorted(os.listdir("sdlc/changes")) if os.path.isdir("sdlc/changes") else []:
        if os.path.isdir(os.path.join("sdlc/changes", d)) and re.match(r"^\d+-", d):
            nums.setdefault(d.split("-")[0], []).append(d)
    for n, ds in nums.items():
        if len(ds) > 1:
            errors.append(f"Change number {n} is used twice: {', '.join(ds)}. Renumber one.")

    # 1 + 2. gate transitions in this PR
    for path in changed:
        m = re.match(r"^sdlc/changes/([^/]+)/(intent|spec|plan)\.md$", path)
        if not m:
            continue
        folder, kind = m.group(1), m.group(2)
        linked.add(folder)
        if not os.path.exists(path):
            continue
        before, after = front(show(base, path)), front(open(path).read())
        st_b, st_a = before.get("status", ""), after.get("status", "")
        if (after.get("risk") or "").startswith("elevated"):
            risk = "elevated"
        if st_a in GATE_STATUS[kind] and st_a != st_b:
            gid = GATE_ID[kind]
            if not after.get("signed_off_by") or not after.get("signed_off_at"):
                errors.append(f"{gid} {path}: status → {st_a} without signed_off_by/signed_off_at. Sign with /sdlc:signoff {folder.split('-')[0]} {kind}.")
            bots = signing_bots(base, a.head, path, GATE_STATUS[kind])
            if bots:
                errors.append(f"{gid} {path}: the status change was committed by a bot ({bots[0]}). Agents draft; a human must sign the gate.")
            notes.append(f"{gid} signed: {kind} {folder} → {st_a} by {after.get('signed_off_by', '?')}")
        d = os.path.join("sdlc/changes", folder)
        if kind == "spec" and st_a == "approved":
            if front(open(os.path.join(d, "intent.md")).read() if os.path.exists(os.path.join(d, "intent.md")) else "").get("status") != "accepted":
                errors.append(f"G2 {path}: spec approved but the intent is not accepted (G1 first).")
            if re.search(r"(?im)^\s*[-*]?\s*\**Decision:?\**:?\s*pending", open(path).read()):
                errors.append(f"G2 {path}: approved spec still has 'Decision: pending'.")
        if kind == "plan" and st_a == "accepted" and not (after.get("spec") or "").lower().startswith("none"):
            sp = os.path.join(d, "spec.md")
            if not os.path.exists(sp) or front(open(sp).read()).get("status") != "approved":
                errors.append(f"G3 {path}: plan accepted but the spec is not approved (G2 first).")

    # 4. code must trace to an accepted plan
    code = [p for p in changed if not match(p, non_code)]
    for n in set(re.findall(r"(?:^|[/_-])(\d{3})(?:-|$)", a.branch)) | set(re.findall(r"sdlc/changes/(\d{3})", body)) | set(re.findall(r"(?i)\bchange[:# ]+(\d{3})\b", body)):
        linked.update(d for d in nums.get(n, []))
    if code and require_plan and trivial_label not in labels:
        ok = []
        for folder in linked:
            pfm = front(open(os.path.join("sdlc/changes", folder, "plan.md")).read()) if os.path.exists(os.path.join("sdlc/changes", folder, "plan.md")) else {}
            if pfm.get("status") in ("accepted", "building", "done"):
                ok.append(folder)
                if (pfm.get("risk") or "").startswith("elevated"):
                    risk = "elevated"
        if not ok:
            errors.append(
                f"This PR changes {len(code)} code file(s) but isn't linked to a change with an accepted plan. "
                f"Name the branch like `sdlc/NNN-slug`, or write `Change: NNN` in the PR description. "
                f"For a genuinely small fix, add the `{trivial_label}` label."
            )
        else:
            notes.append(f"Code traced to accepted plan(s): {', '.join(sorted(ok))}")

    lines = ["## SDLC gates", ""]
    lines += [f"- ❌ {e}" for e in errors] or ["- ✅ All gate rules pass."]
    lines += [f"- {n}" for n in notes]
    lines.append(f"- Risk: **{risk}**")
    report = "\n".join(lines)
    print(report)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        open(os.environ["GITHUB_STEP_SUMMARY"], "a").write(report + "\n")
    if os.environ.get("GITHUB_OUTPUT"):
        open(os.environ["GITHUB_OUTPUT"], "a").write(f"risk={risk}\nchanges={' '.join(sorted(linked))}\n")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
