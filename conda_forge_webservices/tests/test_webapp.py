import json
import hmac
import os
import hashlib

from urllib.parse import urlencode
import unittest.mock as mock

from tornado.testing import AsyncHTTPTestCase

from conda_forge_webservices.webapp import create_webapp


class TestHandlerBase(AsyncHTTPTestCase):
    def get_app(self):
        return create_webapp()


class TestBucketHandler(TestHandlerBase):
    def test_bad_header(self):
        hash = hmac.new(
            os.environ["CF_WEBSERVICES_TOKEN"].encode("utf-8"),
            urlencode({"a": 1}).encode("utf-8"),
            hashlib.sha1,
        ).hexdigest()

        response = self.fetch(
            "/conda-linting/org-hook",
            method="POST",
            body=urlencode({"a": 1}),
            headers={"X-Hub-Signature": "sha1=%s" % hash},
        )
        self.assertEqual(response.code, 404)

    def test_bad_hash(self):
        response = self.fetch(
            "/conda-linting/org-hook",
            method="POST",
            body=urlencode({"a": 1}),
            headers={
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature": "sha1=43abf34",
            },
        )
        self.assertIn(response.code, [403, 500])

    @mock.patch(
        "conda_forge_webservices.linting.compute_lint_message",
        return_value={"message": mock.sentinel.message},
    )
    @mock.patch(
        "conda_forge_webservices.linting.comment_on_pr",
        return_value=mock.MagicMock(html_url=mock.sentinel.html_url),
    )
    @mock.patch("conda_forge_webservices.linting.set_pr_status")
    def test_good_header(self, set_pr_status, comment_on_pr, compute_lint_message):
        PR_number = 16
        body = {
            "repository": {
                "name": "repo_name-feedstock",
                "full_name": "conda-forge/repo_name-feedstock",
                "clone_url": "repo_clone_url",
                "owner": {"login": "conda-forge"},
            },
            "action": "synchronize",
            "pull_request": {
                "number": PR_number,
                "state": "open",
                "labels": [{"name": "stale"}],
            },
        }

        hash = hmac.new(
            os.environ["CF_WEBSERVICES_TOKEN"].encode("utf-8"),
            json.dumps(body).encode("utf-8"),
            hashlib.sha1,
        ).hexdigest()

        response = self.fetch(
            "/conda-linting/org-hook",
            method="POST",
            body=json.dumps(body),
            headers={
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature": "sha1=%s" % hash,
            },
        )

        self.assertEqual(response.code, 200)

        compute_lint_message.assert_called_once_with(
            "conda-forge", "repo_name-feedstock", PR_number, False
        )

        comment_on_pr.assert_called_once_with(
            "conda-forge",
            "repo_name-feedstock",
            PR_number,
            mock.sentinel.message,
            search="conda-forge-linting service",
        )

        set_pr_status.assert_called_once_with(
            "conda-forge",
            "repo_name-feedstock",
            {"message": mock.sentinel.message},
            target_url=mock.sentinel.html_url,
        )

    @mock.patch(
        "conda_forge_webservices.linting.compute_lint_message", return_value=None
    )
    @mock.patch("conda_forge_webservices.linting.set_pr_status", return_value=None)
    @mock.patch("conda_forge_webservices.linting.comment_on_pr", return_value=None)
    @mock.patch(
        "conda_forge_webservices.feedstocks_service.update_feedstock", return_value=None
    )
    @mock.patch(
        "conda_forge_webservices.commands.pr_detailed_comment", return_value=None
    )
    @mock.patch("conda_forge_webservices.commands.pr_comment", return_value=None)
    @mock.patch("conda_forge_webservices.commands.issue_comment", return_value=None)
    @mock.patch("conda_forge_webservices.commands.add_reaction", return_value=None)
    @mock.patch("conda_forge_webservices.update_teams.update_team", return_value=None)
    @mock.patch(
        "conda_forge_webservices.webapp.print_rate_limiting_info", return_value=None
    )
    def test_accept_repos(self, *methods):
        for hook, accepted_repos, accepted_events in [
            (
                "/conda-linting/org-hook",
                ["staged-recipes", "repo-feedstock"],
                ["pull_request"],
            ),
            ("/conda-forge-feedstocks/org-hook", ["repo-feedstock"], ["push"]),
            ("/conda-forge-teams/org-hook", ["repo-feedstock"], ["push"]),
            (
                "/conda-forge-command/org-hook",
                ["staged-recipes", "repo-feedstock"],
                ["pull_request", "issues"],
            ),
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
                for __branch in ["main", "master"]:
                    body = {
                        "after": "324234fdf",
                        "repository": {
                            "name": name,
                            "full_name": "%s/%s" % (owner, name),
                            "clone_url": "repo_clone_url",
                            "owner": {"login": owner},
                        },
                        "pull_request": {
                            "number": 16,
                            "state": "open",
                            "labels": [{"name": "stale"}],
                            "head": {
                                "repo": {
                                    "name": "pr_repo_name",
                                    "owner": {"login": "pr_repo_owner"},
                                },
                                "ref": "ref",
                            },
                            "body": "body",
                            "id": 56767,
                        },
                        "issue": {
                            "number": 16,
                            "body": "body",
                            "title": "title",
                            "id": 56767,
                        },
                        "action": "opened",
                        "ref": "refs/heads/" + __branch,
                    }

                    hash = hmac.new(
                        os.environ["CF_WEBSERVICES_TOKEN"].encode("utf-8"),
                        json.dumps(body).encode("utf-8"),
                        hashlib.sha1,
                    ).hexdigest()

                    for event in ["pull_request", "issues", "push"]:
                        response = self.fetch(
                            hook,
                            method="POST",
                            body=json.dumps(body),
                            headers={
                                "X-GitHub-Event": event,
                                "X-Hub-Signature": "sha1=%s" % hash,
                            },
                        )

                        if (
                            owner == "conda-forge"
                            and name in accepted_repos
                            and event in accepted_events
                        ):
                            self.assertEqual(
                                response.code,
                                200,
                                msg=f"event: {event}, slug: {slug}, hook: {hook}",
                            )
                        else:
                            self.assertNotEqual(
                                response.code,
                                200,
                                msg=f"event: {event}, slug: {slug}, hook: {hook}",
                            )

    @mock.patch(
        "conda_forge_webservices.feedstocks_service.update_feedstock",
        return_value=None,
    )
    @mock.patch(
        "conda_forge_webservices.update_teams.update_team",
        return_value=None,
    )
    @mock.patch(
        "conda_forge_webservices.webapp.print_rate_limiting_info",
        return_value=None,
    )
    def test_skip_commits(self, *args):
        for hook, accepted_repos, accepted_events, skip_slugs in [
            (
                "/conda-forge-feedstocks/org-hook",
                ["repo-feedstock"],
                ["push"],
                ["[cf admin skip]", "[cf admin skip feedstocks]"],
            ),
            (
                "/conda-forge-teams/org-hook",
                ["repo-feedstock"],
                ["push"],
                ["[cf admin skip]", "[cf admin skip teams]"],
            ),
        ]:
            for commit_msg in ["blah"] + skip_slugs:
                test_slugs = [
                    "conda-forge/repo-feedstock",
                    "conda-forge/staged-recipes",
                    "conda-forge/conda-smithy",
                    "dummy/repo-feedstock",
                    "dummy/staged-recipes",
                ]

                for slug in test_slugs:
                    owner, name = slug.split("/")
                    for __branch in ["main", "master"]:
                        body = {
                            "after": "324234fdf",
                            "repository": {
                                "name": name,
                                "full_name": "%s/%s" % (owner, name),
                                "clone_url": "repo_clone_url",
                                "owner": {"login": owner},
                            },
                            "pull_request": {
                                "number": 16,
                                "state": "open",
                                "labels": [{"name": "stale"}],
                                "head": {
                                    "repo": {
                                        "name": "pr_repo_name",
                                        "owner": {"login": "pr_repo_owner"},
                                    },
                                    "ref": "ref",
                                },
                                "body": "body",
                            },
                            "issue": {
                                "number": 16,
                                "body": "body",
                                "title": "title",
                            },
                            "action": "opened",
                            "ref": "refs/heads/" + __branch,
                            "head_commit": {"id": "xyz", "message": commit_msg},
                        }

                        hash = hmac.new(
                            os.environ["CF_WEBSERVICES_TOKEN"].encode("utf-8"),
                            json.dumps(body).encode("utf-8"),
                            hashlib.sha1,
                        ).hexdigest()

                        for event in ["push"]:
                            response = self.fetch(
                                hook,
                                method="POST",
                                body=json.dumps(body),
                                headers={
                                    "X-GitHub-Event": event,
                                    "X-Hub-Signature": "sha1=%s" % hash,
                                },
                            )

                            if (
                                owner == "conda-forge"
                                and name in accepted_repos
                                and event in accepted_events
                                and all(s not in commit_msg for s in skip_slugs)
                            ):
                                assert commit_msg == "blah"
                                self.assertEqual(
                                    response.code,
                                    200,
                                    msg=f"event: {event}, slug: {slug}, hook: {hook}",
                                )
                            else:
                                self.assertNotEqual(
                                    response.code,
                                    200,
                                    msg=f"event: {event}, slug: {slug}, hook: {hook}",
                                )

    @mock.patch(
        "conda_forge_webservices.linting.compute_lint_message",
        return_value={"message": mock.sentinel.message},
    )
    @mock.patch(
        "conda_forge_webservices.linting.comment_on_pr",
        return_value=mock.MagicMock(html_url=mock.sentinel.html_url),
    )
    @mock.patch("conda_forge_webservices.linting.set_pr_status")
    def test_staged_recipes(self, set_pr_status, comment_on_pr, compute_lint_message):
        PR_number = 16
        body = {
            "repository": {
                "name": "staged-recipes",
                "full_name": "conda-forge/staged-recipes",
                "clone_url": "repo_clone_url",
                "owner": {"login": "conda-forge"},
            },
            "action": "synchronize",
            "pull_request": {
                "number": PR_number,
                "state": "open",
                "labels": [{"name": "blah"}],
            },
        }

        hash = hmac.new(
            os.environ["CF_WEBSERVICES_TOKEN"].encode("utf-8"),
            json.dumps(body).encode("utf-8"),
            hashlib.sha1,
        ).hexdigest()

        response = self.fetch(
            "/conda-linting/org-hook",
            method="POST",
            body=json.dumps(body),
            headers={
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature": "sha1=%s" % hash,
            },
        )

        self.assertEqual(response.code, 200)
        compute_lint_message.assert_called_once_with(
            "conda-forge", "staged-recipes", PR_number, True
        )

        comment_on_pr.assert_called_once_with(
            "conda-forge",
            "staged-recipes",
            PR_number,
            mock.sentinel.message,
            search="conda-forge-linting service",
        )

        set_pr_status.assert_called_once_with(
            "conda-forge",
            "staged-recipes",
            {"message": mock.sentinel.message},
            target_url=mock.sentinel.html_url,
        )

    @mock.patch(
        "conda_forge_webservices.linting.compute_lint_message",
        return_value={"message": mock.sentinel.message},
    )
    @mock.patch(
        "conda_forge_webservices.linting.comment_on_pr",
        return_value=mock.MagicMock(html_url=mock.sentinel.html_url),
    )
    @mock.patch("conda_forge_webservices.linting.set_pr_status")
    def test_staged_recipes_stale(
        self, set_pr_status, comment_on_pr, compute_lint_message
    ):
        PR_number = 16
        body = {
            "repository": {
                "name": "staged-recipes",
                "full_name": "conda-forge/staged-recipes",
                "clone_url": "repo_clone_url",
                "owner": {"login": "conda-forge"},
            },
            "action": "synchronize",
            "pull_request": {
                "number": PR_number,
                "state": "open",
                "labels": [{"name": "stale"}],
            },
        }

        hash = hmac.new(
            os.environ["CF_WEBSERVICES_TOKEN"].encode("utf-8"),
            json.dumps(body).encode("utf-8"),
            hashlib.sha1,
        ).hexdigest()

        response = self.fetch(
            "/conda-linting/org-hook",
            method="POST",
            body=json.dumps(body),
            headers={
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature": "sha1=%s" % hash,
            },
        )

        self.assertEqual(response.code, 200)
        compute_lint_message.assert_not_called()

        comment_on_pr.assert_not_called()

        set_pr_status.assert_not_called()
