import os
import subprocess
import json
import tempfile
import shutil
import logging
import github

from git import Repo
import requests

from conda_forge_webservices.tokens import get_app_token_for_webservices_only

# from .utils import tmp_directory

LOGGER = logging.getLogger("conda_forge_webservices.update_me")

PKGS = ["conda-smithy", "conda", "conda-build", "conda-libmamba-solver", "mamba"]


def _run_git_command(args):
    subprocess.run(['git'] + args, check=True)


def get_current_versions():
    r = subprocess.run(
        ["conda", "list", "--json"],
        capture_output=True,
        check=True,
        encoding="utf-8",
    )
    out = json.loads(r.stdout)
    vers = {}
    for item in out:
        if item["name"] in PKGS:
            vers[item["name"]] = item["version"]
    return vers


def main():
    """Get current versions from the heroku app and update if they are old.

    Note this script runs on GHA, not on the heroku app.
    """
    # keep these imports here to protect the webservice from memory errors
    # due to conda
    from conda.core.index import get_index
    from conda.models.match_spec import MatchSpec
    from conda.models.version import VersionOrder
    from conda.resolve import Resolve

    r = requests.get(
        "https://conda-forge.herokuapp.com/conda-webservice-update/versions")
    r.raise_for_status()
    installed_vers = r.json()

    index = get_index(channel_urls=['conda-forge'])
    r = Resolve(index)

    to_install = {}
    final_install = {}

    for pkg in PKGS:
        available_versions = [
            p.version for p in r.get_pkgs(MatchSpec(pkg))
            if "conda-forge" in str(p.channel)
        ]
        available_versions = sorted(available_versions, key=VersionOrder)
        latest_version = available_versions[-1]
        LOGGER.info("%s|latest|installed:" % pkg, latest_version, installed_vers[pkg])
        if VersionOrder(latest_version) != VersionOrder(installed_vers[pkg]):
            to_install[pkg] = latest_version
            final_install[pkg] = latest_version
        else:
            final_install[pkg] = installed_vers[pkg]

    if to_install:
        tmpdir = None
        try:
            gh_token = get_app_token_for_webservices_only()
            tmpdir = tempfile.mkdtemp('_recipe')

            repo_name = "conda-forge-webservices"
            clone_dir = os.path.join(tmpdir, repo_name)
            url = "https://x-access-token:{}@github.com/conda-forge/{}.git".format(
                gh_token, repo_name
            )

            repo = Repo.clone_from(url, clone_dir, depth=1)

            # keep a record around
            pth = os.path.join(clone_dir, "pkg_versions.json")
            with open(pth, "w") as fp:
                json.dump(final_install, fp)
            repo.index.add(pth)

            msg_vers = ", ".join(["{}={}".format(k, v) for k, v in to_install.items()])
            repo.index.commit("redeploy for '%s'" % msg_vers)
            repo.git.push("origin", "main")

        finally:
            if tmpdir is not None:
                shutil.rmtree(tmpdir)

        try:
            gh = github.Github(get_app_token_for_webservices_only())
            repo = gh.get_repo("conda-forge/webservices-dispatch-action")
            workflow = repo.get_workflow("tests.yml")
            workflow.create_dispatch("main")
        except Exception as e:
            print(f"workflow_dispatch for webservices-dispatch-action failed: {e}")
