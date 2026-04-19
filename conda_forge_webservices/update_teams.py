import github
import os
import re
import logging
import math

from conda_smithy.github import configure_github_team
import requests
import threading
import textwrap
from functools import cache

from ruamel.yaml import YAML
from conda_forge_webservices.tokens import (
    get_gh_client,
    get_app_token_for_webservices_only,
)
from conda_forge_webservices.utils import _test_and_raise_besides_file_not_exists
from conda_forge_webservices.utils import (
    log_title_and_message_at_level,
)

from cachetools import cachedmethod, TTLCache

LOGGER = logging.getLogger("conda_forge_webservices.update_teams")

JINJA_PAT = re.compile(r"\{\{([^\{\}]*)\}\}")


# MRB: AI generated a similar code snippet which was a
#   nearly exact copy of this SO post: https://stackoverflow.com/a/62419949
# MRB: I rewrote the function using a TTL cache w/ cachetools.
class _TeamUpdateLocks:
    def __init__(self):
        self._lock = threading.RLock()
        self._locks = TTLCache(maxsize=math.inf, ttl=2 * 60 * 60)

    @cachedmethod(cache=lambda self: self._locks, lock=lambda self: self._lock)
    def get_team_lock(self, param):
        """Generate a unique lock per `param` value.

        The locks are held in a time-to-live cache with no maximum size.

        Locks older than 2 hours will eventually be garbage collected.
        """
        return threading.RLock()


TeamUpdateLocks = _TeamUpdateLocks()


def cancel_invites_cron_job():
    token = get_app_token_for_webservices_only()
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2026-03-10",
    }
    r = requests.get(
        "https://api.github.com/orgs/conda-forge/failed_invitations",
        headers=headers,
    )

    num_processed = 0

    try:
        r.raise_for_status()
    except Exception:
        LOGGER.debug("failed to get failed invites!", exc_info=True)
        pass
    else:
        for invite in r.json():
            ri = requests.delete(
                f"https://api.github.com/orgs/conda-forge/invitations/{invite['id']}",
                headers=headers,
            )
            try:
                ri.raise_for_status()
            except Exception:
                LOGGER.debug("failed to cancel invite!", exc_info=True)
                pass
            else:
                num_processed += 1

    log_title_and_message_at_level(
        level="info",
        title=f"removed {num_processed} failed invites",
    )


def _jinja2_repl(match):
    return "${{" + match.group(1) + "}}"


def _filter_jinja2(line):
    return JINJA_PAT.sub(_jinja2_repl, line)


@cache
def get_filter_out_members():
    gh = github.Github(auth=github.Auth.Token(os.environ["GH_TOKEN"]))
    org = gh.get_organization("conda-forge")
    teams = ["staged-recipes", "help-r", "r"]
    gh_teams = list(org.get_team_by_slug(team) for team in teams)
    members = set()
    for team in gh_teams:
        members.update([m.login for m in team.get_members()])
    return members


def filter_members(members):
    out = get_filter_out_members()
    return [m for m in members if m not in out]


def get_handles(members):
    mem = ["@" + m for m in filter_members(members)]
    return ", ".join(mem)


class DummyMeta:
    def __init__(self, meta_yaml):
        parse_yml = YAML(typ="safe")
        parse_yml.indent(mapping=2, sequence=4, offset=2)
        parse_yml.width = 160
        parse_yml.allow_duplicate_keys = True
        self.meta = parse_yml.load(meta_yaml)


def get_recipe_contents(gh_repo):
    try:
        resp = gh_repo.get_contents("recipe/meta.yaml")
        return resp.decoded_content.decode("utf-8")
    except github.GithubException as e:
        _test_and_raise_besides_file_not_exists(e)
        resp = gh_repo.get_contents("recipe/recipe.yaml")
        return resp.decoded_content.decode("utf-8")


def get_recipe_dummy_meta(recipe_content):
    keep_lines = []
    skip = 0
    for line in recipe_content.splitlines():
        if line.strip().startswith("extra:"):
            skip += 1
        if skip > 0:
            keep_lines.append(_filter_jinja2(line))
    assert skip == 1, "team update failed due to > 1 'extra:' sections"
    return DummyMeta("\n".join(keep_lines))


def update_team(org_name, repo_name, commit=None):
    if not repo_name.endswith("-feedstock"):
        return

    team_name = repo_name.rsplit("-feedstock", 1)[0].lower()
    if team_name in [
        "core",
        "bot",
        "staged-recipes",
        "arm-arch",
        "systems",
    ] or team_name.startswith("help-"):
        return

    gh = get_gh_client()
    org = gh.get_organization(org_name)
    gh_repo = org.get_repo(repo_name)

    recipe_content = get_recipe_contents(gh_repo)
    meta = get_recipe_dummy_meta(recipe_content)

    with TeamUpdateLocks.get_team_lock(team_name):
        (
            current_maintainers,
            prev_maintainers,
            new_conda_forge_members,
        ) = configure_github_team(
            meta,
            gh_repo,
            org,
            team_name,
            remove=True,
        )

        if commit:
            message = textwrap.dedent("""
                Hi! This is the friendly automated conda-forge-webservice.

                I updated the Github team because of this commit.
                """)
            newm = get_handles(new_conda_forge_members)
            if newm:
                message += textwrap.dedent(
                    """
                    - {} {} added to conda-forge. Welcome to conda-forge!
                    Go to https://github.com/orgs/conda-forge/invitation see your invitation.
                """.format(newm, "were" if newm.count(",") >= 1 else "was")  # noqa
                )

            addm = get_handles(
                current_maintainers - prev_maintainers - new_conda_forge_members
            )
            if addm:
                message += textwrap.dedent(
                    """
                    - {} {} added to this feedstock maintenance team.
                """.format(addm, "were" if addm.count(",") >= 1 else "was")
                )

            if addm or newm:
                message += textwrap.dedent("""
                    You should get push access to this feedstock and CI services.

                    Your package won't be available for installation locally until it is built
                    and synced to the anaconda.org CDN (takes 1-2 hours after the build finishes).

                    Feel free to join the community on [Zulip](https://conda-forge.zulipchat.com).

                    NOTE: Please make sure to not push to the repository directly.
                        Use branches in your fork for any changes and send a PR.
                        More details on this are [here](https://conda-forge.org/docs/maintainer/updating_pkgs.html#forking-and-pull-requests).
                """)  # noqa

                c = gh_repo.get_commit(commit)
                c.create_comment(message)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("org")
    parser.add_argument("repo")
    args = parser.parse_args()
    update_team(args.org, args.repo)
