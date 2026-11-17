"""Moves DXC's external submodules to their latest upstream commit with green CI
and opens a pull request, or refreshes the one already open."""

import argparse
import logging
import os
from dataclasses import dataclass

from github_api import GitHub

SUBMODULES = [
    "external/DirectX-Headers",
    "external/SPIRV-Headers",
    "external/SPIRV-Tools",
]
UPSTREAM_BRANCH = "main"
UPDATE_BRANCH = "automation/update-submodules"
PASSING_CHECK_CONCLUSIONS = {"success", "neutral", "skipped"}


@dataclass
class SubmoduleUpdate:
    path: str
    new_sha: str
    compare_url: str
    commit_count: int
    subjects: list[str]

    @property
    def name(self):
        return self.path.split("/")[-1]

    def describe(self):
        header = f"* [{self.name}]({self.compare_url}) ({self.commit_count} commits):"
        return "\n".join([header] + [f"  > {subject}" for subject in self.subjects])


def upstream_repo(url):
    return url.removeprefix("https://github.com/").removesuffix(".git")


def is_green(github, repo, sha):
    check_runs = github.get(f"repos/{repo}/commits/{sha}/check-runs", per_page=100)
    statuses = github.get(f"repos/{repo}/commits/{sha}/status")
    checks_passed = all(
        check["conclusion"] in PASSING_CHECK_CONCLUSIONS
        for check in check_runs["check_runs"]
    )
    statuses_passed = all(
        status["state"] == "success" for status in statuses["statuses"]
    )
    has_ci = check_runs["total_count"] + statuses["total_count"] > 0
    return has_ci and checks_passed and statuses_passed


def find_update(github, repo, base_branch, path):
    submodule = github.get(f"repos/{repo}/contents/{path}", ref=base_branch)
    upstream = upstream_repo(submodule["submodule_git_url"])
    old_sha = submodule["sha"]
    new_sha = github.get(f"repos/{upstream}/commits/{UPSTREAM_BRANCH}")["sha"]

    if old_sha == new_sha:
        logging.info("%s is up to date at %s.", path, old_sha)
        return None
    if not is_green(github, upstream, new_sha):
        logging.info("Skipping %s: CI for %s@%s is not green.", path, upstream, new_sha)
        return None

    comparison = github.get(f"repos/{upstream}/compare/{old_sha}...{new_sha}")
    return SubmoduleUpdate(
        path=path,
        new_sha=new_sha,
        compare_url=comparison["html_url"],
        commit_count=comparison["total_commits"],
        subjects=[
            c["commit"]["message"].splitlines()[0] for c in comparison["commits"]
        ],
    )


def create_update_tree(github, repo, base_sha, updates):
    base_tree = github.get(f"repos/{repo}/git/commits/{base_sha}")["tree"]["sha"]
    entries = [
        {"path": update.path, "mode": "160000", "type": "commit", "sha": update.new_sha}
        for update in updates
    ]
    return github.post(
        f"repos/{repo}/git/trees", {"base_tree": base_tree, "tree": entries}
    )["sha"]


def branch_tree(github, repo, branch):
    refs = github.get(f"repos/{repo}/git/matching-refs/heads/{branch}")
    head = next((ref for ref in refs if ref["ref"] == f"refs/heads/{branch}"), None)
    if head is None:
        return None
    return github.get(f"repos/{repo}/git/commits/{head['object']['sha']}")["tree"][
        "sha"
    ]


def push_update_branch(github, repo, base_sha, tree, message):
    existing_tree = branch_tree(github, repo, UPDATE_BRANCH)
    if existing_tree == tree:
        logging.info("%s already has these updates.", UPDATE_BRANCH)
        return

    commit = github.post(
        f"repos/{repo}/git/commits",
        {"message": message, "tree": tree, "parents": [base_sha]},
    )
    if existing_tree is None:
        github.post(
            f"repos/{repo}/git/refs",
            {"ref": f"refs/heads/{UPDATE_BRANCH}", "sha": commit["sha"]},
        )
    else:
        github.patch(
            f"repos/{repo}/git/refs/heads/{UPDATE_BRANCH}",
            {"sha": commit["sha"], "force": True},
        )


def open_or_refresh_pull_request(github, repo, base_branch, title, body):
    owner = repo.split("/")[0]
    open_prs = github.get(
        f"repos/{repo}/pulls", state="open", head=f"{owner}:{UPDATE_BRANCH}"
    )
    if open_prs:
        pr = github.patch(
            f"repos/{repo}/pulls/{open_prs[0]['number']}",
            {"title": title, "body": body},
        )
    else:
        pr = github.post(
            f"repos/{repo}/pulls",
            {"title": title, "body": body, "head": UPDATE_BRANCH, "base": base_branch},
        )
    return pr["html_url"]


def update_submodules(github, repo, base_branch="main"):
    base_sha = github.get(f"repos/{repo}/git/ref/heads/{base_branch}")["object"]["sha"]
    updates = [
        update
        for path in SUBMODULES
        if (update := find_update(github, repo, base_branch, path))
    ]
    if not updates:
        logging.info("No submodule updates to propose.")
        return None

    title = "Update submodules: " + ", ".join(
        f"{update.name} to {update.new_sha[:8]}" for update in updates
    )
    body = "\n".join(update.describe() for update in updates)

    tree = create_update_tree(github, repo, base_sha, updates)
    push_update_branch(github, repo, base_sha, tree, f"{title}\n\n{body}")
    pr_url = open_or_refresh_pull_request(github, repo, base_branch, title, body)
    logging.info("Pull request: %s", pr_url)
    return pr_url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--base-branch", default="main")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    github = GitHub(os.environ["GITHUB_TOKEN"])
    update_submodules(github, args.repo, args.base_branch)


if __name__ == "__main__":
    main()
