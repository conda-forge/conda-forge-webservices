from git import GitCommandError, Repo
from conda_build.metadata import MetaData
import github
import os
import subprocess
from .utils import tmp_directory
from conda_smithy.github import configure_github_team, get_cached_team
import textwrap
from functools import lru_cache
import conda_build.api


@lru_cache(maxsize=None)
def get_filter_out_members():
    gh = github.Github(os.environ['GH_TOKEN'])
    org = gh.get_organization('conda-forge')
    teams = ['staged-recipes', 'help-r']
    gh_teams = list(team for team in org.get_teams() if team.name in teams)
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


def update_team(org_name, repo_name, commit=None):
    if not repo_name.endswith("-feedstock"):
        return

    gh = github.Github(os.environ['GH_TOKEN'])
    org = gh.get_organization(org_name)
    gh_repo = org.get_repo(repo_name)

    with tmp_directory() as tmp_dir:
        repo = Repo.clone_from(gh_repo.clone_url, tmp_dir)
        meta = conda_build.api.render(tmp_dir,
                  permit_undefined_jinja=True, finalize=False,
                  bypass_env_check=True, trim_skip=False)[0][0]

        current_maintainers, prev_maintainers, new_conda_forge_members = \
            configure_github_team(meta, gh_repo, org, repo_name.replace("-feedstock", ""))

        if commit:
            message = textwrap.dedent("""
                Hi! This is the friendly automated conda-forge-webservice.

                I updated the Github team because of this commit.
                """)
            newm = get_handles(new_conda_forge_members)
            if newm:
                message += textwrap.dedent("""
                    - {} {} added to conda-forge. Welcome to conda-forge!"
                      Go to https://github.com/orgs/conda-forge/invitation see your invitation.
                """.format(newm, "were" if newm.count(",") >= 1 else "was"))

            addm = get_handles(current_maintainers - prev_maintainers - new_conda_forge_members)
            if addm:
                message += textwrap.dedent("""
                    - {} {} added to this feedstock maintenance team.
                """.format(addm, "were" if addm.count(",") >= 1 else "was"))

            if addm or newm:
                message += textwrap.dedent("""
                    You should get push access to this feedstock and CI services.

                    Feel free to join the community [chat room](https://gitter.im/conda-forge/conda-forge.github.io).

                    NOTE: Please make sure to not push to the repository directly.
                          Use branches in your fork for any changes and send a PR.
                          More details [here](https://conda-forge.org/docs/conda-forge_gotchas.html#using-a-fork-vs-a-branch-when-updating-a-recipe)
                """)

                c = gh_repo.get_commit(commit)
                c.create_comment(message)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('org')
    parser.add_argument('repo')
    args = parser.parse_args()
    update_team(args.org, args.repo)
