from __future__ import print_function
from contextlib import contextmanager
import shutil
import tempfile
import textwrap
import unittest

from conda_forge_webservices.linting import compute_lint_message


@contextmanager
def tmp_directory():
    tmp_dir = tempfile.mkdtemp('recipe_')
    yield tmp_dir
    shutil.rmtree(tmp_dir)


class Test_compute_lint_message(unittest.TestCase):
    def test_skip_ci_recipe(self):
        lint = compute_lint_message('conda-forge', 'conda-forge-webservices', 58)
        self.assertFalse(lint)

    def test_skip_lint_recipe(self):
        lint = compute_lint_message('conda-forge', 'conda-forge-webservices', 59)
        self.assertFalse(lint)

    def test_ci_skip_recipe(self):
        lint = compute_lint_message('conda-forge', 'conda-forge-webservices', 65)
        self.assertFalse(lint)

    def test_lint_skip_recipe(self):
        lint = compute_lint_message('conda-forge', 'conda-forge-webservices', 66)
        self.assertFalse(lint)

    def test_good_recipe(self):
        # a message similar to this comes out
        """
        Hi! This is the friendly automated conda-forge-linting service.

        I just wanted to let you know that I linted all conda-recipes in your PR (```recipes/good_recipe```) and found it was in an excellent condition.

        """  # noqa

        lint = compute_lint_message('conda-forge', 'conda-forge-webservices', 16)
        self.assertTrue(lint)
        self.assertTrue(
            "found it was in an excellent condition." in lint['message'])

    def test_ok_recipe_above_good_recipe(self):
        expected_message = textwrap.dedent("""
        Hi! This is the friendly automated conda-forge-linting service.

        I just wanted to let you know that I linted all conda-recipes in your PR (```recipe```, ```recipes/recipe```) and found it was in an excellent condition.

        """)  # noqa

        lint = compute_lint_message('conda-forge', 'conda-forge-webservices', 54)
        self.assertMultiLineEqual(expected_message, lint['message'])

    def test_ok_recipe_beside_good_recipe(self):
        expected_message = textwrap.dedent("""
        Hi! This is the friendly automated conda-forge-linting service.

        I just wanted to let you know that I linted all conda-recipes in your PR (```recipe```, ```recipes/recipe```) and found it was in an excellent condition.

        """)  # noqa

        lint = compute_lint_message('conda-forge', 'conda-forge-webservices', 62)
        self.assertMultiLineEqual(expected_message, lint['message'])

    def test_ok_recipe_above_ignored_good_recipe(self):
        expected_message = textwrap.dedent("""
        Hi! This is the friendly automated conda-forge-linting service.

        I just wanted to let you know that I linted all conda-recipes in your PR (```recipe```) and found it was in an excellent condition.

        """)  # noqa

        lint = compute_lint_message('conda-forge', 'conda-forge-webservices', 54, True)
        self.assertMultiLineEqual(expected_message, lint['message'])

    def test_ok_recipe_beside_ignored_good_recipe(self):
        expected_message = textwrap.dedent("""
        Hi! This is the friendly automated conda-forge-linting service.

        I just wanted to let you know that I linted all conda-recipes in your PR (```recipe```) and found it was in an excellent condition.

        """)  # noqa

        lint = compute_lint_message('conda-forge', 'conda-forge-webservices', 62, True)
        self.assertMultiLineEqual(expected_message, lint['message'])

    def test_conflict_ok_recipe(self):
        expected_message = textwrap.dedent("""
        Hi! This is the friendly automated conda-forge-linting service.

        I was trying to look for recipes to lint for you, but it appears we have a merge conflict.
        Please try to merge or rebase with the base branch to resolve this conflict.

        Please ping the 'conda-forge/core' team (using the @ notation in a comment) if you believe this is a bug.
        """)  # noqa

        lint = compute_lint_message('conda-forge', 'conda-forge-webservices', 56)
        self.assertTrue(lint)
        self.assertMultiLineEqual(expected_message, lint['message'])

    def test_conflict_2_ok_recipe(self):
        expected_message = textwrap.dedent("""
        Hi! This is the friendly automated conda-forge-linting service.

        I was trying to look for recipes to lint for you, but it appears we have a merge conflict.
        Please try to merge or rebase with the base branch to resolve this conflict.

        Please ping the 'conda-forge/core' team (using the @ notation in a comment) if you believe this is a bug.
        """)  # noqa

        lint = compute_lint_message('conda-forge', 'conda-forge-webservices', 57)
        self.assertTrue(lint)
        self.assertMultiLineEqual(expected_message, lint['message'])

    def test_bad_recipe(self):
        # a message similar to this comes out
        """
        Hi! This is the friendly automated conda-forge-linting service.

        I wanted to let you know that I linted all conda-recipes in your PR (```recipes/bad_recipe```) and found some lint.

        Here's what I've got...


        For **recipes/bad_recipe**:

         * The home item is expected in the about section.
         * The license item is expected in the about section.
         * The summary item is expected in the about section.
         * The recipe must have some tests.
         * The recipe must have a `build/number` section.
         * There are 2 too many lines.  There should be one empty line at the end of the file.
         * Feedstock with the same name exists in conda-forge
         * Recipe maintainer "support" does not exist
        """  # noqa

        lint = compute_lint_message('conda-forge', 'conda-forge-webservices', 17)
        self.assertTrue(lint)
        self.assertTrue("found some lint" in lint['message'])
        self.assertTrue(
            "The home item is expected in the about section." in lint['message'])
        self.assertTrue("For **recipes/bad_recipe**:" in lint["message"])

    def test_mixed_recipe(self):
        # a message similar to this comes out
        """
        Hi! This is the friendly automated conda-forge-linting service.

        I just wanted to let you know that I linted all conda-recipes in your PR (```recipes/hints_only```) and found it was in an excellent condition.


        I do have some suggestions for making it better though...


        For **recipes/hints_only**:

         * Whenever possible python packages should use pip. See https://conda-forge.org/docs/maintainer/adding_pkgs.html#use-pip
        """  # noqa

        lint = compute_lint_message('conda-forge', 'conda-forge-webservices', 217)
        self.assertTrue(lint)
        self.assertTrue(
            "I do have some suggestions for making it better though" in lint['message'])

    def test_no_recipe(self):
        expected_message = textwrap.dedent("""
        Hi! This is the friendly automated conda-forge-linting service.

        I was trying to look for recipes to lint for you, but couldn't find any.
        Please ping the 'conda-forge/core' team (using the @ notation in a comment) if you believe this is a bug.
        """)  # noqa

        lint = compute_lint_message('conda-forge', 'conda-forge-webservices', 18)
        self.assertTrue(lint)
        self.assertMultiLineEqual(expected_message, lint['message'])

    def test_closed_pr(self):
        lint = compute_lint_message('conda-forge', 'conda-forge-webservices', 52)
        self.assertFalse(lint)
        self.assertEqual(lint, {})


if __name__ == '__main__':
    unittest.main()
