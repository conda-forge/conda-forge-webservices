from git import GitCommandError, Repo
from conda_build.metadata import MetaData
import github
import os
import subprocess
from .utils import tmp_directory

def update_team(org_name, repo_name):
    gh = github.Github(os.environ['GH_TOKEN'])
    org = gh.get_organization(org_name)
    remote_repo = org.get_repo(repo_name)

    with tmp_directory() as tmp_dir:
        repo = Repo.clone_from(remote_repo.clone_url, tmp_dir)
        subprocess.check_output(["conda", "smithy", "register-github", "--organization", org_name, "--add-teams", tmp_dir])


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('org')
    parser.add_argument('repo')
    args = parser.parse_args()
    update_team(args.org, args.repo)
