#!/usr/bin/env python3
"""Build DXC for SPIR-V and run *every* SPIR-V test, across all harnesses.

Steps 2 & 3 of the RC pipeline. Configures DXC with SPIR-V codegen enabled, builds
it, then runs every SPIR-V test we can find regardless of harness:

  * gtest -- ClangSPIRVTests: the SPIR-V backend unit suite (links SPIRV-Tools,
    runs spirv-val internally).
  * lit   -- tools/clang/test/CodeGenSPIRV/: ~1300 FileCheck tests driven through
    the built dxc, the actual codegen corpus.
  * TAEF  -- the SPIR-V tests inside ClangHLSLTests (e.g. RewriterTest::RunSpirv),
    selected by a "*Spirv*" name filter.

Each harness runs independently and is non-fatal: results (pass/fail counts +
failing test names) are recorded per-suite in rc-manifest.json. With
--allow-test-failures the script still exits 0 so the pipeline publishes the
release candidate regardless; the manifest's top-level "validated" is true only
when every suite ran and passed.

The cmake flags mirror the existing GCP build (gcp-pipelines/x86_64-linux-clang.yml).
Stdlib only. Builds whatever the SPIRV-Headers/SPIRV-Tools submodules are checked
out at -- the submodule pointers are the source of truth.
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
CODEGEN_SPIRV_DIR = REPO_ROOT / "tools" / "clang" / "test" / "CodeGenSPIRV"

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
# spirv-val is built separately, from a standalone SPIRV-Tools configure (see
# build_spirv_val). DXC's in-tree build can't compile the SPIRV-Tools executables:
# its global CMAKE_INCLUDE_CURRENT_DIR puts each target's source dir on the include
# path, so tools/io.cpp's `#include <io.h>` picks up SPIRV-Tools' own tools/io.h
# instead of the CRT header. A separate configure (no that flag) builds it cleanly,
# and we drop the binary into build/bin so the SPIR-V tests find it on PATH.
SPIRV_TOOLS_SRC = REPO_ROOT / "external" / "SPIRV-Tools"
SPIRV_HEADERS_SRC = REPO_ROOT / "external" / "SPIRV-Headers"

# Built unconditionally; a failure here is fatal (no binary => no release candidate).
# lit.cfg registers substitutions for the whole DX tool family (%dxc %dxv %dxa
# %dxopt %dxr %dxl, plus %batch -> dxc_batch on Windows) and escapes every
# substitution value up front -- so any of these we DON'T build is None and crashes
# lit on *every* test, even though CodeGenSPIRV itself only invokes %dxc. lit.cfg
# also shells out to llvm-config, and error-path RUN lines use `not`/`count`.
# ClangSPIRVTests is the gtest suite.
REQUIRED_TARGETS = ["dxc", "dxv", "dxa", "dxopt", "dxr", "dxl",
                    "FileCheck", "llvm-config", "not", "count", "ClangSPIRVTests"]
if sys.platform == "win32":
    REQUIRED_TARGETS.append("dxc_batch")  # %batch substitution, Windows-only
# Best-effort: ClangHLSLTests hosts the TAEF SPIR-V tests. If it fails to build we
# note it and carry on.
OPTIONAL_TARGETS = ["ClangHLSLTests"]

GTEST_TARGET = "ClangSPIRVTests"
TAEF_DLL = "ClangHLSLTests"


def run(cmd, **kwargs):
    printable = " ".join(str(c) for c in cmd)
    print(f"\n$ {printable}", flush=True)
    result = subprocess.run([str(c) for c in cmd], **kwargs)
    if result.returncode != 0:
        raise SystemExit(f"command failed ({result.returncode}): {printable}")
    return result


# --------------------------------------------------------------------------- #
# Configure / build
# --------------------------------------------------------------------------- #

def configure(build_dir):
    cache = REPO_ROOT / "cmake" / "caches" / "PredefinedParams.cmake"
    cmd = [
        "cmake", "-S", str(REPO_ROOT), "-B", str(build_dir), "-GNinja",
        *SPIRV_CMAKE_FLAGS, "-C", str(cache),
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


def build(build_dir, targets, jobs):
    cmd = ["cmake", "--build", str(build_dir), "--target", *targets]
    if jobs:
        cmd += ["-j", str(jobs)]
    run(cmd, cwd=str(REPO_ROOT))


def build_optional(build_dir, target, jobs):
    """Build a target, returning True/False instead of raising on failure."""
    cmd = ["cmake", "--build", str(build_dir), "--target", target]
    if jobs:
        cmd += ["-j", str(jobs)]
    print(f"\n$ {' '.join(str(c) for c in cmd)}", flush=True)
    ok = subprocess.run([str(c) for c in cmd], cwd=str(REPO_ROOT)).returncode == 0
    if not ok:
        print(f"(optional target '{target}' failed to build; dependent tests will be skipped)")
    return ok


def find_file(build_dir, name):
    for path in build_dir.rglob(name):
        return path
    return None


def build_spirv_val(main_build_dir, jobs):
    """Build spirv-val from a standalone SPIRV-Tools configure and drop it into
    build/bin so the SPIR-V tests can invoke it on PATH. Best-effort: a failure is
    logged but does not abort the pipeline (it can't build in-tree -- see the note
    on SPIRV_TOOLS_SRC above). Returns the path to the binary, or None."""
    if not SPIRV_TOOLS_SRC.is_dir() or not SPIRV_HEADERS_SRC.is_dir():
        print("(spirv-val: SPIRV-Tools/SPIRV-Headers not checked out; skipping)")
        return None
    st_build = main_build_dir / "spirv-tools-standalone"
    configure = [
        "cmake", "-S", str(SPIRV_TOOLS_SRC), "-B", str(st_build), "-GNinja",
        "-DCMAKE_BUILD_TYPE=Release", "-DSPIRV_SKIP_TESTS=ON",
        # Forward slashes: SPIRV-Tools embeds this path into a cmake string (the
        # --extinst-grammar command), where Windows backslashes become invalid
        # escapes ("\a", ...). as_posix() avoids that.
        f"-DSPIRV-Headers_SOURCE_DIR={SPIRV_HEADERS_SRC.as_posix()}",
    ]
    print(f"\n$ {' '.join(configure)}", flush=True)
    if subprocess.run(configure, cwd=str(REPO_ROOT)).returncode != 0:
        print("WARNING: standalone SPIRV-Tools configure failed; spirv-val unavailable.")
        return None
    if not build_optional(st_build, "spirv-val", jobs):
        print("WARNING: standalone spirv-val build failed; spirv-val unavailable.")
        return None

    exe = find_file(st_build, "spirv-val.exe") or find_file(st_build, "spirv-val")
    if not exe:
        print("WARNING: standalone build produced no spirv-val binary.")
        return None
    dest = main_build_dir / "bin" / exe.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(exe, dest)
    print(f"spirv-val -> {dest}")
    return dest


# --------------------------------------------------------------------------- #
# Test harnesses -- each returns a suite-result dict, never raises on failure
# --------------------------------------------------------------------------- #

def _suite(name, harness, status, passed=None, failed=None, total=None,
           failed_tests=None, returncode=None, note=None):
    return {
        "name": name, "harness": harness, "status": status,
        "passed": passed, "failed": failed, "total": total,
        "failed_tests": failed_tests or [], "returncode": returncode, "note": note,
    }


def suite_gtest(build_dir):
    """ClangSPIRVTests -- the SPIR-V backend unit suite."""
    binary = find_file(build_dir, GTEST_TARGET + ".exe") or find_file(build_dir, GTEST_TARGET)
    if not binary:
        return _suite("spirv-unit", "gtest", "skipped", note=f"{GTEST_TARGET} not found")
    xml = build_dir / "spirv-unit-results.xml"
    proc = _capture([str(binary), f"--gtest_output=xml:{xml}"])
    passed = _int(re.search(r"\[\s*PASSED\s*\] (\d+) test", proc.stdout))
    failed = _int(re.search(r"\[\s*FAILED\s*\] (\d+) test", proc.stdout))
    names = _dedup(re.findall(r"\[\s*FAILED\s*\] (\S+\.\S+)", proc.stdout))
    return _suite("spirv-unit", "gtest", "ran", passed, failed,
                  (passed or 0) + (failed or 0), names, proc.returncode)


def suite_lit(build_dir):
    """The CodeGenSPIRV FileCheck corpus, via the built llvm-lit."""
    litpy = find_file(build_dir, "llvm-lit.py") or find_file(build_dir, "llvm-lit")
    if not litpy:
        return _suite("spirv-codegen", "lit", "skipped", note="llvm-lit not found")
    if not find_file(build_dir, "FileCheck.exe") and not find_file(build_dir, "FileCheck"):
        return _suite("spirv-codegen", "lit", "skipped", note="FileCheck not built")
    xml = build_dir / "spirv-codegen-results.xml"
    proc = _capture([sys.executable, str(litpy), "-v", "--no-progress-bar",
                     f"--xunit-xml-output={xml}", str(CODEGEN_SPIRV_DIR)])
    out = proc.stdout

    def tally(label):
        m = re.search(rf"{label}\s*:\s*(\d+)", out)
        return int(m.group(1)) if m else 0

    # lit's summary uses "Expected Passes", "Unexpected Failures", etc. -- NOT
    # plain "Passed"/"Failed". XFAIL (Expected Failures) and Unsupported are not
    # failures; XPASS (Unexpected Passes), Unresolved and Timed Out are.
    passed = tally("Expected Passes")
    failed = (tally("Unexpected Failures") + tally("Unexpected Passes")
              + tally("Unresolved Tests") + tally("Timed Out Tests"))
    # Names of the genuinely-bad results (FAIL / XPASS / UNRESOLVED / TIMEOUT).
    names = _dedup(re.findall(r"^(?:FAIL|XPASS|UNRESOLVED|TIMEOUT): .*?:: (\S+)",
                              out, re.MULTILINE))
    ran = bool(re.search(r"Expected Passes\s*:|Testing Time:", out))
    return _suite("spirv-codegen", "lit", "ran" if ran else "skipped",
                  passed, failed, passed + failed, names, proc.returncode,
                  note=None if ran else "no lit summary found")


def suite_taef(build_dir):
    """SPIR-V tests inside the TAEF ClangHLSLTests dll (e.g. RewriterTest::RunSpirv)."""
    te = os.environ.get("TAEF_EXECUTABLE")
    if not te or not Path(te).is_file():
        return _suite("spirv-taef", "taef", "skipped", note="te.exe (TAEF_EXECUTABLE) unavailable")
    dll = find_file(build_dir, TAEF_DLL + ".dll")
    if not dll:
        return _suite("spirv-taef", "taef", "skipped", note=f"{TAEF_DLL}.dll not built")
    # te.exe needs dxcompiler.dll (build/bin) on PATH and HLSL_SRC_DIR to locate
    # test data files (GetPathToHlslDataFile). Run only SPIR-V-named tests.
    env = dict(os.environ)
    env["HLSL_SRC_DIR"] = str(REPO_ROOT)
    env["PATH"] = str(dll.parent) + os.pathsep + env.get("PATH", "")
    proc = _capture([te, str(dll), "/select:@Name='*Spirv*'"], env=env)
    total = _int(re.search(r"Total[=:]\s*(\d+)", proc.stdout))
    passed = _int(re.search(r"Passed[=:]\s*(\d+)", proc.stdout))
    failed = _int(re.search(r"Failed[=:]\s*(\d+)", proc.stdout))
    return _suite("spirv-taef", "taef", "ran", passed, failed, total,
                  None, proc.returncode)


# Trivially-valid shaders, one per stage, used to exercise the standalone
# spirv-val explicitly. (The CodeGenSPIRV corpus validates in-process via the
# linked SPIRV-Tools library and never shells out to spirv-val, so without this
# the spirv-val binary is built but never actually runs.)
SPIRV_VAL_SHADERS = [
    ("ps_6_0", "float4 main() : SV_Target { return float4(1, 0, 0, 1); }"),
    ("vs_6_0", "float4 main(float4 p : POSITION) : SV_Position { return p; }"),
    ("cs_6_0", "[numthreads(1, 1, 1)] void main() { }"),
]


def suite_spirv_val(build_dir):
    """Compile shaders with the built dxc and validate the SPIR-V with the
    standalone spirv-val -- an explicit, logged validator run against dxc output."""
    spirv_val = (find_file(build_dir / "bin", "spirv-val.exe")
                 or find_file(build_dir, "spirv-val.exe") or find_file(build_dir, "spirv-val"))
    dxc = (find_file(build_dir / "bin", "dxc.exe")
           or find_file(build_dir, "dxc.exe") or find_file(build_dir, "dxc"))
    if not spirv_val:
        return _suite("spirv-val", "spirv-val", "skipped", note="spirv-val not built")
    if not dxc:
        return _suite("spirv-val", "spirv-val", "skipped", note="dxc not found")

    # Print the version first so the log unambiguously shows spirv-val running.
    _capture([str(spirv_val), "--version"])

    tmp = build_dir / "spirv-val-tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    passed = failed = 0
    failed_tests = []
    for i, (profile, src) in enumerate(SPIRV_VAL_SHADERS):
        hlsl = tmp / f"case{i}_{profile}.hlsl"
        spv = tmp / f"case{i}_{profile}.spv"
        hlsl.write_text(src)
        comp = _capture([str(dxc), "-T", profile, "-E", "main", "-spirv",
                         "-fspv-target-env=vulkan1.0", "-Fo", str(spv), str(hlsl)])
        if comp.returncode != 0 or not spv.is_file():
            failed += 1
            failed_tests.append(f"{profile}:compile")
            continue
        val = _capture([str(spirv_val), "--target-env", "vulkan1.0", str(spv)])
        if val.returncode == 0:
            passed += 1
        else:
            failed += 1
            failed_tests.append(f"{profile}:validate")
    return _suite("spirv-val", "spirv-val", "ran", passed, failed,
                  passed + failed, failed_tests, 0 if failed == 0 else 1)


def _capture(cmd, env=None):
    print(f"\n$ {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), text=True,
                          capture_output=True, env=env)
    print(proc.stdout)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)
    return proc


def _int(match):
    return int(match.group(1)) if match else None


def _dedup(seq):
    out = []
    for item in seq:
        if item not in out:
            out.append(item)
    return out


# --------------------------------------------------------------------------- #
# Manifest
# --------------------------------------------------------------------------- #

def read_pinned_commits():
    """The SPIR-V dependency commits are whatever the submodules are checked out
    at -- the submodule pointers are the source of truth."""
    deps = {}
    for name in ("SPIRV-Headers", "SPIRV-Tools"):
        sub = REPO_ROOT / "external" / name
        r = subprocess.run(["git", "-C", str(sub), "rev-parse", "HEAD"],
                           text=True, capture_output=True)
        deps[name] = r.stdout.strip() if r.returncode == 0 else "unknown"
    return deps


def git_head():
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT),
                       text=True, capture_output=True)
    return r.stdout.strip() if r.returncode == 0 else "unknown"


def suite_clean(s):
    return s["status"] == "ran" and not s["failed"] and s["returncode"] == 0


def write_manifest(build_dir, dxc_sha, suites):
    manifest = {
        "dxc_commit": dxc_sha,
        "spirv_dependencies": read_pinned_commits(),
        "test_suites": suites,
        # True only when every SPIR-V suite ran and passed. A published RC with
        # validated=false built fine but had test failures/skips -- downstream
        # consumers can gate on this.
        "validated": all(suite_clean(s) for s in suites),
    }
    out = build_dir / "rc-manifest.json"
    out.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"\nWrote release-candidate manifest -> {out}")
    return out


# --------------------------------------------------------------------------- #

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build-dir", default=str(REPO_ROOT / "build"),
                        help="Build directory (default: ./build).")
    parser.add_argument("--jobs", "-j", type=int, default=0,
                        help="Parallel build jobs (default: let the generator decide).")
    parser.add_argument("--skip-build", action="store_true",
                        help="Skip configure/build; just run the existing test binaries.")
    parser.add_argument("--allow-test-failures", action="store_true",
                        help="Record test failures in the manifest but still exit 0, "
                             "so the pipeline publishes the release candidate anyway.")
    args = parser.parse_args(argv)

    build_dir = Path(args.build_dir).resolve()

    if not args.skip_build:
        if shutil.which("ninja") is None:
            raise SystemExit("ninja not found on PATH; install it or adjust the generator.")
        configure(build_dir)
        build(build_dir, REQUIRED_TARGETS, args.jobs)  # fatal on failure
        for target in OPTIONAL_TARGETS:
            build_optional(build_dir, target, args.jobs)
        # spirv-val, built standalone and dropped into build/bin (on the tests' PATH).
        build_spirv_val(build_dir, args.jobs)

    # Run every SPIR-V harness. Each is independent and non-fatal.
    suites = [suite_gtest(build_dir), suite_lit(build_dir), suite_taef(build_dir),
              suite_spirv_val(build_dir)]
    manifest = write_manifest(build_dir, git_head(), suites)

    print("\n=== SPIR-V test summary ===")
    any_problem = False
    for s in suites:
        if s["status"] != "ran":
            any_problem = True
            print(f"  {s['name']:<14} {s['harness']:<6} SKIPPED ({s['note']})")
        else:
            mark = "ok" if suite_clean(s) else "FAILURES"
            any_problem = any_problem or not suite_clean(s)
            print(f"  {s['name']:<14} {s['harness']:<6} {s['passed']} passed, "
                  f"{s['failed']} failed  [{mark}]")
            for name in s["failed_tests"]:
                print(f"        FAILED: {name}")
    print(f"Release candidate manifest: {manifest}")

    if any_problem and not args.allow_test_failures:
        return 1
    if any_problem:
        print("\nPublishing release candidate anyway (--allow-test-failures); "
              "see validated=false in the manifest.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
