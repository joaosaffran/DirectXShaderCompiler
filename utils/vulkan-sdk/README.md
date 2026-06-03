# DXC Vulkan SDK release-candidate pipeline (prototype)

A prototype for [hlsl-specs#878](https://github.com/microsoft/hlsl-specs/issues/878)
/ INF-0007. It automates the DXC-owned slice of the LunarG Vulkan SDK release
checklist: keeping DXC's SPIR-V dependencies current, building DXC for SPIR-V,
validating its output, and publishing a release candidate for downstream
pipelines.

## The DXC slice of the SDK release

Of the full LunarG checklist, only a few steps are DXC's responsibility:

| SDK phase            | Manual step today                                              | Automated by |
|----------------------|---------------------------------------------------------------|--------------|
| Toolchain RC         | Update SPIRV-Headers + SPIRV-Tools deps in DXC                 | `sync_deps.py` |
| Toolchain RC         | Test the SPIRV-Tools RC against DXC                            | `build_and_test.py` |
| Toolchain RC         | Communicate the resulting DXC commit ID to LunarG             | `rc-manifest.json` artifact |
| Code freeze / release| Record + publish the frozen DXC commit for SDK builders       | workflow artifact |

## How it works

The pipeline runs on every push to a `release/vulkan/<version>` branch
(`.github/workflows/vulkan-sdk-rc.yml`):

1. **Sync** — `sync_deps.py` checks out `external/SPIRV-Headers` and
   `external/SPIRV-Tools` at the exact commits pinned in
   [`known_good.json`](known_good.json). Deterministic and the single source of
   truth for "which SPIR-V are we shipping."
2. **Build** — `build_and_test.py` configures DXC with `ENABLE_SPIRV_CODEGEN=ON`
   `SPIRV_BUILD_TESTS=ON` (same flags as the existing GCP build) and builds `dxc`.
   On Windows the configure also needs TAEF (DXC's test framework); the workflow
   restores the `Microsoft.Taef` nuget package (from the public feed
   microsoft/terminal uses — it's not on nuget.org) and exports `TAEF_INCLUDE_DIR`
   + `TAEF_EXECUTABLE`, which `build_and_test.py` forwards to pre-seed
   `FindTAEF.cmake`. No DXC tests are disabled.
3. **Validate** — it runs *every* SPIR-V test, across all three harnesses:
   - **gtest** `ClangSPIRVTests` — the SPIR-V backend unit suite (links
     `SPIRV-Tools`, runs `spirv-val`).
   - **lit** `tools/clang/test/CodeGenSPIRV/` — the ~1300-file FileCheck codegen
     corpus, driven through the built `dxc`.
   - **TAEF** the SPIR-V tests in `ClangHLSLTests` (e.g. `RewriterTest::RunSpirv`),
     selected by a `*Spirv*` name filter.

   Each harness runs independently and is non-fatal; per-suite pass/fail counts
   and failing test names are recorded in the manifest. The top-level `validated`
   flag is true only when every suite ran and passed. `--allow-test-failures`
   keeps failures from blocking publication.
4. **Publish** — the `dxc` binary, `rc-manifest.json` (DXC commit + pinned SPIR-V
   commits + per-suite results + `validated` flag), and the per-harness JUnit
   reports are uploaded as an artifact other pipelines (shaderc, glslang, …)
   can consume.

## Bumping the pinned versions

To advance to the latest candidate before cutting a release branch:

```sh
python utils/vulkan-sdk/sync_deps.py --bump   # fetch branch tips, rewrite known_good.json
git diff utils/vulkan-sdk/known_good.json     # review the new SHAs
python utils/vulkan-sdk/build_and_test.py     # build + validate locally
```

Then commit `known_good.json` onto a `release/vulkan/<version>` branch and let
the pipeline produce the validated RC.

## Run it locally

```sh
python utils/vulkan-sdk/sync_deps.py
python utils/vulkan-sdk/build_and_test.py --build-dir build
```

## Not yet prototyped (discussion points for the spec)

- Tagging the validated commit `vulkan-sdk-X.Y.ZZZ.w` and aligning it with a
  formal DXC GitHub/NuGet release (the Godbolt goal in INF-0007).
- Publishing to a durable, cross-org consumable location (vs. a workflow artifact).
- A scheduled "build DXC against SPIR-V tip nightly" job so regressions surface
  before an RC is ever requested.
