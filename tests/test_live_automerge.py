import os
import subprocess
import tempfile
import time
import uuid

import github
from conda_forge_webservices.utils import pushd
from flaky import flaky

TEST_BASE_BRANCH = "automerge-live-test-base-branch"
TEST_HEAD_BRANCH = f"automerge-live-test-head-branch-h{uuid.uuid4().hex[:6]}"
DEBUG = False

WAIT_TIME = 720  # seconds


def _run_git_cmd(*args):
    subprocess.run(["git", *list(args)], check=True)


@flaky
def test_live_automerge(pytestconfig, skip_if_no_tokens):
    branch = pytestconfig.getoption("branch")

    print("making an edit to the head ref...", flush=True)
    with tempfile.TemporaryDirectory() as tmpdir:
        with pushd(tmpdir):
            print("cloning...", flush=True)
            _run_git_cmd(
                "clone",
                f"https://x-access-token:{os.environ['GH_TOKEN']}@github.com/conda-forge/"
                "cf-autotick-bot-test-package-feedstock.git",
            )

            with pushd("cf-autotick-bot-test-package-feedstock"):
                pr = None
                try:
                    print("checkout branch...", flush=True)
                    _run_git_cmd("checkout", TEST_BASE_BRANCH)
                    _run_git_cmd("checkout", "-b", TEST_HEAD_BRANCH)

                    print("adding a correct recipe and conda-forge.yml...", flush=True)
                    test_dir = os.path.dirname(__file__)
                    subprocess.run(
                        ["cp", f"{test_dir}/conda-forge.yml", "."],
                        check=True,
                    )
                    subprocess.run(
                        ["cp", f"{test_dir}/meta.yaml", "recipe/meta.yaml"],
                        check=True,
                    )

                    print("rerendering...", flush=True)
                    subprocess.run(
                        [
                            "conda",
                            "smithy",
                            "rerender",
                            "-c",
                            "auto",
                            "--no-check-uptodate",
                        ],
                        check=True,
                    )

                    print("making a commit...", flush=True)
                    _run_git_cmd("add", ".")
                    _run_git_cmd(
                        "commit", "--allow-empty", "-m", "test commit for automerge"
                    )

                    print("push to branch...", flush=True)
                    _run_git_cmd("push", "-u", "origin", TEST_HEAD_BRANCH)

                    print("making a PR...", flush=True)
                    gh = github.Github(auth=github.Auth.Token(os.environ["GH_TOKEN"]))
                    repo = gh.get_repo(
                        "conda-forge/cf-autotick-bot-test-package-feedstock"
                    )

                    pr = repo.create_pull(
                        TEST_BASE_BRANCH,
                        TEST_HEAD_BRANCH,
                        title="[DO NOT TOUCH] test pr for automerge",
                        body=(
                            "This is a test PR for automerge from "
                            f"GHA run {os.environ['GHA_URL']}. "
                            "Please do not make any changes!"
                        ),
                        maintainer_can_modify=True,
                        draft=False,
                    )
                    pr.add_to_labels("automerge")

                    print("waiting for the PR to be merged...", flush=True)
                    tot = 0
                    merged = False
                    while tot < WAIT_TIME:
                        time.sleep(10)
                        tot += 10
                        print(f"    slept {tot} seconds out of {WAIT_TIME}", flush=True)
                        if tot % 30 == 0:
                            if pr.is_merged():
                                print("PR was merged!", flush=True)
                                merged = True
                                break
                            elif tot > 0:
                                uid = uuid.uuid4().hex
                                cfws_repo = gh.get_repo(
                                    "conda-forge/conda-forge-webservices"
                                )
                                workflow = cfws_repo.get_workflow("automerge.yml")
                                workflow.create_dispatch(
                                    ref=branch,
                                    inputs={
                                        "repo": (
                                            "cf-autotick-bot-test-package-feedstock"
                                        ),
                                        "sha": pr.head.sha,
                                        "uuid": uid,
                                    },
                                )

                    if not merged:
                        raise RuntimeError(f"PR {pr.number} was not merged!")

                finally:
                    if not DEBUG:
                        print("closing PR if it is open...", flush=True)
                        if pr is not None and not pr.is_merged():
                            pr.edit(state="closed")

                        print("deleting the test branch...", flush=True)
                        _run_git_cmd("checkout", TEST_BASE_BRANCH)
                        _run_git_cmd("branch", "-d", TEST_HEAD_BRANCH)
                        _run_git_cmd("push", "-d", "origin", TEST_HEAD_BRANCH)
