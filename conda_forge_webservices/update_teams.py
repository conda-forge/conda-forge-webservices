from git import GitCommandError, Repo
from conda_build.metadata import MetaData
import github
import os

from .utils import tmp_directory

def update_team(org_name, repo_name):
    if not repo_name.endswith("-feedstock"):
        return

    gh = github.Github(os.environ['GH_TOKEN'])
    org = gh.get_organization(org_name)
    remote_repo = org.get_repo(repo_name)

    with tmp_directory() as tmp_dir:
        repo = Repo.clone_from(remote_repo.clone_url, tmp_dir)
        meta = MetaData(tmp_dir)
        updated_maintainers = set(meta.meta.get('extra', {}).get('recipe-maintainers', []))

        team_name = repo_name[:-len("-feedstock")]
        team = next(team for team in org.get_teams() if team.name == team_name)
        current_maintainers = set([maintainer.login.lower() for maintainer in team.get_members()])

        for new_maintainer in updated_maintainers - current_maintainers:
            headers, data = team._requester.requestJsonAndCheck(
                "PUT",
                team.url + "/memberships/" + new_maintainer
            )

        for old_maintainer in current_maintainers - updated_maintainers:
            headers, data = team._requester.requestJsonAndCheck(
                "DELETE",
                team.url + "/memberships/" + old_maintainer
            )


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('org')
    parser.add_argument('repo')
    args = parser.parse_args()
    update_team(args.org, args.repo)
