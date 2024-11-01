import os

import pytest

from ..automerge import _circle_is_active, pushd


@pytest.mark.parametrize(
    "fname", ["fast_finish_ci_pr_build.sh", "checkout_merge_commit.sh"]
)
def test_circle_is_active_file(tmpdir, fname):
    with pushd(tmpdir):
        os.makedirs(os.path.join(tmpdir, ".circleci"))
        with open(os.path.join(tmpdir, ".circleci", fname), "w") as fp:
            fp.write("dummy")
        assert _circle_is_active()


@pytest.mark.parametrize(
    "txt,val",
    [
        (
            """\
  filters:
    branches:
      ignore:
        - /.*/
""",
            False,
        ),
        (
            """\
   filters:

     branches:
       ignore:
         - /.*/
""",
            True,
        ),
        (
            """\
     branches:
       ignore:
         - /.*/
""",
            True,
        ),
    ],
)
def test_circle_is_active_config(tmpdir, txt, val):
    with pushd(tmpdir):
        os.makedirs(os.path.join(tmpdir, ".circleci"))
        with open(os.path.join(tmpdir, ".circleci", "config.yml"), "w") as fp:
            fp.write(txt)
        if val:
            assert _circle_is_active()
        else:
            assert not _circle_is_active()
