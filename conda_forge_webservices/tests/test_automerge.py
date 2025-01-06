import os
import unittest
import tempfile
from parameterized import parameterized

from ruamel.yaml import YAML

from git import Repo

from ..commands import add_bot_automerge, remove_bot_automerge
from ..utils import pushd


class TestAddAutomerge(unittest.TestCase):
    def test_automerge_already_on(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with pushd(tmpdir):
                repo_pth = os.path.join(tmpdir, "myrepo-feedstock")
                repo = Repo.init(repo_pth)

                cfg = {"bot": {"automerge": True}}
                cfg_pth = os.path.join(repo_pth, "conda-forge.yml")
                with open(cfg_pth, "w") as fp:
                    yaml = YAML(typ="safe")
                    yaml.dump(cfg, fp)

                assert not add_bot_automerge(repo)

    @parameterized.expand(
        [
            (
                "empty",
                {},
            ),
            (
                "off",
                {"travis": "blah", "bot": {"automerge": False}},
            ),
        ]
    )
    def test_automerge_add(self, _, cfg):
        yaml = YAML(typ="safe")

        with tempfile.TemporaryDirectory() as tmpdir:
            with pushd(tmpdir):
                repo_pth = os.path.join(tmpdir, "myrepo-feedstock")
                repo = Repo.init(repo_pth)
                with pushd(repo_pth):
                    cfg_pth = os.path.join(repo_pth, "conda-forge.yml")

                    if cfg:
                        with open(cfg_pth, "w") as fp:
                            yaml.dump(cfg, fp)

                    # ensure it says to commit
                    assert add_bot_automerge(repo)

                    with open(cfg_pth) as fp:
                        am_cfg = yaml.load(fp)

                    # make sure automerge is on
                    assert am_cfg["bot"]["automerge"]

                    # make sure of we had keys they are still there
                    if cfg:
                        assert cfg["travis"] == "blah"

                    # make sure the commit is correct
                    cmt_msg = repo.head.ref.commit.message
                    assert (
                        "[ci skip] [cf admin skip] ***NO_CI*** added bot automerge"
                        in cmt_msg
                    )
                    # make sure both the conda-forge.yml and the main.yml are
                    # tracked
                    assert not repo.is_dirty()


class TestRemoveAutomerge(unittest.TestCase):
    @parameterized.expand(
        [
            (
                "empty",
                {},
            ),
            (
                "off",
                {"travis": "blah", "bot": {"automerge": False}},
            ),
        ]
    )
    def test_automerge_already_off(self, _, cfg):
        with tempfile.TemporaryDirectory() as tmpdir:
            with pushd(tmpdir):
                repo_pth = os.path.join(tmpdir, "myrepo-feedstock")
                repo = Repo.init(repo_pth)

                cfg_pth = os.path.join(repo_pth, "conda-forge.yml")
                with open(cfg_pth, "w") as fp:
                    yaml = YAML(typ="safe")
                    yaml.dump(cfg, fp)

                assert not remove_bot_automerge(repo)

    @parameterized.expand(
        [
            (
                "on-and-other-bot-key",
                dict(travis="blah", bot=dict(automerge=True, x=5)),
            ),
            ("on-and-only-bot-key", dict(travis="blah", bot=dict(automerge=True))),
        ]
    )
    def test_automerge_remove(self, _, cfg):
        yaml = YAML(typ="safe")

        with tempfile.TemporaryDirectory() as tmpdir:
            with pushd(tmpdir):
                repo_pth = os.path.join(tmpdir, "myrepo-feedstock")
                repo = Repo.init(repo_pth)
                with pushd(repo_pth):
                    cfg_pth = os.path.join(repo_pth, "conda-forge.yml")

                    if cfg:
                        with open(cfg_pth, "w") as fp:
                            yaml.dump(cfg, fp)

                    # ensure it says to commit
                    assert remove_bot_automerge(repo)

                    with open(cfg_pth) as fp:
                        am_cfg = yaml.load(fp)

                    # make sure automerge is off
                    current_automerge_value = am_cfg.get("bot", {}).get(
                        "automerge", False
                    )
                    assert not current_automerge_value

                    # make other keys are still there
                    assert cfg["travis"] == "blah"

                    # make sure the commit is correct
                    cmt_msg = repo.head.ref.commit.message
                    assert (
                        "[ci skip] [cf admin skip] ***NO_CI*** removed bot automerge"
                        in cmt_msg
                    )

                    # make sure both the conda-forge.yml and the main.yml are
                    # tracked
                    assert not repo.is_dirty()
