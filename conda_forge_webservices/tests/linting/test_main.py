import os
import subprocess
import sys


def test_cli_skip_ci(skip_if_no_tokens, skip_if_linting_via_gha):
    child = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "conda_forge_webservices.linting",
            "conda-forge/conda-forge-webservices",
            "58",
            "--enable-commenting",
        ],
        stdout=subprocess.PIPE,
        env=os.environ,
    )
    out, _ = child.communicate()
    assert child.returncode == 0, out


def test_cli_success_bad(skip_if_no_tokens, skip_if_linting_via_gha):
    child = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "conda_forge_webservices.linting",
            "conda-forge/conda-forge-webservices",
            "17",
            "--enable-commenting",
        ],
        stdout=subprocess.PIPE,
        env=os.environ,
    )
    out, _ = child.communicate()
    assert child.returncode == 0, out


def test_cli_success_ok_above_ignored_good(skip_if_no_tokens, skip_if_linting_via_gha):
    child = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "conda_forge_webservices.linting",
            "conda-forge/conda-forge-webservices",
            "54",
            "--enable-commenting",
            "--ignore-base",
        ],
        stdout=subprocess.PIPE,
        env=os.environ,
    )
    out, _ = child.communicate()
    assert child.returncode == 0, out


def test_cli_success_ok_beside_ignored_good(skip_if_no_tokens, skip_if_linting_via_gha):
    child = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "conda_forge_webservices.linting",
            "conda-forge/conda-forge-webservices",
            "62",
            "--enable-commenting",
            "--ignore-base",
        ],
        stdout=subprocess.PIPE,
        env=os.environ,
    )
    out, _ = child.communicate()
    assert child.returncode == 0, out


def test_cli_success_conflict_ok(skip_if_no_tokens, skip_if_linting_via_gha):
    child = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "conda_forge_webservices.linting",
            "conda-forge/conda-forge-webservices",
            "56",
            "--enable-commenting",
        ],
        stdout=subprocess.PIPE,
        env=os.environ,
    )
    out, _ = child.communicate()
    assert child.returncode == 0, out


def test_cli_success_conflict_2_ok(skip_if_no_tokens, skip_if_linting_via_gha):
    child = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "conda_forge_webservices.linting",
            "conda-forge/conda-forge-webservices",
            "57",
            "--enable-commenting",
        ],
        stdout=subprocess.PIPE,
        env=os.environ,
    )
    out, _ = child.communicate()
    assert child.returncode == 0, out


def test_cli_success_good(skip_if_no_tokens, skip_if_linting_via_gha):
    child = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "conda_forge_webservices.linting",
            "conda-forge/conda-forge-webservices",
            "16",
            "--enable-commenting",
        ],
        stdout=subprocess.PIPE,
        env=os.environ,
    )
    out, _ = child.communicate()
    assert child.returncode == 0, out


def test_cli_success_no_recipe(skip_if_no_tokens, skip_if_linting_via_gha):
    child = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "conda_forge_webservices.linting",
            "conda-forge/conda-forge-webservices",
            "523",
            "--enable-commenting",
        ],
        stdout=subprocess.PIPE,
        env=os.environ,
    )
    out, _ = child.communicate()
    assert child.returncode == 0, out


def test_cli_success_v1_recipe(skip_if_no_tokens, skip_if_linting_via_gha):
    child = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "conda_forge_webservices.linting",
            "conda-forge/conda-forge-webservices",
            "632",
            "--enable-commenting",
        ],
        stdout=subprocess.PIPE,
        env=os.environ,
    )
    out, _ = child.communicate()
    assert child.returncode == 0, out
