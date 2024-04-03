import os
import subprocess
import json
import tempfile
import shutil
import logging

from git import Repo
import requests

from conda_forge_webservices.tokens import get_app_token_for_webservices_only

# from .utils import tmp_directory

LOGGER = logging.getLogger("conda_forge_webservices.update_me")

WEBSERVICE_PKGS = [
    "conda-smithy",
]

DOCKER_IMAGE_PKGS = [
    "anaconda-client",
    "conda-smithy",
    "conda",
    "conda-build",
    "conda-libmamba-solver",
    "mamba",
]


def _run_git_command(args):
    subprocess.run(['git'] + args, check=True)


def update(repo_name, pkgs):
    # keep these imports here to protect the webservice from memory errors
    # due to conda
    from conda.core.index import get_index
    from conda.models.match_spec import MatchSpec
    from conda.models.version import VersionOrder
    from conda.resolve import Resolve

    if repo_name == "conda-forge-webservices":
        url = "https://conda-forge.herokuapp.com/conda-webservice-update/versions"
    else:
        # We don't have a way to know which versions were actually built into the
        # docker image. Assume that the latest ones were installed.
        url = (f"https://raw.githubusercontent.com/conda-forge/{repo_name}"
               "/main/pkg_versions.json")

    r = requests.get(url)
    r.raise_for_status()
    installed_vers = r.json()

    index = get_index(channel_urls=['conda-forge'])
    r = Resolve(index)

    to_install = {}
    final_install = {}

    for pkg in pkgs:
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
            tmpdir = tempfile.mkdtemp('_cf_repo')

            clone_dir = os.path.join(tmpdir, repo_name)
            url = "https://x-access-token:{}@github.com/conda-forge/{}.git".format(
                gh_token, repo_name
            )

            repo = Repo.clone_from(url, clone_dir, depth=1)

            # keep a record around
            pth = os.path.join(clone_dir, "pkg_versions.json")
            with open(pth, "w") as fp:
                json.dump(final_install, fp, indent=2)
                fp.write("\n")
            repo.index.add(pth)

            if len(to_install) > 1:
                msg = (
                    "Redeploy for package updates\n\n" +
                    "\n".join(["* `{}={}`".format(k, v) for k, v in to_install.items()])
                )
            else:
                (k, v), = to_install.items()
                msg = f"Redeploy for package update: `{k}={v}`"

            repo.index.commit(msg)
            repo.git.push("origin", "main")

        finally:
            if tmpdir is not None:
                shutil.rmtree(tmpdir)


def main():
    """Get current versions from the heroku app and update if they are old.

    Note this script runs on GHA, not on the heroku app.
    """
    update("conda-forge-webservices", WEBSERVICE_PKGS)
    update("webservices-dispatch-action", DOCKER_IMAGE_PKGS)
