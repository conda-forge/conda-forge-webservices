import datetime
import unittest

import pytest

from ..automerge import _get_github_statuses


class DummyStatus:
    """Dummy object to mock up something from the API.

    We could use an actual mock, but shrug.
    """

    def __init__(self, context, state, updated_at):
        self.context = context
        self.state = state
        self.updated_at = updated_at


@pytest.mark.parametrize(
    "stats,ret",
    [
        ([], {}),
        (
            [
                DummyStatus("blah", "pending", datetime.datetime.now()),
                DummyStatus("blah", "success", datetime.datetime.now()),
            ],
            {"blah": True},
        ),
        (
            [
                DummyStatus("blah", "pending", datetime.datetime.now()),
                DummyStatus("blah1", "pending", datetime.datetime.now()),
            ],
            {"blah": None, "blah1": None},
        ),
        (
            [
                DummyStatus("blah", "failure", datetime.datetime.now()),
                DummyStatus("blah1", "error", datetime.datetime.now()),
            ],
            {"blah": False, "blah1": False},
        ),
        (
            [
                DummyStatus("blah", "success", datetime.datetime.now()),
                DummyStatus("blah1", "success", datetime.datetime.now()),
            ],
            {"blah": True, "blah1": True},
        ),
        (
            [
                DummyStatus("blah", "failure", datetime.datetime.now()),
                DummyStatus("blah1", "error", datetime.datetime.now()),
                DummyStatus("blah2", "pending", datetime.datetime.now()),
            ],
            {"blah": False, "blah1": False, "blah2": None},
        ),
        (
            [
                DummyStatus("blah", "success", datetime.datetime.now()),
                DummyStatus("blah1", "error", datetime.datetime.now()),
                DummyStatus("blah2", "pending", datetime.datetime.now()),
            ],
            {"blah": True, "blah1": False, "blah2": None},
        ),
        (
            [
                DummyStatus("blah", "success", datetime.datetime.now()),
                DummyStatus("blah1", "error", datetime.datetime.now()),
                DummyStatus("blah2", "failure", datetime.datetime.now()),
            ],
            {"blah": True, "blah1": False, "blah2": False},
        ),
        (
            [
                DummyStatus("blah", "success", datetime.datetime.now()),
                DummyStatus("blah1", "error", datetime.datetime.now()),
                DummyStatus("blah2", "failure", datetime.datetime.now()),
                DummyStatus("blah3", "pending", datetime.datetime.now()),
            ],
            {"blah": True, "blah1": False, "blah2": False, "blah3": None},
        ),
    ],
)
def test_get_github_statuses(stats, ret):
    repo = unittest.mock.MagicMock()
    pr = unittest.mock.MagicMock()
    repo.get_commit.return_value.get_statuses.return_value = stats
    stat = _get_github_statuses(repo, pr)
    assert stat == ret
    repo.get_commit.assert_called_once_with(pr.head.sha)
