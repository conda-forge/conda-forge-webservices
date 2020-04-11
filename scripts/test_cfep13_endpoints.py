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

import requests
import pytest

from conda_forge_webservices.utils import pushd
from conda_forge_webservices.feedstock_outputs import TOKENS_REPO

token_path = "${HOME}/.conda-smithy/conda-forge_staged-recipes_feedstock.token"
with open(os.path.expandvars(token_path), "r") as fp:
    sr_token = fp.read().strip()

headers = {
    "FEEDSTOCK_TOKEN": sr_token,
}


def test_feedstock_tokens_register_works():
    with tempfile.TemporaryDirectory() as tmpdir:
        with pushd(tmpdir):
            subprocess.run(
                " ".join([
                    "git",
                    "clone",
                    "--depth=1",
                    TOKENS_REPO,
                ]),
                check=True,
                shell=True,
            )

            with pushd("feedstock-tokens"):
                subprocess.run(
                    " ".join([
                        "git",
                        "remote",
                        "set-url",
                        "--push",
                        "origin",
                        TOKENS_REPO,
                    ]),
                    check=True,
                    shell=True,
                )
                subprocess.run(
                    ["git", "rm", "tokens/cf-autotick-bot-test-package.json"],
                    check=True,
                )

                subprocess.run(
                    [
                        "git",
                        "commit",
                        "-m",
                        "'removed cf-autotick-bot-test-package.json for testing'"
                    ],
                    check=True,
                )

                subprocess.run(
                    ["git", "pull"],
                    check=True,
                )

                subprocess.run(
                    ["git", "push"],
                    check=True,
                )

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
    assert r.status_code == 403


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
            "outputs": ["cf-autotick-bot-test-package"],
        },
    )

    assert r.status_code == 200, r.status_code
    assert r.json() == {"cf-autotick-bot-test-package": True}, r.json()


def test_feedstock_outputs_validate_badoutput():
    r = requests.post(
        "http://127.0.0.1:5000/feedstock-outputs/validate",
        json={
            "feedstock": "cf-autotick-bot-test-package-feedstock",
            "outputs": ["cf-autotick-bot-test-package", "python"],
        },
    )

    assert r.status_code == 403, r.status_code
    assert r.json() == {"cf-autotick-bot-test-package": True, "python": False}, r.json()


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
