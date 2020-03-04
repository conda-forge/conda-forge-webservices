import os
import subprocess
import tempfile
from git import Repo

import contextlib

import json

import requests

PKGS = ["conda-build", "conda-smithy", "conda-forge-pinning"]


def _run_git_command(args):
    subprocess.run(['git'] + args, check=True)


# https://stackoverflow.com/questions/6194499/pushd-through-os-system
@contextlib.contextmanager
def pushd(new_dir):
    previous_dir = os.getcwd()
    os.chdir(new_dir)
    try:
        yield
    finally:
        os.chdir(previous_dir)


def get_current_versions():
    r = subprocess.run(["conda", "list"], capture_output=True)
    out = r.stdout.decode("utf-8")
    vers = {}
    for line in out.split("\n"):
        for pkg in PKGS:
            if pkg in line:
                items = line.split()
                vers[pkg] = items[1]
    return vers


def main():
    """Get current versions from the heroku app and update if they are old.

    Note this script runs on CircleCI, not on the heroku app.
    """
    # keep these imports here to protect the webservice from memory errors
    # due to conda
    from conda_build.conda_interface import (
        VersionOrder, MatchSpec, get_index, Resolve)

    r = requests.get(
        "https://conda-forge.herokuapp.com/conda-webservice-update/versions")
    r.raise_for_status()
    installed_vers = r.json()

    index = get_index(channel_urls=['conda-forge'])
    r = Resolve(index)

    to_install = {}
    final_install = {}

    for pkg in PKGS:
        available_versions = [p.version for p in r.get_pkgs(MatchSpec(pkg))]
        available_versions = sorted(available_versions, key=VersionOrder)
        latest_version = available_versions[-1]
        print("%s|latest|installed:" % pkg, latest_version, installed_vers[pkg])
        if VersionOrder(latest_version) != VersionOrder(installed_vers[pkg]):
            to_install[pkg] = latest_version
            final_install[pkg] = latest_version
        else:
            final_install[pkg] = installed_vers[pkg]

    if to_install:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_name = "conda-forge-webservices"
            clone_dir = os.path.join(tmpdir, repo_name)
            url = "https://{}@github.com/conda-forge/{}.git".format(
                os.environ['GH_TOKEN'], repo_name)

            repo = Repo.clone_from(url, clone_dir)

            # keep a record around
            pth = os.path.join(clone_dir, "pkg_versions.json")
            with open(pth, "w") as fp:
                json.dump(final_install, fp)
            repo.index.add(pth)

            msg_vers = ", ".join(["{}={}".format(k, v) for k, v in to_install.items()])
            repo.index.commit("[ci skip] ***NO_CI*** redeploy for '%s'" % msg_vers)
            repo.git.push("origin", "master")
