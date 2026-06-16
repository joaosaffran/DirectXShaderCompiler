# DXC Vulkan SDK release-candidate pipeline (prototype)

A prototype for [hlsl-specs#878](https://github.com/microsoft/hlsl-specs/issues/878)
/ INF-0007. It automates the DXC-owned slice of the LunarG Vulkan SDK release
checklist: keeping DXC's SPIR-V dependencies current, building DXC for SPIR-V,
validating its output, and producing a candidate for downstream testing.

## Source of truth

The `external/SPIRV-Headers` and `external/SPIRV-Tools` **submodule pointers** are
the single source of truth for which SPIR-V DXC is built against. There is no
separate pin file — whatever the submodules are committed at is what gets built,
tested, and shipped.

## The pipeline

Everything lives in one workflow, **`vulkan-sdk-rc.yml`**, with three triggers:

- **schedule (weekly)** — runs the `bump` job: advances the SPIRV-Headers /
  SPIRV-Tools submodules to their latest upstream commit and, if anything changed,
  commits that bump onto a fresh `vk-update/<date>` branch and opens a PR against
  the default branch. Pushing that branch runs build-and-validate, so the bump's
  test results attach to the commit and show on the PR — merged only once it is
  green. (Uses a PAT — `WEEKLY_BUMP_TOKEN`, contents:write + pull-requests:write —
  because pushes/PRs made with the default `GITHUB_TOKEN` don't trigger workflows.)
- **push to `vk-update/**` or `release/vulkan/**`** — runs build-and-validate (and,
  on a release branch, the deliverable steps below).
- **workflow_dispatch (manual)** — `update_deps` runs the `bump` instead of
  building; otherwise it builds, with `publish_artifact` controlling the deliverable.

`build-and-validate` always:

1. **Checkout** the repo with `submodules: true`, so DXC builds against exactly the
   SPIR-V the submodule pointers name.
2. **Build** — `build_and_test.py` configures DXC with `ENABLE_SPIRV_CODEGEN=ON`
   `SPIRV_BUILD_TESTS=ON` and builds `dxc`. On Windows the configure also needs
   TAEF (DXC's test framework); the workflow restores the `Microsoft.Taef` nuget
   package (from the public feed microsoft/terminal uses — not on nuget.org) and
   pre-seeds `FindTAEF.cmake`. No DXC tests are disabled.
3. **Validate** — runs every SPIR-V test the DXC repo provides — the lit tests, the
   TAEF tests, and the googletest unit tests — plus a standalone `spirv-val` check
   of dxc's output. Each is non-fatal; per-suite results and a top-level
   `validated` flag are recorded in `rc-manifest.json` (`--allow-test-failures`
   keeps failures from blocking publication).

The **deliverable** steps run only on `release/vulkan/**` pushes or a manual opt-in
(`publish_artifact`) — not on the weekly `vk-update` validation runs:

4. **Publish** — uploads `dxc.exe` + `dxv.exe` + `dxcompiler.dll`, `rc-manifest.json`
   (DXC commit + the submodule SPIR-V commits + per-suite results + `validated`),
   and the JUnit reports as the `dxc_rc_<version>` artifact.
5. **Offload tests** — the `offload-tests` job runs the suite against the candidate
   on software renderers (see below).

So the weekly bump → `vk-update` PR → build/test chain is automatic; an actual SDK
release candidate is a `release/vulkan/<version>` branch, which additionally
publishes the artifact and runs the offload tests. The submodule commit is the
candidate.

## Bumping the submodules manually

The weekly job does this for you, but to bump + review locally:

```sh
git submodule update --remote external/SPIRV-Headers external/SPIRV-Tools
git diff --submodule -- external/SPIRV-Headers external/SPIRV-Tools   # review the bump
python utils/vulkan-sdk/build_and_test.py --build-dir build           # build + validate
```

## Run it locally

```sh
git submodule update --init external/SPIRV-Headers external/SPIRV-Tools
python utils/vulkan-sdk/build_and_test.py --build-dir build
```

## Downstream: execution on software renderers (OffloadTest)

`offload-tests.yml` clones `llvm/offload-test-suite`, builds it with `DXC_DIR` pointed
at the candidate's `dxc`, and runs its Vulkan tests (`check-hlsl-vk`) — the DXC Vulkan
SDK candidate emits SPIR-V, so the Vulkan path is what matters. It runs on **two
software Vulkan backends**, no physical GPU: **lavapipe** (`lvp`, Mesa's pure-CPU
Vulkan) and **Dozen** (`dzn`, Mesa's Vulkan-on-D3D12) on the **WARP** software adapter
— on a runner with no hardware GPU, Dozen's D3D12 device is WARP.

Both ICDs come from the Windows Mesa build (`pal1000/mesa-dist-win`) and are
**registered in the registry** (`HKLM\SOFTWARE\Khronos\Vulkan\Drivers`, via `reg.exe`)
because the Vulkan loader **ignores `VK_DRIVER_FILES`/`VK_ICD_FILENAMES` when the
process is elevated** — and GitHub-hosted runners run elevated — so the registry is the
only discovery path it trusts. `check-hlsl-vk` runs once per backend, selecting the
device with the suite's own `OFFLOADTEST_GPU_NAME` (a Python-side regex on the device
description: `llvmpipe` for lavapipe, `Basic Render Driver` for WARP), with results in
`results-lavapipe.xml` / `results-warp.xml`. The driver folder is put on `PATH` so the
ICD DLLs resolve their dependencies (Dozen's `vulkan_dzn.dll` needs `dxil.dll`). The
Vulkan SDK is installed so the suite's `find_package(Vulkan)` succeeds and
`check-hlsl-vk` is built.

This workflow is **reusable**: the RC pipeline invokes it via `workflow_call`, and you
can run it **standalone** from the Actions UI (`workflow_dispatch`) against an existing
candidate — give it the `run_id` of a prior pipeline run and the `artifact_name` (e.g.
`dxc_rc_main`). That lets you iterate on the offload/WARP setup without rebuilding DXC.

## Not yet prototyped (discussion points for the spec)

- Tagging the validated commit `vulkan-sdk-X.Y.ZZZ.w` and aligning it with a
  formal DXC GitHub/NuGet release (the Godbolt goal in INF-0007).
- Publishing to a durable, cross-org consumable location (vs. a workflow artifact).
