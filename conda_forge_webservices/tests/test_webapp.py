import json
import unittest
try:
    from urllib.parse import urlencode
    import unittest.mock as mock
except ImportError:
    from urllib import urlencode
    import mock

from tornado.testing import AsyncHTTPTestCase

from conda_forge_webservices.webapp import create_webapp
from conda_forge_webservices.linting import compute_lint_message

class TestHandlerBase(AsyncHTTPTestCase):
    def get_app(self):
        return create_webapp()


class TestBucketHandler(TestHandlerBase):
    def test_bad_header(self):
        response = self.fetch('/conda-linting/org-hook', method='POST',
                              body=urlencode({'a': 1}))
        self.assertEqual(response.code, 404)

    @mock.patch('conda_forge_webservices.linting.compute_lint_message', return_value={'message': mock.sentinel.message})
    @mock.patch('conda_forge_webservices.linting.comment_on_pr', return_value=mock.MagicMock(html_url=mock.sentinel.html_url))
    @mock.patch('conda_forge_webservices.linting.set_pr_status')
    def test_good_header(self, set_pr_status, comment_on_pr, compute_lint_message):
        PR_number = 16
        body = {'repository': {'name': 'repo_name-feedstock',
                               'full_name': 'conda-forge/repo_name-feedstock',
                               'clone_url': 'repo_clone_url',
                               'owner': {'login': 'conda-forge'}},
                'pull_request': {'number': PR_number,
                                 'state': 'open',
                                 'labels': [{'name': 'stale'}]}}

        response = self.fetch('/conda-linting/org-hook', method='POST',
                              body=json.dumps(body),
                              headers={'X-GitHub-Event': 'pull_request'})

        self.assertEqual(response.code, 200)

        compute_lint_message.assert_called_once_with('conda-forge', 'repo_name-feedstock',
                                                     PR_number, False)

        comment_on_pr.assert_called_once_with('conda-forge', 'repo_name-feedstock',
                                              PR_number, mock.sentinel.message,
                                              search='conda-forge-linting service')

        set_pr_status.assert_called_once_with('conda-forge', 'repo_name-feedstock',
                                              {'message': mock.sentinel.message},
                                              target_url=mock.sentinel.html_url)

    @mock.patch('conda_forge_webservices.linting.compute_lint_message', return_value=None)
    @mock.patch('conda_forge_webservices.linting.set_pr_status', return_value=None)
    @mock.patch('conda_forge_webservices.linting.comment_on_pr', return_value=None)
    @mock.patch('conda_forge_webservices.feedstocks_service.update_listing', return_value=None)
    @mock.patch('conda_forge_webservices.feedstocks_service.update_feedstock', return_value=None)
    @mock.patch('conda_forge_webservices.commands.pr_detailed_comment', return_value=None)
    @mock.patch('conda_forge_webservices.commands.pr_comment', return_value=None)
    @mock.patch('conda_forge_webservices.commands.issue_comment', return_value=None)
    @mock.patch('conda_forge_webservices.update_teams.update_team', return_value=None)
    @mock.patch('conda_forge_webservices.webapp.print_rate_limiting_info', return_value=None)
    def test_accept_repos(self, *methods):
        for hook, accepted_repos, accepted_events in [
            ("/conda-linting/org-hook", ["staged-recipes", "repo-feedstock"], ["pull_request"]),
            ("/conda-forge-feedstocks/org-hook", ["staged-recipes", "repo-feedstock", "conda-forge.github.io"], ["push"]),
            ("/conda-forge-teams/org-hook", ["repo-feedstock"], ["push"]),
            ("/conda-forge-command/org-hook", ["staged-recipes", "repo-feedstock"], ["pull_request", "issues"]),
        ]:
            test_slugs = [
                "conda-forge/repo-feedstock",
                "conda-forge/staged-recipes",
                "conda-forge/conda-smithy",
                "dummy/repo-feedstock",
                "dummy/staged-recipes",
            ]

            for slug in test_slugs:
                owner, name = slug.split("/")
                body = {
                    'repository': {
                        'name': name,
                        'full_name': '%s/%s' % (owner, name),
                        'clone_url': 'repo_clone_url',
                        'owner': {'login': owner},
                    },
                    'pull_request': {
                        'number': 16,
                        'state': 'open',
                        'labels': [{'name': 'stale'}],
                        'head': {
                            'repo': {
                                'name': "pr_repo_name",
                                'owner': {'login': 'pr_repo_owner'},
                            },
                            'ref': 'ref',
                        },
                        'body': 'body',
                    },
                    'issue': {
                        'number': 16,
                        'body': 'body',
                        'title': 'title',
                    },
                    'action': 'opened',
                    'ref': 'refs/heads/master',

                }

                for event in ["pull_request", "issues", "push"]:
                    response = self.fetch(hook, method='POST',
                                  body=json.dumps(body),
                                  headers={'X-GitHub-Event': event})

                    if owner == "conda-forge" and name in accepted_repos and event in accepted_events:
                        self.assertEqual(response.code, 200, msg=f"event: {event}, slug: {slug}, hook: {hook}")
                    else:
                        self.assertNotEqual(response.code, 200, msg=f"event: {event}, slug: {slug}, hook: {hook}")


    @mock.patch('conda_forge_webservices.linting.compute_lint_message', return_value={'message': mock.sentinel.message})
    @mock.patch('conda_forge_webservices.linting.comment_on_pr', return_value=mock.MagicMock(html_url=mock.sentinel.html_url))
    @mock.patch('conda_forge_webservices.linting.set_pr_status')
    def test_staged_recipes(self, set_pr_status, comment_on_pr, compute_lint_message):
        PR_number = 16
        body = {'repository': {'name': 'staged-recipes',
                               'full_name': 'conda-forge/staged-recipes',
                               'clone_url': 'repo_clone_url',
                               'owner': {'login': 'conda-forge'}},
                'pull_request': {'number': PR_number,
                                 'state': 'open',
                                 'labels': [{'name': 'blah'}]}}

        response = self.fetch('/conda-linting/org-hook', method='POST',
                              body=json.dumps(body),
                              headers={'X-GitHub-Event': 'pull_request'})

        self.assertEqual(response.code, 200)
        compute_lint_message.assert_called_once_with('conda-forge', 'staged-recipes',
                                                     PR_number, True)

        comment_on_pr.assert_called_once_with('conda-forge', 'staged-recipes',
                                              PR_number, mock.sentinel.message,
                                              search='conda-forge-linting service')

        set_pr_status.assert_called_once_with('conda-forge', 'staged-recipes',
                                              {'message': mock.sentinel.message},
                                              target_url=mock.sentinel.html_url)

    @mock.patch('conda_forge_webservices.linting.compute_lint_message', return_value={'message': mock.sentinel.message})
    @mock.patch('conda_forge_webservices.linting.comment_on_pr', return_value=mock.MagicMock(html_url=mock.sentinel.html_url))
    @mock.patch('conda_forge_webservices.linting.set_pr_status')
    def test_staged_recipes_stale(self, set_pr_status, comment_on_pr, compute_lint_message):
        PR_number = 16
        body = {'repository': {'name': 'staged-recipes',
                               'full_name': 'conda-forge/staged-recipes',
                               'clone_url': 'repo_clone_url',
                               'owner': {'login': 'conda-forge'}},
                'pull_request': {'number': PR_number,
                                 'state': 'open',
                                 'labels': [{'name': 'stale'}]}}

        response = self.fetch('/conda-linting/org-hook', method='POST',
                              body=json.dumps(body),
                              headers={'X-GitHub-Event': 'pull_request'})

        self.assertEqual(response.code, 200)
        compute_lint_message.assert_not_called()

        comment_on_pr.assert_not_called()

        set_pr_status.assert_not_called()
