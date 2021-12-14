import time
import base64
import os
import io
import sys
from contextlib import redirect_stdout, redirect_stderr

from github import Github
import jwt
import requests
from cryptography.hazmat.backends import default_backend

TOKEN_RESET_TIMES = {}
TEN_MINS = 10*60


def inject_app_token(full_name, repo=None):
    """Inject the webservices app token into the repo secrets.

    Parameters
    ----------
    full_name : str
        The full name of the repo (e.g., "conda-forge/blah").
    repo : pygithub Repository
        Optional repo object to use. If not passed, a new one will be made.

    Returns
    -------
    injected : bool
        True if the token was injected, False otherwise.
    """
    if repo is None:
        gh = Github(os.environ['GH_TOKEN'])
        repo = gh.get_repo(full_name)

    # this is for testing - will turn it on for all repos later
    if repo.name != "cf-autotick-bot-test-package-feedstock":
        return False

    if not repo.name.endswith("-feedstock"):
        return False

    global TOKEN_RESET_TIMES

    now = time.time()
    if TOKEN_RESET_TIMES.get(repo.name, now) <= now + TEN_MINS:
        token = generate_app_token(
            os.environ["CF_WEBSERVICES_APP_ID"],
            os.environ["CF_WEBSERVICES_PRIVATE_KEY"].encode(),
            repo.name,
        )
        if token is not None:
            worked = repo.create_secret("RERENDERING_GITHUB_TOKEN", token)
            if worked:
                TOKEN_RESET_TIMES[repo.name] = Github(token).rate_limiting_resettime
            return worked
        else:
            return False
    else:
        return True


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
            "contents": "write", "metadata": "read", "workflows": "write",
            "checks": "read", "pulls": "write", "statuses": "read",
        }
        assert set(r["name"] for r in r.json()["repositories"]) == {repo}

    except Exception:
        gh_token = None

    return gh_token
