from unittest import mock

import pytest

from conda_forge_webservices.prefix_dev import COPY_MUTATION, copy_to_prefix_dev

DIST = "noarch/boo-0.1-py_10.conda"
SHA = "a" * 64


@pytest.fixture
def prefix_env(monkeypatch):
    monkeypatch.setenv("PREFIX_DEV_TOKEN", "pfx_token")
    monkeypatch.setenv("PREFIX_DEV_CHANNEL", "conda-forge/conda-forge-shadow")


def test_disabled_without_env(monkeypatch):
    monkeypatch.delenv("PREFIX_DEV_TOKEN", raising=False)
    with mock.patch("conda_forge_webservices.prefix_dev.requests.post") as post:
        copy_to_prefix_dev({DIST: SHA})
    post.assert_not_called()


def test_copy(prefix_env):
    with mock.patch("conda_forge_webservices.prefix_dev.requests.post") as post:
        post.return_value.json.return_value = {
            "data": {"copyPackagesFromUrls": {"id": "job-1"}}
        }
        copy_to_prefix_dev({DIST: SHA})

    post.assert_called_once()
    assert post.call_args.kwargs["headers"] == {"Authorization": "Bearer pfx_token"}
    assert post.call_args.kwargs["json"] == {
        "query": COPY_MUTATION,
        "variables": {
            "channelName": "conda-forge/conda-forge-shadow",
            "packages": [
                {
                    "url": f"https://conda-web.anaconda.org/conda-forge/{DIST}",
                    "sha256": SHA,
                }
            ],
        },
    }


@pytest.mark.parametrize(
    "side_effect,json",
    [
        (ConnectionError("boom"), None),
        (None, {"errors": [{"message": "not allowed"}]}),
    ],
)
def test_errors_are_not_raised(prefix_env, side_effect, json):
    with mock.patch("conda_forge_webservices.prefix_dev.requests.post") as post:
        post.side_effect = side_effect
        post.return_value.json.return_value = json
        copy_to_prefix_dev({DIST: SHA})
