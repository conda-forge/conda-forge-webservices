import os
import subprocess
import tempfile
import time
import uuid

import github
import requests
from flaky import flaky

import conda_forge_webservices
from conda_forge_webservices.utils import pushd
from conda_forge_webservices.commands import (
    get_workflow_run_from_uid,
    set_convert_v1_pr_status,
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


def _change_to_schema(schema_version, branch):

    if schema_version == 0:
        filename = "recipe/meta.yaml"
        filename_to_remove = "recipe/recipe.yaml"
        cfy = "conda-forge.yml"
    else:
        filename = "recipe/recipe.yaml"
        filename_to_remove = "recipe/meta.yaml"
        cfy = "conda-forge-for-recipe.yml"

    subprocess.run(
        ["git", "checkout", branch],
        check=True,
    )
    subprocess.run(["git", "pull"], check=True)

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

    with open(os.path.join(os.path.dirname(__file__), os.path.basename(cfy))) as fp:
        new_cfy = fp.read()

    with open("conda-forge.yml", "w") as fp:
        fp.write(new_cfy)

    subprocess.run(
        ["git", "add", "conda-forge.yml"],
        check=True,
    )

    print("rerendering...", flush=True)
    subprocess.run(
        [
            "conda-smithy",
            "rerender",
            "-c",
            "auto",
            "--no-check-uptodate",
        ],
        check=True,
    )
    subprocess.run(
        ["git", "add", "."],
        check=True,
    )

    print("making a commit...", flush=True)
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


def _pr_title(new=None):
    repo = GH.get_repo(REPO)
    pr = repo.get_pull(PR_NUM)
    old = pr.title
    if new:
        pr.edit(title=new)
    return old


def _conversion_is_ok(verbose=False):

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

                if not os.path.exists(filename):
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

    return True


def _run_test(branch):
    print("sending workflow dispatch event to recipe converter...", flush=True)
    pr_head_sha = GH.get_repo(REPO).get_pull(PR_NUM).head.sha
    uid = uuid.uuid4().hex
    repo = GH.get_repo("conda-forge/conda-forge-webservices")
    workflow = repo.get_workflow("webservices-workflow-dispatch.yml")
    running = workflow.create_dispatch(
        ref=branch,
        inputs={
            "task": "convert_v1",
            "repo": REPO_NAME,
            "pr_number": str(PR_NUM),
            "container_tag": conda_forge_webservices.__version__.replace("+", "."),
            "requested_version": "null",
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

        set_convert_v1_pr_status(
            GH.get_repo(REPO), PR_NUM, "pending", target_url=target_url, sha=pr_head_sha
        )

    print(
        f"sleeping for {WAIT_TIME} seconds to let the conversion happen...",
        flush=True,
    )
    tot = 0
    while tot < WAIT_TIME:
        time.sleep(10)
        tot += 10
        print(f"    slept {tot} seconds out of {WAIT_TIME}", flush=True)
        if tot % 30 == 0 and tot > 0:
            if _conversion_is_ok():
                break

    print("checking repo for the conversion...", flush=True)
    update_is_ok = _conversion_is_ok(verbose=True)
    if not update_is_ok:
        set_convert_v1_pr_status(
            GH.get_repo(REPO),
            PR_NUM,
            "failure",
            target_url=target_url,
        )
    assert update_is_ok
    print("tests passed!", flush=True)


def _run_test_try_finally(branch):
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
                    _change_to_schema(0, "main")
                    _change_to_schema(0, BRANCH)
                    original_title = _pr_title(new="chore: update package version")
                    _set_pr_draft()
                    _run_test(branch)
                finally:
                    _change_to_schema(0, "main")
                    _change_to_schema(0, BRANCH)
                    _pr_title(new=original_title)
                    _set_pr_not_draft()


@flaky
def test_live_convert_recipe_to_vi(pytestconfig, skip_if_no_tokens):
    global GH
    GH = github.Github(auth=github.Auth.Token(os.environ["GH_TOKEN"]))
    branch = pytestconfig.getoption("branch")
    _run_test_try_finally(branch)
