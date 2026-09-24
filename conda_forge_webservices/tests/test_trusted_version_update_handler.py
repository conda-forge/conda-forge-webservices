import json
import unittest.mock as mock

from tornado.testing import AsyncHTTPTestCase

from conda_forge_webservices import trusted_version_updates as tvu
from conda_forge_webservices.trusted_publishing import GITHUB_ISSUER
from conda_forge_webservices.webapp import create_webapp

URL = "/trusted-publishing/version-update"
BODY = json.dumps({"feedstock": "lbenv-feedstock", "version": "1.2.3"})

REQUEST = tvu.Authorized(
    feedstock="lbenv-feedstock",
    version="1.2.3",
    default_branch="main",
    branch="main",
    sha="a" * 40,
    claims={
        "iss": GITHUB_ISSUER,
        "job_workflow_ref": "a/b/.github/workflows/c.yml@refs/tags/v1",
        "run_id": "1",
    },
)

OPENED = {"pull_request": 7, "url": "https://x/pull/7", "version_update_started": True}


class TestTrustedPublishingVersionUpdateHandler(AsyncHTTPTestCase):
    def get_app(self):
        return create_webapp()

    def _post(self, body=BODY, authorization="Bearer a.b.c"):
        headers = {"Content-Type": "application/json"}
        if authorization is not None:
            headers["Authorization"] = authorization
        return self.fetch(URL, method="POST", body=body, headers=headers)

    def test_a_bare_bearer_scheme_carries_no_token(self):
        response = self._post(authorization="Bearer ")
        self.assertEqual(response.code, 401)

    def test_a_body_that_is_not_a_json_object_is_refused(self):
        for body in ["not json", "[1, 2]"]:
            with self.assertLogs("conda_forge_webservices", level="INFO") as logs:
                response = self._post(body=body)
            self.assertEqual(response.code, 400)
            self.assertIn("not a json object", "\n".join(logs.output))

    @mock.patch.object(tvu, "authorize_request")
    def test_a_refusal_is_logged(self, authorize):
        authorize.side_effect = tvu.BadRequest(403, "matches no trusted publisher")
        with self.assertLogs("conda_forge_webservices", level="INFO") as logs:
            self._post()
        self.assertIn("matches no trusted publisher", "\n".join(logs.output))

    @mock.patch.object(tvu, "authorize_request")
    def test_the_token_goes_to_authorization_whatever_the_case(self, authorize):
        authorize.side_effect = tvu.BadRequest(403, "nope")
        self._post(authorization="bearer a.b.c")
        authorize.assert_called_once_with("a.b.c", "lbenv-feedstock", "1.2.3", None)

    @mock.patch.object(tvu, "authorize_request")
    def test_a_refusal_worth_retrying_says_when(self, authorize):
        authorize.side_effect = tvu.BadRequest(503, "later", retry_after=15)
        response = self._post()
        self.assertEqual(response.code, 503)
        self.assertEqual(response.headers["Retry-After"], "15")
        self.assertEqual(json.loads(response.body), {"error": "later"})

    @mock.patch.object(tvu, "release_cooldown")
    @mock.patch.object(tvu, "open_version_update_pr", return_value=OPENED)
    @mock.patch.object(tvu, "authorize_request", return_value=REQUEST)
    def test_an_authorized_request_opens_a_pull_request(
        self, authorize, open_pr, release
    ):
        response = self._post()
        self.assertEqual(response.code, 202)
        self.assertEqual(json.loads(response.body), OPENED)
        open_pr.assert_called_once_with(REQUEST)
        release.assert_not_called()

    @mock.patch.object(tvu, "release_cooldown")
    @mock.patch.object(tvu, "open_version_update_pr", side_effect=RuntimeError("git"))
    @mock.patch.object(tvu, "authorize_request", return_value=REQUEST)
    def test_nothing_opened_lets_the_branch_be_asked_for_again(
        self, authorize, open_pr, release
    ):
        response = self._post()
        self.assertEqual(response.code, 500)
        self.assertIn("could not be opened", json.loads(response.body)["error"])
        release.assert_called_once_with(REQUEST)
