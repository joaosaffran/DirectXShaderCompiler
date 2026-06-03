#!/usr/bin/env python3
"""Sync DXC's SPIR-V submodules to the commits pinned in known_good.json.

Step 1 of the Vulkan SDK release-candidate pipeline (see INF-0007). This is the
automated replacement for the manual "Update SPIRV-Headers and SPIRV-Tools
dependency in DXC" step of the LunarG SDK release checklist.

Two modes:

  sync_deps.py
      Check out each dependency at the exact `commit` recorded in known_good.json.
      Deterministic: same JSON in -> same submodule state out. This is what the
      pipeline runs.

  sync_deps.py --bump
      Fetch the latest commit on each dependency's tracked `branch`, check it out,
      and rewrite known_good.json with the new SHAs. This is how a human (or a
      scheduled job) advances the pins to "the most recent candidate" before
      opening the release/vulkan/<version> branch.

Stdlib only; no third-party deps. Run from anywhere -- paths are resolved
relative to the DXC repo root.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

# Repo root is two levels up from this file: utils/vulkan-sdk/sync_deps.py
REPO_ROOT = Path(__file__).resolve().parents[2]
KNOWN_GOOD = Path(__file__).resolve().parent / "known_good.json"


def git(*args, cwd=REPO_ROOT, capture=False):
    """Run a git command, echoing it. Raises on failure."""
    printable = "git " + " ".join(str(a) for a in args)
    print(f"  $ {printable}", flush=True)
    result = subprocess.run(
        ["git", *[str(a) for a in args]],
        cwd=str(cwd),
        text=True,
        capture_output=capture,
    )
    if result.returncode != 0:
        if capture and result.stderr:
            print(result.stderr, file=sys.stderr)
        raise SystemExit(f"git command failed ({result.returncode}): {printable}")
    return result.stdout.strip() if capture else None


def load_known_good():
    with open(KNOWN_GOOD, encoding="utf-8") as f:
        return json.load(f)


def save_known_good(data):
    with open(KNOWN_GOOD, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    print(f"Updated {KNOWN_GOOD.relative_to(REPO_ROOT)}")


def ensure_initialized(dep):
    """Make sure the submodule working tree exists before we fetch/checkout."""
    path = REPO_ROOT / dep["path"]
    if not (path / ".git").exists():
        print(f"Initializing submodule {dep['path']}")
        git("submodule", "update", "--init", dep["path"])


def sync(deps):
    """Check out each dependency at its pinned commit."""
    for dep in deps:
        print(f"\n== {dep['name']} -> {dep['commit']}")
        ensure_initialized(dep)
        sub = REPO_ROOT / dep["path"]
        # Fetch the specific commit in case it's newer than the local clone.
        git("fetch", "origin", dep["commit"], cwd=sub)
        git("checkout", "--detach", dep["commit"], cwd=sub)
        # Stage the gitlink so the RC branch records exactly what was tested.
        git("add", dep["path"])


def bump(deps):
    """Advance each dependency to the tip of its tracked branch."""
    for dep in deps:
        branch = dep.get("branch", "main")
        print(f"\n== {dep['name']} -> tip of {branch}")
        ensure_initialized(dep)
        sub = REPO_ROOT / dep["path"]
        git("fetch", "origin", branch, cwd=sub)
        new_sha = git("rev-parse", "FETCH_HEAD", cwd=sub, capture=True)
        print(f"  {dep['commit']} -> {new_sha}")
        dep["commit"] = new_sha


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--bump",
        action="store_true",
        help="Fetch latest commit on each tracked branch and rewrite known_good.json.",
    )
    args = parser.parse_args(argv)

    data = load_known_good()
    deps = data["dependencies"]

    if args.bump:
        bump(deps)
        save_known_good(data)
        print("\nBumped pins. Review the diff, then re-run without --bump to check them out.")
    else:
        sync(deps)
        print("\nSubmodules synced to known_good.json and staged.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
