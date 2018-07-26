import os
from git import Repo, Actor

from conda_build.conda_interface import (VersionOrder, MatchSpec,
    get_installed_version, root_dir, get_index, Resolve)

from .utils import tmp_directory


def update_me():
    pkgs = ["conda-build", "conda-smithy", "conda-forge-pinning"]
    installed_vers = get_installed_version(root_dir, pkgs)
    index = get_index(channel_urls=['conda-forge'])
    r = Resolve(index)

    to_install = {}

    for pkg in pkgs:
        available_versions = [p.version for p in r.get_pkgs(MatchSpec(pkg))]
        available_versions = sorted(available_versions, key=VersionOrder)
        latest_version = available_versions[-1]
        print(latest_version, installed_vers[pkg])
        if VersionOrder(latest_version) > VersionOrder(installed_vers[pkg]):
            to_install[pkg] = latest_version

    if not to_install:
        return

    with tmp_directory() as tmp_dir:
        repo_name = "conda-forge-webservices"
        clone_dir = os.path.join(tmp_dir, repo_name)
        url = "https://{}@github.com/conda-forge/{}.git".format(
            os.environ['GH_TOKEN'], repo_name)

        repo = Repo.clone_from(url, clone_dir)
        msg_vers = ", ".join(["{}={}".format(k, v) for k,v in to_install.items()])
        author = Actor("conda-forge-admin", "pelson.pub+conda-forge@gmail.com")
        repo.index.commit("Empty commit to rebuild for {}".format(msg_vers))
        repo.git.push("origin", "master")

