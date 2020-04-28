"""
To run these tests

1. start the web server locally via

   python -u -m conda_forge_webservices.webapp --local

2. Make sure you have valid CI tokens for smithy in your `${HOME}/.conda-smithy/`
   directory, including the feedstock token for staged recipes.

3. Make sure you have a github token in the GH_TOKEN environment variable.

4. Run these tests via pytest -vv test_cfep13_endpoints.py
"""

import os
import tempfile
import subprocess
import uuid

import requests
import pytest

from conda_forge_webservices.utils import pushd
from conda_forge_webservices.feedstock_outputs import TOKENS_REPO, OUTPUTS_REPO

token_path = "${HOME}/.conda-smithy/conda-forge_staged-recipes_feedstock.token"
with open(os.path.expandvars(token_path), "r") as fp:
    sr_token = fp.read().strip()

headers = {
    "FEEDSTOCK_TOKEN": sr_token,
}


def _run_git_command(*args):
    subprocess.run(
        " ".join(["git"] + list(args)),
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
                    _run_git_command(
                        "commit",
                        "-m",
                        "'removed %s for testing'" % file_to_remove,
                    )
                    _run_git_command("pull", "--rebase", "--commit")
                    _run_git_command("push")


def test_feedstock_tokens_register_works():
    file_to_remove = "tokens/cf-autotick-bot-test-package.json"
    _clone_and_remove(TOKENS_REPO, file_to_remove)

    r = requests.post(
        "http://127.0.0.1:5000/feedstock-tokens/register",
        headers=headers,
        json={
            "feedstock": "cf-autotick-bot-test-package-feedstock",
        },
    )
    assert r.status_code == 200


def test_feedstock_tokens_register_token_exists():
    r = requests.post(
        "http://127.0.0.1:5000/feedstock-tokens/register",
        headers=headers,
        json={
            "feedstock": "cf-autotick-bot-test-package-feedstock",
        },
    )
    assert r.status_code == 200


def test_feedstock_tokens_register_not_a_feedstock():
    r = requests.post(
        "http://127.0.0.1:5000/feedstock-tokens/register",
        headers=headers,
        json={
            "feedstock": "cf-autotick-bot-test-packageeeee-feedstock",
        },
    )
    assert r.status_code == 403


def test_feedstock_tokens_register_no_header():
    r = requests.post(
        "http://127.0.0.1:5000/feedstock-tokens/register",
        json={
            "feedstock": "cf-autotick-bot-test-package-feedstock",
        },
    )
    assert r.status_code == 403


def test_feedstock_outputs_validate():
    r = requests.post(
        "http://127.0.0.1:5000/feedstock-outputs/validate",
        json={
            "feedstock": "cf-autotick-bot-test-package-feedstock",
            "outputs": ["noarch/cf-autotick-bot-test-package-0.1-py_11.tar.bz2"],
        },
    )

    assert r.status_code == 200, r.status_code
    assert r.json() == {
        "noarch/cf-autotick-bot-test-package-0.1-py_11.tar.bz2": True}, r.json()


def test_feedstock_outputs_validate_badoutput():
    r = requests.post(
        "http://127.0.0.1:5000/feedstock-outputs/validate",
        json={
            "feedstock": "cf-autotick-bot-test-package-feedstock",
            "outputs": [
                "noarch/cf-autotick-bot-test-package-0.1-py_11.tar.bz2",
                "noarch/python-0.1-py_10.tar.bz2"
            ],
        },
    )

    assert r.status_code == 403, r.status_code
    assert r.json() == {
        "noarch/cf-autotick-bot-test-package-0.1-py_11.tar.bz2": True,
        "noarch/python-0.1-py_10.tar.bz2": False}, r.json()


def test_feedstock_outputs_copy_bad_token():
    r = requests.post(
        "http://127.0.0.1:5000/feedstock-outputs/copy",
        headers=headers,  # this has the staged recipes token
        json={
            "feedstock": "cf-autotick-bot-test-package-feedstock",
            "outputs": {},
            "channel": "main",
        },
    )

    assert r.status_code == 403, r.status_code


def test_feedstock_outputs_copy_missing_token():
    r = requests.post(
        "http://127.0.0.1:5000/feedstock-outputs/copy",
        json={
            "feedstock": "staged-recipes",
            "outputs": {},
            "channel": "main",
        },
    )

    assert r.status_code == 403, r.status_code


@pytest.mark.parametrize('key', ["outputs", "feedstock", "channel"])
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


def test_feedstock_outputs_copy_bad_data():
    name = "blah_h" + uuid.uuid4().hex
    try:
        _clone_and_remove(OUTPUTS_REPO, "outputs/%s.json" % name)

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
        _clone_and_remove(OUTPUTS_REPO, "outputs/%s.json" % name)
