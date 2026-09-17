"""Live test of the pull request plumbing shared by the admin commands.

The unit tests mock Repo and get_gh_client, so nothing else covers the fork,
clone, branch, push and pull request creation against a real feedstock.
"""

import os
import uuid

import github
from flaky import flaky
from git import Actor

from conda_forge_webservices.commands import (
    admin_feedstock_branch,
    open_admin_pr,
)

REPO_OWNER = "conda-forge"
REPO_NAME = "cf-autotick-bot-test-package-feedstock"
REPO = f"{REPO_OWNER}/{REPO_NAME}"

# the feedstock builds on every push, so keep this off its CI
SKIP_CI = "[ci skip] [skip ci] [cf admin skip] ***NO_CI***"


def _dummy_commit(git_repo):
    readme = os.path.join(git_repo.working_dir, "README.md")
    with open(readme, "a") as fp:
        fp.write("\n<!-- live test of the admin pull request plumbing -->\n")
    git_repo.index.add([readme])
    git_repo.index.commit(
        f"{SKIP_CI} live test of the admin pull request plumbing",
        author=Actor(
            "conda-forge-webservices[bot]",
            "121827174+conda-forge-webservices[bot]@users.noreply.github.com",
        ),
    )


@flaky
def test_live_admin_pr(skip_if_no_tokens):
    gh = github.Github(auth=github.Auth.Token(os.environ["GH_TOKEN"]))
    repo = gh.get_repo(REPO)
    default_branch = repo.default_branch
    branch = f"conda_forge_admin_live_test_{uuid.uuid4().hex[:8]}"

    pr = None
    forked_user = None
    work_dir = None
    try:
        with admin_feedstock_branch(
            gh, REPO_OWNER, REPO_NAME, default_branch, branch
        ) as (git_repo, forked_user):
            work_dir = git_repo.working_dir

            assert git_repo.active_branch.name == branch
            assert "upstream" in [remote.name for remote in git_repo.remotes]

            _dummy_commit(git_repo)

            # the clone is removed when the context manager exits, so the push
            # this does has to happen inside it
            pr = open_admin_pr(
                repo,
                git_repo,
                forked_user,
                branch,
                base=default_branch,
                title=f"{SKIP_CI} live test of the admin pull request plumbing",
                body=(
                    "Opened by the conda-forge-webservices live tests. Safe to close."
                ),
                draft=True,
            )

        assert not os.path.exists(work_dir)

        assert pr.state == "open"
        assert pr.draft
        assert pr.base.ref == default_branch
        assert pr.head.label == f"{forked_user}:{branch}"
    finally:
        if pr is not None:
            pr.edit(state="closed")
        if forked_user is not None:
            try:
                fork = gh.get_repo(f"{forked_user}/{REPO_NAME}")
                fork.get_git_ref(f"heads/{branch}").delete()
            except github.GithubException:
                pass
