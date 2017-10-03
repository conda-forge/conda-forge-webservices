import git
import github
import os
from .utils import tmp_directory


def update_feedstock(org_name, repo_name):
    if not repo_name.endswith("-feedstock"):
        return

    gh = github.Github(os.environ['FEEDSTOCKS_GH_TOKEN'])

    repo_gh = gh.get_repo("{}/{}".format(org_name, repo_name))
    name = repo_name[:-len("-feedstock")]

    with tmp_directory() as tmp_dir:
        feedstocks_url = (
            "https://{}@github.com/conda-forge/feedstocks.git"
            "".format(os.environ["FEEDSTOCKS_GH_TOKEN"])
        )
        feedstocks_repo = git.Repo.clone_from(
            feedstocks_url,
            tmp_dir
        )

        # Get the submodule
        feedstock_submodule = feedstocks_repo.create_submodule(
            name=name,
            path=os.path.join("feedstocks", name),
            url=repo_gh.clone_url,
            branch="master"
        )
        # Hack needed if the submodule already exists.
        # Borrows the fix accepted upstream.
        # PR: https://github.com/gitpython-developers/GitPython/pull/679
        feedstock_submodule._name = name

        # Update the feedstocks submodule
        feedstock_submodule.update(init=True, recursive=False, force=True)
        feedstock_submodule.branch.checkout(force=True)
        feedstock_submodule.update(
            init=True,
            recursive=False,
            force=True,
            to_latest_revision=True
        )

        # Submit changes
        if feedstocks_repo.is_dirty():
            author = git.Actor(
                "conda-forge-coordinator", "conda.forge.coordinator@gmail.com"
            )
            feedstocks_repo.git.add(update=True)
            feedstocks_repo.index.commit(
                "Updated feedstocks submodules. [ci skip]",
                author=author,
                committer=author
            )
            feedstocks_repo.remote().pull(rebase=True)
            feedstocks_repo.remote().push()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('org')
    parser.add_argument('repo')
    args = parser.parse_args()
    update_team(args.org, args.repo)
