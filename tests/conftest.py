import os
import subprocess

import pytest

TOKENS = [
    "GH_TOKEN",
]
MISSING_TOKENS = any(token not in os.environ for token in TOKENS)


def _merge_main_to_branch(branch, verbose=False):
    if verbose:
        print("merging main into branch...", flush=True)
    subprocess.run(["git", "checkout", "main"], check=True)
    subprocess.run(["git", "pull"], check=True)
    subprocess.run(["git", "checkout", branch], check=True)
    subprocess.run(["git", "pull"], check=True)
    subprocess.run(
        ["git", "merge", "--no-edit", "--strategy-option", "theirs", "main"],
        check=True,
    )
    subprocess.run(["git", "push"], check=True)


def pytest_addoption(parser):
    parser.addoption("--branch", action="store")


@pytest.fixture
def skip_if_no_tokens():
    if not MISSING_TOKENS:
        yield
    else:
        pytest.skip("No conda-forge-webservices app tokens available for testing!")
