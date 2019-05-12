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


class TestHandlerBase(AsyncHTTPTestCase):
    def get_app(self):
        return create_webapp()


class TestBucketHandler(TestHandlerBase):
    def test_bad_header(self):
        response = self.fetch('/conda-linting/hook', method='POST',
                              body=urlencode({'a': 1}))
        self.assertEqual(response.code, 404)

    @mock.patch('conda_forge_webservices.linting.compute_lint_message', return_value={'message': mock.sentinel.message})
    @mock.patch('conda_forge_webservices.linting.comment_on_pr', return_value=mock.MagicMock(html_url=mock.sentinel.html_url))
    @mock.patch('conda_forge_webservices.linting.set_pr_status')
    def test_good_header(self, set_pr_status, comment_on_pr, compute_lint_message):
        PR_number = 16
        body = {'repository': {'name': 'repo_name',
                               'clone_url': 'repo_clone_url',
                               'owner': {'login': 'conda-forge'}},
                'pull_request': {'number': PR_number,
                                 'state': 'open'}}

        response = self.fetch('/conda-linting/hook', method='POST',
                              body=json.dumps(body),
                              headers={'X-GitHub-Event': 'pull_request'})

        self.assertEqual(response.code, 200)
        compute_lint_message.assert_called_once_with('conda-forge', 'repo_name',
                                                     PR_number, False)

        comment_on_pr.assert_called_once_with('conda-forge', 'repo_name',
                                              PR_number, mock.sentinel.message,
                                              search='conda-forge-linting service')

        set_pr_status.assert_called_once_with('conda-forge', 'repo_name',
                                              {'message': mock.sentinel.message},
                                              target_url=mock.sentinel.html_url)

    @mock.patch('conda_forge_webservices.linting.compute_lint_message', return_value={'message': mock.sentinel.message})
    @mock.patch('conda_forge_webservices.linting.comment_on_pr', return_value=mock.MagicMock(html_url=mock.sentinel.html_url))
    @mock.patch('conda_forge_webservices.linting.set_pr_status')
    def test_staged_recipes(self, set_pr_status, comment_on_pr, compute_lint_message):
        PR_number = 16
        body = {'repository': {'name': 'staged-recipes',
                               'clone_url': 'repo_clone_url',
                               'owner': {'login': 'conda-forge'}},
                'pull_request': {'number': PR_number,
                                 'state': 'open'}}

        response = self.fetch('/conda-linting/hook', method='POST',
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
