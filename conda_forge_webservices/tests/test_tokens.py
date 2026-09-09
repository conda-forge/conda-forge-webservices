import hmac
import tempfile
import subprocess

import pytest
from flaky import flaky

from ..tokens import (
    get_app_token_for_webservices_only,
)
from ..utils import with_action_url


@pytest.mark.parametrize("token_repo", ["cf-autotick-bot-test-package-feedstock"])
def test_github_app_tokens_for_webservices(token_repo):
    token = get_app_token_for_webservices_only()
    assert token is not None
    repo = "cf-autotick-bot-test-package-feedstock"

    with tempfile.TemporaryDirectory() as tmpdir:
        subprocess.run(
            f"cd {tmpdir} && git clone https://github.com/conda-forge/{repo}.git",
            shell=True,
            check=True,
        )

        subprocess.run(
            f"cd {tmpdir}/{repo} && "
            "git remote set-url --push origin "
            f"https://x-access-token:{token}@github.com/conda-forge/{repo}.git",
            shell=True,
            check=True,
        )

        msg = with_action_url("[ci skip] test webservices app token can commit")
        subprocess.run(
            f"cd {tmpdir}/{repo} && git commit -m '{msg}' --allow-empty",
            shell=True,
            check=True,
        )

        out = subprocess.run(
            f"cd {tmpdir}/{repo} && git push",
            shell=True,
        )

    assert out.returncode == 0


@flaky
def test_github_app_tokens_for_webservices_cache():
    token = get_app_token_for_webservices_only()
    assert token is not None
    token_again = get_app_token_for_webservices_only()
    if not hmac.compare_digest(token_again, token):
        assert False, "Token should be cached but is not!"
