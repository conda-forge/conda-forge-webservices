"""
To run these tests

1. start the web server locally via

   python -u -m conda_forge_webservices.webapp --local

2. Run these tests via pytest -vv test_trusted_publishing_endpoint.py

The tests that send bad requests need nothing else. The one that sends a good
request needs an identity token, so it only runs inside a GitHub Actions job
with `id-token: write` and skips anywhere else.
"""

import datetime
import os
import socket
import threading
import time

import jwt
import pytest
import requests
import yaml
from cryptography.hazmat.primitives.asymmetric import rsa

ENDPOINT = "http://127.0.0.1:5000/trusted-publishing/version-update"

# the feedstock conda-forge uses for live tests, which has to list this
# repository's workflow under trusted_publishers for the good request to pass
FEEDSTOCK = "cf-autotick-bot-test-package-feedstock"

AUDIENCE = "conda-forge-updater"


def _post(body, token="not.a.token", **kwargs):
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return requests.post(ENDPOINT, json=body, headers=headers, timeout=30, **kwargs)


# no issuer will ever publish this key, so anything it signs has to be refused
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _signed(claims, *, key=KEY, algorithm="RS256"):
    now = datetime.datetime.now(datetime.timezone.utc)
    return jwt.encode(
        {
            "aud": AUDIENCE,
            "iat": now,
            "exp": now + datetime.timedelta(minutes=5),
            "sub": "repo:conda-forge/conda-forge-webservices:ref:refs/heads/main",
            "repository": "conda-forge/conda-forge-webservices",
            "repository_owner": "conda-forge",
            "repository_owner_id": "11897326",
            "job_workflow_ref": (
                "conda-forge/conda-forge-webservices/.github/workflows/tests.yml"
                "@refs/heads/main"
            ),
            "ref": "refs/heads/main",
            "ref_type": "branch",
            **claims,
        },
        key,
        algorithm=algorithm,
    )


@pytest.fixture(scope="module")
def configured():
    """Fail unless the feedstock lists this workflow as a trusted publisher.

    tests/conda-forge.yml and tests/conda-forge-for-recipe.yml, which the
    version update live tests write over the feedstock's own, carry the entry,
    so a feedstock without it means something has wiped it. Read the way the
    endpoint reads it, from raw.githubusercontent.com.
    """
    res = requests.get(
        f"https://raw.githubusercontent.com/conda-forge/{FEEDSTOCK}/HEAD/conda-forge.yml",
        timeout=30,
    )
    res.raise_for_status()
    publishers = (yaml.safe_load(res.text) or {}).get("trusted_publishers") or []
    assert any(
        publisher.get("repository") == "conda-forge/conda-forge-webservices"
        and publisher.get("workflow") == "tests.yml"
        for publisher in publishers
    ), (
        f"conda-forge/{FEEDSTOCK} does not list this workflow under "
        "trusted_publishers; tests/conda-forge.yml should put it there"
    )


def _id_token():
    """Ask GitHub Actions for an identity token for this job."""
    url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
    request_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not url or not request_token:
        pytest.skip("no identity token available outside a GitHub Actions job")

    res = requests.get(
        url,
        params={"audience": AUDIENCE},
        headers={"Authorization": f"Bearer {request_token}"},
        timeout=30,
    )
    res.raise_for_status()
    return res.json()["value"]


def _post_until_keys_are_held(body, token):
    """The server fetches keys as it starts, and says when to come back."""
    for _ in range(5):
        res = _post(body, token=token)
        if res.status_code != 503:
            return res
        time.sleep(int(res.headers.get("Retry-After", "5")))
    return res


def test_a_request_with_no_token_is_refused():
    res = _post({"feedstock": FEEDSTOCK, "version": "1.2.3"}, token=None)
    assert res.status_code == 401, res.text


def test_a_request_with_an_empty_token_is_refused():
    res = _post({"feedstock": FEEDSTOCK, "version": "1.2.3"}, token="")
    assert res.status_code == 401, res.text


@pytest.mark.parametrize(
    "body",
    [
        {"version": "1.2.3"},
        {"feedstock": FEEDSTOCK},
        {"feedstock": "staged-recipes", "version": "1.2.3"},
        {"feedstock": "../../etc/passwd", "version": "1.2.3"},
        {"feedstock": FEEDSTOCK, "version": "1.0.0-rc1"},
        {"feedstock": FEEDSTOCK, "version": "{{ version }}"},
        {"feedstock": FEEDSTOCK, "version": "1.2.3; rm -rf /"},
        {"feedstock": FEEDSTOCK, "version": "1.2.3", "branch": "../main"},
        {"feedstock": FEEDSTOCK, "version": 123},
    ],
)
def test_a_request_we_will_not_read_is_refused(body):
    res = _post(body)
    assert res.status_code == 400, res.text


def test_a_body_that_is_not_json_is_refused():
    res = requests.post(
        ENDPOINT,
        data="not json",
        headers={"Authorization": "Bearer not.a.token"},
        timeout=30,
    )
    assert res.status_code == 400, res.text


