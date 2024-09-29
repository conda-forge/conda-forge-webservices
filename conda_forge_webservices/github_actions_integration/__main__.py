import json
import logging
import os
import pprint
import subprocess
import sys

import click
from git import Repo

# this is the only import that should go here
# everything else should be in the functions
# this import hides the env vars and has to run first-ish
import conda_forge_webservices.github_actions_integration  # noqa: F401

LOGGER = logging.getLogger(__name__)


def _pull_docker_image():
    try:
        print("::group::docker image pull", flush=True)
        subprocess.run(
            [
                "docker",
                "pull",
                f"{os.environ['CF_FEEDSTOCK_OPS_CONTAINER_NAME']}:{os.environ['CF_FEEDSTOCK_OPS_CONTAINER_TAG']}",
            ],
        )
        sys.stderr.flush()
        sys.stdout.flush()
    finally:
        print("::endgroup::", flush=True)


@click.command(name="conda-forge-webservices-run-task")
@click.option("--task", required=True, type="str")
@click.option("--repo", required=True, type="str")
@click.option("--pr-number", required=True, type="str")
@click.option("--task-data-dir", required=True, type="str")
def main_run_task(task, repo, pr_number, task_data_dir):
    from .rerendering import rerender

    logging.basicConfig(level=logging.INFO)

    LOGGER.info("running task %s for conda-forge/%s#%s", task, repo, pr_number)

    feedstock_dir = os.path.join(
        task_data_dir,
        repo,
    )
    os.makedirs(feedstock_dir, exist_ok=True)
    repo_url = f"https://github.com/conda-forge/{repo}.git"
    git_repo = Repo.clone_from(
        repo_url,
        feedstock_dir,
    )
    git_repo.remotes.origin.fetch([f"pull/{pr_number}/head:pull/{pr_number}/head"])
    git_repo.git.switch(f"pull/{pr_number}/head")

    task_data = {"task": task, "repo": repo, "pr_number": pr_number, "task_results": {}}

    if task == "rerender":
        _pull_docker_image()
        changed, rerender_error, info_message = rerender(git_repo)
        task_data["task_results"]["changed"] = changed
        task_data["task_results"]["rerender_error"] = rerender_error
        task_data["task_results"]["info_message"] = info_message
    else:
        raise ValueError(f"Task `{task}` is not valid!")

    with open(os.path.join(task_data_dir, "task_data.json"), "w") as f:
        json.dump(task_data, f)

    subprocess.run(
        ["rm", "-rf", os.path.join(feedstock_dir, ".git")],
        check=True,
        capture_output=True,
    )


@click.command(name="conda-forge-webservices-finalize-task")
@click.option("--task-data-dir", required=True, type="str")
def main_finalize_task(task_data_dir):
    from .utils import flush_logger

    logging.basicConfig(level=logging.INFO)

    with open(os.path.join(task_data_dir, "task_data.json")) as f:
        task_data = json.load(f)

    task = task_data["task"]
    repo = task_data["repo"]
    pr_number = task_data["pr_number"]
    task_results = task_data["task_results"]

    LOGGER.info("running task %s for conda-forge/%s#%s", task, repo, pr_number)
    LOGGER.info("task results:")
    flush_logger(LOGGER)
    print(pprint.pformat(task_results), flush=True)
    flush_logger(LOGGER)
