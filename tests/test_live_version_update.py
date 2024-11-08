import os
import subprocess
import tempfile
import time
import uuid

import github
import requests
from conftest import _merge_main_to_branch

import conda_forge_webservices
from conda_forge_webservices.utils import pushd

REPO_OWNER = "conda-forge"
REPO_NAME = "cf-autotick-bot-test-package-feedstock"
REPO = f"{REPO_OWNER}/{REPO_NAME}"
BRANCH = "version-update-live-test"
PR_NUM = 483
GH = github.Github(auth=github.Auth.Token(os.environ["GH_TOKEN"]))


def _set_pr_draft():
    repo = GH.get_repo(REPO)
    pr = repo.get_pull(PR_NUM)

    if pr.draft:
        return

    # based on this post: https://github.com/orgs/community/discussions/70061
    mutation = f"""
        mutation {{
            convertPullRequestToDraft(input:{{pullRequestId: "{pr.node_id:s}"}}) {{
                pullRequest{{id, isDraft}}
            }}
        }}
        """

    headers = {"Authorization": f"Bearer {os.environ['GH_TOKEN']}"}
    req = requests.post(
        "https://api.github.com/graphql",
        json={"query": mutation},
        headers=headers,
    )
    if "errors" in req.json():
        raise ValueError(req.json()["errors"])


def _set_pr_not_draft():
    # based on this post: https://github.com/orgs/community/discussions/70061
    repo = GH.get_repo(REPO)
    pr = repo.get_pull(PR_NUM)

    if not pr.draft:
        return

    mutation = f"""
        mutation {{
            markPullRequestReadyForReview(input:{{pullRequestId: "{pr.node_id:s}"}}) {{
                pullRequest{{id, isDraft}}
            }}
        }}
        """

    headers = {"Authorization": f"Bearer {os.environ['GH_TOKEN']}"}
    req = requests.post(
        "https://api.github.com/graphql",
        json={"query": mutation},
        headers=headers,
    )
    if "errors" in req.json():
        raise ValueError(req.json()["errors"])


def _change_version(new_version="0.13", branch="main"):
    import random

    new_sha = "".join(random.choices("0123456789abcdef", k=64))
    if new_version == "0.14":
        new_sha = "f6c45d5788f51dbe1cc55e1010f3e9ebd18b6c0f21907fc35499468a59827eef"

    print("changing the version to an old one...", flush=True)
    subprocess.run(["git", "checkout", branch], check=True)

    subprocess.run(["git", "pull"], check=True)

    new_lines = []
    with open("recipe/meta.yaml") as fp:
        for line in fp.readlines():
            if line.startswith("{% set version ="):
                new_lines.append(f'{{% set version = "{new_version}" %}}\n')
            elif line.startswith("  sha256: "):
                new_lines.append(f"  sha256: {new_sha}\n")
            else:
                new_lines.append(line)
    with open("recipe/meta.yaml", "w") as fp:
        fp.write("".join(new_lines))

    print("committing file...", flush=True)
    subprocess.run(["git", "add", "recipe/meta.yaml"], check=True)
    subprocess.run(
        [
            "git",
            "commit",
            "--allow-empty",
            "-m",
            f"[ci skip] moved version to {new_version}",
        ],
        check=True,
    )

    print("push to origin...", flush=True)
    subprocess.run(["git", "pull"], check=True)
    subprocess.run(["git", "push"], check=True)


def _pr_title(new=None):
    repo = GH.get_repo(REPO)
    pr = repo.get_pull(PR_NUM)
    old = pr.title
    if new:
        pr.edit(title=new)
    return old


def _run_test(branch, version):
    print("sending workflow dispatch event to version updater...", flush=True)
    uid = uuid.uuid4().hex
    repo = GH.get_repo("conda-forge/conda-forge-webservices")
    workflow = repo.get_workflow("webservices-workflow-dispatch.yml")
    workflow.create_dispatch(
        ref=branch,
        inputs={
            "task": "version_update",
            "repo": REPO_NAME,
            "pr_number": str(PR_NUM),
            "container_tag": conda_forge_webservices.__version__.replace("+", "."),
            "requested_version": version or "null",
            "uuid": uid,
        },
    )

    print("sleeping for four minutes to let the version update happen...", flush=True)
    tot = 0
    while tot < 240:
        time.sleep(10)
        tot += 10
        print(f"    slept {tot} seconds out of 240", flush=True)

    print("checking repo for the version update...", flush=True)
    with tempfile.TemporaryDirectory() as tmpdir:
        with pushd(tmpdir):
            print("cloning...", flush=True)
            subprocess.run(
                [
                    "git",
                    "clone",
                    f"https://github.com/{REPO}.git",
                ],
                check=True,
            )

            with pushd(REPO_NAME):
                print("checkout branch...", flush=True)
                subprocess.run(
                    ["git", "checkout", BRANCH],
                    check=True,
                )

                print("checking the git history", flush=True)
                c = subprocess.run(
                    ["git", "log", "--pretty=oneline", "-n", "1"],
                    capture_output=True,
                    check=True,
                )
                output = c.stdout.decode("utf-8")
                print("    last commit:", output.strip(), flush=True)
                assert "Re-" in output or "ENH:" in output

    if version:
        assert _pr_title() == f"ENH: update package version to {version}"
    else:
        assert "ENH: update package version to " in _pr_title()

    repo = GH.get_repo(REPO)
    pr = repo.get_pull(PR_NUM)

    assert not pr.draft

    print("tests passed!", flush=True)


def _run_test_try_finally(branch, version):
    print("making an edit to the head ref...", flush=True)
    with tempfile.TemporaryDirectory() as tmpdir:
        with pushd(tmpdir):
            print("cloning...", flush=True)
            subprocess.run(
                [
                    "git",
                    "clone",
                    f"https://x-access-token:{os.environ['GH_TOKEN']}@github.com/{REPO}.git",
                ],
                check=True,
            )

            with pushd(REPO_NAME):
                try:
                    _change_version(new_version="0.13", branch="main")
                    _merge_main_to_branch(BRANCH, verbose=True)
                    _change_version(new_version="0.13", branch=BRANCH)
                    original_title = _pr_title(new="ENH: update package version")
                    _set_pr_draft()
                    _run_test(branch, version)
                finally:
                    _change_version(new_version="0.14", branch="main")
                    _merge_main_to_branch(BRANCH, verbose=True)
                    _change_version(new_version="0.13", branch=BRANCH)
                    _pr_title(new=original_title)
                    _set_pr_not_draft()


def test_live_version_update_with_finding_version(pytestconfig):
    branch = pytestconfig.getoption("branch")
    _run_test_try_finally(branch, None)


def test_live_version_update_with_input_version(pytestconfig):
    branch = pytestconfig.getoption("branch")
    _run_test_try_finally(branch, "0.14")
