import glob
import os
import subprocess
import tempfile
import time
import uuid

import conda_forge_webservices
import github
from flaky import flaky

from conda_forge_webservices.utils import pushd, get_workflow_run_from_uid
from conda_forge_webservices.commands import set_rerender_pr_status
from conftest import _merge_main_to_branch

WAIT_TIME = 600  # seconds


def _rerender_is_ok(verbose=False):
    with tempfile.TemporaryDirectory() as tmpdir:
        with pushd(tmpdir):
            if verbose:
                print("cloning...", flush=True)
            subprocess.run(
                [
                    "git",
                    "clone",
                    "https://github.com/conda-forge/cf-autotick-bot-test-package-feedstock.git",
                ],
                check=True,
            )

            with pushd("cf-autotick-bot-test-package-feedstock"):
                if verbose:
                    print("checkout branch...", flush=True)
                subprocess.run(
                    ["git", "checkout", "rerender-live-test"],
                    check=True,
                )

                if verbose:
                    print("checking the git history...", flush=True)
                c = subprocess.run(
                    ["git", "log", "--pretty=oneline", "-n", "1"],
                    capture_output=True,
                    check=True,
                )
                output = c.stdout.decode("utf-8")
                if verbose:
                    print("    last commit:", output.strip(), flush=True)
                if "MNT:" not in output:
                    return False

                if verbose:
                    print("checking rerender undid workflow edits...", flush=True)
                if os.path.exists(".github/workflows/automerge.yml"):
                    with open(".github/workflows/automerge.yml") as fp:
                        lines = fp.readlines()
                    if any(
                        line.startswith("# test line for rerender edits")
                        for line in lines
                    ):
                        return False

    return True


def _run_test(branch):
    pr_number = 445
    print("sending workflow dispatch event to rerender...", flush=True)
    uid = uuid.uuid4().hex
    gh = github.Github(auth=github.Auth.Token(os.environ["GH_TOKEN"]))
    repo = gh.get_repo("conda-forge/conda-forge-webservices")
    workflow = repo.get_workflow("webservices-workflow-dispatch.yml")
    pr_sha = (
        gh.get_repo("conda-forge/cf-autotick-bot-test-package-feedstock")
        .get_pull(pr_number)
        .head.sha
    )
    running = workflow.create_dispatch(
        ref=branch,
        inputs={
            "task": "rerender",
            "repo": "cf-autotick-bot-test-package-feedstock",
            "pr_number": str(pr_number),
            "container_tag": conda_forge_webservices.__version__.replace("+", "."),
            "uuid": uid,
            "sha": pr_sha,
        },
    )
    assert running, f"Workflow dispatch failed for rerendering on PR {pr_number}!"
    run = get_workflow_run_from_uid(workflow, uid, branch)
    assert run, f"Workflow run not found for rerendering on PR {pr_number}!"
    target_url = run.html_url
    print(f"target_url for PR {pr_number}: {target_url}", flush=True)

    set_rerender_pr_status(
        gh.get_repo("conda-forge/cf-autotick-bot-test-package-feedstock"),
        pr_number,
        "pending",
        target_url=target_url,
        sha=pr_sha,
    )

    print(f"sleeping for {WAIT_TIME} seconds to let the rerender happen...", flush=True)
    tot = 0
    while tot < WAIT_TIME:
        time.sleep(10)
        tot += 10
        print(f"    slept {tot} seconds out of {WAIT_TIME}", flush=True)
        if tot % 30 == 0 and tot > 0:
            if _rerender_is_ok():
                break

    print("checking repo for the rerender...", flush=True)
    assert _rerender_is_ok(verbose=True)
    print("tests passed!")


@flaky
def test_live_rerender(pytestconfig):
    branch = pytestconfig.getoption("branch")

    print("\nmaking an edit to the head ref...", flush=True)
    with tempfile.TemporaryDirectory() as tmpdir:
        with pushd(tmpdir):
            print("cloning...", flush=True)
            subprocess.run(
                [
                    "git",
                    "clone",
                    f"https://x-access-token:{os.environ['GH_TOKEN']}@github.com/conda-forge/cf-autotick-bot-test-package-feedstock.git",
                ],
                check=True,
            )

            with pushd("cf-autotick-bot-test-package-feedstock"):
                try:
                    print("checkout branch...", flush=True)
                    subprocess.run(
                        ["git", "checkout", "rerender-live-test"],
                        check=True,
                    )

                    ci_support_files = glob.glob(".ci_support/*.yaml")
                    if len(ci_support_files) > 0:
                        print("removing files...", flush=True)
                        subprocess.run(["git", "rm", *ci_support_files], check=True)

                        print("making an edit to a workflow...", flush=True)
                        with open(".github/workflows/automerge.yml", "a") as fp:
                            fp.write("# test line for rerender edits\n")
                        subprocess.run(
                            ["git", "add", "-f", ".github/workflows/automerge.yml"],
                            check=True,
                        )

                        print("git status...", flush=True)
                        subprocess.run(["git", "status"], check=True)

                        print("committing...", flush=True)
                        subprocess.run(
                            [
                                "git",
                                "commit",
                                "-m",
                                "[ci skip] remove ci scripts to trigger rerender",
                            ],
                            check=True,
                        )

                        print("push to origin...", flush=True)
                        subprocess.run(["git", "push"], check=True)

                    _run_test(branch)

                finally:
                    print("checkout branch...", flush=True)
                    subprocess.run(
                        ["git", "checkout", "rerender-live-test"],
                        check=True,
                    )
                    subprocess.run(
                        ["git", "pull"],
                        check=True,
                    )

                    if os.path.exists(".github/workflows/automerge.yml"):
                        print("removing old workflow file...", flush=True)
                        subprocess.run(
                            ["git", "rm", "-f", ".github/workflows/automerge.yml"],
                            check=True,
                        )

                        print("committing...", flush=True)
                        subprocess.run(
                            [
                                "git",
                                "commit",
                                "--allow-empty",
                                "-m",
                                "[ci skip] remove workflow changes if any",
                            ],
                            check=True,
                        )

                        print("push to origin...", flush=True)
                        subprocess.run(["git", "push"], check=True)

                    _merge_main_to_branch("rerender-live-test", verbose=True)
