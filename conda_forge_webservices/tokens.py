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
    GithubException,
)
from github.InstallationAuthorization import InstallationAuthorization

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
        log_title_and_message_at_level(
            level="info",
            title=f"app token exists - timeout {(APP_TOKEN_RESET_TIME - now) / 60}m",
        )

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


def inject_app_token_into_feedstock(full_name, repo=None):
    """Inject the cf-webservices-tokens app token into the repo secrets.

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
    return _inject_app_token_into_feedstock(full_name, repo=repo, readonly=False)


def inject_app_token_into_feedstock_readonly(full_name, repo=None):
    """Inject the cf-webservices-tokens app token into the repo secrets.

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
    return _inject_app_token_into_feedstock(full_name, repo=repo, readonly=True)


def _inject_app_token_into_feedstock(full_name, repo=None, readonly=False):
    repo_name = full_name.split("/")[1]

    # this is for testing - will turn it on for all repos later
    # if repo_name != "cf-autotick-bot-test-package-feedstock":
    # this is OFF permanently
    return False

    if not repo_name.endswith("-feedstock"):
        return False

    global FEEDSTOCK_TOKEN_RESET_TIMES
    global READONLY_FEEDSTOCK_TOKEN_RESET_TIMES

    if readonly:
        reset_times_dict = READONLY_FEEDSTOCK_TOKEN_RESET_TIMES
        token_name = "READONLY_GITHUB_TOKEN"
    else:
        reset_times_dict = FEEDSTOCK_TOKEN_RESET_TIMES
        token_name = "RERENDERING_GITHUB_TOKEN"

    now = time.time()
    now_plus_30min = now + 30 * 60
    if reset_times_dict.get(repo_name, now_plus_30min) <= now_plus_30min:
        token = generate_app_token_for_feedstock(
            os.environ["CF_WEBSERVICES_FEEDSTOCK_APP_ID"],
            os.environ["CF_WEBSERVICES_FEEDSTOCK_PRIVATE_KEY"].encode(),
            repo_name,
            readonly=readonly,
        )
        if token is not None:
            if repo is None:
                gh = get_gh_client()
                repo = gh.get_repo(full_name)
            try:
                repo.create_secret(token_name, token)
                reset_times_dict[repo_name] = Github(
                    auth=Auth.Token(token)
                ).rate_limiting_resettime
                log_title_and_message_at_level(
                    level="info",
                    title=(
                        f"injected app token for repo {repo_name} - "
                        f"timeout {(reset_times_dict[repo_name] - now) / 60}m"
                    ),
                )
                worked = True
            except Exception:
                log_title_and_message_at_level(
                    level="info",
                    title=f"app token could not be pushed to secrets for {repo_name}",
                )
                worked = False

            return worked
        else:
            LOGGER.info("")
            LOGGER.info("===================================================")
            LOGGER.info("app token could not be made for %s", repo_name)
            LOGGER.info("===================================================")
            return False
    else:
        log_title_and_message_at_level(
            level="info",
            title=(
                f"app token exists for repo {repo_name} "
                f"- timeout {(reset_times_dict[repo_name] - now) / 60}m"
            ),
        )
        return True


# see https://github.com/PyGithub/PyGithub/issues/3037 for why we do this
class MyGithubIntegration(GithubIntegration):
    def get_access_token(
        self,
        installation_id: int,
        permissions: dict[str, str] | None = None,
        repositories: list[str] | None = None,
    ) -> InstallationAuthorization:
        """
        :calls: `POST /app/installations/{installation_id}/access_tokens
        <https://docs.github.com/en/rest/apps/apps#create-an-installation-access-token-for-an-app>`
        """
        if permissions is None:
            permissions = {}

        if not isinstance(permissions, dict):
            raise GithubException(
                status=400, data={"message": "Invalid permissions"}, headers=None
            )

        body = {"permissions": permissions, "repositories": repositories}
        headers, response = self._GithubIntegration__requester.requestJsonAndCheck(
            "POST",
            f"/app/installations/{installation_id}/access_tokens",
            headers=self._get_headers(),
            input=body,
        )

        return InstallationAuthorization(
            requester=self._GithubIntegration__requester,
            headers=headers,
            attributes=response,
            completed=True,
        )


def generate_app_token_for_feedstock(app_id, raw_pem, repo, readonly=False):
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
    readonly : bool
        If True, the token will only have read access.

    Returns
    -------
    gh_token : str
        The github token. May return None if there is an error.
    """
    read_or_write = "read" if readonly else "write"
    permissions = {
        "actions": read_or_write,
        "checks": read_or_write,
        "contents": read_or_write,
        "issues": read_or_write,
        "metadata": "read",
        "pull_requests": read_or_write,
        "statuses": read_or_write,
        "workflows": read_or_write,
    }

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
            integration = MyGithubIntegration(auth=gh_auth)
        if "GITHUB_ACTIONS" in os.environ and os.environ["GITHUB_ACTIONS"] == "true":
            sys.stdout.flush()
            print("loaded Github Integration", flush=True)

        with redirect_stdout(f), redirect_stderr(f):
            installation = integration.get_repo_installation("conda-forge", repo)
        if "GITHUB_ACTIONS" in os.environ and os.environ["GITHUB_ACTIONS"] == "true":
            sys.stdout.flush()
            print("found Github installation", flush=True)

        with redirect_stdout(f), redirect_stderr(f):
            gh_token_data = integration.get_access_token(
                installation.id,
                permissions=permissions,
                repositories=[repo],
            )

            assert gh_token_data.permissions == permissions, gh_token_data.permissions
            assert gh_token_data.repository_selection == "selected", (
                gh_token_data.repository_selection
            )
            returned_repos = set(
                rp["name"] for rp in gh_token_data.raw_data["repositories"]
            )
            assert returned_repos == set([repo]), returned_repos

            gh_token = gh_token_data.token

        if "GITHUB_ACTIONS" in os.environ and os.environ["GITHUB_ACTIONS"] == "true":
            sys.stdout.flush()
            print("made GITHUB token and masking it for GitHub Actions", flush=True)
            print(f"::add-mask::{gh_token}", flush=True)

    except Exception:
        gh_token = None

    return gh_token
