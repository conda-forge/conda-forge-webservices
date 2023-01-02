import time
import base64
import os
import io
import sys
import logging
from contextlib import redirect_stdout, redirect_stderr

from github import Github
import jwt
import requests
from cryptography.hazmat.backends import default_backend

LOGGER = logging.getLogger("conda_forge_webservices.tokens")

TOKEN_RESET_TIMES = {}


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
    repo_name = full_name.split("/")[1]

    # this is for testing - will turn it on for all repos later
    if repo_name != "cf-autotick-bot-test-package-feedstock":
        return False

    if not repo_name.endswith("-feedstock"):
        return False

    global TOKEN_RESET_TIMES

    now = time.time()
    if TOKEN_RESET_TIMES.get(repo_name, now) <= now:
        token = generate_app_token(
            os.environ["CF_WEBSERVICES_TOKENS_APP_ID"],
            os.environ["CF_WEBSERVICES_TOKENS_PRIVATE_KEY"].encode(),
            repo_name,
        )
        if token is not None:
            if repo is None:
                gh = Github(os.environ['GH_TOKEN'])
                repo = gh.get_repo(full_name)
            try:
                repo.create_secret("RERENDERING_GITHUB_TOKEN", token)
                TOKEN_RESET_TIMES[repo_name] = Github(token).rate_limiting_resettime
                LOGGER.info("")
                LOGGER.info("===================================================")
                LOGGER.info(
                    "injected app token for repo %s - timeout %sm",
                    repo_name,
                    (TOKEN_RESET_TIMES[repo_name] - now)/60,
                )
                LOGGER.info("===================================================")
                worked = True
            except Exception:
                LOGGER.info("")
                LOGGER.info("===================================================")
                LOGGER.info(
                    "app token could not be pushed to secrets for %s", repo_name)
                LOGGER.info("===================================================")
                worked = False

            return worked
        else:
            LOGGER.info("")
            LOGGER.info("===================================================")
            LOGGER.info(
                "app token could not be made for %s", repo_name)
            LOGGER.info("===================================================")
            return False
    else:
        LOGGER.info("")
        LOGGER.info("===================================================")
        LOGGER.info(
            "app token exists for repo %s - timeout %sm",
            repo_name,
            (TOKEN_RESET_TIMES[repo_name] - now)/60,
        )
        LOGGER.info("===================================================")
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
    if (
        "GITHUB_ACTIONS" in os.environ
        and os.environ["GITHUB_ACTIONS"] == "true"
    ):
        sys.stdout.flush()
        print(
            "making github app token:",
            os.environ["GITHUB_ACTIONS"] == "true",
            flush=True
        )

    try:
        if raw_pem[0:1] != b'-':
            raw_pem = base64.b64decode(raw_pem)

            if (
                "GITHUB_ACTIONS" in os.environ
                and os.environ["GITHUB_ACTIONS"] == "true"
            ):
                sys.stdout.flush()
                print("base64 decoded PEM", flush=True)

        f = io.StringIO()
        with redirect_stdout(f), redirect_stderr(f):
            private_key = default_backend().load_pem_private_key(raw_pem, None)

        if (
            "GITHUB_ACTIONS" in os.environ
            and os.environ["GITHUB_ACTIONS"] == "true"
        ):
            sys.stdout.flush()
            print("loaded PEM", flush=True)

        f = io.StringIO()
        with redirect_stdout(f), redirect_stderr(f):
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
            print("made JWT and masking it for github actions", flush=True)
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
                json={
                    "repositories": [repo],
                    "permissions": {
                        "contents": "write",
                        "metadata": "read",
                        "workflows": "write",
                        "checks": "read",
                        "pull_requests": "write",
                        "statuses": "read",
                    }
                },
            )
            r.raise_for_status()

            gh_token = r.json()["token"]

        if (
            "GITHUB_ACTIONS" in os.environ
            and os.environ["GITHUB_ACTIONS"] == "true"
        ):
            sys.stdout.flush()
            print("made GITHUB token and masking it for github actions", flush=True)
            print("::add-mask::%s" % gh_token, flush=True)

        assert r.json()["permissions"] == {
            "contents": "write", "metadata": "read", "workflows": "write",
            "checks": "read", "pull_requests": "write", "statuses": "read",
        }
        assert set(r["name"] for r in r.json()["repositories"]) == {repo}

    except Exception as e:
        if (
            "Could not deserialize key" in str(e)
            and "GITHUB_ACTIONS" in os.environ
            and os.environ["GITHUB_ACTIONS"] == "true"
        ):
            print("bad PEM format", flush=True)

        gh_token = None

    return gh_token
