import os
import subprocess
import tempfile
import time
import uuid

import github

import conda_forge_webservices
from conda_forge_webservices.utils import get_workflow_run_from_uid, pushd
from conda_forge_webservices.github_actions_integration.linting import set_pr_status


WAIT_TIME = 600

TEST_CASES = [
    (
        733,
        "failure",
        [
            "failed to even lint the recipe",
        ],
    ),
    (
        632,
        "failure",
        [
            "and found some lint.",
            "feedstock has no `.ci_support` files and thus will not build any packages",
        ],
    ),
    (
        523,
        "failure",
        [
            "I was trying to look for recipes to lint for you, but couldn't find any.",
        ],
    ),
    (
        217,
        "success",
        [
            "I do have some suggestions for making it better though...",
        ],
    ),
    (
        62,
        "success",
        [
            "I do have some suggestions for making it better though...",
        ],
    ),
    (
        57,
        "failure",
        [
            "I was trying to look for recipes to lint for you, but it "
            "appears we have a merge conflict.",
        ],
    ),
    (
        56,
        "failure",
        [
            "I was trying to look for recipes to lint for you, but it appears "
            "we have a merge conflict.",
        ],
    ),
    (
        54,
        "success",
        [
            "I do have some suggestions for making it better though...",
        ],
    ),
    (
        17,
        "failure",
        [
            "and found some lint.",
        ],
    ),
    (
        16,
        "success",
        [
            "and found it was in an excellent condition.",
        ],
    ),
]


def _make_empty_commit(pr_num):
    with tempfile.TemporaryDirectory() as tmpdir, pushd(tmpdir):
        subprocess.run(
            [
                "git",
                "clone",
                "https://github.com/conda-forge/conda-forge-webservices.git",
            ],
            check=True,
        )
        with pushd("conda-forge-webservices"):
            subprocess.run(
                [
                    "git",
                    "remote",
                    "set-url",
                    "--push",
                    "origin",
                    f"https://x-access-token:{os.environ['GH_TOKEN']}@github.com/"
                    "conda-forge/conda-forge-webservices.git",
                ]
            )
            subprocess.run(["gh", "pr", "checkout", f"{pr_num}"], check=True)
            subprocess.run(
                ["git", "commit", "--allow-empty", "-m", "[ci skip] empty commit"],
                check=True,
            )
            subprocess.run(["git", "push"], check=True)

    time.sleep(2.0)


def test_linter_pr(pytestconfig):
    branch = pytestconfig.getoption("branch")

    gh = github.Github(auth=github.Auth.Token(os.environ["GH_TOKEN"]))
    repo = gh.get_repo("conda-forge/conda-forge-webservices")

    target_urls = {}
    for pr_number, _, _ in TEST_CASES:
        _make_empty_commit(pr_number)

        uid = uuid.uuid4().hex
        pr = repo.get_pull(pr_number)
        pr_sha = pr.head.sha
        workflow = repo.get_workflow("webservices-workflow-dispatch.yml")
        workflow_ran = workflow.create_dispatch(
            ref=branch,
            inputs={
                "task": "lint",
                "repo": "conda-forge-webservices",
                "pr_number": str(pr_number),
                "container_tag": conda_forge_webservices.__version__.replace("+", "."),
                "uuid": uid,
                "sha": pr_sha,
            },
        )
        assert workflow_ran, f"Workflow did not run for PR {pr_number}!"
        run = get_workflow_run_from_uid(workflow, uid, branch)
        if run:
            target_url = run.html_url
        else:
            target_url = None
        assert target_url is not None, f"target url is None for PR #{pr_number}"
        target_urls[pr_number] = target_url
        print(f"target_url for PR {pr_number}: {target_url}", flush=True)
        set_pr_status(repo, pr_sha, "pending", target_url=target_url)

    print(
        f"\nsleeping for {WAIT_TIME / 60:0.1f} minutes to let the linter work...",
        flush=True,
    )
    tot = 0
    while tot < WAIT_TIME:
        time.sleep(30)
        tot += 30
        print(f"    slept {tot} seconds out of {WAIT_TIME}", flush=True)

    for pr_number, expected_status, expected_msgs in TEST_CASES:
        print(f"checking pr {pr_number}...", flush=True)

        pr = repo.get_pull(pr_number)
        commit = repo.get_commit(pr.head.sha)

        status = None
        for _status in commit.get_statuses():
            if _status.context == "conda-forge-linter":
                status = _status
                break

        assert status is not None, (
            f"status is None for PR #{pr_number}: see {target_urls[pr_number]}"
        )

        comment = None
        for _comment in pr.get_issue_comments():
            if (
                "Hi! This is the friendly automated conda-forge-linting service."
                in _comment.body
            ):
                comment = _comment

        assert comment is not None, (
            f"comment is None for PR #{pr_number}: see {target_urls[pr_number]}"
        )

        assert status.state == expected_status, (
            pr_number,
            status.state,
            expected_status,
            comment.body,
            f"status is not expected statusfor PR #{pr_number}: see {target_urls[pr_number]}",
        )

        for expected_msg in expected_msgs:
            assert expected_msg in comment.body, (
                f"expected message missing for PR #{pr_number}: see {target_urls[pr_number]}"
            )
