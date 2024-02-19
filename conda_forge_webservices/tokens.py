import time
import base64
import os
import io
import sys
import logging
from contextlib import redirect_stdout, redirect_stderr

from github import Auth, Github, GithubIntegration

LOGGER = logging.getLogger("conda_forge_webservices.tokens")

APP_TOKEN_RESET_TIME = None


def get_app_token_for_webservices_only(full_name=None, fallback_env_token=None):
    """Get's an app token that should only be used in the webservices bot.

    This function caches the token and only returns a new one when the current
    one is expired or about to expire in the next minute.

    Parameters
    ----------
    full_name : str, optional
        The full name of the repo (e.g., "conda-forge/blah"). If given,
        app tokens are only made for the test feedstock.
    fallback_env_token : str, optional
        If not None, then this token from the environment variables
        is used for every feedstock except for the testing feedstock.

    Returns
    -------
    token: str
        The app token.
    """
    global APP_TOKEN_RESET_TIME
    global APP_TOKEN

    # this is for testing - will turn it on for all repos later
    if full_name is not None and fallback_env_token is not None:
        repo_name = full_name.split("/")[1]
        if repo_name != "cf-autotick-bot-test-package-feedstock":
            return os.environ[fallback_env_token]

    # add a minute to make sure token doesn't expire
    # while we are using it
    now = time.time()
    now_plus_1min = now + 60
    if APP_TOKEN_RESET_TIME is None or APP_TOKEN_RESET_TIME <= now_plus_1min:
        token = generate_app_token_for_webservices_only(
            os.environ["CF_WEBSERVICES_APP_ID"],
            os.environ["CF_WEBSERVICES_PRIVATE_KEY"].encode(),
        )
        if token is not None:
            try:
                APP_TOKEN_RESET_TIME = Github(token).rate_limiting_resettime
            except Exception:
                LOGGER.info("")
                LOGGER.info("===================================================")
                LOGGER.info("app token did not generate proper reset time")
                LOGGER.info("===================================================")
                token = None
        else:
            LOGGER.info("")
            LOGGER.info("===================================================")
            LOGGER.info("app token could not be made")
            LOGGER.info("===================================================")

        APP_TOKEN = token
    else:
        LOGGER.info("")
        LOGGER.info("===================================================")
        LOGGER.info(
            "app token exists - timeout %sm",
            (APP_TOKEN_RESET_TIME - now)/60,
        )
        LOGGER.info("===================================================")

    assert APP_TOKEN is not None, (
        "app token is None!"
    )

    return APP_TOKEN


def generate_app_token_for_webservices_only(app_id, raw_pem):
    """Get an app token that should only be used in the webservices bot.

    Parameters
    ----------
    app_id : str
        The github app ID.
    raw_pem : bytes
        An app private key as bytes.

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
            "running in github actions",
            flush=True,
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

        if isinstance(raw_pem, bytes):
            raw_pem = raw_pem.decode("utf-8")
            if (
                "GITHUB_ACTIONS" in os.environ
                and os.environ["GITHUB_ACTIONS"] == "true"
            ):
                sys.stdout.flush()
                print("utf-8 decoded PEM", flush=True)

        f = io.StringIO()
        with redirect_stdout(f), redirect_stderr(f):
            gh_auth = Auth.AppAuth(app_id=app_id, private_key=raw_pem)

        if (
            "GITHUB_ACTIONS" in os.environ
            and os.environ["GITHUB_ACTIONS"] == "true"
        ):
            sys.stdout.flush()
            print("loaded Github Auth", flush=True)

        integration = GithubIntegration(auth=gh_auth)
        if (
            "GITHUB_ACTIONS" in os.environ
            and os.environ["GITHUB_ACTIONS"] == "true"
        ):
            sys.stdout.flush()
            print("loaded Github Integration", flush=True)

        installation = integration.get_org_installation("conda-forge")
        if (
            "GITHUB_ACTIONS" in os.environ
            and os.environ["GITHUB_ACTIONS"] == "true"
        ):
            sys.stdout.flush()
            print("found Github installation", flush=True)

        gh_token = installation.get_access_token(installation.id)
        if (
            "GITHUB_ACTIONS" in os.environ
            and os.environ["GITHUB_ACTIONS"] == "true"
        ):
            sys.stdout.flush()
            print("made GITHUB token and masking it for github actions", flush=True)
            print("::add-mask::%s" % gh_token, flush=True)

    except Exception:
        gh_token = None

    return gh_token
