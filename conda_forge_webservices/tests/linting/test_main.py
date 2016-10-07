from __future__ import print_function

import os
import subprocess
import sys
import unittest


class TestCLI_recipe_lint(unittest.TestCase):
    def test_cli_skip_ci(self):
        child = subprocess.Popen([sys.executable, '-m' 'conda_forge_webservices.linting',
                                  'conda-forge/conda-forge-webservices', '58', '--enable-commenting'],
                                 stdout=subprocess.PIPE, env=os.environ)
        out, _ = child.communicate()
        self.assertEqual(child.returncode, 0, out)

    def test_cli_success_bad(self):
        child = subprocess.Popen([sys.executable, '-m' 'conda_forge_webservices.linting',
                                  'conda-forge/conda-forge-webservices', '17', '--enable-commenting'],
                                 stdout=subprocess.PIPE, env=os.environ)
        out, _ = child.communicate()
        self.assertEqual(child.returncode, 0, out)

    def test_cli_success_ok_above_ignored_good(self):
        child = subprocess.Popen([sys.executable, '-m' 'conda_forge_webservices.linting',
                                  'conda-forge/conda-forge-webservices', '54',
                                  '--enable-commenting', '--ignore-base'],
                                 stdout=subprocess.PIPE, env=os.environ)
        out, _ = child.communicate()
        self.assertEqual(child.returncode, 0, out)

    def test_cli_success_ok_beside_ignored_good(self):
        child = subprocess.Popen([sys.executable, '-m' 'conda_forge_webservices.linting',
                                  'conda-forge/conda-forge-webservices', '62',
                                  '--enable-commenting', '--ignore-base'],
                                 stdout=subprocess.PIPE, env=os.environ)
        out, _ = child.communicate()
        self.assertEqual(child.returncode, 0, out)

    def test_cli_success_conflict_ok(self):
        child = subprocess.Popen([sys.executable, '-m' 'conda_forge_webservices.linting',
                                  'conda-forge/conda-forge-webservices', '56', '--enable-commenting'],
                                 stdout=subprocess.PIPE, env=os.environ)
        out, _ = child.communicate()
        self.assertEqual(child.returncode, 0, out)

    def test_cli_success_conflict_2_ok(self):
        child = subprocess.Popen([sys.executable, '-m' 'conda_forge_webservices.linting',
                                  'conda-forge/conda-forge-webservices', '57', '--enable-commenting'],
                                 stdout=subprocess.PIPE, env=os.environ)
        out, _ = child.communicate()
        self.assertEqual(child.returncode, 0, out)

    def test_cli_success_good(self):
        child = subprocess.Popen([sys.executable, '-m' 'conda_forge_webservices.linting',
                                  'conda-forge/conda-forge-webservices', '16', '--enable-commenting'],
                                 stdout=subprocess.PIPE, env=os.environ)
        out, _ = child.communicate()
        self.assertEqual(child.returncode, 0, out)


if __name__ == '__main__':
    unittest.main()
