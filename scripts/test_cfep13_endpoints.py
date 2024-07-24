"""
To run these tests

1. start the web server locally via

   python -u -m conda_forge_webservices.webapp --local

2. Make sure you have a github token in the GH_TOKEN environment variable.

3. Run these tests via pytest -vv test_cfep13_endpoints.py
"""

import os
import tempfile
import subprocess
import uuid

import github
import requests
import pytest

from conda_forge_webservices.utils import pushd, with_action_url

OUTPUTS_REPO = (
    "https://x-access-token:${GH_TOKEN}@github.com/conda-forge" "/feedstock-outputs.git"
)

try:
    token_path = "${HOME}/.conda-smithy/conda-forge_staged-recipes.token"
    with open(os.path.expandvars(token_path)) as fp:
        sr_token = fp.read().strip()

    headers: dict[str, str] | None = {
        "FEEDSTOCK_TOKEN": sr_token,
    }
except Exception:
    headers = None

bad_headers = {
    "FEEDSTOCK_TOKEN": "not a valid token",
}

try:
    GH = github.Github(os.environ["GH_TOKEN"])
except Exception:
    GH = None


def _run_git_command(*args):
    subprocess.run(
        " ".join(["git", *list(args)]),
        check=True,
        shell=True,
    )


def _clone_and_remove(repo, file_to_remove):
    with tempfile.TemporaryDirectory() as tmpdir:
        with pushd(tmpdir):
            _run_git_command("clone", "--depth=1", repo)

            with pushd(os.path.split(repo)[1].replace(".git", "")):
                _run_git_command(
                    "remote",
                    "set-url",
                    "--push",
                    "origin",
                    repo,
                )
                if os.path.exists(file_to_remove):
                    _run_git_command("rm", file_to_remove)
                    msg = with_action_url(f"removed {file_to_remove} for testing")
                    _run_git_command(
                        "commit",
                        "-m",
                        f"'{msg}'",
                    )
                    _run_git_command("pull", "--rebase", "--commit")
                    _run_git_command("push")


@pytest.mark.skipif(headers is None, reason="No feedstock token for testing!")
@pytest.mark.skipif(GH is None, reason="No GitHub token for testing!")
def test_feedstock_outputs_copy_bad_token():
    repo = GH.get_repo("conda-forge/cf-autotick-bot-test-package-feedstock")
    sha = repo.get_branch(repo.default_branch).commit.commit.sha
    r = requests.post(
        "http://127.0.0.1:5000/feedstock-outputs/copy",
        headers=bad_headers,
        json={
            "feedstock": "cf-autotick-bot-test-package-feedstock",
            "outputs": {},
            "channel": "main",
            "git_sha": sha,
        },
    )

    assert r.status_code == 403, r.status_code


@pytest.mark.skipif(headers is None, reason="No feedstock token for testing!")
@pytest.mark.skipif(GH is None, reason="No GitHub token for testing!")
def test_feedstock_outputs_copy_missing_token():
    repo = GH.get_repo("conda-forge/cf-autotick-bot-test-package-feedstock")
    sha = repo.get_branch(repo.default_branch).commit.commit.sha
    r = requests.post(
        "http://127.0.0.1:5000/feedstock-outputs/copy",
        json={
            "feedstock": "cf-autotick-bot-test-package-feedstock",
            "outputs": {},
            "channel": "main",
            "git_sha": sha,
        },
    )

    assert r.status_code == 403, r.status_code


@pytest.mark.skipif(headers is None, reason="No feedstock token for testing!")
@pytest.mark.skipif(GH is None, reason="No GitHub token for testing!")
@pytest.mark.parametrize("key", ["outputs", "feedstock", "channel"])
def test_feedstock_outputs_copy_missing_data(key):
    json_data = {
        "feedstock": "staged-recipes",
        "outputs": {},
        "channel": "main",
    }
    del json_data[key]
    r = requests.post(
        "http://127.0.0.1:5000/feedstock-outputs/copy",
        headers=headers,
        json=json_data,
    )
    assert r.status_code == 403, r.status_code


@pytest.mark.skipif(headers is None, reason="No feedstock token for testing!")
@pytest.mark.skipif(GH is None, reason="No GitHub token for testing!")
def test_feedstock_outputs_copy_bad_hash():
    name = "blah_h" + uuid.uuid4().hex
    try:
        _clone_and_remove(OUTPUTS_REPO, f"outputs/b/l/a/{name}.json")

        json_data = {
            "feedstock": "staged-recipes",
            "outputs": {"blah": "jkdfhslk"},
            "channel": "main",
            "hash_type": "afdfas",
        }
        r = requests.post(
            "http://127.0.0.1:5000/feedstock-outputs/copy",
            headers=headers,
            json=json_data,
        )
        assert r.status_code == 403, r.status_code
    finally:
        _clone_and_remove(OUTPUTS_REPO, f"outputs/b/l/a/{name}.json")


@pytest.mark.skipif(headers is None, reason="No feedstock token for testing!")
@pytest.mark.skipif(GH is None, reason="No GitHub token for testing!")
def test_feedstock_outputs_copy_bad_data():
    name = "blah_h" + uuid.uuid4().hex
    try:
        _clone_and_remove(OUTPUTS_REPO, f"outputs/b/l/a/{name}.json")

        json_data = {
            "feedstock": "staged-recipes",
            "outputs": {"blah": "jkdfhslk"},
            "channel": "main",
        }
        r = requests.post(
            "http://127.0.0.1:5000/feedstock-outputs/copy",
            headers=headers,
            json=json_data,
        )
        assert r.status_code == 403, r.status_code
    finally:
        _clone_and_remove(OUTPUTS_REPO, f"outputs/b/l/a/{name}.json")
