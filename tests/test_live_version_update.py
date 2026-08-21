import os
import subprocess
import tempfile
import time
import uuid

import github
import requests

# from flaky import flaky
import pytest

import conda_forge_webservices
from conda_forge_webservices.utils import pushd
from conftest import _merge_main_to_branch
from conda_forge_webservices.commands import (
    get_workflow_run_from_uid,
    set_version_update_pr_status,
)
from conda_forge_webservices import __version__

REPO_OWNER = "conda-forge"
REPO_NAME = "cf-autotick-bot-test-package-feedstock"
REPO = f"{REPO_OWNER}/{REPO_NAME}"
BRANCH = "version-update-live-test"
PR_NUM = 1844
GH = None
WAIT_TIME = 300  # seconds


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


def _change_version(schema_version, new_version="0.13", branch="main", build_number=0):
    import random

    random.seed(new_version)
    new_sha = "".join(random.choices("0123456789abcdef", k=64))
    if new_version == "0.14":
        new_sha = "f6c45d5788f51dbe1cc55e1010f3e9ebd18b6c0f21907fc35499468a59827eef"

    print("changing the version to an old one...", flush=True)
    subprocess.run(["git", "checkout", branch], check=True)

    subprocess.run(["git", "pull"], check=True)

    if schema_version == 0:
        filename = "recipe/meta.yaml"
    else:
        filename = "recipe/recipe.yaml"

    new_lines = []
    with open(filename) as fp:
        for line in fp.readlines():
            if line.startswith("{% set version ="):
                new_lines.append(f'{{% set version = "{new_version}" %}}\n')
            elif line.startswith("  sha256: "):
                new_lines.append(f"  sha256: {new_sha}\n")
            elif line.startswith("  number:"):
                new_lines.append(f"  number: {build_number}\n")
            elif line.startswith("  version: "):
                new_lines.append(f'  version: "{new_version}"')
            else:
                new_lines.append(line)
    with open(filename, "w") as fp:
        fp.write("".join(new_lines))

    print("committing file...", flush=True)
    subprocess.run(["git", "add", filename], check=True)
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


def _version_update_is_ok(version, schema_version, verbose=False):

    if schema_version == 0:
        filename = "recipe/meta.yaml"
    else:
        filename = "recipe/recipe.yaml"

    with tempfile.TemporaryDirectory() as tmpdir:
        with pushd(tmpdir):
            if verbose:
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
                if verbose:
                    print("checkout branch...", flush=True)
                subprocess.run(
                    ["git", "checkout", BRANCH],
                    check=True,
                )

                with open(filename) as fp:
                    test_line = None
                    for line in fp.readlines():
                        if line.startswith("  number:"):
                            test_line = line
                            break
                if test_line is None:
                    return False

                if test_line.strip() != "number: 0":
                    return False

                if verbose:
                    print("checking the git history", flush=True)
                c = subprocess.run(
                    ["git", "log", "--pretty=oneline", "-n", "1"],
                    capture_output=True,
                    check=True,
                )
                output = c.stdout.decode("utf-8")
                if verbose:
                    print("    last commit:", output.strip(), flush=True)
                if not ("Re-" in output or "chore:" in output):
                    return False

    if version:
        if _pr_title() != f"chore: update package version to {version}":
            return False
    else:
        if "chore: update package version to " not in _pr_title():
            return False

    repo = GH.get_repo(REPO)
    pr = repo.get_pull(PR_NUM)

    if pr.draft:
        return False

    return True


