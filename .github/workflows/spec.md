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

DXC is included in the Vulkan SDK. Before each SDK release, the DXC submodule
references (SPIRV-Headers, SPIRV-Tools) need to be updated and the product needs to be
tested. This process has previously been mostly performed manually. This document sets
out the high-level strategy for ensuring DXC is ready for inclusion in the Vulkan SDK.
It separates the release *policy* from the *machinery* that runs the validation, and
surfaces the questions we need to align on before filling in the details.

## Motivation

SPIRV-Headers and SPIRV-Tools need to be kept up to date so that the most recent SPIRV
features are available in DXC. We need to verify that DXC is generating valid SPIRV
code and that there are no regressions. The process needs to be documented and
automated enough so that it does not rely on individuals with special knowledge.
Additionally, we want to align the version included in the Vulkan SDK with a formal DXC
release so that it matches up with GitHub and NuGet releases and can be ingested into
Godbolt.

## Proposed solution

Getting DXC into the Vulkan SDK touches two separable concerns, and this proposal keeps
them apart:

* **Policy** — how we manage branches and releases, what a release is called, and which
  SPIRV submodule revisions we build against and when.
* **Machinery** — how we build DXC and run the tests that decide whether a build is good
  enough to ship.

The goal of this proposal is to align on the policy and on the high-level shape of the
validation. The machinery is an implementation detail that can change without
re-opening the strategy, so it is deliberately left out here.

### Staying current vs. blessing a release

Which SPIRV revisions DXC builds against depends on what we are doing, and the two cases
must be treated separately:

* **Continuous integration** tracks the *latest* SPIRV-Headers and SPIRV-Tools so
  regressions against upstream are caught early, independent of any release. This is a
  currency check, not a release.
* **Blessing a release** builds against the *specific* SPIRV-Headers and SPIRV-Tools
  commits LunarG gives us for that SDK. We do not choose these revisions; we validate
  DXC against them.

### Validation

To be included in the Vulkan SDK, a DXC build must:

* build against the required SPIRV-Headers and SPIRV-Tools commits;
* pass DXC's SPIRV tests — no regressions, and valid SPIRV output; and
* correctly execute the shaders it compiles.

Validation runs in CI and produces a result we can point at when blessing a release.
The concrete test suites, how they are run, and how results are reported are machinery
and are not specified here.

## Scope

This proposal is limited to the high-level validation strategy and the policy for how
Vulkan SDK releases relate to it. It deliberately does **not** cover:

* exact branch names, tag formats, or validation-report formats;
* the full set of validation required for the NuGet and Vpack releases (see below); or
* automating submodule updates.

Several of these are likely better handled as their own proposals once we have aligned
on the strategy here.

## Open questions

We should agree on the following before settling any details:

1. **Do we have a single validation process that covers everything we need for the
   Vulkan SDK, NuGet, and Vpack releases?** To answer this we first need a single,
   written list of the validation we do today for the NuGet and Vpack releases, and then
   to decide whether Vulkan SDK validation is the same process or an extension of it.
   "How do we validate a DXC release?" may deserve its own proposal.

2. **Are we aligning all DXC releases?** The motivation assumes we align the Vulkan SDK
   version with a formal DXC release. If so, we need to work through what that implies for
   servicing DXC Vpacks and DXC preview releases — which in turn needs a summary of how
   DXC releases work today.

3. **Do we want to automate submodule updates, including the DirectX headers?** Keeping
   the SPIRV submodules current in CI is useful, but automating updates more broadly is a
   larger decision that could be a separate proposal.
