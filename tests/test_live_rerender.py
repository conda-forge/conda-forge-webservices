import glob
import os
import subprocess
import tempfile
import time

import conda_forge_webservices
import github
from flaky import flaky

import pytest

from conda_forge_webservices.utils import pushd
from conda_forge_webservices.commands import set_rerender_pr_status

WAIT_TIME = 600  # seconds


def _rerender_is_ok(target_pr_branch, verbose=False):
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
                    ["git", "checkout", target_pr_branch],
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
                if ("chore:" not in output) and ("MNT:" not in output):
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


def _run_test(branch, target_pr_branch, pr_number):
    print("sending workflow dispatch event to rerender...", flush=True)
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
            "sha": pr_sha,
        },
        return_run_details=True,
    )
    assert running, f"Workflow dispatch failed for rerendering on PR {pr_number}!"
    target_url = running.html_url
    print(f"target_url for PR {pr_number} at sha {pr_sha}: {target_url}", flush=True)

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
            if _rerender_is_ok(target_pr_branch):
                break

    print("checking repo for the rerender...", flush=True)
    assert _rerender_is_ok(target_pr_branch, verbose=True)
    print("tests passed!")


def _convert_to_schema(schema_version):
    if schema_version == 0:
        recipe_file = os.path.join(os.path.dirname(__file__), "meta.yaml")
        cf_yaml = os.path.join(os.path.dirname(__file__), "conda-forge.yml")
    else:
        recipe_file = os.path.join(os.path.dirname(__file__), "recipe.yaml")
        cf_yaml = os.path.join(os.path.dirname(__file__), "conda-forge-for-recipe.yml")

    # copy in proper recipe
    subprocess.run(
        ["git", "rm", "--ignore-unmatch", "-f", "recipe/meta.yaml"],
        check=True,
    )
    subprocess.run(
        ["git", "rm", "--ignore-unmatch", "-f", "recipe/recipe.yaml"],
        check=True,
    )
    subprocess.run(
        ["cp", recipe_file, f"recipe/{os.path.basename(recipe_file)}"],
        check=True,
    )
    subprocess.run(
        ["cp", cf_yaml, "conda-forge.yml"],
        check=True,
    )
    subprocess.run(
        ["git", "add", "-f", "conda-forge.yml"],
        check=True,
    )
    subprocess.run(
        ["git", "add", "-f", f"recipe/{os.path.basename(recipe_file)}"],
        check=True,
    )


@flaky
@pytest.mark.parametrize("schema_version", [1, 0])
def test_live_rerender(pytestconfig, skip_if_no_tokens, schema_version):
    branch = pytestconfig.getoption("branch")

    if schema_version == 0:
        target_pr_branch = "rerender-live-test"
        pr_number = 445
    else:
        target_pr_branch = "rerender-live-test-recipe-yaml"
        pr_number = 2323

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
                        ["git", "checkout", target_pr_branch],
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

                        # copy in proper recipe
                        _convert_to_schema(schema_version)

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

                    _run_test(branch, target_pr_branch, pr_number)

                finally:
                    print("checkout branch...", flush=True)
                    subprocess.run(
                        ["git", "checkout", target_pr_branch],
                        check=True,
                    )

                    print("reset to main...", flush=True)
                    subprocess.run(
                        ["git", "reset", "--hard", "main"],
                        check=True,
                    )

                    print("committing...", flush=True)
                    subprocess.run(
                        [
                            "git",
                            "commit",
                            "--allow-empty",
                            "-m",
                            "[ci skip] reset branch",
                        ],
                        check=True,
                    )

                    print("push to origin...", flush=True)
                    subprocess.run(
                        ["git", "push", "--force", "origin", target_pr_branch],
                        check=True,
                    )
