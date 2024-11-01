import unittest

from ..automerge import _get_github_checks


@unittest.mock.patch(
    "conda_forge_webservices.github_actions_integration.automerge._get_checks"
)
def test_get_github_checks_nochecks(get_mock):
    get_mock.return_value = {}
    stat = _get_github_checks(1, 2)
    get_mock.assert_called_once_with(1, 2)
    assert stat == {}


@unittest.mock.patch(
    "conda_forge_webservices.github_actions_integration.automerge._get_checks"
)
def test_check_github_checks_all_pending(get_mock):
    get_mock.return_value = [
        {
            "app": {"slug": "c1"},
            "status": "blah",
            "conclusion": "blah",
        },
        {
            "app": {"slug": "c2"},
            "status": "blah",
            "conclusion": "blah",
        },
    ]
    stat = _get_github_checks(1, 2)
    get_mock.assert_called_once_with(1, 2)
    assert stat == {"c1": None, "c2": None}


@unittest.mock.patch(
    "conda_forge_webservices.github_actions_integration.automerge._get_checks"
)
def test_check_github_checks_all_fail(get_mock):
    get_mock.return_value = [
        {
            "app": {"slug": "c1"},
            "status": "completed",
            "conclusion": "error",
        },
        {
            "app": {"slug": "c2"},
            "status": "completed",
            "conclusion": "failure",
        },
    ]
    stat = _get_github_checks(1, 2)
    get_mock.assert_called_once_with(1, 2)
    assert stat == {"c1": False, "c2": False}


@unittest.mock.patch(
    "conda_forge_webservices.github_actions_integration.automerge._get_checks"
)
def test_check_github_checks_all_success(get_mock):
    get_mock.return_value = [
        {
            "app": {"slug": "c1"},
            "status": "completed",
            "conclusion": "success",
        },
        {
            "app": {"slug": "c2"},
            "status": "completed",
            "conclusion": "success",
        },
    ]
    stat = _get_github_checks(1, 2)
    get_mock.assert_called_once_with(1, 2)
    assert stat == {"c1": True, "c2": True}


@unittest.mock.patch(
    "conda_forge_webservices.github_actions_integration.automerge._get_checks"
)
def test_check_github_checks_success_plus_pending(get_mock):
    get_mock.return_value = [
        {
            "app": {"slug": "c1"},
            "status": "blah",
            "conclusion": "success",
        },
        {
            "app": {"slug": "c2"},
            "status": "completed",
            "conclusion": "success",
        },
    ]
    stat = _get_github_checks(1, 2)
    get_mock.assert_called_once_with(1, 2)
    assert stat == {"c1": None, "c2": True}


@unittest.mock.patch(
    "conda_forge_webservices.github_actions_integration.automerge._get_checks"
)
def test_check_github_checks_success_plus_fail(get_mock):
    get_mock.return_value = [
        {
            "app": {"slug": "c1"},
            "status": "completed",
            "conclusion": "error",
        },
        {
            "app": {"slug": "c2"},
            "status": "completed",
            "conclusion": "failure",
        },
        {
            "app": {"slug": "c3"},
            "status": "completed",
            "conclusion": "success",
        },
    ]
    stat = _get_github_checks(1, 2)
    get_mock.assert_called_once_with(1, 2)
    assert stat == {"c1": False, "c2": False, "c3": True}


@unittest.mock.patch(
    "conda_forge_webservices.github_actions_integration.automerge._get_checks"
)
def test_check_github_checks_pending_plus_fail(get_mock):
    get_mock.return_value = [
        {
            "app": {"slug": "c1"},
            "status": "completed",
            "conclusion": "error",
        },
        {
            "app": {"slug": "c2"},
            "status": "completed",
            "conclusion": "failure",
        },
        {
            "app": {"slug": "c3"},
            "status": "blah",
            "conclusion": "success",
        },
    ]
    stat = _get_github_checks(1, 2)
    get_mock.assert_called_once_with(1, 2)
    assert stat == {"c1": False, "c2": False, "c3": None}


@unittest.mock.patch(
    "conda_forge_webservices.github_actions_integration.automerge._get_checks"
)
def test_check_github_checks_pending_plus_success_plus_fail(get_mock):
    get_mock.return_value = [
        {
            "app": {"slug": "c1"},
            "status": "completed",
            "conclusion": "error",
        },
        {
            "app": {"slug": "c2"},
            "status": "completed",
            "conclusion": "failure",
        },
        {
            "app": {"slug": "c3"},
            "status": "blah",
            "conclusion": "success",
        },
        {
            "app": {"slug": "c4"},
            "status": "completed",
            "conclusion": "success",
        },
    ]
    stat = _get_github_checks(1, 2)
    get_mock.assert_called_once_with(1, 2)
    assert stat == {"c1": False, "c2": False, "c3": None, "c4": True}
