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

from conda_forge_webservices.utils import pushd

OUTPUTS_REPO = "https://${GH_TOKEN}@github.com/conda-forge/feedstock-outputs.git"

token_path = "${HOME}/.conda-smithy/conda-forge_staged-recipes.token"
with open(os.path.expandvars(token_path), "r") as fp:
    sr_token = fp.read().strip()

headers = {
    "FEEDSTOCK_TOKEN": sr_token,
}


appveyor_token_path = "${HOME}/.conda-smithy/conda-forge_appveyor-is-ok.token"
with open(os.path.expandvars(appveyor_token_path), "r") as fp:
    app_token = fp.read().strip()

appveyor_headers = {
    "FEEDSTOCK_TOKEN": app_token,
}

GH = github.Github(os.environ["GH_TOKEN"])


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


def test_feedstock_outputs_copy_bad_token():
    repo = GH.get_repo("conda-forge/cf-autotick-bot-test-package-feedstock")
    sha = repo.get_branch("master").commit.commit.sha
    r = requests.post(
        "http://127.0.0.1:5000/feedstock-outputs/copy",
        headers=headers,  # this has the staged recipes token
        json={
            "feedstock": "cf-autotick-bot-test-package-feedstock",
            "outputs": {},
            "channel": "main",
            "git_sha": sha,
        },
    )

    assert r.status_code == 403, r.status_code


def test_feedstock_outputs_copy_appveyor_ok_list():
    r = requests.post(
        "http://127.0.0.1:5000/feedstock-outputs/copy",
        headers=appveyor_headers,
        json={
            "feedstock": "python-feedstock",
            "outputs": {},
            "channel": "main",
        },
    )

    assert r.status_code == 200, r.status_code


def test_feedstock_outputs_copy_appveyor_ok_list_badtoken():
    r = requests.post(
        "http://127.0.0.1:5000/feedstock-outputs/copy",
        headers=headers,  # this has the staged recipes token
        json={
            "feedstock": "python-feedstock",
            "outputs": {},
            "channel": "main",
        },
    )

    assert r.status_code == 403, r.status_code


def test_feedstock_outputs_copy_appveyor_ok_list_notonlist():
    r = requests.post(
        "http://127.0.0.1:5000/feedstock-outputs/copy",
        headers=appveyor_headers,
        json={
            "feedstock": "blah-feedstock",
            "outputs": {},
            "channel": "main",
        },
    )

    assert r.status_code == 403, r.status_code


def test_feedstock_outputs_copy_appveyor_ok_list_badout():
    r = requests.post(
        "http://127.0.0.1:5000/feedstock-outputs/copy",
        headers=appveyor_headers,
        json={
            "feedstock": "python-feedstock",
            "outputs": {
                "noarch/cf-autotick-bot-test-package-0.1-py_11.tar.bz2": "ababa",
            },
            "channel": "main",
        },
    )

    assert r.status_code == 403, r.status_code

    info = r.json()
    assert any("win-64-only" in e for e in info["errors"])


def test_feedstock_outputs_copy_appveyor_ok_list_badout_python():
    r = requests.post(
        "http://127.0.0.1:5000/feedstock-outputs/copy",
        headers=appveyor_headers,
        json={
            "feedstock": "python-feedstock",
            "outputs": {
                "win-64/cf-autotick-bot-test-package-0.1-py_11.tar.bz2": "ababa",
            },
            "channel": "main",
        },
    )

    assert r.status_code == 403, r.status_code

    info = r.json()
    assert any(
        "not allowed for conda-forge/python-feedstock" in e for e in info["errors"])


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
