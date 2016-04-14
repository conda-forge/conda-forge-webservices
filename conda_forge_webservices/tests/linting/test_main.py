from __future__ import print_function

import subprocess
import sys
import unittest

from conda_smithy.github import gh_token


class TestCLI_recipe_lint(unittest.TestCase):
    def test_cli_success(self):
        env = {'GH_TOKEN': gh_token()}
        child = subprocess.Popen([sys.executable, '-m' 'conda_forge_webservices.linting',
                                  'conda-forge/conda-forge-webservices', '17', '--enable-commenting'],
                                 stdout=subprocess.PIPE, env=env)
        out, _ = child.communicate()
        self.assertEqual(child.returncode, 0, out)


if __name__ == '__main__':
    unittest.main()
