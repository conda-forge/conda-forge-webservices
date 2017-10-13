import git
import jinja2
import os
from .utils import tmp_directory


def handle_feedstock_event(org_name, repo_name):
    if repo_name == "staged-recipes":
        update_listing()
    elif repo_name.endswith("-feedstock"):
        update_feedstock(org_name, repo_name)


def update_listing():
    with tmp_directory() as tmp_dir:
        feedstocks_url = (
            "https://{}@github.com/conda-forge/feedstocks.git"
            "".format(os.environ["FEEDSTOCKS_GH_TOKEN"])
        )
        feedstocks_repo = git.Repo.clone_from(
            feedstocks_url,
            tmp_dir
        )
        feedstocks_dir = os.path.dirname(feedstocks_repo.git_dir)

        repos = sorted(os.listdir(os.path.join(
            feedstocks_dir, "feedstocks"
        )))

        env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(os.path.join(
                os.path.dirname(__file__), "html"
            ))
        )
        context = {"feedstock_names": repos}
        tmpl = env.get_template("feedstocks.html.tmpl")
        feedstocks_html = os.path.join(feedstocks_dir, "feedstocks.html")
        with open(feedstocks_html, 'w') as fh:
            fh.write(tmpl.render(context))

        if feedstocks_repo.is_dirty(untracked_files=True):
            author = git.Actor(
                "conda-forge-coordinator", "conda.forge.coordinator@gmail.com"
            )
            feedstocks_repo.index.add([os.path.relpath(
                feedstocks_html, feedstocks_dir
            )])
            feedstocks_repo.index.commit(
                "Updated the feedstock listing.",
                author=author,
                committer=author
            )
            feedstocks_repo.remote().pull(rebase=True)
            feedstocks_repo.remote().push()


def update_feedstock(org_name, repo_name):
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
            url="https://github.com/{0}/{1}.git".format(org_name, repo_name),
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
        if feedstocks_repo.is_dirty(untracked_files=True):
            author = git.Actor(
                "conda-forge-coordinator", "conda.forge.coordinator@gmail.com"
            )
            feedstocks_repo.git.add(update=True)
            feedstocks_repo.index.commit(
                "Updated the {0} feedstock.".format(name),
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
    update_feedstock(args.org, args.repo)
