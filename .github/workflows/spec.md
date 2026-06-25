---
title: "INF-0007 - DXC Vulkan SDK Release Strategy"
params:
  authors:
    - damyanp: Damyan Pepper
    - joaosaffran: João Saffran
  sponsors:
    - damyanp: Damyan Pepper
    - joaosaffran: João Saffran
  status: Under Consideration
---

* Impacted Projects: DXC

## Introduction

DXC is included in the Vulkan SDK. Before each SDK release, the DXC submodule references
(SPIRV-Headers and SPIRV-Tools) need to be updated and the product needs to be tested.
This has previously been done mostly by hand. This document describes the strategy for
ensuring DXC is ready for inclusion in the Vulkan SDK. It is concerned with the policy
for how we manage these releases, not with the details of how the tests are run.

## Motivation

SPIRV-Headers and SPIRV-Tools need to be kept up to date so that the most recent SPIRV
features are available in DXC. We need to verify that DXC generates valid SPIRV and that
there are no regressions. The process needs to be documented and automated enough that it
does not rely on individuals with special knowledge. We also want to align the version
included in the Vulkan SDK with a formal DXC release, so that it matches the GitHub and
NuGet releases and can be ingested into Godbolt.

## Proposed solution

This proposal covers the release policy at a high level: how the SPIRV dependencies are
kept current, which dependency versions a release is built against, and when a build is
considered ready for the Vulkan SDK. It does not cover the test pipelines, the branch and
tag names, or the formats of any files those pipelines produce. Those are implementation
details that can change without changing the policy, and are decided separately.

### Continuous integration and release builds

DXC is built against different SPIRV dependency versions depending on the case, and the
two cases are kept separate.

* Continuous integration builds against the latest SPIRV-Headers and SPIRV-Tools, so that
  regressions against upstream are found early. This is independent of any release.
* A release is built against the specific SPIRV-Headers and SPIRV-Tools commits that
  LunarG specifies for that SDK. We do not choose these commits; we validate DXC against
  them.

### Validation

A build is included in the Vulkan SDK only after it passes DXC's validation against the
required SPIRV dependency commits. What that validation must cover, and whether it is the
same validation used for the NuGet and Vpack releases, is one of the open questions below.
This document does not specify the test suites, runners, or reports.

## Open questions

The following need to be agreed before the policy is settled.

1. Do we have a single validation process that covers the Vulkan SDK, NuGet, and Vpack
   releases? To answer this we need a single written list of the validation currently done
   for the NuGet and Vpack releases, whose pipelines are migrating to DXCBuild. We can then
   decide whether Vulkan SDK validation is the same process or an extension of it. "How do
   we validate a DXC release?" may be a separate proposal.

2. Are we aligning all DXC releases? The motivation assumes the Vulkan SDK version is
   aligned with a formal DXC release. If so, we need to work through what that means for
   servicing DXC Vpacks and DXC preview releases. This requires a summary of how DXC
   releases work today.

3. Do we want to automate submodule updates, including the DirectX headers? Keeping the
   SPIRV submodules current in CI is useful, but automating updates more broadly is a
   larger decision that may be a separate proposal.
