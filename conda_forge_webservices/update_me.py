import os
from git import Repo
from concurrent.futures import ProcessPoolExecutor

from .utils import tmp_directory


def _run_solver():
    from conda_build.conda_interface import (
        VersionOrder, MatchSpec,
        get_installed_version, root_dir, get_index, Resolve)

    pkgs = ["conda-build", "conda-smithy", "conda-forge-pinning"]
    installed_vers = get_installed_version(root_dir, pkgs)
    index = get_index(channel_urls=['conda-forge'])
    r = Resolve(index)

    to_install = {}

    for pkg in pkgs:
        available_versions = [p.version for p in r.get_pkgs(MatchSpec(pkg))]
        available_versions = sorted(available_versions, key=VersionOrder)
        latest_version = available_versions[-1]
        print("%s:" % pkg, latest_version, installed_vers[pkg])
        if VersionOrder(latest_version) > VersionOrder(installed_vers[pkg]):
            to_install[pkg] = latest_version

    return to_install


def update_me():
    """
    Update the webservice on Heroku by pushing a commit to this repo.
    """

    # conda build appears to cache data or something and the memory usage
    # spikes - so we run it in a separate process
    with ProcessPoolExecutor(max_workers=1) as pool:
        to_install = pool.submit(_run_solver).result()

    if not to_install:
        return

    with tmp_directory() as tmp_dir:
        repo_name = "conda-forge-webservices"
        clone_dir = os.path.join(tmp_dir, repo_name)
        url = "https://{}@github.com/conda-forge/{}.git".format(
            os.environ['GH_TOKEN'], repo_name)

        repo = Repo.clone_from(url, clone_dir)
        msg_vers = ", ".join(["{}={}".format(k, v) for k, v in to_install.items()])
        repo.index.commit("Empty commit to rebuild for {}".format(msg_vers))
        repo.git.push("origin", "master")
