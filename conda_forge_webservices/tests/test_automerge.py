import os
import unittest
import tempfile
import contextlib
from parameterized import parameterized

from ruamel.yaml import YAML

from git import Repo

from ..commands import add_bot_automerge


# from https://stackoverflow.com/questions/6194499/pushd-through-os-system
@contextlib.contextmanager
def pushd(new_dir):
    previous_dir = os.getcwd()
    os.chdir(new_dir)
    try:
        yield
    finally:
        os.chdir(previous_dir)


class TestAutomerge(unittest.TestCase):
    def test_automerge_already_on(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with pushd(tmpdir):
                repo_pth = os.path.join(tmpdir, "myrepo-feedstock")
                repo = Repo.init(repo_pth)

                cfg = {'bot': {'automerge': True}}
                cfg_pth = os.path.join(repo_pth, 'conda-forge.yml')
                with open(cfg_pth, "w") as fp:
                    yaml = YAML()
                    yaml.dump(cfg, fp)

                assert not add_bot_automerge(repo)

    @parameterized.expand([
        ("empty", {},),
        ("off", {'travis': 'blah', 'bot': {'automerge': False}},)])
    def test_automerge_add(self, _, cfg):
        yaml = YAML()

        with tempfile.TemporaryDirectory() as tmpdir:
            with pushd(tmpdir):
                repo_pth = os.path.join(tmpdir, "myrepo-feedstock")
                repo = Repo.init(repo_pth)
                with pushd(repo_pth):
                    cfg_pth = os.path.join(repo_pth, 'conda-forge.yml')

                    if cfg:
                        with open(cfg_pth, "w") as fp:
                            yaml.dump(cfg, fp)

                    # ensure it says to commit
                    assert add_bot_automerge(repo)

                    with open(cfg_pth, 'r') as fp:
                        _cfg = yaml.load(fp)

                    # make sure automerge is on
                    assert _cfg['bot']['automerge']

                    # make sure of we had keys they are still there
                    if cfg:
                        assert cfg['travis'] == 'blah'

                    # make sure have the config
                    main_yml = os.path.join(
                        repo_pth,
                        '.github',
                        'workflows',
                        'main.yml',
                    )
                    assert os.path.exists(main_yml)

                    # make sure the commit is correct
                    cmt_msg = repo.head.ref.commit.message
                    assert '[ci skip] ***NO_CI*** added bot automerge' in cmt_msg

                    # make sure both the conda-forge.yml and the main.yml are
                    # tracked
                    assert not repo.is_dirty()
