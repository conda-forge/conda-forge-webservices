import time
import base64
import os
import io
import sys
from contextlib import redirect_stdout, redirect_stderr

import jwt
import requests
from cryptography.hazmat.backends import default_backend


def generate_app_token(app_id, raw_pem, repo):
    """Get an app token.

    Parameters
    ----------
    app_id : str
        The github app ID.
    raw_pem : bytes
        An app private key as bytes.
    repo : str
        The name of the repo for which the token is scoped.
        This should be like `ngmix-feedstock` without the org `conda-forge`
        in front.

    Returns
    -------
    gh_token : str
        The github token. May return None if there is an error.
    """
    try:
        if raw_pem[0:1] != b'-':
            raw_pem = base64.b64decode(raw_pem)

        f = io.StringIO()
        with redirect_stdout(f), redirect_stderr(f):
            private_key = default_backend().load_pem_private_key(raw_pem, None)

            ti = int(time.time())
            token = jwt.encode(
                {
                    'iat': ti,
                    'exp': ti + 60*10,
                    'iss': app_id,
                },
                private_key,
                algorithm='RS256',
            )

        if (
            "GITHUB_ACTIONS" in os.environ
            and os.environ["GITHUB_ACTIONS"] == "true"
        ):
            sys.stdout.flush()
            print("masking JWT token for github actions", flush=True)
            print("::add-mask::%s" % token, flush=True)

        with redirect_stdout(f), redirect_stderr(f):
            r = requests.get(
                "https://api.github.com/app/installations",
                headers={
                    'Authorization': 'Bearer %s' % token,
                    'Accept': 'application/vnd.github.machine-man-preview+json',
                },
            )
            r.raise_for_status()

            r = requests.post(
                "https://api.github.com/app/installations/"
                "%s/access_tokens" % r.json()[0]["id"],
                headers={
                    'Authorization': 'Bearer %s' % token,
                    'Accept': 'application/vnd.github.machine-man-preview+json',
                },
                json={"repositories": [repo]},
            )
            r.raise_for_status()

            gh_token = r.json()["token"]

        if (
            "GITHUB_ACTIONS" in os.environ
            and os.environ["GITHUB_ACTIONS"] == "true"
        ):
            sys.stdout.flush()
            print("masking GITHUB token for github actions", flush=True)
            print("::add-mask::%s" % gh_token, flush=True)

        assert r.json()["permissions"] == {
            "contents": "write", "metadata": "read", "workflows": "write"
        }
        assert set(r["name"] for r in r.json()["repositories"]) == {repo}

    except Exception:
        gh_token = None

    return gh_token
