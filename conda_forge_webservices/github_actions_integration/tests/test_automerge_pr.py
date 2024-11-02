import unittest.mock
from unittest.mock import MagicMock

import pytest

from ..automerge import automerge_pr


@unittest.mock.patch(
    "conda_forge_webservices.github_actions_integration.automerge._get_conda_forge_config"
)
def test_automerge_pr_bad_user(get_cfg_mock):
    get_cfg_mock.return_value = {}
    repo = MagicMock()
    repo.full_name = "go"

    pr = MagicMock()
    pr.user.login = "blah"

    pr_for_admin = MagicMock()
    pr_for_admin.user.login = "regro-cf-autotick-bot"

    did_merge, reason = automerge_pr(repo, pr, pr_for_admin)

    assert not did_merge
    assert "user blah" in reason
    get_cfg_mock.assert_called_once_with(pr)
    pr_for_admin.create_issue_comment.assert_not_called()
    pr_for_admin.get_issue_comments.assert_not_called()
    pr_for_admin.merge.assert_not_called()


@unittest.mock.patch(
    "conda_forge_webservices.github_actions_integration.automerge._get_conda_forge_config"
)
def test_automerge_pr_no_title_slug(get_cfg_mock):
    get_cfg_mock.return_value = {}
    repo = MagicMock()
    repo.full_name = "go"

    pr = MagicMock()
    pr.user.login = "regro-cf-autotick-bot"
    pr.title = "blah"

    pr_for_admin = MagicMock()
    pr_for_admin.user.login = "regro-cf-autotick-bot"

    did_merge, reason = automerge_pr(repo, pr, pr_for_admin)

    assert not did_merge
    assert "slug in the title" in reason
    get_cfg_mock.assert_called_once_with(pr)
    pr_for_admin.create_issue_comment.assert_not_called()
    pr_for_admin.get_issue_comments.assert_not_called()
    pr_for_admin.merge.assert_not_called()


@pytest.mark.parametrize(
    "cfg",
    [
        {},
        {"bot": {}},
        {"bot": {"automerge": False}},
    ],
)
@unittest.mock.patch(
    "conda_forge_webservices.github_actions_integration.automerge._get_conda_forge_config"
)
def test_automerge_pr_feedstock_off(get_cfg_mock, cfg):
    get_cfg_mock.return_value = cfg
    repo = MagicMock()
    repo.full_name = "go"

    pr = MagicMock()
    pr.user.login = "regro-cf-autotick-bot"
    pr.title = "[bot-automerge] blah"

    pr_for_admin = MagicMock()
    pr_for_admin.user.login = "regro-cf-autotick-bot"

    did_merge, reason = automerge_pr(repo, pr, pr_for_admin)

    assert not did_merge
    assert "off for this feedstock" in reason
    get_cfg_mock.assert_called_once_with(pr)
    pr_for_admin.create_issue_comment.assert_not_called()
    pr_for_admin.get_issue_comments.assert_not_called()
    pr_for_admin.merge.assert_not_called()


@pytest.mark.parametrize("fail", ["check", "status"])
@unittest.mock.patch(
    "conda_forge_webservices.github_actions_integration.automerge._get_conda_forge_config"
)
@unittest.mock.patch(
    "conda_forge_webservices.github_actions_integration.automerge._get_required_checks_and_statuses"
)
@unittest.mock.patch(
    "conda_forge_webservices.github_actions_integration.automerge._get_github_checks"
)
@unittest.mock.patch(
    "conda_forge_webservices.github_actions_integration.automerge._get_github_statuses"
)
def test_automerge_pr_feedstock_status_or_check_fail(
    stat_mock, check_mock, req_mock, get_cfg_mock, fail
):
    check_mock.return_value = {"check1": True, "check2": True, "check3": False}
    stat_mock.return_value = {"status1": True, "status2": True, "status3": True}
    req_mock.return_value = ["check1", "check2", "status1", "status3"]
    get_cfg_mock.return_value = {"bot": {"automerge": True}}

    if fail == "check":
        check_mock.return_value["check2"] = False
    else:
        stat_mock.return_value["status1"] = False

    repo = MagicMock()
    repo.full_name = "go"

    pr = MagicMock()
    pr.user.login = "regro-cf-autotick-bot"
    pr.title = "[bot-automerge] blah"

    pr_for_admin = MagicMock()
    pr_for_admin.user.login = "regro-cf-autotick-bot"
    pr_for_admin.get_issue_comments.return_value = []

    did_merge, reason = automerge_pr(repo, pr, pr_for_admin)

    assert not did_merge
    assert "pending statuses" in reason
    get_cfg_mock.assert_called_once_with(pr)
    check_mock.assert_called_once_with(repo, pr)
    stat_mock.assert_called_once_with(repo, pr)
    req_mock.assert_called_once_with(pr, get_cfg_mock.return_value)
    pr_for_admin.create_issue_comment.assert_called_once()
    pr_for_admin.get_issue_comments.assert_called()
    pr_for_admin.merge.assert_not_called()


@unittest.mock.patch(
    "conda_forge_webservices.github_actions_integration.automerge._get_conda_forge_config"
)
@unittest.mock.patch(
    "conda_forge_webservices.github_actions_integration.automerge._get_required_checks_and_statuses"
)
@unittest.mock.patch(
    "conda_forge_webservices.github_actions_integration.automerge._get_github_checks"
)
@unittest.mock.patch(
    "conda_forge_webservices.github_actions_integration.automerge._get_github_statuses"
)
def test_automerge_pr_feedstock_no_statuses_or_checks(
    stat_mock, check_mock, req_mock, get_cfg_mock
):
    check_mock.return_value = {}
    stat_mock.return_value = {}
    req_mock.return_value = []
    get_cfg_mock.return_value = {"bot": {"automerge": True}}

    repo = MagicMock()
    repo.full_name = "go"

    pr = MagicMock()
    pr.user.login = "regro-cf-autotick-bot"
    pr.title = "[bot-automerge] blah"

    pr_for_admin = MagicMock()
    pr_for_admin.user.login = "regro-cf-autotick-bot"

    did_merge, reason = automerge_pr(repo, pr, pr_for_admin)

    assert not did_merge
    assert "At least one status or check must be required" in reason
    get_cfg_mock.assert_called_once_with(pr)
    check_mock.assert_called_once_with(repo, pr)
    stat_mock.assert_called_once_with(repo, pr)
    req_mock.assert_called_once_with(pr, get_cfg_mock.return_value)
    pr_for_admin.create_issue_comment.assert_not_called()
    pr_for_admin.get_issue_comments.assert_not_called()
    pr_for_admin.merge.assert_not_called()
