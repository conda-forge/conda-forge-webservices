import os
import unittest

try:
    import unittest.mock as mock
except ImportError:
    import mock

from conda_forge_webservices.commands import (
    pr_detailed_comment as _pr_detailed_comment,
    issue_comment as _issue_comment)


def pr_detailed_comment(comment, org_name='conda-forge',
                        repo_name='python-feedstock', pr_repo=None,
                        pr_owner='some-user', pr_branch='master', pr_num=1):
    if pr_repo is None:
        pr_repo = repo_name
    return _pr_detailed_comment(org_name, repo_name,
                                pr_owner, pr_repo, pr_branch, pr_num, comment)


def issue_comment(title, comment, issue_num=1,
                  org_name='conda-forge', repo_name='python-feedstock'):
    return _issue_comment(org_name, repo_name, issue_num, title, comment)


class TestCommands(unittest.TestCase):
    def setUp(self):
        if 'GH_TOKEN' not in os.environ:
            os.environ['GH_TOKEN'] = 'fake'  # github access is mocked anyway
            self.kill_token = True
        else:
            self.kill_token = False

    def tearDown(self):
        if self.kill_token:
            del os.environ['GH_TOKEN']

    @mock.patch('conda_forge_webservices.commands.rerender')
    @mock.patch('conda_forge_webservices.commands.make_noarch')
    @mock.patch('conda_forge_webservices.commands.relint')
    @mock.patch('conda_forge_webservices.commands.update_team')
    @mock.patch('conda_forge_webservices.commands.update_circle')
    @mock.patch('conda_forge_webservices.commands.tmp_directory')
    @mock.patch('github.Github')
    @mock.patch('conda_forge_webservices.commands.Repo')
    def test_pr_command_triggers(
            self, repo, gh, tmp_directory, update_circle,
            update_team, relint, make_noarch, rerender):
        tmp_directory.return_value.__enter__.return_value = '/tmp'

        commands = [
            (rerender, False, [
                '@conda-forge-admin, please rerender',
                '@conda-forge-admin, please re-render',
                '@conda-forge-admin: PLEASE RERENDER',
                'something something. @conda-forge-admin: please re-render',
             ], [
                '@conda-forge admin is pretty cool. please rerender for me?',
                '@conda-forge-admin, go ahead and rerender for me',
                'please re-render, @conda-forge-admin',
                '@conda-forge-linter, please lint',
             ]),
            (make_noarch, False, [
                '@conda-forge-admin, please add noarch python',
                '@conda-forge-linter, please lint, and @conda-forge-admin, please make `noarch: python`',
                '@CONDA-FORGE-ADMIN please add `noarch python`',
                'hey @conda-forge-admin : please make noarch: python',
             ], [
                '@conda-forge-linter, please lint',
                'sure wish @conda-forge-admin would please add noarch python',
             ]),
            (relint, True, [
                '@conda-forge-admin, please lint',
                '@CONDA-FORGE-LINTER, please relint',
                'hey @conda-forge-linter please re-lint!',
             ], [
                '@conda-forge-admin should probably lint again',
             ]),
        ]

        for command, on_sr, should, should_not in commands:
            for msg in should:
                command.reset_mock()
                print(msg, end=' ' * 30 + '\r')
                pr_detailed_comment(msg)
                command.assert_called()

                command.reset_mock()
                print(msg, end=' ' * 30 + '\r')
                pr_detailed_comment(msg, repo_name='staged-recipes')
                if on_sr:
                    command.assert_called()
                else:
                    command.assert_not_called()

            for msg in should_not:
                command.reset_mock()
                print(msg, end=' ' * 30 + '\r')
                pr_detailed_comment(msg)
                command.assert_not_called()

    @mock.patch('conda_forge_webservices.commands.rerender')
    @mock.patch('conda_forge_webservices.commands.make_noarch')
    @mock.patch('conda_forge_webservices.commands.relint')
    @mock.patch('conda_forge_webservices.commands.update_team')
    @mock.patch('conda_forge_webservices.commands.update_circle')
    @mock.patch('conda_forge_webservices.commands.tmp_directory')
    @mock.patch('github.Github')
    @mock.patch('conda_forge_webservices.commands.Repo')
    def test_issue_command_triggers(
            self, repo, gh, tmp_directory, update_circle,
            update_team, relint, make_noarch, rerender):
        tmp_directory.return_value.__enter__.return_value = '/tmp'

        commands = [
            (make_noarch, [
                '@conda-forge-admin, please add noarch python',
                '@conda-forge-admin, please make `noarch: python`',
                '@conda-forge-admin please add `noarch python`',
                'hey @conda-forge-admin : please make noarch: python',
             ], [
                '@conda-forge-linter, please lint',
                'sure wish @conda-forge-admin would please add noarch python',
             ]),
            (update_team, [
                '@conda-forge-admin: please update team',
                '@conda-forge-admin, please update the team',
                '@conda-forge-admin, please refresh team',
             ], [
                '@conda-forge-admin please make noarch: python',
                '@conda-forge-linter, please lint. and can someone refresh the team?',
             ]),
            (update_circle, [
                '@conda-forge-admin, please update circle',
                'hey @conda-forge-admin, PLEASE update circle',
                '@conda-forge-admin: please refresh the circle key',
             ], [
                '@conda-forge-admin, please lint',
             ]),
        ]

        for command, should, should_not in commands:
            issue = gh.return_value.get_repo.return_value.get_issue.return_value
            for msg in should:
                print(msg, end=' ' * 30 + '\r')

                command.reset_mock()
                issue.reset_mock()
                issue_comment(title="hi", comment=msg)
                command.assert_called()
                issue.edit.assert_not_called()

                command.reset_mock()
                issue.reset_mock()
                issue_comment(title=msg, comment="As in title")
                command.assert_called()
                issue.edit.assert_called_with(state="closed")

                command.reset_mock()
                print(msg, end=' ' * 30 + '\r')
                issue_comment(msg, msg, repo_name='staged-recipes')
                command.assert_not_called()

            for msg in should_not:
                print(msg, end=' ' * 30 + '\r')

                command.reset_mock()
                issue.reset_mock()
                issue_comment(title="hi", comment=msg)
                command.assert_not_called()
                issue.edit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
