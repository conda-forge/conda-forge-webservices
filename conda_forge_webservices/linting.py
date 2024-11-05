import os
import textwrap
import time
from tempfile import TemporaryDirectory
import logging
from pathlib import Path
from typing import TypedDict
import uuid

from git import GitCommandError, Repo
import conda_smithy.lint_recipe

from conda_forge_webservices.tokens import get_gh_client
from ._version import __version__

LOGGER = logging.getLogger("conda_forge_webservices.linting")
SKIP_MSGS = [
    "[ci skip]",
    "[skip ci]",
    "[lint skip]",
    "[skip lint]",
]
LINT_VIA_GHA = True


class LintInfo(TypedDict):
    message: str
    status: str
    sha: str


def lint_via_github_actions(full_name: str, pr_num: int) -> bool:
    gh = get_gh_client()
    repo = gh.get_repo(full_name)
    repo_owner, repo_name = full_name.split("/")
    pr = repo.get_pull(pr_num)
    sha = pr.head.sha
    commit = gh.get_repo(pr.head.repo.full_name).get_git_commit(sha)
    commit_msg = commit.message

    should_skip = any([msg in commit_msg for msg in SKIP_MSGS])
    if should_skip:
        return False

    uid = uuid.uuid4().hex
    ref = __version__.replace("+", ".")
    workflow = gh.get_repo("conda-forge/conda-forge-webservices").get_workflow(
        "webservices-workflow-dispatch.yml"
    )
    running = workflow.create_dispatch(
        ref=ref,
        inputs={
            "task": "lint",
            "repo": repo_name,
            "pr_number": str(pr_num),
            "container_tag": ref,
            "uuid": uid,
        },
    )

    if running:
        _set_pr_status(repo_owner, repo_name, sha, "pending")

    return running


def find_recipes(path: Path) -> list[Path]:
    """
    Returns all `meta.yaml` and `recipe.yaml` files in the given path.
    """
    meta_yamls = path.rglob("meta.yaml")
    recipe_yamls = path.rglob("recipe.yaml")

    return [x for x in (list(meta_yamls) + list(recipe_yamls))]


def lint_all_recipes(all_recipe_dir: Path, base_recipes: list[Path]) -> tuple[str, str]:
    """
    Lint all recipes in the given directory.
    """
    recipes = find_recipes(all_recipe_dir)
    all_pass = True
    messages = []
    hints = []
    hints_found = False

    # Exclude some things from our list of recipes.
    # Sort the recipes for consistent linting order (which glob doesn't give us).
    pr_recipes = sorted(set(recipes) - set(base_recipes))

    rel_pr_recipes = []
    for recipe in pr_recipes:
        recipe_dir = recipe.parent
        rel_path = recipe.relative_to(all_recipe_dir)
        rel_pr_recipes.append(rel_path)

        try:
            lints, hints = conda_smithy.lint_recipe.main(
                str(recipe_dir), conda_forge=True, return_hints=True
            )

        except Exception as err:
            import traceback

            LOGGER.warning("LINTING ERROR: %s", repr(err))
            LOGGER.warning("LINTING ERROR TRACEBACK: %s", traceback.format_exc())
            lints = [
                "Failed to even lint the recipe, probably because "
                "of a conda-smithy bug :cry:. "
                "This likely indicates a problem in your `meta.yaml`, though. "
                "To get a traceback to help figure out what's going on, "
                "install conda-smithy "
                "and run `conda smithy recipe-lint .` from the recipe directory. "
            ]
        if lints:
            all_pass = False
            messages.append(
                "\nFor **{}**:\n\n{}".format(
                    rel_path, "\n".join(f" * {lint}" for lint in lints)
                )
            )
        if hints:
            hints_found = True
            messages.append(
                "\nFor **{}**:\n\n{}".format(
                    rel_path, "\n".join(f" * {hint}" for hint in hints)
                )
            )

    # Put the recipes in the form "```recipe/a```, ```recipe/b```".
    recipe_code_blocks = ", ".join(f"```{r}```" for r in rel_pr_recipes)

    good = textwrap.dedent(
        f"""
    Hi! This is the friendly automated conda-forge-linting service.

    I just wanted to let you know that I linted all conda-recipes in your PR ({recipe_code_blocks}) and found it was in an excellent condition.

    """  # noqa: E501
    )

    mixed = good + textwrap.dedent("""
    I do have some suggestions for making it better though...

    {}
    """).format("\n".join(messages))

    bad = textwrap.dedent(
        f"""
    Hi! This is the friendly automated conda-forge-linting service.

    I wanted to let you know that I linted all conda-recipes in your PR ({recipe_code_blocks}) and found some lint.

    Here's what I've got...

    {{}}
    """  # noqa: E501
    ).format("\n".join(messages))

    if not pr_recipes:
        message = textwrap.dedent("""
            Hi! This is the friendly automated conda-forge-linting service.

            I was trying to look for recipes to lint for you, but couldn't find any.
            Please ping the 'conda-forge/core' team (using the @ notation in a comment) if you believe this is a bug.
            """)  # noqa
        status = "no recipes"
    elif all_pass and hints_found:
        message = mixed
        status = "mixed"
    elif all_pass:
        message = good
        status = "good"
    else:
        message = bad
        status = "bad"

    return message, status


