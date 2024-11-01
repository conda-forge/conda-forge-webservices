import os
import unittest

import pytest

from ..automerge import (
    _all_statuses_and_checks_ok,
    _get_required_checks_and_statuses,
    pushd,
)


@pytest.mark.parametrize("val", [True, False])
def test_all_statuses_and_checks_ok(val):
    status_states = {
        "A-ci": val,
        "b-CI": None if not val else True,
        "c-ci": False,
    }
    check_states = {
        "e-ci": None if not val else True,
        "e-ci-1": False if not val else True,
        "e-ci-2": True,
        "d-ci": True,
        "f-ci": val,
    }
    req_checks_and_states = ["a", "b-ci", "e-c", "f-"]
    ok, stats = _all_statuses_and_checks_ok(
        status_states, check_states, req_checks_and_states
    )
    if val:
        assert ok
        assert stats == {"a": True, "b-ci": True, "e-c": True, "f-": True}
    else:
        assert not ok
        assert stats == {"a": False, "b-ci": None, "e-c": None, "f-": False}


@pytest.mark.parametrize(
    "fname",
    [
        "appveyor.yml",
        ".appveyor.yml",
        ".drone.yml",
        ".travis.yml",
        "azure-pipelines.yml",
        ".circleci/config.yml",
    ],
)
@pytest.mark.parametrize("ignore_linter", ["conda-forge-linter", "linter", None])
@unittest.mock.patch(
    "conda_forge_webservices.github_actions_integration.automerge._run_git_command"
)
@unittest.mock.patch(
    "conda_forge_webservices.github_actions_integration.automerge.tempfile"
)
def test_get_required_checks_and_statuses(
    tmpmock, submock, tmpdir, fname, ignore_linter
):
    tmpmock.TemporaryDirectory.return_value.__enter__.return_value = str(tmpdir)

    pr = unittest.mock.MagicMock()
    if ignore_linter is not None:
        cfg = {"bot": {"automerge_options": {"ignored_statuses": [ignore_linter]}}}
    else:
        cfg = {}

    with pushd(tmpdir):
        os.makedirs(".circleci")
        with open(fname, "w") as fp:
            fp.write("dummy")

    req = _get_required_checks_and_statuses(pr, cfg)

    if fname in ["appveyor.yml", ".appveyor.yml"]:
        name = "appveyor"
    elif fname == ".drone.yml":
        name = "drone"
    elif fname == ".travis.yml":
        name = "travis"
    elif fname == "azure-pipelines.yml":
        name = "azure"
    else:
        name = "circle"

    assert name in req
    if ignore_linter is None:
        assert "linter" in req
        assert len(req) == 2, req
    else:
        assert "linter" not in req
        assert len(req) == 1, req

    submock.assert_any_call("clone", pr.head.repo.clone_url, str(tmpdir))
    submock.assert_any_call("checkout", pr.head.sha)
