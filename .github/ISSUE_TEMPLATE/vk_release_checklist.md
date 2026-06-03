---
name: Vulkan SDK Release Checklist
about: Track the DXC-side steps to prepare and hand off a Vulkan SDK release candidate
title: 'Vulkan SDK release checklist: <version>'
labels: ['spirv']
assignees: ''

---

Tracking issue for preparing a DXC release candidate for the LunarG Vulkan SDK. 

**Target version:** `<version>` — the SDK major and minor version, e.g. `1.4.360`

## Dependencies

- [ ] Get the SPIRV-Headers and SPIRV-Tools commits LunarG is releasing against.
- [ ] Update the `external/SPIRV-Headers` and `external/SPIRV-Tools` submodules to
      those commits.

## Build the candidate

- [ ] Push a `release/vulkan/<version>` branch (e.g. `release/vulkan/1.4.360`) with the
      updated submodules. This triggers the release-candidate pipeline (build + SPIRV
      tests + offload tests).

## Validate

The candidate is ready only when all of the following hold:

- [ ] It builds against the pinned SPIRV-Headers / SPIRV-Tools commits.
- [ ] Every SPIRV test harness passes — googletest, lit, and TAEF — and the emitted
      SPIRV validates under `spirv-val`.
- [ ] The offload tests pass on both software renderers: SPIRV on lavapipe (Vulkan)
      and DXIL on WARP (Direct3D 12).
- [ ] The manifest (`rc-manifest.json`) records the per-suite results and the
      lavapipe / WARP driver versions.

## Release the candidate

Each handoff to LunarG gets its own release-candidate tag; repeat as needed.

- [ ] Tag the validated commit with the next release-candidate tag, e.g.
      `vulkan-sdk-1.4.360.0rc1` (increment the `rc` number on each handoff: `rc2`, ...).
      The `vulkan-sdk-` prefix marks it as a Vulkan SDK version, distinct from DXC's
      own release tags.
- [ ] Report that tag to LunarG — by email, or by updating the Khronos GitLab release
      issue with most recent release-candidate tag.
- [ ] If LunarG reports a problem, fix it on the release branch, re-validate, and cut
      the next release-candidate tag.
- [ ] Once the Vulkan SDK is published, promote the latest release candidate to a
      release tag with no `rc` suffix, e.g. `vulkan-sdk-1.4.360.0`.
