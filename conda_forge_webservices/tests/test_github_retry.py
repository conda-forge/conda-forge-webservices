import github

from conda_forge_webservices.github_actions_integration.api_sessions import (
    _create_api_sessions,
)
from conda_forge_webservices.tokens import _get_gh_client
from conda_forge_webservices.utils import MAX_RATE_LIMIT_WAIT, github_retry


def _retry_of(gh):
    return gh._Github__requester._Requester__retry


def test_the_policy_retries_rate_limits():
    retry = github_retry()
    assert isinstance(retry, github.GithubRetry)
    assert 403 in retry.status_forcelist


def test_the_wait_is_capped_but_long_enough_for_secondary_limits():
    # a cap below secondary_rate_wait makes secondary rate limits raise straight
    # away rather than waiting, which is the opposite of what we want
    assert MAX_RATE_LIMIT_WAIT >= github.GithubRetry().secondary_rate_wait
    assert github_retry().max_rate_limit_wait == MAX_RATE_LIMIT_WAIT


def test_both_clients_understand_rate_limits():
    """Guards the regression this fixes.

    A plain urllib3 Retry does not retry 403 at all, so replacing PyGithub's
    default with one silently turns rate limit handling off.
    """
    for gh in [_get_gh_client("not-a-token"), _create_api_sessions("not-a-token")[1]]:
        assert isinstance(_retry_of(gh), github.GithubRetry)
        assert _retry_of(gh).max_rate_limit_wait == MAX_RATE_LIMIT_WAIT