def test_a_token_is_verified_before_the_feedstock_is_looked_up():
    res = _post({"feedstock": "this-does-not-exist-feedstock", "version": "1.2.3"})
    assert res.status_code == 403, res.text


def test_a_feedstock_that_does_not_exist_is_not_found():
    res = _post_until_keys_are_held(
        {"feedstock": "this-does-not-exist-feedstock", "version": "1.2.3"},
        _id_token(),
    )
    assert res.status_code == 404, res.text


def test_a_token_that_is_not_a_token_is_refused():
    res = _post({"feedstock": FEEDSTOCK, "version": "1.2.3"})
    assert res.status_code == 403, res.text


def test_a_token_from_the_wrong_audience_is_refused():
    url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
    request_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not url or not request_token:
        pytest.skip("no identity token available outside a GitHub Actions job")

    res = requests.get(
        url,
        params={"audience": "https://pypi.org"},
        headers={"Authorization": f"Bearer {request_token}"},
        timeout=30,
    )
    res.raise_for_status()

    res = _post_until_keys_are_held(
        {"feedstock": FEEDSTOCK, "version": "1.2.3"}, res.json()["value"]
    )
    assert res.status_code == 403, res.text


def test_a_token_we_signed_ourselves_is_refused():
    """Claims that would match, over a signature no issuer vouches for."""
    res = _post({"feedstock": FEEDSTOCK, "version": "1.2.3"}, token=_signed({}))
    assert res.status_code == 403, res.text


def test_an_unsigned_token_is_refused():
    token = _signed({}, key=None, algorithm="none")
    res = _post({"feedstock": FEEDSTOCK, "version": "1.2.3"}, token=token)
    assert res.status_code == 403, res.text


def test_an_issuer_the_feedstock_never_named_is_not_contacted():
    """The token picks the key, so it must not be able to pick the address.

    Holds whether the feedstock has registered a publisher or not: either way
    nothing should open a connection to somewhere only the token named.
    """
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(10)
    reached = []
    thread = threading.Thread(target=lambda: reached.append(listener.accept()))
    thread.daemon = True
    thread.start()

    try:
        token = _signed({"iss": f"https://127.0.0.1:{listener.getsockname()[1]}"})
        res = _post({"feedstock": FEEDSTOCK, "version": "1.2.3"}, token=token)
        assert res.status_code == 403, res.text

        time.sleep(1)
        assert reached == [], "the endpoint went looking for an issuer nobody named"
    finally:
        listener.close()


def test_a_good_request_opens_a_pull_request(configured):
    token = _id_token()
    res = _post_until_keys_are_held({"feedstock": FEEDSTOCK, "version": "0.13"}, token)
    assert res.status_code == 202, res.text

    data = res.json()
    assert data["pull_request"]
    assert FEEDSTOCK in data["url"]

    try:
        # the version updater was dispatched, at this branch, on the pull request
        assert data["version_update_started"], data
        assert data["version_update_run"], data
        assert _version_update_status(data["pull_request"]) == "pending"

        # the same token is spent, so it opens nothing more
        reused = _post({"feedstock": FEEDSTOCK, "version": "0.14"}, token=token)
        assert reused.status_code == 403, reused.text
        assert "already been used" in reused.text

        # and the cooldown holds for a feedstock that just had one opened
        again = _post({"feedstock": FEEDSTOCK, "version": "0.14"}, token=_id_token())
        assert again.status_code == 429, again.text
        assert again.headers["Retry-After"]
    finally:
        # the updater pushes to the pull request's branch, so it is left to
        # finish rather than closing the pull request out from under it
        _wait_for(data.get("version_update_run"))
        _close(data["pull_request"])


def _version_update_status(number):
    import github

    gh = github.Github(auth=github.Auth.Token(os.environ["GH_TOKEN"]))
    repo = gh.get_repo(f"conda-forge/{FEEDSTOCK}")
    sha = repo.get_pull(number).head.sha
    for status in repo.get_commit(sha).get_statuses():
        if status.context == "conda-forge-version-update-service":
            return status.state
    return None


RUN_WAIT = 20 * 60


def _wait_for(run_url):
    if not run_url:
        return
    import github

    gh = github.Github(auth=github.Auth.Token(os.environ["GH_TOKEN"]))
    run_id = int(run_url.rstrip("/").rsplit("/", 1)[1])
    run = gh.get_repo("conda-forge/conda-forge-webservices").get_workflow_run(run_id)
    deadline = time.monotonic() + RUN_WAIT
    while time.monotonic() < deadline:
        run.update()
        if run.status == "completed":
            print(f"the version updater finished: {run.conclusion}", flush=True)
            return
        time.sleep(20)
    print(f"the version updater did not finish within {RUN_WAIT}s", flush=True)


def _close(number):
    import github

    gh = github.Github(auth=github.Auth.Token(os.environ["GH_TOKEN"]))
    pull = gh.get_repo(f"conda-forge/{FEEDSTOCK}").get_pull(number)
    pull.edit(state="closed")
    try:
        gh.get_repo(pull.head.repo.full_name).get_git_ref(
            f"heads/{pull.head.ref}"
        ).delete()
    except Exception:
        pass
