import json
import threading
import time
import unittest.mock as mock

from tornado.testing import AsyncHTTPTestCase

from conda_forge_webservices import webapp
from conda_forge_webservices.webapp import create_webapp

OUTPUTS = {
    "linux-64/foo-1.0-h0_0.conda": "aaa",
    "noarch/foo-data-1.0-h0_0.conda": "bbb",
}


def _request(outputs=OUTPUTS, **extra):
    return {
        "feedstock": "foo-feedstock",
        "outputs": outputs,
        "channel": "main",
        "hash_type": "sha256",
        "git_sha": "abc123",
        **extra,
    }


@mock.patch("conda_forge_webservices.webapp.comment_on_outputs_copy")
@mock.patch(
    "conda_forge_webservices.webapp.stage_dist_to_post_staging_and_possibly_copy_to_prod",
    return_value=(True, []),
)
@mock.patch(
    "conda_forge_webservices.webapp.validate_feedstock_outputs",
    side_effect=lambda feedstock, outputs, hash_type, label: (
        dict.fromkeys(outputs, True),
        [],
    ),
)
@mock.patch(
    "conda_forge_webservices.webapp._is_valid_output_hash",
    side_effect=lambda outputs, hash_type, channel, label: dict.fromkeys(
        outputs, False
    ),
)
@mock.patch(
    "conda_forge_webservices.webapp.is_valid_feedstock_token", return_value=True
)
@mock.patch("conda_forge_webservices.webapp._repo_exists", return_value=True)
class TestAsyncCopy(AsyncHTTPTestCase):
    def get_app(self):
        return create_webapp()

    def setUp(self):
        super().setUp()
        webapp.ASYNC_COPIES.clear()
        webapp.ASYNC_COPY_IDS.clear()

    def post(self, body, token="token"):
        return self.fetch(
            "/feedstock-outputs/copy-async",
            method="POST",
            body=json.dumps(body),
            headers={"FEEDSTOCK_TOKEN": token},
        )

    def status(self, copy_id):
        return self.fetch(f"/feedstock-outputs/copy-async/{copy_id}")

    def wait_for(self, copy_id):
        deadline = time.time() + 10
        while time.time() < deadline:
            data = json.loads(self.status(copy_id).body)
            if data["state"] in ("done", "failed"):
                return data
            time.sleep(0.05)
        raise AssertionError(f"copy {copy_id} did not finish")

    def test_copy(self, _repo, _token, _on_prod, validate, stage, comment):
        response = self.post(_request())

        self.assertEqual(response.code, 202)
        started = json.loads(response.body)
        self.assertIn(started["state"], ("queued", "copying", "done"))

        data = self.wait_for(started["id"])
        self.assertEqual(data["state"], "done")
        self.assertEqual(data["copied"], dict.fromkeys(OUTPUTS, True))
        self.assertEqual(data["errors"], [])
        self.assertEqual(stage.call_count, len(OUTPUTS))
        comment.assert_not_called()

    def test_invalid_token(self, _repo, token, _on_prod, validate, stage, comment):
        token.return_value = False

        response = self.post(_request())

        self.assertEqual(response.code, 400)
        validate.assert_not_called()
        stage.assert_not_called()

    def test_sending_again_joins_the_copy(
        self, _repo, _token, _on_prod, validate, stage, comment
    ):
        release = threading.Event()

        def slow_stage(*args):
            release.wait(10)
            return True, []

        stage.side_effect = slow_stage

        first = json.loads(self.post(_request()).body)
        second = json.loads(self.post(_request()).body)
        release.set()

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(self.wait_for(first["id"])["state"], "done")
        self.assertEqual(stage.call_count, len(OUTPUTS))

    def test_outputs_already_on_prod(
        self, _repo, _token, on_prod, validate, stage, comment
    ):
        on_prod.side_effect = lambda outputs, hash_type, channel, label: {
            o: o.startswith("linux-64/") for o in outputs
        }

        started = json.loads(self.post(_request()).body)

        data = self.wait_for(started["id"])
        self.assertEqual(data["state"], "done")
        self.assertEqual(data["copied"], dict.fromkeys(OUTPUTS, True))
        validate.assert_called_once_with(
            "foo-feedstock",
            {"noarch/foo-data-1.0-h0_0.conda": "bbb"},
            "sha256",
            "main",
        )
        stage.assert_called_once_with(
            "noarch/foo-data-1.0-h0_0.conda", "main", "sha256", "bbb"
        )

    def test_all_outputs_already_on_prod(
        self, _repo, _token, on_prod, validate, stage, comment
    ):
        on_prod.side_effect = lambda outputs, hash_type, channel, label: dict.fromkeys(
            outputs, True
        )

        response = self.post(_request())

        self.assertEqual(response.code, 202)
        self.assertEqual(json.loads(response.body)["state"], "done")
        validate.assert_not_called()
        stage.assert_not_called()

    def test_invalid_outputs(self, _repo, _token, _on_prod, validate, stage, comment):
        validate.side_effect = lambda feedstock, outputs, hash_type, label: (
            {o: not o.startswith("noarch/") for o in outputs},
            ["not allowed"],
        )

        response = self.post(_request())

        self.assertEqual(response.code, 400)
        self.assertEqual(json.loads(response.body)["errors"], ["not allowed"])
        stage.assert_not_called()
        comment.assert_called_once()

    def test_failed_copy(self, _repo, _token, _on_prod, validate, stage, comment):
        stage.side_effect = lambda dist, *args: (
            not dist.startswith("noarch/"),
            ["hash mismatch"],
        )

        first = json.loads(self.post(_request()).body)
        data = self.wait_for(first["id"])

        self.assertEqual(data["state"], "failed")
        self.assertIn("hash mismatch", data["errors"])
        self.assertFalse(data["copied"]["noarch/foo-data-1.0-h0_0.conda"])
        comment.assert_called_once()

        # a failed copy is not joined, so it can be tried again
        second = json.loads(self.post(_request()).body)
        self.assertNotEqual(first["id"], second["id"])

    def test_copied_by_an_earlier_request(
        self, _repo, _token, on_prod, validate, stage, comment
    ):
        # off cf-staging by the time the copy runs, and on conda-forge
        stage.return_value = (False, ["not on cf-staging"])
        on_prod.side_effect = lambda outputs, hash_type, channel, label: dict.fromkeys(
            outputs, stage.called
        )

        started = json.loads(self.post(_request()).body)
        data = self.wait_for(started["id"])

        self.assertEqual(data["state"], "done")
        self.assertEqual(data["errors"], [])
        comment.assert_not_called()

    def test_unknown_copy(self, _repo, _token, _on_prod, validate, stage, comment):
        response = self.status("0" * 32)

        self.assertEqual(response.code, 404)
