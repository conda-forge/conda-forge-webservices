import argparse
import datetime
import os
import subprocess
import json
import tempfile
import shutil
import logging

from git import Repo
import requests

from conda_forge_webservices.tokens import get_app_token_for_webservices_only
from conda_forge_webservices.utils import with_action_url

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
    "conda-forge-tick",
]


def _run_git_command(args):
    subprocess.run(["git", *args], check=True)


def update(repo_name, pkgs, force=False):
    # keep these imports here to protect the webservice from memory errors
    # due to conda
    from conda.core.index import get_index
    from conda.models.match_spec import MatchSpec
    from conda.models.version import VersionOrder
    from conda.resolve import Resolve

    LOGGER.info(f"updating {repo_name}")

    if repo_name == "conda-forge-webservices":
        url = "https://conda-forge.herokuapp.com/conda-webservice-update/versions"
    else:
        # We don't have a way to know which versions were actually built into the
        # docker image. Assume that the latest ones were installed.
        url = (
            f"https://raw.githubusercontent.com/conda-forge/{repo_name}"
            "/main/pkg_versions.json"
        )

    r = requests.get(url)
    r.raise_for_status()
    installed_vers = r.json()

    index = get_index(channel_urls=["conda-forge"])
    r = Resolve(index)

    to_install = {}
    final_install = {}

    for pkg in pkgs:
        available_versions = [
            p.version
            for p in r.get_pkgs(MatchSpec(pkg))
            if "conda-forge" in str(p.channel)
        ]
        available_versions = sorted(available_versions, key=VersionOrder)
        latest_version = available_versions[-1]
        installed_version = installed_vers.get(pkg, None)
        LOGGER.info(f"{pkg} - latest|installed: {latest_version}|{installed_version}")
        if installed_version is None or VersionOrder(latest_version) != VersionOrder(
            installed_version
        ):
            to_install[pkg] = latest_version
            final_install[pkg] = latest_version
        else:
            final_install[pkg] = installed_vers[pkg]

    if to_install or force:
        tmpdir = None
        try:
            gh_token = get_app_token_for_webservices_only()
            tmpdir = tempfile.mkdtemp("_cf_repo")

            clone_dir = os.path.join(tmpdir, repo_name)
            url = f"https://x-access-token:{gh_token}@github.com/conda-forge/{repo_name}.git"

            repo = Repo.clone_from(url, clone_dir, depth=1)

            # keep a record around
            tstamp = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()
            final_install["conda-forge-webservices-update-timestamp"] = tstamp
            pth = os.path.join(clone_dir, "pkg_versions.json")
            with open(pth, "w") as fp:
                json.dump(final_install, fp, indent=2)
                fp.write("\n")
            repo.index.add(pth)

            if to_install:
                if len(to_install) > 1:
                    msg = "Redeploy for package updates\n\n" + "\n".join(
                        [f"* `{k}={v}`" for k, v in to_install.items()]
                    )
                else:
                    ((k, v),) = to_install.items()
                    msg = f"Redeploy for package update: `{k}={v}`"
            else:
                msg = "forcibly redeploy"

            repo.index.commit(
                with_action_url(msg),
            )
            repo.git.push("origin", "main")

        finally:
            if tmpdir is not None:
                shutil.rmtree(tmpdir)


def main():
    """Get current versions from the heroku app and update if they are old.

    Note this script runs on GHA, not on the heroku app.
    """

    logging.basicConfig(stream=sys.stdout, level=logging.INFO)

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force", action="store_true", help="Force the service to update."
    )
    args = parser.parse_args()
    update("conda-forge-webservices", WEBSERVICE_PKGS, force=args.force)
    update("webservices-dispatch-action", DOCKER_IMAGE_PKGS, force=args.force)
