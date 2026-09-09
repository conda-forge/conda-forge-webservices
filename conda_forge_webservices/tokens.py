import time
import base64
import os
import io
import sys
import logging
from contextlib import redirect_stdout, redirect_stderr
from functools import lru_cache

from typing import Any

from github import (
    Auth,
    Github,
    GithubIntegration,
)

from conda_forge_webservices.utils import log_title_and_message_at_level

LOGGER = logging.getLogger("conda_forge_webservices.tokens")

FEEDSTOCK_TOKEN_RESET_TIMES: dict[str, Any] = {}
READONLY_FEEDSTOCK_TOKEN_RESET_TIMES: dict[str, Any] = {}
APP_TOKEN_RESET_TIME = None


@lru_cache(maxsize=1)
def _get_gh_client(token):
    return Github(auth=Auth.Token(token))


def get_gh_client():
    return _get_gh_client(get_app_token_for_webservices_only())


def get_app_token_for_webservices_only():
    """Get's an app token that should only be used in the webservices bot.

    This function caches the token and only returns a new one when the current
    one is expired or about to expire in the next minute.

    Returns
    -------
    token: str
        The app token.
    """
    global APP_TOKEN_RESET_TIME
    global APP_TOKEN

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
                APP_TOKEN_RESET_TIME = Github(
                    auth=Auth.Token(token)
                ).rate_limiting_resettime
            except Exception:
                log_title_and_message_at_level(
                    level="info",
                    title="app token did not generate proper reset time",
                )
                token = None
        else:
            log_title_and_message_at_level(
                level="info",
                title="app token could not be made",
            )

        APP_TOKEN = token
    else:
        pass
        # log_title_and_message_at_level(
        #     level="info",
        #     title=f"app token exists - timeout {(APP_TOKEN_RESET_TIME - now) / 60}m",
        # )

    assert APP_TOKEN is not None, "app token is None!"

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
    if "GITHUB_ACTIONS" in os.environ and os.environ["GITHUB_ACTIONS"] == "true":
        sys.stdout.flush()
        print(
            "running in GitHub Actions",
            flush=True,
        )
        print(f"::add-mask::{raw_pem}", flush=True)

    try:
        f = io.StringIO()
        if raw_pem[0:1] != b"-":
            with redirect_stdout(f), redirect_stderr(f):
                raw_pem = base64.b64decode(raw_pem)
            if (
                "GITHUB_ACTIONS" in os.environ
                and os.environ["GITHUB_ACTIONS"] == "true"
            ):
                sys.stdout.flush()
                print("base64 decoded PEM", flush=True)
                print(f"::add-mask::{raw_pem}", flush=True)

        if isinstance(raw_pem, bytes):
            with redirect_stdout(f), redirect_stderr(f):
                raw_pem = raw_pem.decode()
            if (
                "GITHUB_ACTIONS" in os.environ
                and os.environ["GITHUB_ACTIONS"] == "true"
            ):
                sys.stdout.flush()
                print("utf-8 decoded PEM", flush=True)
                print(f"::add-mask::{raw_pem}", flush=True)

        with redirect_stdout(f), redirect_stderr(f):
            gh_auth = Auth.AppAuth(app_id=app_id, private_key=raw_pem)
        if "GITHUB_ACTIONS" in os.environ and os.environ["GITHUB_ACTIONS"] == "true":
            sys.stdout.flush()
            print("loaded Github Auth", flush=True)

        with redirect_stdout(f), redirect_stderr(f):
            integration = GithubIntegration(auth=gh_auth)
        if "GITHUB_ACTIONS" in os.environ and os.environ["GITHUB_ACTIONS"] == "true":
            sys.stdout.flush()
            print("loaded Github Integration", flush=True)

        with redirect_stdout(f), redirect_stderr(f):
            installation = integration.get_org_installation("conda-forge")
        if "GITHUB_ACTIONS" in os.environ and os.environ["GITHUB_ACTIONS"] == "true":
            sys.stdout.flush()
            print("found Github installation", flush=True)

        with redirect_stdout(f), redirect_stderr(f):
            gh_token = integration.get_access_token(installation.id).token
        if "GITHUB_ACTIONS" in os.environ and os.environ["GITHUB_ACTIONS"] == "true":
            sys.stdout.flush()
            print("made GITHUB token and masking it for GitHub Actions", flush=True)
            print(f"::add-mask::{gh_token}", flush=True)

    except Exception:
        gh_token = None

    return gh_token
