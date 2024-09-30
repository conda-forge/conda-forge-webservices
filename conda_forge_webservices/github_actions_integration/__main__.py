import json
import logging
import os
import pprint
import subprocess
import sys
import tempfile

import click
from conda_forge_feedstock_ops.os_utils import sync_dirs
from git import Repo

from .utils import (
    comment_and_push_if_changed,
    dedent_with_escaped_continue,
    flush_logger,
    mark_pr_as_ready_for_review,
)
from .api_sessions import create_api_sessions
from .rerendering import rerender


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
@click.option("--task", required=True, type=str)
@click.option("--repo", required=True, type=str)
@click.option("--pr-number", required=True, type=str)
@click.option("--task-data-dir", required=True, type=str)
def main_run_task(task, repo, pr_number, task_data_dir):
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
        changed, rerender_error, info_message, commit_message = rerender(git_repo)
        task_data["task_results"]["changed"] = changed
        task_data["task_results"]["rerender_error"] = rerender_error
        task_data["task_results"]["info_message"] = info_message
        task_data["task_results"]["commit_message"] = commit_message
    else:
        raise ValueError(f"Task `{task}` is not valid!")

    with open(os.path.join(task_data_dir, "task_data.json"), "w") as f:
        json.dump(task_data, f)

    subprocess.run(
        ["rm", "-rf", os.path.join(feedstock_dir, ".git")],
        check=True,
        capture_output=True,
    )


def _push_rerender_changes(
    rerender_error,
    info_message,
    changed,
    git_repo,
    pr,
    pr_branch,
    pr_owner,
    pr_repo,
    repo_name,
):
    more_info_message = "\n" + dedent_with_escaped_continue(
        """
        The following suggestions might help debug any issues:
        * Is the `recipe/{{meta.yaml,recipe.yaml}}` file valid?
        * If there is a `recipe/conda-build-config.yaml` file in \\
        the feedstock make sure that it is compatible with the current \\
        [global pinnnings]({}).
        * Is the fork used for this PR on an organization or user GitHub account? \\
        Automated rerendering via the webservices admin bot only works for user \\
        GitHub accounts.
    """.format(
            "https://github.com/conda-forge/conda-forge-pinning-feedstock/"
            "blob/master/recipe/conda_build_config.yaml"
        )
    )
    if rerender_error:
        if info_message is None:
            info_message = ""
        info_message += more_info_message

    push_error = comment_and_push_if_changed(
        action="rerender",
        changed=changed,
        error=rerender_error,
        git_repo=git_repo,
        pull=pr,
        pr_branch=pr_branch,
        pr_owner=pr_owner,
        pr_repo=pr_repo,
        repo_name=repo_name,
        close_pr_if_no_changes_or_errors=False,
        help_message=(
            " or you can try [rerendering locally]"
            "(https://conda-forge.org/docs/maintainer/updating_pkgs.html"
            "#rerendering-with-conda-smithy-locally"
        ),
        info_message=info_message,
    )

    if rerender_error or push_error:
        raise RuntimeError(
            f"Rerendering failed! error in push|rerender: {push_error}|{rerender_error}"
        )


@click.command(name="conda-forge-webservices-finalize-task")
@click.option("--task-data-dir", required=True, type=str)
def main_finalize_task(task_data_dir):
    logging.basicConfig(level=logging.INFO)

    with open(os.path.join(task_data_dir, "task_data.json")) as f:
        task_data = json.load(f)

    task = task_data["task"]
    repo = task_data["repo"]
    pr_number = task_data["pr_number"]
    task_results = task_data["task_results"]

    LOGGER.info("finalizing task %s for conda-forge/%s#%s", task, repo, pr_number)
    LOGGER.info("task results:")
    flush_logger(LOGGER)
    print(pprint.pformat(task_results), flush=True)
    flush_logger(LOGGER)

    with tempfile.TemporaryDirectory() as tmpdir:
        # commit the changes
        if task in ["rerender"]:
            _, gh = create_api_sessions()
            gh_repo = gh.get_repo(f"conda-forge/{repo}")
            pr = gh_repo.get_pull(int(pr_number))
            pr_branch = pr.head.ref
            pr_owner = pr.head.repo.owner.login
            pr_repo = pr.head.repo.name
            repo_url = f"https://github.com/{pr_owner}/{pr_repo}.git"
            feedstock_dir = os.path.join(
                tmpdir,
                pr_repo,
            )
            git_repo = Repo.clone_from(
                repo_url,
                feedstock_dir,
                branch=pr_branch,
            )

            source_feedstock_dir = os.path.join(
                task_data_dir,
                repo,
            )

            sync_dirs(
                source_feedstock_dir,
                feedstock_dir,
                ignore_dot_git=True,
                update_git=True,
            )
            subprocess.run(
                ["git", "add", "."],
                cwd=feedstock_dir,
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "commit",
                    "-m",
                    task_results["commit_message"],
                    "--allow-empty",
                ],
                cwd=feedstock_dir,
                check=True,
            )

        # now do any comments and/or pushes
        if task == "rerender":
            if pr.state == "closed":
                raise RuntimeError("Closed PRs cannot be rerendered!")

            _push_rerender_changes(
                task_results["rerender_error"],
                task_results["info_message"],
                task_results["changed"],
                git_repo,
                pr,
                pr_branch,
                pr_owner,
                pr_repo,
                f"conda-forge/{repo}",
            )

            # if the pr was made by the bot, mark it as ready for review
            if pr.title == "MNT: rerender" and pr.user.login == "conda-forge-admin":
                mark_pr_as_ready_for_review(pr)
        else:
            raise ValueError(f"Task `{task}` is not valid!")
