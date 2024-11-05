import glob
import os
import subprocess
import tempfile
import time
import uuid

import conda_forge_webservices
import github
from conda_forge_webservices.utils import pushd

from conftest import _merge_main_to_branch


def _run_test(branch):
    print("sending workflow dispatch event to rerender...", flush=True)
    uid = uuid.uuid4().hex
    gh = github.Github(auth=github.Auth.Token(os.environ["GH_TOKEN"]))
    repo = gh.get_repo("conda-forge/conda-forge-webservices")
    workflow = repo.get_workflow("webservices-workflow-dispatch.yml")
    workflow.create_dispatch(
        ref=branch,
        inputs={
            "task": "rerender",
            "repo": "cf-autotick-bot-test-package-feedstock",
            "pr_number": "445",
            "container_tag": conda_forge_webservices.__version__.replace("+", "."),
            "uuid": uid,
        },
    )

    print("sleeping for four minutes to let the rerender happen...", flush=True)
    tot = 0
    while tot < 240:
        time.sleep(10)
        tot += 10
        print(f"    slept {tot} seconds out of 240", flush=True)

    print("checking repo for the rerender...", flush=True)
    with tempfile.TemporaryDirectory() as tmpdir:
        with pushd(tmpdir):
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
                print("checkout branch...", flush=True)
                subprocess.run(
                    ["git", "checkout", "rerender-live-test"],
                    check=True,
                )

                print("checking the git history...", flush=True)
                c = subprocess.run(
                    ["git", "log", "--pretty=oneline", "-n", "1"],
                    capture_output=True,
                    check=True,
                )
                output = c.stdout.decode("utf-8")
                print("    last commit:", output.strip(), flush=True)
                assert "MNT:" in output

                print("checking rerender undid workflow edits...", flush=True)
                if os.path.exists(".github/workflows/automerge.yml"):
                    with open(".github/workflows/automerge.yml") as fp:
                        lines = fp.readlines()
                    assert not any(
                        line.startswith("# test line for rerender edits")
                        for line in lines
                    )

    print("tests passed!")


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
