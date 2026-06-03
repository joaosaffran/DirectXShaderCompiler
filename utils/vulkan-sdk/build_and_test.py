#!/usr/bin/env python3
"""Build DXC for SPIR-V and validate its output. Steps 2 & 3 of the RC pipeline.

This configures DXC with SPIR-V codegen enabled, builds it, then builds and runs
ClangSPIRVTests -- the gtest suite for the SPIR-V backend. It links SPIRV-Tools
directly and runs spirv-val, so a green run means "DXC produces valid SPIR-V
against the pinned SPIRV-Tools/SPIRV-Headers". (Note: this is the unit-test suite;
the larger tools/clang/test/CodeGenSPIRV/ FileCheck corpus runs separately via lit.)

Test failures are recorded in the manifest. With --allow-test-failures the script
still exits 0 so the pipeline publishes the release candidate regardless.

The cmake flags mirror the existing GCP build (gcp-pipelines/x86_64-linux-clang.yml)
so this stays consistent with how DXC is already built for the shader toolchain.

Stdlib only. Intended to run after sync_deps.py has pinned the submodules.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Keep this list in sync with gcp-pipelines/x86_64-linux-clang.yml.
#
# On Windows this configure pulls in TAEF-dependent unittests (gated by the
# default HLSL_INCLUDE_TESTS=ON in PredefinedParams.cmake). The pipeline restores
# TAEF and points FindTAEF.cmake at it via the TAEF_* env vars handled in
# configure() -- so no TAEF flags belong in this list. See vulkan-sdk-rc.yml.
SPIRV_CMAKE_FLAGS = [
    "-DCMAKE_BUILD_TYPE=Release",
    "-DENABLE_SPIRV_CODEGEN=ON",
    "-DSPIRV_BUILD_TESTS=ON",
    "-DLLVM_ENABLE_WERROR=On",
]

# The unittest target that compiles the CodeGenSPIRV corpus and runs spirv-val.
TEST_TARGET = "ClangSPIRVTests"


def run(cmd, **kwargs):
    printable = " ".join(str(c) for c in cmd)
    print(f"\n$ {printable}", flush=True)
    result = subprocess.run([str(c) for c in cmd], **kwargs)
    if result.returncode != 0:
        raise SystemExit(f"command failed ({result.returncode}): {printable}")
    return result


def configure(build_dir, jobs):
    cache = REPO_ROOT / "cmake" / "caches" / "PredefinedParams.cmake"
    cmd = [
        "cmake",
        "-S", str(REPO_ROOT),
        "-B", str(build_dir),
        "-GNinja",
        *SPIRV_CMAKE_FLAGS,
        "-C", str(cache),
    ]
    # On Windows the configure needs TAEF (find_package(TAEF REQUIRED) in the
    # HLSL/dxilconv unittests). The pipeline restores the Microsoft.Taef nuget
    # package and exports these env vars; forwarding them pre-seeds FindTAEF.cmake's
    # find_path/find_program cache entries so it skips searching (the package's
    # binaries dir is named differently across versions). The TAEF .libs are then
    # found relative to TAEF_INCLUDE_DIR, so we don't need to point at them.
    for cache_var in ("TAEF_INCLUDE_DIR", "TAEF_EXECUTABLE"):
        value = os.environ.get(cache_var)
        if value:
            cmd.append(f"-D{cache_var}={value}")
    run(cmd, cwd=str(REPO_ROOT))


def build(build_dir, target, jobs):
    cmd = ["cmake", "--build", str(build_dir), "--target", target]
    if jobs:
        cmd += ["-j", str(jobs)]
    run(cmd, cwd=str(REPO_ROOT))


def find_test_binary(build_dir):
    names = [TEST_TARGET, TEST_TARGET + ".exe"]
    for name in names:
        for path in build_dir.rglob(name):
            if os.access(path, os.X_OK) or path.suffix == ".exe":
                return path
    raise SystemExit(f"Could not find {TEST_TARGET} under {build_dir}")


def read_pinned_commits():
    """Report which SPIR-V commits we actually built against, for the manifest."""
    kg = json.loads((Path(__file__).resolve().parent / "known_good.json").read_text())
    return {d["name"]: d["commit"] for d in kg["dependencies"]}


def run_tests(test_bin, build_dir):
    """Run the gtest suite to completion (gtest never stops early) and summarize.

    Returns a dict with pass/fail counts, the failing test names, the process
    return code, and the path to a JUnit-style xml report. Never raises on test
    failure -- the caller decides whether that blocks publishing.
    """
    xml = build_dir / "spirv-test-results.xml"
    cmd = [str(test_bin), f"--gtest_output=xml:{xml}"]
    print(f"\n$ {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), text=True, capture_output=True)
    print(proc.stdout)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)

    passed = failed = 0
    m = re.search(r"\[\s*PASSED\s*\] (\d+) test", proc.stdout)
    if m:
        passed = int(m.group(1))
    m = re.search(r"\[\s*FAILED\s*\] (\d+) test", proc.stdout)
    if m:
        failed = int(m.group(1))
    # Failing test names look like "[  FAILED  ] Suite.Case"; the count line
    # ("[  FAILED  ] 3 tests, listed below:") has no dot so it won't match.
    failed_tests = []
    for name in re.findall(r"\[\s*FAILED\s*\] (\S+\.\S+)", proc.stdout):
        if name not in failed_tests:
            failed_tests.append(name)

    return {
        "returncode": proc.returncode,
        "passed": passed,
        "failed": failed,
        "total": passed + failed,
        "failed_tests": failed_tests,
        "results_xml": str(xml),
    }


def write_manifest(build_dir, dxc_sha, test_summary):
    manifest = {
        "dxc_commit": dxc_sha,
        "spirv_dependencies": read_pinned_commits(),
        "test_target": TEST_TARGET,
        "tests": {
            "total": test_summary["total"],
            "passed": test_summary["passed"],
            "failed": test_summary["failed"],
            "failed_tests": test_summary["failed_tests"],
        },
        # True only when the suite ran clean. A published RC with validated=false
        # built fine but had test failures -- downstream consumers can gate on this.
        "validated": test_summary["failed"] == 0 and test_summary["returncode"] == 0,
    }
    out = build_dir / "rc-manifest.json"
    out.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"\nWrote release-candidate manifest -> {out}")
    return out


def git_head():
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT),
                       text=True, capture_output=True)
    return r.stdout.strip() if r.returncode == 0 else "unknown"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build-dir", default=str(REPO_ROOT / "build"),
                        help="Build directory (default: ./build).")
    parser.add_argument("--jobs", "-j", type=int, default=0,
                        help="Parallel build jobs (default: let the generator decide).")
    parser.add_argument("--skip-build", action="store_true",
                        help="Skip configure/build; just run the existing test binary.")
    parser.add_argument("--allow-test-failures", action="store_true",
                        help="Record test failures in the manifest but still exit 0, "
                             "so the pipeline publishes the release candidate anyway.")
    args = parser.parse_args(argv)

    build_dir = Path(args.build_dir).resolve()

    if not args.skip_build:
        if shutil.which("ninja") is None:
            raise SystemExit("ninja not found on PATH; install it or adjust the generator.")
        configure(build_dir, args.jobs)
        # Build the compiler (artifact) and the validating test binary. A build
        # failure is always fatal -- there's no release candidate without a binary.
        build(build_dir, "dxc", args.jobs)
        build(build_dir, TEST_TARGET, args.jobs)

    test_bin = find_test_binary(build_dir)
    summary = run_tests(test_bin, build_dir)
    manifest = write_manifest(build_dir, git_head(), summary)

    if summary["failed"] or summary["returncode"] != 0:
        print(f"\n{summary['failed']} of {summary['total']} tests FAILED: "
              f"{', '.join(summary['failed_tests']) or 'see output above'}")
        if not args.allow_test_failures:
            return 1
        print("Publishing release candidate anyway (--allow-test-failures); "
              "see validated=false in the manifest.")
    else:
        print(f"\nAll {summary['total']} tests passed.")

    print(f"Release candidate manifest: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
