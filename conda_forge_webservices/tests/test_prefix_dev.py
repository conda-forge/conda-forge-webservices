import threading
from unittest import mock

import pytest

from conda_forge_webservices import prefix_dev
from conda_forge_webservices.prefix_dev import (
    COPY_MUTATION,
    is_prefix_dev_copy_enabled,
    request_prefix_dev_copy,
    schedule_prefix_dev_copy,
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
                    "url": f"https://conda-web.anaconda.org/conda-forge/{DIST}",
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


def test_enabled_check_never_raises(prefix_env):
    with mock.patch(
        "conda_forge_webservices.prefix_dev.prefix_dev_channel",
        side_effect=RuntimeError("boom"),
    ):
        assert is_prefix_dev_copy_enabled("main", "sha256") is False


def test_schedule_disabled(no_prefix_env):
    with mock.patch(
        "conda_forge_webservices.prefix_dev.request_prefix_dev_copy"
    ) as request:
        assert schedule_prefix_dev_copy({DIST: SHA}) is False
        assert schedule_prefix_dev_copy({}) is False
    request.assert_not_called()


def test_schedule_does_not_wait_for_prefix_dev(prefix_env):
    started = threading.Event()
    release = threading.Event()

    def slow_request(outputs):
        started.set()
        release.wait(10)
        return ["job-1"], []

    with mock.patch(
        "conda_forge_webservices.prefix_dev.request_prefix_dev_copy",
        side_effect=slow_request,
    ) as request:
        assert schedule_prefix_dev_copy({DIST: SHA}) is True
        # the request is still running in the background
        assert started.wait(10)
        assert not release.is_set()
        release.set()
        prefix_dev._executor().submit(lambda: None).result(10)

    request.assert_called_once_with({DIST: SHA})
    assert prefix_dev._PENDING == 0


def test_schedule_errors_are_not_raised(prefix_env):
    with mock.patch(
        "conda_forge_webservices.prefix_dev.request_prefix_dev_copy",
        side_effect=RuntimeError("boom"),
    ):
        assert schedule_prefix_dev_copy({DIST: SHA}) is True
        prefix_dev._executor().submit(lambda: None).result(10)
    assert prefix_dev._PENDING == 0


def test_schedule_drops_requests_when_too_many_are_pending(prefix_env, monkeypatch):
    monkeypatch.setattr(prefix_dev, "_PENDING", prefix_dev.MAX_PENDING_REQUESTS)
    with mock.patch(
        "conda_forge_webservices.prefix_dev.request_prefix_dev_copy"
    ) as request:
        assert schedule_prefix_dev_copy({DIST: SHA}) is False
    request.assert_not_called()


def test_schedule_submit_failure_is_not_raised(prefix_env):
    executor = mock.MagicMock()
    executor.submit.side_effect = RuntimeError("cannot schedule new futures")
    with mock.patch(
        "conda_forge_webservices.prefix_dev._executor", return_value=executor
    ):
        assert schedule_prefix_dev_copy({DIST: SHA}) is False
    assert prefix_dev._PENDING == 0


def test_shutdown_executor_does_not_wait(monkeypatch):
    executor = mock.MagicMock()
    monkeypatch.setattr(prefix_dev, "_EXECUTOR", executor)
    prefix_dev._shutdown_executor()
    executor.shutdown.assert_called_once_with(wait=False)


def test_shutdown_executor_without_executor(monkeypatch):
    monkeypatch.setattr(prefix_dev, "_EXECUTOR", None)
    prefix_dev._shutdown_executor()
