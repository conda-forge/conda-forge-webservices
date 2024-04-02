import tempfile
import subprocess

import pytest

from ..tokens import (
    get_app_token_for_webservices_only,
)


@pytest.mark.parametrize("token_repo", [
    "cf-autotick-bot-test-package-feedstock"
])
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

        subprocess.run(
            f"cd {tmpdir}/{repo} && "
            "git commit -m '[ci skip] test webservices app token can commit' "
            "--allow-empty",
            shell=True,
            check=True,
        )

        out = subprocess.run(
            f"cd {tmpdir}/{repo} && git push",
            shell=True,
        )

    assert out.returncode == 0


def test_github_app_tokens_for_webservices_cache():
    token = get_app_token_for_webservices_only()
    assert token is not None
    token_again = get_app_token_for_webservices_only()
    assert token_again == token


def test_github_app_tokens_for_webservices_feedstock():
    token = get_app_token_for_webservices_only()
    assert token is not None
    token_again = get_app_token_for_webservices_only(
        full_name="conda-forge/cf-autotick-bot-test-package-feedstock",
        fallback_env_token="GH_TOKEN",
    )
    assert token_again == token