def _run_test(branch, version, schema_version):
    print("sending workflow dispatch event to version updater...", flush=True)
    pr_head_sha = GH.get_repo(REPO).get_pull(PR_NUM).head.sha
    uid = uuid.uuid4().hex
    repo = GH.get_repo("conda-forge/conda-forge-webservices")
    workflow = repo.get_workflow("webservices-workflow-dispatch.yml")
    running = workflow.create_dispatch(
        ref=branch,
        inputs={
            "task": "version_update",
            "repo": REPO_NAME,
            "pr_number": str(PR_NUM),
            "container_tag": conda_forge_webservices.__version__.replace("+", "."),
            "requested_version": version or "null",
            "uuid": uid,
            "sha": pr_head_sha,
        },
    )

    if running:
        run = get_workflow_run_from_uid(workflow, uid, __version__.replace("+", "."))
        if run:
            target_url = run.html_url
        else:
            target_url = None

        set_version_update_pr_status(
            GH.get_repo(REPO), PR_NUM, "pending", target_url=target_url, sha=pr_head_sha
        )

    print(
        f"sleeping for {WAIT_TIME} seconds to let the version update happen...",
        flush=True,
    )
    tot = 0
    while tot < WAIT_TIME:
        time.sleep(10)
        tot += 10
        print(f"    slept {tot} seconds out of {WAIT_TIME}", flush=True)
        if tot % 30 == 0 and tot > 0:
            if _version_update_is_ok(version, schema_version):
                break

    print("checking repo for the version update...", flush=True)
    update_is_ok = _version_update_is_ok(version, schema_version, verbose=True)
    if not update_is_ok:
        set_version_update_pr_status(
            GH.get_repo(REPO),
            PR_NUM,
            "failed",
            target_url=target_url,
        )
    assert update_is_ok
    print("tests passed!", flush=True)


def _change_to_schema(schema_version, branch):

    if schema_version == 0:
        filename = "recipe/meta.yaml"
        filename_to_remove = "recipe/recipe.yaml"
        cfy = "conda-forge.yml"
    else:
        filename = "recipe/recipe.yaml"
        filename_to_remove = "recipe/meta.yaml"
        cfy = "conda-forge-for-recipe.yml"

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
                subprocess.run(
                    ["git", "checkout", branch],
                    check=True,
                )

                if os.path.exists(filename_to_remove):
                    subprocess.run(
                        ["git", "rm", filename_to_remove],
                        check=True,
                    )
                with open(
                    os.path.join(os.path.dirname(__file__), os.path.basename(filename))
                ) as fp:
                    new_recipe = fp.read()

                with open(filename, "w") as fp:
                    fp.write(new_recipe)

                subprocess.run(
                    ["git", "add", filename],
                    check=True,
                )

                with open(
                    os.path.join(os.path.dirname(__file__), os.path.basename(cfy))
                ) as fp:
                    new_cfy = fp.read()

                with open("conda-forge.yml", "w") as fp:
                    fp.write(new_cfy)

                subprocess.run(
                    ["git", "add", "conda-forge.yml"],
                    check=True,
                )

                subprocess.run(
                    [
                        "git",
                        "commit",
                        "--allow-empty",
                        "-m",
                        f"[ci skip] moved schema to {schema_version}",
                    ],
                    check=True,
                )

                print("push to origin...", flush=True)
                subprocess.run(["git", "pull"], check=True)
                subprocess.run(["git", "push"], check=True)


def _run_test_try_finally(branch, version, schema_version):
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
                    _change_to_schema(schema_version, "main")
                    _change_version(
                        schema_version,
                        new_version="0.13",
                        branch="main",
                        build_number=4312,
                    )
                    _change_to_schema(schema_version, BRANCH)
                    _merge_main_to_branch(BRANCH, verbose=True)
                    original_title = _pr_title(new="chore: update package version")
                    _set_pr_draft()
                    _run_test(branch, version, schema_version)
                finally:
                    _change_to_schema(0, "main")
                    _change_version(
                        schema_version,
                        new_version="0.14",
                        branch="main",
                        build_number=0,
                    )
                    _change_to_schema(0, BRANCH)
                    _merge_main_to_branch(BRANCH, verbose=True)
                    _pr_title(new=original_title)
                    _set_pr_not_draft()


# @flaky
@pytest.mark.parametrize("schema_version", [0, 1])
def test_live_version_update_with_finding_version(
    pytestconfig, skip_if_no_tokens, schema_version
):
    global GH
    GH = github.Github(auth=github.Auth.Token(os.environ["GH_TOKEN"]))
    branch = pytestconfig.getoption("branch")
    _run_test_try_finally(branch, None, schema_version)


# @flaky
@pytest.mark.parametrize("schema_version", [0, 1])
def test_live_version_update_with_input_version(
    pytestconfig, skip_if_no_tokens, schema_version
):
    global GH
    GH = github.Github(auth=github.Auth.Token(os.environ["GH_TOKEN"]))
    branch = pytestconfig.getoption("branch")
    _run_test_try_finally(branch, "0.14", schema_version)