def _set_pr_status(
    owner: str, repo_name: str, sha: str, status: str, target_url: str | None = None
):
    if target_url is not None:
        kwargs = {"target_url": target_url}
    else:
        kwargs = {}

    gh = get_gh_client()
    user = gh.get_user(owner)
    repo = user.get_repo(repo_name)
    commit = repo.get_commit(sha)
    commit.create_status(
        status,
        description="Linting in progress...",
        context="conda-forge-linter",
        **kwargs,
    )


def compute_lint_message(
    repo_owner: str,
    repo_name: str,
    pr_id: int,
    ignore_base: bool = False,
    set_pending_status: bool = True,
) -> LintInfo | None:
    gh = get_gh_client()

    owner = gh.get_user(repo_owner)
    remote_repo = owner.get_repo(repo_name)

    mergeable = None
    while mergeable is None:
        time.sleep(1.0)
        pull_request = remote_repo.get_pull(pr_id)
        if pull_request.state != "open":
            return None
        mergeable = pull_request.mergeable

    tmp_dir = TemporaryDirectory(suffix="_recipe")

    try:
        # Check if pr_id is provided and set the environment variable accordingly
        if pr_id is not None and repo_name == "staged-recipes":
            os.environ["STAGED_RECIPES_PR_NUMBER"] = str(pr_id)

        repo = Repo.clone_from(remote_repo.clone_url, tmp_dir.name, depth=1)

        # Retrieve the PR refs.
        try:
            repo.remotes.origin.fetch(
                [
                    f"pull/{pr_id}/head:pull/{pr_id}/head",
                    f"pull/{pr_id}/merge:pull/{pr_id}/merge",
                ]
            )
            ref_head = repo.refs[f"pull/{pr_id}/head"]
            ref_merge = repo.refs[f"pull/{pr_id}/merge"]
        except GitCommandError:
            # Either `merge` doesn't exist because the PR was opened
            # in conflict or it is closed and it can't be the latter.
            repo.remotes.origin.fetch([f"pull/{pr_id}/head:pull/{pr_id}/head"])
            ref_head = repo.refs[f"pull/{pr_id}/head"]
        sha = str(ref_head.commit.hexsha)

        # Check if the linter is skipped via the commit message.
        commit_msg = repo.commit(sha).message
        should_skip = any([msg in commit_msg for msg in SKIP_MSGS])
        if should_skip:
            return None

        if set_pending_status:
            _set_pr_status(repo_owner, repo_name, sha, "pending")

        # Raise an error if the PR is not mergeable.
        if not mergeable:
            message = textwrap.dedent("""
                Hi! This is the friendly automated conda-forge-linting service.

                I was trying to look for recipes to lint for you, but it appears we have a merge conflict.
                Please try to merge or rebase with the base branch to resolve this conflict.

                Please ping the 'conda-forge/core' team (using the @ notation in a comment) if you believe this is a bug.
                """)  # noqa
            status = "merge_conflict"

            return {"message": message, "status": status, "sha": sha}

        # Collect recipes from base that should be ignored.
        base_recipes = []
        if ignore_base:
            num_parents = len(ref_merge.commit.parents)
            assert num_parents == 2, textwrap.dedent(
                f"""
                   Expected merging our PR with the base branch would have two parents.
                   Instead there were {num_parents} parents found. :/
                   """
            )
            base_commit = (set(ref_merge.commit.parents) - {ref_head.commit}).pop()
            ref_base = repo.create_head(f"pull/{pr_id}/base", base_commit)
            ref_base.checkout(force=True)
            base_recipes = find_recipes(Path(tmp_dir.name))

        # Get the list of recipes and prep for linting.
        ref_merge.checkout(force=True)

        message, status = lint_all_recipes(Path(tmp_dir.name), base_recipes)
    finally:
        # Remove the environment variable if it was set in this function
        os.environ.pop("STAGED_RECIPES_PR_NUMBER", None)

        tmp_dir.cleanup()

    pull_request = remote_repo.get_pull(pr_id)
    if pull_request.state == "open":
        return {"message": message, "status": status, "sha": sha}
    else:
        if set_pending_status:
            # won't happen later with a comment and we should not leave things pending
            _set_pr_status(repo_owner, repo_name, sha, status)
        return None


