import os
import time
import uuid

import github

import conda_forge_webservices
from conda_forge_webservices.linting import _set_pr_status, _get_workflow_run_from_uid

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
            "I was trying to look for recipes to lint for "
            "you, but couldn't find any.",
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


def test_linter_pr(pytestconfig):
    branch = pytestconfig.getoption("branch")

    gh = github.Github(auth=github.Auth.Token(os.environ["GH_TOKEN"]))
    repo = gh.get_repo("conda-forge/conda-forge-webservices")

    for pr_number, _, _ in TEST_CASES:
        uid = uuid.uuid4().hex
        pr = repo.get_pull(pr_number)
        workflow = repo.get_workflow("webservices-workflow-dispatch.yml")
        workflow.create_dispatch(
            ref=branch,
            inputs={
                "task": "lint",
                "repo": "conda-forge-webservices",
                "pr_number": str(pr_number),
                "container_tag": conda_forge_webservices.__version__.replace("+", "."),
                "uid": uid,
            },
        )
        run = _get_workflow_run_from_uid(workflow, uid, branch)
        if run:
            target_url = run.html_url
        else:
            target_url = None
        _set_pr_status(
            "conda-forge",
            "conda-forge-webservices",
            pr.head.sha,
            "pending",
            target_url=target_url,
        )

    print("\nsleeping for four minutes to let the linter work...", flush=True)
    tot = 0
    while tot < 240:
        time.sleep(10)
        tot += 10
        print(f"    slept {tot} seconds out of 240", flush=True)

    for pr_number, expected_status, expected_msgs in TEST_CASES:
        pr = repo.get_pull(pr_number)
        commit = repo.get_commit(pr.head.sha)

        status = None
        for _status in commit.get_statuses():
            if _status.context == "conda-forge-linter":
                status = _status
                break

        assert status is not None

        comment = None
        for _comment in pr.get_issue_comments():
            if (
                "Hi! This is the friendly automated conda-forge-linting service."
                in _comment.body
            ):
                comment = _comment

        assert comment is not None

        assert status.state == expected_status, (
            pr_number,
            status.state,
            expected_status,
            comment.body,
        )

        for expected_msg in expected_msgs:
            assert expected_msg in comment.body
