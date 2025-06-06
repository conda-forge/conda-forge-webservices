import os
import logging
import shutil
import time
import tempfile
from contextlib import contextmanager

import github

ALLOWED_CMD_NON_FEEDSTOCKS = ["staged-recipes", "admin-requests"]
LOGGER = logging.getLogger("conda_forge_webservices")


@contextmanager
def tmp_directory():
    tmp_dir = tempfile.mkdtemp("_recipe")
    yield tmp_dir
    shutil.rmtree(tmp_dir)


# https://stackoverflow.com/questions/6194499/pushd-through-os-system
@contextmanager
def pushd(new_dir):
    previous_dir = os.getcwd()
    os.chdir(new_dir)
    try:
        yield
    finally:
        os.chdir(previous_dir)


def parse_conda_pkg(pkg):
    """Parse a conda package into its parts.

    code due to Isuru F. and CJ Wright

    Returns platform, name, version and build string
    """
    if pkg.endswith(".tar.bz2"):
        pkg = pkg[: -len(".tar.bz2")]
    elif pkg.endswith(".conda"):
        pkg = pkg[: -len(".conda")]
    else:
        raise RuntimeError("Can only process packages that end in .tar.bz2 or .conda!")
    plat, pkg_name = pkg.split("/")
    name_ver, build = pkg_name.rsplit("-", 1)
    name, ver = name_ver.rsplit("-", 1)
    return plat, name, ver, build


def with_action_url(msg: str) -> str:
    action_url = os.getenv("ACTION_URL")
    if action_url:
        msg += f"\n\nGenerated by {action_url}"
    return msg


def get_workflow_run_from_uid(workflow, uid, ref):
    for _ in range(10):
        time.sleep(1)
        run = _inner_get_workflow_run_from_uid(workflow, uid, ref)
        if run:
            return run
    return None


def _inner_get_workflow_run_from_uid(workflow, uid, ref):
    num_try = 0
    max_try = 100
    for run in workflow.get_runs(branch=ref, event="workflow_dispatch"):
        if uid in run.name:
            return run

        num_try += 1
        if num_try > max_try:
            break

    return None


def _test_and_raise_besides_file_not_exists(e: github.GithubException):
    if isinstance(e, github.UnknownObjectException):
        return
    if e.status == 404 and "No object found" in e.data["message"]:
        return
    raise e


def log_title_and_message_at_level(*, level, title, msg=None):
    func = getattr(LOGGER, level)
    total_msg = f"""
===================================================
>>> {title}"""
    if msg is not None:
        total_msg += f"\n{msg.rstrip()}"
    total_msg += "\n==================================================="
    func(total_msg)
