from unittest import mock

import pytest

from conda_forge_webservices.feedstock_outputs import (
    POST_STAGING,
    STAGING,
    stage_dist_to_post_staging_and_possibly_copy_to_prod,
)
from conda_forge_webservices.prefix_dev import (
    COPY_MUTATION,
    is_prefix_dev_copy_enabled,
    request_prefix_dev_copy,
)

DIST = "noarch/boo-0.1-py_10.conda"
SHA = "a" * 64


@pytest.fixture
def prefix_env(monkeypatch):
    monkeypatch.setenv("PREFIX_DEV_TOKEN", "pfx_token")
    monkeypatch.setenv("PREFIX_DEV_CHANNEL", "conda-forge-shadow")
    monkeypatch.delenv("PREFIX_DEV_URL", raising=False)


@pytest.fixture
def no_prefix_env(monkeypatch):
    monkeypatch.delenv("PREFIX_DEV_TOKEN", raising=False)
    monkeypatch.delenv("PREFIX_DEV_CHANNEL", raising=False)


def _response(json_data, status=200):
    resp = mock.MagicMock()
    resp.status_code = status
    resp.json.return_value = json_data
    if status >= 400:
        resp.raise_for_status.side_effect = RuntimeError(f"HTTP {status}")
    return resp


def test_disabled_without_env(no_prefix_env):
    assert not is_prefix_dev_copy_enabled("main", "sha256")
    with mock.patch("conda_forge_webservices.prefix_dev.requests.post") as post:
        assert request_prefix_dev_copy({DIST: SHA}) == ([], [])
        post.assert_not_called()


def test_disabled_without_channel(monkeypatch):
    monkeypatch.setenv("PREFIX_DEV_TOKEN", "pfx_token")
    monkeypatch.delenv("PREFIX_DEV_CHANNEL", raising=False)
    assert not is_prefix_dev_copy_enabled("main", "sha256")


@pytest.mark.parametrize(
    "label,hash_type,enabled",
    [
        ("main", "sha256", True),
        ("main", "md5", False),
        ("rc", "sha256", False),
    ],
)
def test_enabled_only_for_main_and_sha256(prefix_env, label, hash_type, enabled):
    assert is_prefix_dev_copy_enabled(label, hash_type) is enabled


def test_request_copy(prefix_env):
    with mock.patch("conda_forge_webservices.prefix_dev.requests.post") as post:
        post.return_value = _response(
            {"data": {"copyPackagesFromUrls": {"id": "job-1", "status": "PENDING"}}}
        )
        job_ids, errors = request_prefix_dev_copy({DIST: SHA})

    assert job_ids == ["job-1"]
    assert errors == []
    post.assert_called_once()
    args, kwargs = post.call_args
    assert args == ("https://prefix.dev/api/graphql",)
    assert kwargs["headers"] == {"Authorization": "Bearer pfx_token"}
    assert kwargs["json"] == {
        "query": COPY_MUTATION,
        "variables": {
            "channelName": "conda-forge-shadow",
            "packages": [
                {
                    "url": f"https://conda.anaconda.org/cf-staging/{DIST}",
                    "sha256": SHA,
                }
            ],
        },
    }


def test_request_copy_custom_url(prefix_env, monkeypatch):
    monkeypatch.setenv("PREFIX_DEV_URL", "https://staging.prefix.dev/")
    with mock.patch("conda_forge_webservices.prefix_dev.requests.post") as post:
        post.return_value = _response(
            {"data": {"copyPackagesFromUrls": {"id": "job-1", "status": "PENDING"}}}
        )
        request_prefix_dev_copy({DIST: SHA})
    assert post.call_args[0] == ("https://staging.prefix.dev/api/graphql",)


def test_request_copy_batches(prefix_env, monkeypatch):
    monkeypatch.setattr(
        "conda_forge_webservices.prefix_dev.MAX_PACKAGES_PER_REQUEST", 2
    )
    outputs = {f"noarch/boo-0.{i}-py_0.conda": SHA for i in range(5)}
    with mock.patch("conda_forge_webservices.prefix_dev.requests.post") as post:
        post.side_effect = [
            _response({"data": {"copyPackagesFromUrls": {"id": f"job-{i}"}}})
            for i in range(3)
        ]
        job_ids, errors = request_prefix_dev_copy(outputs)

    assert job_ids == ["job-0", "job-1", "job-2"]
    assert errors == []
    sizes = [
        len(c.kwargs["json"]["variables"]["packages"]) for c in post.call_args_list
    ]
    assert sizes == [2, 2, 1]


@pytest.mark.parametrize(
    "response",
    [
        _response({"errors": [{"message": "not allowed"}]}),
        _response({}, status=500),
    ],
)
def test_request_copy_errors_are_not_raised(prefix_env, response):
    with mock.patch("conda_forge_webservices.prefix_dev.requests.post") as post:
        post.return_value = response
        job_ids, errors = request_prefix_dev_copy({DIST: SHA})
    assert job_ids == []
    assert len(errors) == 1
    assert DIST in errors[0]


def test_request_copy_network_error_is_not_raised(prefix_env):
    with mock.patch("conda_forge_webservices.prefix_dev.requests.post") as post:
        post.side_effect = ConnectionError("boom")
        job_ids, errors = request_prefix_dev_copy({DIST: SHA})
    assert job_ids == []
    assert "boom" in errors[0]


@pytest.mark.parametrize("prefix_enabled", [True, False])
@mock.patch("conda_forge_webservices.feedstock_outputs._remove_dist")
@mock.patch("conda_forge_webservices.feedstock_outputs._is_valid_output_hash")
@mock.patch(
    "conda_forge_webservices.feedstock_outputs._copy_feedstock_outputs_between_channels"
)
@mock.patch("conda_forge_webservices.feedstock_outputs._get_ac_api_prod")
@mock.patch("conda_forge_webservices.feedstock_outputs._get_ac_api_post_staging")
@mock.patch("conda_forge_webservices.feedstock_outputs._get_ac_api_staging")
def test_staging_kept_when_prefix_dev_enabled(
    ac_staging,
    ac_post_staging,
    ac_prod,
    copy_between,
    valid_hash,
    remove_dist,
    prefix_enabled,
    monkeypatch,
):
    if prefix_enabled:
        monkeypatch.setenv("PREFIX_DEV_TOKEN", "pfx_token")
        monkeypatch.setenv("PREFIX_DEV_CHANNEL", "conda-forge-shadow")
    else:
        monkeypatch.delenv("PREFIX_DEV_TOKEN", raising=False)

    copy_between.return_value = {DIST: True}
    valid_hash.return_value = {DIST: True}

    copied, errors = stage_dist_to_post_staging_and_possibly_copy_to_prod(
        DIST, "main", "sha256", SHA
    )

    assert copied
    assert errors == []
    removed_from = [c.args[1] for c in remove_dist.call_args_list]
    # post-staging is always cleaned up
    assert POST_STAGING in removed_from
    # staging is kept for prefix.dev to download from
    assert (STAGING in removed_from) is (not prefix_enabled)