def comment_on_pr(
    owner: str,
    repo_name: str,
    pr_id: int,
    message: str,
    force: bool = False,
    search: str | None = None,
):
    gh = get_gh_client()

    user = gh.get_user(owner)
    repo = user.get_repo(repo_name)
    issue = repo.get_issue(pr_id)

    if force:
        return issue.create_comment(message)

    comments = list(issue.get_comments())
    comment_owners = [comment.user.login for comment in comments]

    my_last_comment = None
    try:
        my_login = gh.get_user().login
    except Exception:
        if "CF_WEBSERVICES_TEST" in os.environ:
            my_login = "conda-forge-curator[bot]"
        else:
            my_login = "conda-forge-webservices[bot]"
    if my_login in comment_owners:
        my_comments = [
            comment for comment in comments if comment.user.login == my_login
        ]
        if search is not None:
            my_comments = [comment for comment in my_comments if search in comment.body]
        if len(my_comments) > 0:
            my_last_comment = my_comments[-1]

    # Only comment if we haven't before, or if the message we have is different.
    if my_last_comment is None or my_last_comment.body != message:
        my_last_comment = issue.create_comment(message)

    return my_last_comment


def set_pr_status(
    owner: str, repo_name: str, lint_info: LintInfo, target_url: str | None = None
):
    gh = get_gh_client()

    user = gh.get_user(owner)
    repo = user.get_repo(repo_name)
    if lint_info:
        commit = repo.get_commit(lint_info["sha"])

        # get the last github status by the linter, if any
        # API emmits these in reverse time order so first is latest
        statuses = commit.get_statuses()
        last_status = None
        for status in statuses:
            if status.context == "conda-forge-linter":
                last_status = status
                break

        # convert the linter status to a state
        lint_status_to_state = {"good": "success", "mixed": "success"}
        lint_new_state = lint_status_to_state.get(lint_info["status"], "failure")

        # make a status only if it is different or we have not ever done it
        # for this commit
        if (
            last_status is None
            or last_status.state != lint_new_state
            or last_status.target_url != target_url
        ):
            if lint_info["status"] == "good":
                commit.create_status(
                    "success",
                    description="All recipes are excellent.",
                    context="conda-forge-linter",
                    target_url=target_url,
                )
            elif lint_info["status"] == "mixed":
                commit.create_status(
                    "success",
                    description="Some recipes have hints.",
                    context="conda-forge-linter",
                    target_url=target_url,
                )
            else:
                commit.create_status(
                    "failure",
                    description="Some recipes need some changes.",
                    context="conda-forge-linter",
                    target_url=target_url,
                )


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("repo")
    parser.add_argument("pr", type=int)
    parser.add_argument(
        "--enable-commenting", help="Turn on PR commenting", action="store_true"
    )
    parser.add_argument(
        "--ignore-base",
        help="Ignore recipes in the base branch of the PR",
        action="store_true",
    )

    args = parser.parse_args()
    owner, repo_name = args.repo.split("/")

    lint_info = compute_lint_message(owner, repo_name, args.pr, args.ignore_base)

    if not lint_info:
        print("Linting was skipped.")
    elif args.enable_commenting:
        msg = comment_on_pr(owner, repo_name, args.pr, lint_info["message"])
        set_pr_status(owner, repo_name, lint_info, target_url=msg.html_url)
    else:
        print(
            "Comments not published, but the following would "
            "have been the message:\n{}".format(lint_info["message"])
        )


if __name__ == "__main__":
    main()
