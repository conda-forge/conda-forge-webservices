import github
import os
import logging

from conda_smithy.github import configure_github_team
import textwrap
from functools import lru_cache

from ruamel.yaml import YAML
from conda_forge_webservices.tokens import get_app_token_for_webservices_only

LOGGER = logging.getLogger("conda_forge_webservices.update_teams")


@lru_cache(maxsize=None)
def get_filter_out_members():
    gh = github.Github(os.environ['GH_TOKEN'])
    org = gh.get_organization('conda-forge')
    teams = ['staged-recipes', 'help-r', 'r']
    gh_teams = list(org.get_team_by_slug(team) for team in teams)
    members = set()
    for team in gh_teams:
        members.update([m.login for m in team.get_members()])
    return members


def filter_members(members):
    out = get_filter_out_members()
    return [m for m in members if m not in out]


def get_handles(members):
    mem = ['@' + m for m in filter_members(members)]
    return ', '.join(mem)


class DummyMeta(object):
    def __init__(self, meta_yaml):
        _yml = YAML(typ='safe')
        _yml.indent(mapping=2, sequence=4, offset=2)
        _yml.width = 160
        _yml.allow_duplicate_keys = True
        self.meta = _yml.load(meta_yaml)


def update_team(org_name, repo_name, commit=None):
    if not repo_name.endswith("-feedstock"):
        return

    team_name = repo_name.replace("-feedstock", "").lower()
    if (
        team_name in ["core", "bot", "staged-recipes", "arm-arch", "systems"]
        or team_name.startswith("help-")
    ):
        return

    gh = github.Github(get_app_token_for_webservices_only())
    org = gh.get_organization(org_name)
    gh_repo = org.get_repo(repo_name)

    if resp := gh_repo.get_contents("recipe/meta.yaml"):
        keep_lines = []
        skip = 0
        for line in resp.decoded_content.decode("utf-8").splitlines():
            if line.strip().startswith("extra:"):
                skip += 1
            if skip > 0:
                keep_lines.append(line)
        assert skip == 1, "team update failed due to > 1 'extra:' sections"
        meta = DummyMeta("\n".join(keep_lines))
    elif resp := gh_repo.get_contents("recipe/recipe.yaml"):
        meta = DummyMeta(resp.decoded_content.decode("utf-8"))

    (
        current_maintainers,
        prev_maintainers,
        new_conda_forge_members,
    ) = configure_github_team(
        meta,
        gh_repo,
        org,
        repo_name.replace("-feedstock", ""),
        remove=True,
    )

    if commit:
        message = textwrap.dedent("""
            Hi! This is the friendly automated conda-forge-webservice.

            I updated the Github team because of this commit.
            """)
        newm = get_handles(new_conda_forge_members)
        if newm:
            message += textwrap.dedent("""
                - {} {} added to conda-forge. Welcome to conda-forge!
                  Go to https://github.com/orgs/conda-forge/invitation see your invitation.
            """.format(newm, "were" if newm.count(",") >= 1 else "was"))  # noqa

        addm = get_handles(
            current_maintainers - prev_maintainers - new_conda_forge_members)
        if addm:
            message += textwrap.dedent("""
                - {} {} added to this feedstock maintenance team.
            """.format(addm, "were" if addm.count(",") >= 1 else "was"))

        if addm or newm:
            message += textwrap.dedent("""
                You should get push access to this feedstock and CI services.

                Your package won't be available for installation locally until it is built
                and synced to the anaconda.org CDN (takes 1-2 hours after the build finishes).

                Feel free to join the community [Element channel](https://app.element.io/#/room/#conda-forge:matrix.org).

                NOTE: Please make sure to not push to the repository directly.
                      Use branches in your fork for any changes and send a PR.
                      More details on this are [here](https://conda-forge.org/docs/maintainer/updating_pkgs.html#forking-and-pull-requests).
            """)  # noqa

            c = gh_repo.get_commit(commit)
            c.create_comment(message)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('org')
    parser.add_argument('repo')
    args = parser.parse_args()
    update_team(args.org, args.repo)
