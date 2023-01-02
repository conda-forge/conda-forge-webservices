import os
import tempfile
import subprocess

import pytest

from ..tokens import generate_app_token, inject_app_token


@pytest.mark.parametrize("token_repo", [
    "staged-recipes",
    "cf-autotick-bot-test-package-feedstock"
])
def test_github_app_tokens(token_repo):
    app_id = os.environ["CF_WEBSERVICES_TOKENS_APP_ID"]
    raw_pem = os.environ["CF_WEBSERVICES_TOKENS_PRIVATE_KEY"].encode()
    token = generate_app_token(
        app_id, raw_pem,
        token_repo,
    )
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
            f"cd {tmpdir}/{repo} && git commit -m '[ci skip] test' --allow-empty",
            shell=True,
            check=True,
        )

        out = subprocess.run(
            f"cd {tmpdir}/{repo} && git push",
            shell=True,
        )

    if token_repo == repo:
        assert out.returncode == 0
    else:
        assert out.returncode != 0


@pytest.mark.parametrize("token_repo", [
    "staged-recipes",
    "cf-autotick-bot-test-package-feedstock"
])
def test_inject_app_token(token_repo):
    res = inject_app_token("conda-forge/" + token_repo)
    if token_repo == "cf-autotick-bot-test-package-feedstock":
        assert res
    else:
        assert not res
