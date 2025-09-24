import os
from pathlib import Path

import pytest
from git import Repo
from conda_forge_webservices.commands import add_user, remove_user


@pytest.fixture
def pillow_feedstock(tmp_path):
    yield Repo.clone_from(
        "https://github.com/conda-forge/pillow-feedstock.git",
        tmp_path,
        depth=1,
    )


def _read_codeowners(repo):
    return Path(repo.working_dir, ".github", "CODEOWNERS").read_text().split()


def test_add_and_remove_user(pillow_feedstock):
    assert remove_user(pillow_feedstock, "doesnotexist") is False
    assert "@doesnotexist" not in _read_codeowners(pillow_feedstock)

    assert add_user(pillow_feedstock, "doesnotexist") is True
    assert "@doesnotexist" in _read_codeowners(pillow_feedstock)

    assert remove_user(pillow_feedstock, "doesnotexist") is True
    assert "@doesnotexist" not in _read_codeowners(pillow_feedstock)

    os.rename(
        os.path.join(pillow_feedstock.working_dir, "recipe"),
        os.path.join(pillow_feedstock.working_dir, "recipe-moved"),
    )
    assert remove_user(pillow_feedstock, "doesnotexist") is None
    assert "@doesnotexist" not in _read_codeowners(pillow_feedstock)
