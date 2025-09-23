from git import GitCommandError, Repo, Actor
import github
import io
import os
import re
import time
import shutil
import tempfile
import textwrap
import uuid
from ruamel.yaml import YAML
import requests
from requests.exceptions import RequestException
import logging
from typing import Literal

# from .utils import tmp_directory
from .linting import (
    compute_lint_message,
    comment_on_pr,
    set_pr_status,
    lint_via_github_actions,
    LINT_VIA_GHA,
)
from .update_teams import update_team
from .utils import (
    ALLOWED_CMD_NON_FEEDSTOCKS,
    with_action_url,
    get_workflow_run_from_uid,
    _test_and_raise_besides_file_not_exists,
)
from ._version import __version__
from conda_forge_webservices.tokens import (
    get_app_token_for_webservices_only,
    get_gh_client,
    inject_app_token_into_feedstock,
    inject_app_token_into_feedstock_readonly,
)

LOGGER = logging.getLogger("conda_forge_webservices.commands")
NUM_GIT_CLONE_TRIES = 10
NUM_GH_API_TRIES = 10

pre = r"@conda-forge-(admin|linter)\s*[,:]?\s*"
COMMAND_PREFIX = re.compile(pre, re.I)
ADD_NOARCH_MSG = re.compile(pre + "(please )?(add|make) `?noarch:? python`?", re.I)
RERENDER_MSG = re.compile(pre + "(please )?re-?render", re.I)
RESTART_CI = re.compile(pre + "(please )?restart (build|builds|ci)", re.I)
LINT_MSG = re.compile(pre + "(please )?(re-?)?lint", re.I)
UPDATE_TEAM_MSG = re.compile(pre + "(please )?(update|refresh) (the )?team", re.I)
UPDATE_CB3_MSG = re.compile(
    pre + "(please )?update (for )?(cb|conda[- ]build)[- ]?3", re.I
)
PING_TEAM = re.compile(pre + r"(please )?ping (?P<team>\S+)", re.I)
RERUN_BOT = re.compile(pre + "(please )?rerun (the )?bot", re.I)
ADD_BOT_AUTOMERGE = re.compile(pre + "(please )?(add|enable) bot auto-?merge", re.I)
REMOVE_BOT_AUTOMERGE = re.compile(
    pre + "(please )?(remove|delete|stop|disable) bot auto-?merge", re.I
)
ADD_USER = re.compile(pre + r"(please )?add user @(?P<user>\S+)$", re.I)
REMOVE_USER = re.compile(pre + r"(please )?remove user @(?P<user>\S+)$", re.I)
UPDATE_VERSION = re.compile(
    pre + r"(please )?update (the )?version( to (?P<ver>\S+))?",
    re.I,
)


def _get_yaml_parser(typ="rt"):
    parser = YAML(typ=typ)
    parser.indent(mapping=2, sequence=4, offset=2)
    parser.width = 320
    parser.preserve_quotes = True
    return parser


def _get_conda_forge_yml(org_name: str, repo_name: str) -> dict:
    url = (
        f"https://raw.githubusercontent.com/{org_name}/{repo_name}/main/conda-forge.yml"
    )
    try:
        r = requests.get(url)
        r.raise_for_status()
        yaml = _get_yaml_parser()
        return yaml.load(r.text)
    except requests.HTTPError:
        return {}


def _find_reactable_comment(
    repo: github.Repository.Repository,
    issue_number: int,
    comment_id: int | None = None,
    review_id: int | None = None,
):
    if len([arg for arg in (comment_id, review_id) if arg is not None]) != 1:
        raise ValueError("Must provide either comment_id or review_id")

    if comment_id == -1:  # we pass comment_id = -1 for issue/PR descriptions
        return repo.get_issue(issue_number)
    elif comment_id is not None:  # actual comment (not the opening message)
        return repo.get_issue(issue_number).get_comment(comment_id)
    elif review_id is not None:
        for comment_type in (
            "get_comment",  # same as issue comment, just in case
            "get_review_comment",  # comments of a submitted review
            "get_single_review_comments",  # summary/description of a review
        ):
            try:
                pull = repo.get_pull(issue_number)
                comment = getattr(pull, comment_type)(review_id)
                if isinstance(comment, github.PaginatedList.PaginatedList):
                    comment = next(iter(comment), None)
                if hasattr(comment, "create_reaction"):
                    return comment
            except Exception as inner_exc:
                LOGGER.info(
                    "Cannot find PR/issue comment with %s. Trying again...",
                    comment_type,
                    exc_info=inner_exc,
                )
                continue
    raise RuntimeError(
        "Couldn't find {}={} for issue {}".format(
            "comment_id" if comment_id is not None else "review_id",
            comment_id if comment_id is not None else review_id,
            issue_number,
        )
    )


def add_reaction(
    reaction: str,
    repo: github.Repository.Repository,
    issue_number: int,
    comment_id: int | None = None,
    review_id: int | None = None,
    errors_ok: bool = True,
):
    assert reaction in (
        "+1",
        "-1",
        "confused",
        "eyes",
        "heart",
        "hooray",
        "laugh",
        "rocket",
    )

    try:
        for i in range(NUM_GH_API_TRIES):
            try:
                comment = _find_reactable_comment(
                    repo, issue_number, comment_id, review_id
                )
                break
            except RuntimeError as exc:
                # There seems to be a race condition where we get the payload before the
                # API can return the actual comment, so let's retry for a tiny bit
                if i < 4:
                    time.sleep(0.050 * 2**i)
                    continue
                raise exc
        comment.create_reaction(reaction)
    except Exception as exc:
        if errors_ok:
            LOGGER.info("add_reaction failed", exc_info=exc)
        else:
            raise exc


def pr_comment(org_name, repo_name, issue_num, comment, comment_id=None):
    if not COMMAND_PREFIX.search(comment):
        return
    gh = get_gh_client()
    repo = gh.get_repo(f"{org_name}/{repo_name}")
    pr = repo.get_pull(int(issue_num))
    pr_detailed_comment(
        org_name,
        repo_name,
        pr.head.user.login,
        pr.head.repo.name,
        pr.head.ref,
        issue_num,
        comment,
        comment_id,
    )


def pr_detailed_comment(
    org_name,
    repo_name,
    pr_owner,
    pr_repo,
    pr_branch,
    pr_num,
    comment,
    comment_id=None,
    review_id=None,
):
    is_allowed_cmd = repo_name in ALLOWED_CMD_NON_FEEDSTOCKS
    if not (repo_name.endswith("-feedstock") or is_allowed_cmd):
        return

    if not is_allowed_cmd:
        gh = get_gh_client()
        repo = gh.get_repo(f"{org_name}/{repo_name}")
        pull = repo.get_pull(int(pr_num))
        if pull.head.repo.full_name.split("/")[0] == "conda-forge":
            if (
                "upload_on_branch" not in _get_conda_forge_yml(org_name, repo_name)
                and repo_name != "cf-autotick-bot-test-package-feedstock"
            ):
                message = textwrap.dedent("""
                        Hi! This is the friendly automated conda-forge-webservice.

                        It appears you are making a pull request from a branch in your feedstock and not a fork. This procedure will generate a separate build for each push to the branch and is thus not allowed. See our [documentation](https://conda-forge.org/docs/maintainer/updating_pkgs.html#forking-and-pull-requests) for more details.

                        Please close this pull request and remake it from a fork of this feedstock.

                        Have a great day!
                        """)  # noqa
                try:
                    pull.create_issue_comment(message)
                except github.GithubException:
                    LOGGER.warning(
                        "PR from branch warning failure for "
                        f"repo {pull.head.repo.full_name}",
                    )
                return

    if RESTART_CI.search(comment):
        gh = get_gh_client()
        repo = gh.get_repo(f"{org_name}/{repo_name}")
        if comment_id is not None or review_id is not None:
            add_reaction("rocket", repo, pr_num, comment_id, review_id)
        restart_pull_request_ci(repo, int(pr_num))

    if PING_TEAM.search(comment):
        # get the team
        m = PING_TEAM.search(comment)
        if m.group("team"):
            team = m.group("team").strip()
            if team == "team":
                team = repo_name.replace("-feedstock", "")
            else:
                if "conda-forge/" in team:
                    team = team.split("/")[1].strip()
                if team.endswith("-feedstock"):
                    team = team[: -len("-feedstock")]
        else:
            team = repo_name.replace("-feedstock", "")

        gh = get_gh_client()
        repo = gh.get_repo(f"{org_name}/{repo_name}")
        if comment_id is not None or review_id is not None:
            add_reaction("rocket", repo, pr_num, comment_id, review_id)
        pull = repo.get_pull(int(pr_num))
        message = textwrap.dedent(f"""
            Hi! This is the friendly automated conda-forge-webservice.

            I was asked to ping @conda-forge/{team} and so here I am doing that.
            """)
        pull.create_issue_comment(message)

    if not is_allowed_cmd and RERUN_BOT.search(comment):
        gh = get_gh_client()
        repo = gh.get_repo(f"{org_name}/{repo_name}")
        if comment_id is not None or review_id is not None:
            add_reaction("rocket", repo, pr_num, comment_id, review_id)
        add_bot_rerun_label(repo, pr_num)

    #################################################
    # below here we only allow staged recipes + feedstocks
    is_staged_recipes = repo_name == "staged-recipes"
    if not (repo_name.endswith("-feedstock") or is_staged_recipes):
        return

    pr_commands = [LINT_MSG]
    if not is_staged_recipes:
        pr_commands += [ADD_NOARCH_MSG, RERENDER_MSG, UPDATE_CB3_MSG]

    if not any(command.search(comment) for command in pr_commands):
        return

    if comment_id is not None or review_id is not None:
        repo = get_gh_client().get_repo(f"{org_name}/{repo_name}")
        add_reaction("rocket", repo, pr_num, comment_id, review_id)

    tmp_dir = None
    try:
        tmp_dir = tempfile.mkdtemp("_recipe")

        gh_token = get_app_token_for_webservices_only()
        feedstock_dir = os.path.join(tmp_dir, repo_name)
        repo_url = (
            f"https://x-access-token:{gh_token}@github.com/{pr_owner}/{pr_repo}.git"
        )

        for _git_try_num in range(NUM_GIT_CLONE_TRIES):
            try:
                repo = Repo.clone_from(
                    repo_url, feedstock_dir, branch=pr_branch, depth=1
                )
            except Exception as _git_try_err:
                if _git_try_num == NUM_GIT_CLONE_TRIES - 1:
                    raise _git_try_err
            else:
                break

        if LINT_MSG.search(comment):
            relint(org_name, repo_name, pr_num)

        changed_anything = False
        expected_changes = []
        if not is_staged_recipes:
            do_noarch = do_rerender = False
            if ADD_NOARCH_MSG.search(comment):
                do_noarch = do_rerender = True
                expected_changes.append("add noarch")
            if RERENDER_MSG.search(comment):
                do_rerender = True

            if do_noarch:
                changed_anything |= make_noarch(repo)

        message = None
        if expected_changes:
            if len(expected_changes) > 1:
                expected_changes[-1] = "and " + expected_changes[-1]
            joiner = ", " if len(expected_changes) > 2 else " "
            changes_str = joiner.join(expected_changes)

            if changed_anything:
                try:
                    repo.remotes.origin.push()
                except GitCommandError:
                    message = textwrap.dedent("""
                        Hi! This is the friendly automated conda-forge-webservice.

                        I tried to {} for you, but it looks like I wasn't able to push to the {} branch of {}/{}.
                        Did you check the "Allow edits from maintainers" box?
                        """).format(changes_str, pr_branch, pr_owner, pr_repo)  # noqa
                    pull.create_issue_comment(message)
            else:
                message = textwrap.dedent("""
                    Hi! This is the friendly automated conda-forge-webservice.

                    I tried to {} for you, but it looks like there was nothing to do.
                    """).format(changes_str)

        rerender_error = False
        if not is_staged_recipes and do_rerender:
            try:
                rerender_error = rerender(org_name + "/" + repo_name, int(pr_num))
            except RequestException:
                rerender_error = True

        if rerender_error:
            doc_url = (
                "https://conda-forge.org/docs/maintainer/updating_pkgs.html"
                "#rerendering-with-conda-smithy-locally"
            )
            if message is None:
                message = textwrap.dedent("""
                    Hi! This is the friendly automated conda-forge-webservice.
                    """)

            message += textwrap.dedent("""

                I tried to rerender for you but ran into an issue with kicking GitHub Actions to do the rerender.
                Please ping conda-forge/core for further assistance. You can also try [re-rendering locally]({}).
                """).format(doc_url)  # noqa

        if message is not None:
            gh = get_gh_client()
            gh_repo = gh.get_repo(f"{org_name}/{repo_name}")
            pull = gh_repo.get_pull(int(pr_num))
            pull.create_issue_comment(message)

    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir)


def issue_comment(org_name, repo_name, issue_num, title, comment, comment_id=None):
    if not repo_name.endswith("-feedstock"):
        return
    if comment is None:
        comment = ""
    if title is None:
        title = ""

    text = comment + title

    issue_commands = [
        UPDATE_TEAM_MSG,
        ADD_NOARCH_MSG,
        RERENDER_MSG,
        UPDATE_CB3_MSG,
        ADD_BOT_AUTOMERGE,
        ADD_USER,
        REMOVE_USER,
        REMOVE_BOT_AUTOMERGE,
        UPDATE_VERSION,
    ]
    send_pr_commands = [
        ADD_NOARCH_MSG,
        RERENDER_MSG,
        UPDATE_CB3_MSG,
        ADD_BOT_AUTOMERGE,
        ADD_USER,
        REMOVE_USER,
        REMOVE_BOT_AUTOMERGE,
        UPDATE_VERSION,
    ]

    if not any(command.search(text) for command in issue_commands):
        return

    # sometimes the webhook outpaces other bits of the API so we try a bit
    for i in range(NUM_GH_API_TRIES):
        try:
            # this token has to be that of an actual bot since we use this
            # to make a fork
            # the bot used does not need admin permissions
            gh = github.Github(auth=github.Auth.Token(os.environ["GH_TOKEN"]))
            repo = gh.get_repo(f"{org_name}/{repo_name}")
            default_branch = repo.default_branch
            break
        except Exception as e:
            if i < 4:
                time.sleep(0.050 * 2**i)
                continue
            else:
                raise e

    # these are used when the app takes actions
    app_repo = get_gh_client().get_repo(f"{org_name}/{repo_name}")
    app_issue = app_repo.get_issue(int(issue_num))

    if comment_id is not None:
        add_reaction("rocket", app_repo, issue_num, comment_id)

    if UPDATE_TEAM_MSG.search(text):
        update_team(org_name, repo_name)
        message = textwrap.dedent(
            f"""
            Hi! This is the friendly automated conda-forge-webservice.

            I just wanted to let you know that I updated the team with maintainers from {default_branch}.
            """  # noqa
        )
        app_issue.create_comment(message)
        if UPDATE_TEAM_MSG.search(title):
            app_issue.edit(state="closed")

    if any(command.search(text) for command in send_pr_commands):
        forked_user_gh = gh.get_user()
        forked_user = forked_user_gh.login

        # make the fork if it does not exist
        try:
            forked_user_repo = gh.get_repo(f"{forked_user}/{repo_name}")
        except github.GithubException as e:
            _test_and_raise_besides_file_not_exists(e)

            forked_user_gh.create_fork(gh.get_repo(f"{org_name}/{repo_name}"))
            # we have to wait since the call above is async
            for i in range(NUM_GH_API_TRIES):
                try:
                    forked_user_repo = gh.get_repo(f"{forked_user}/{repo_name}")
                    break
                except Exception as e:
                    if i < 4:
                        time.sleep(0.050 * 2**i)
                        continue
                    else:
                        raise e

        tmp_dir = None
        try:
            tmp_dir = tempfile.mkdtemp("_recipe")

            if forked_user_repo.default_branch != default_branch:
                _sync_default_branch(
                    repo_name,
                    forked_user,
                    forked_user_repo.default_branch,
                    default_branch,
                    gh,
                )

            gh_token = get_app_token_for_webservices_only()
            feedstock_dir = os.path.join(tmp_dir, repo_name)
            repo_url = "https://x-access-token:{}@github.com/{}/{}.git".format(
                os.environ["GH_TOKEN"], forked_user, repo_name
            )
            upstream_repo_url = f"https://x-access-token:{gh_token}@github.com/{org_name}/{repo_name}.git"

            for _git_try_num in range(NUM_GIT_CLONE_TRIES):
                try:
                    git_repo = Repo.clone_from(repo_url, feedstock_dir, depth=1)
                except Exception as _git_try_err:
                    if _git_try_num == NUM_GIT_CLONE_TRIES - 1:
                        raise _git_try_err
                    else:
                        time.sleep(0.050 * 2**_git_try_num)
                        pass
                else:
                    break

            forked_repo_branch = f"conda_forge_admin_{issue_num}"
            upstream = git_repo.create_remote("upstream", upstream_repo_url)
            upstream.fetch()
            new_branch = git_repo.create_head(
                forked_repo_branch, getattr(upstream.refs, default_branch)
            )
            new_branch.checkout()

            err_msg = None
            changed_anything = False
            check_bump_build = True
            do_rerender = False
            do_version_update = False
            extra_msg = ""
            input_ver = None
            if ADD_NOARCH_MSG.search(text):
                pr_title = "MNT: Add noarch: python"
                comment_msg = "made the recipe `noarch: python`"
                to_close = ADD_NOARCH_MSG.search(title)

                changed_anything |= make_noarch(git_repo)
                do_rerender = True
                changed_anything |= make_rerender_dummy_commit(git_repo)
            elif RERENDER_MSG.search(text):
                pr_title = "MNT: rerender"
                comment_msg = "started rerendering the recipe"
                to_close = RERENDER_MSG.search(title)
                extra_msg = (
                    "\n\nIf I find any needed changes to the recipe, "
                    "I'll push them to this PR shortly. Thank you for "
                    "waiting!\n"
                )

                do_rerender = True
                changed_anything |= make_rerender_dummy_commit(git_repo)
            elif UPDATE_VERSION.search(text):
                if UPDATE_VERSION.search(title):
                    m = UPDATE_VERSION.search(title)
                    input_ver = m.group("ver")
                elif UPDATE_VERSION.search(comment):
                    m = UPDATE_VERSION.search(comment)
                    input_ver = m.group("ver")

                pr_title = "ENH: update package version"
                comment_msg = "started a version update"
                to_close = UPDATE_VERSION.search(title)
                check_bump_build = False
                extra_msg = (
                    "\n\nI'm currently searching for "
                    "new versions and will update this PR shortly "
                    "if I find one! Thank you for waiting!\n"
                )

                do_version_update = True
                changed_anything |= make_rerender_dummy_commit(git_repo)
            elif ADD_BOT_AUTOMERGE.search(text):
                pr_title = "[ci skip] [cf admin skip] ***NO_CI*** adding bot automerge"
                comment_msg = "added bot automerge"
                to_close = ADD_BOT_AUTOMERGE.search(title)
                check_bump_build = False
                extra_msg = "\n\nMerge this PR to enable bot automerging.\n"

                changed_anything |= add_bot_automerge(git_repo)
            elif REMOVE_BOT_AUTOMERGE.search(text):
                pr_title = (
                    "[ci skip] [cf admin skip] ***NO_CI*** removing bot automerge"
                )
                comment_msg = "removing bot automerge"
                to_close = REMOVE_BOT_AUTOMERGE.search(title)
                check_bump_build = False
                extra_msg = "\n\nMerge this PR to disable bot automerging.\n"

                changed_anything |= remove_bot_automerge(git_repo)
            elif ADD_USER.search(text) or REMOVE_USER.search(text):
                if m := (ADD_USER.search(title) or ADD_USER.search(comment)):
                    verb, past_verb, gerund = "add", "added", "adding"
                    user = m.group("user")
                elif m := (REMOVE_USER.search(title) or REMOVE_USER.search(comment)):
                    verb, past_verb, gerund = "remove", "removed", "removing"
                    user = m.group("user")
                else:
                    verb, past_verb, gerund = None, None, None
                    user = None
                comment_msg = f"{past_verb} user @{user}"

                if user is None:
                    err_msg = (
                        "the user to process in the feedstock could not be found "
                        "from the issue title or text"
                    )
                    to_close = False
                else:
                    if verb == "add":
                        handled_user = add_user(git_repo, user)
                    else:
                        handled_user = remove_user(git_repo, user)
                    if handled_user is None:
                        err_msg = (
                            "the recipe meta.yaml and/or CODEOWNERS file could "
                            "not be found or parsed properly when processing "
                            f"user @{user} in the feedstock"
                        )
                        to_close = False
                    else:
                        if not handled_user:
                            if verb == "add":
                                err_msg = f"the recipe already has maintainer @{user}"
                            else:
                                err_msg = f"the recipe doesn't have maintainer @{user}"
                            to_close = True
                        else:
                            do_rerender = False
                            check_bump_build = False
                            pr_title = f"[ci skip] {gerund} user @{user}"
                            to_close = ADD_USER.search(title) or REMOVE_USER.search(title)
                            extra_msg = (
                                f"\n\nMerge this PR to {verb} the user. Please do not rerender "  # noqa
                                "this PR or change it in any way. It has `[ci skip]` in "  # noqa
                                "the commit message to avoid pushing a new build and so "  # noqa
                                "the build configuration in the feedstock should not be "  # noqa
                                "changed.\n\nPlease contact [conda-forge/core](https://"
                                "conda-forge.org/docs/maintainer/maintainer_faq.html"
                                "#mfaq-contact-core) to have this PR merged, if the "
                                "maintainer is unresponsive."
                            )
                            changed_anything |= handled_user

            if changed_anything:
                git_repo.git.push("origin", forked_repo_branch)
                pr_message = textwrap.dedent("""
                        Hi! This is the friendly automated conda-forge-webservice.

                        I've {} as instructed in #{}.{}
                        """).format(comment_msg, issue_num, extra_msg)
                if check_bump_build:
                    pr_message += textwrap.dedent("""

                        Here's a checklist to do before merging.
                        - [ ] Bump the build number [if needed](https://conda-forge.org/docs/maintainer/updating_pkgs.html#updating-recipes)
                        """)

                if to_close:
                    pr_message += f"\nFixes #{issue_num}"

                pr = repo.create_pull(
                    title=pr_title,
                    body=pr_message,
                    base=default_branch,
                    head=f"{forked_user}:{forked_repo_branch}",
                    draft=do_rerender or do_version_update,
                )

                message = textwrap.dedent("""
                        Hi! This is the friendly automated conda-forge-webservice.

                        I just wanted to let you know that I {} in {}/{}#{}.
                        """).format(comment_msg, org_name, repo_name, pr.number)
                app_issue.create_comment(message)

                if do_rerender:
                    rerender_error = False
                    try:
                        rerender_error = rerender(
                            org_name + "/" + repo_name,
                            pr.number,
                        )
                    except RequestException:
                        rerender_error = True

                    if rerender_error:
                        doc_url = (
                            "https://conda-forge.org/docs/maintainer/updating_pkgs.html"
                            "#rerendering-with-conda-smithy-locally"
                        )
                        message = textwrap.dedent("""
                            Hi! This is the friendly automated conda-forge-webservice.

                            I tried to rerender for you but ran into an issue with kicking GitHub Actions to do the rerender.
                            Please ping conda-forge/core for further assistance. You can also try [re-rendering locally]({}).
                            """).format(doc_url)  # noqa

                        pr.create_issue_comment(message)

                if do_version_update:
                    version_update_error = False
                    try:
                        version_update_error = update_version(
                            org_name + "/" + repo_name,
                            pr.number,
                            input_ver,
                        )
                    except RequestException:
                        version_update_error = True

                    if version_update_error:
                        message = textwrap.dedent("""
                            Hi! This is the friendly automated conda-forge-webservice.

                            I tried to update the version for you but ran into an issue with kicking GitHub Actions to do
                            the update. Please ping conda-forge/core for further assistance.
                            """)  # noqa

                        pr.create_issue_comment(message)
            else:
                if err_msg:
                    message = textwrap.dedent("""
                            Hi! This is the friendly automated conda-forge-webservice.

                            I tried to {} as requested, but {} so no changes were made.
                            """).format(comment_msg, err_msg)
                else:
                    message = textwrap.dedent("""
                            Hi! This is the friendly automated conda-forge-webservice.

                            I've {} as requested, but nothing actually changed.
                            """).format(comment_msg)
                app_issue.create_comment(message)
                if to_close:
                    app_issue.edit(state="closed")

        finally:
            if tmp_dir is not None:
                shutil.rmtree(tmp_dir)


def _sync_default_branch(
    repo_name, forked_user, forked_default_branch, default_branch, gh
):
    r = requests.post(
        f"https://api.github.com/repos/{forked_user}/"
        f"{repo_name}/branches/{forked_default_branch}/rename",
        json={"new_name": default_branch},
        headers={
            "Authorization": f"token {os.environ['GH_TOKEN']}",
            "Content-Type": "application/json",
            "Accept": "application/vnd.github.v3+json",
        },
    )
    # ignore no such branch errors?
    if r.status_code != 404:
        r.raise_for_status()

    # poll until ready since this call is async
    for i in range(5):
        try:
            new_forked_default_branch = gh.get_repo(
                f"{forked_user}/{repo_name}"
            ).default_branch
            if new_forked_default_branch == default_branch:
                break
            else:
                raise RuntimeError(
                    f"Forked repo branch {forked_default_branch} could not be renamed to {default_branch} for repo {repo_name}"  # noqa
                )
        except Exception as e:
            if i < 4:
                time.sleep(0.050 * 2**i)
                continue
            else:
                raise e


def restart_pull_request_ci(repo, pr_num):
    pull = repo.get_pull(pr_num)
    commit = repo.get_commit(pull.head.sha)
    statuses = commit.get_statuses()
    drone_status = None
    for status in statuses:
        if "continuous-integration/drone" in status.context:
            drone_status = status
            break

    if drone_status:
        drone_build = drone_status.target_url.split("/")[-1]
        from conda_smithy.ci_register import drone_session

        session = drone_session()
        session.post(
            f"/api/repos/conda-forge/{repo.name}/builds/{drone_build}",
        )

    pull.edit(state="closed")
    time.sleep(1)  # wait a bit to be sure things are ok
    pull.edit(state="open")


def _determine_recipe_path(repo):
    """Determine v1 or v0 path."""
    recipe_path = os.path.join(repo.working_dir, "recipe", "meta.yaml")
    if os.path.exists(recipe_path):
        return recipe_path
    v1_recipe_path = os.path.join(repo.working_dir, "recipe", "recipe.yaml")
    if os.path.exists(v1_recipe_path):
        return v1_recipe_path
    return None


def _update_user(repo, user, action: Literal["add", "remove"] = "add"):
    # a feedstock has user names in three spots as of 2021/06/19
    # 1. the recipe maintainers section
    # 2. the CODEOWNERS file
    # 3. the README
    #
    # The location in the README is subject to change and so we won't adjust it.
    # However, the recipe and the CODEOWNERS file are structured and so we can
    # adjust those easily enough.
    # Those happen to also be the only locations where adding a user really matters.

    recipe_path = _determine_recipe_path(repo)
    if not recipe_path:
        return None
    co_path = os.path.join(repo.working_dir, ".github", "CODEOWNERS")
    yaml = _get_yaml_parser()
    if os.path.exists(recipe_path):
        # get the current maintainers - if user is in them, return False
        with io.StringIO() as fp_out:
            with open(recipe_path) as fp_in:
                extra_section = False
                for line in fp_in:
                    if line.strip().startswith("extra:"):
                        if extra_section:
                            raise ValueError(
                                "team update failed due to > 1 'extra:' sections"
                            )
                        extra_section = True
                    if extra_section:
                        fp_out.writelines([line])
            fp_out.seek(0)
            data = yaml.load(fp_out)
        curr_users: list[str] = data["extra"]["recipe-maintainers"]
        if user in curr_users:
            if action == "add":
                return False
            else:
                curr_users.remove(user)
        else:
            if action == "remove":
                return False
            if os.path.exists(co_path):
                # do code owners first
                with open(co_path) as fp:
                    lines = [ln.strip() for ln in fp.readlines()]

                # get any current lines with "* " at the front
                co_lines = []
                other_lines = []
                for i in range(len(lines)):
                    if lines[i].startswith("* "):
                        co_lines.append(lines[i])
                    else:
                        other_lines.append(lines[i])
                if action == "add":
                    all_users = ["@" + user]
                else:
                    all_users = []
                for co_line in co_lines:
                    parts = co_line.split("*", 1)
                    if len(parts) > 1:
                        this_line_users = parts[1].strip().split(" ")
                        if action == "remove":
                            this_line_users = [
                                u
                                for u in this_line_users
                                if u.lower() != f"@{user.lower()}"
                            ]
                        all_users.extend(this_line_users)
                other_lines = ["* " + " ".join(all_users), *other_lines]
                with open(co_path, "w") as fp:
                    fp.write("\n".join(other_lines))

            # now the recipe
            # we cannot use yaml because sometimes reading a recipe via the yaml
            # is impossible or lossy
            # so we have to parse it directly :/
            with open(recipe_path) as fp:
                lines = fp.read().splitlines()
            new_lines = []
            found_extra = False
            found_rm = False
            updated_user = False
            for line in lines:
                if line.strip().startswith("extra:"):
                    found_extra = True
                    new_lines.append(line)
                elif line.strip().startswith("recipe-maintainers:"):
                    found_rm = True
                    new_lines.append(line)
                elif found_extra and found_rm and not updated_user:
                    dashind = line.find("-")
                    if dashind == -1:
                        return None
                    head = line[:dashind]
                    if action == "add":
                        new_lines.append(head + "- " + user)
                        updated_user = True
                    elif user.lower() in [word.lower() for word in line.split()]:
                        updated_user = True
                        continue  # skip line == remove user
                    new_lines.append(line)
                else:
                    new_lines.append(line)

            if not updated_user:
                return None

            with open(recipe_path, "w") as fp:
                fp.write("\n".join(new_lines) + "\n")

            # and commit
            repo.index.add([recipe_path])
            if os.path.exists(co_path):
                repo.index.add([co_path])
            author = Actor(
                "conda-forge-webservices[bot]",
                "121827174+conda-forge-webservices[bot]@users.noreply.github.com",
            )
            # do not @-mention users in commit messages - it causes lots of
            # extra notifications
            verb = "added" if action == "add" else "removed"
            repo.index.commit(
                with_action_url(f"[ci skip] {verb} user {user}"),
                author=author,
            )

            return True
    else:
        return None


def add_user(repo, user):
    return _update_user(repo, user, action="add")


def remove_user(repo, user):
    return _update_user(repo, user, action="remove")


def add_bot_automerge(repo):
    yaml = _get_yaml_parser()

    cf_yml = os.path.join(repo.working_dir, "conda-forge.yml")
    if os.path.exists(cf_yml):
        with open(cf_yml) as fp:
            cfg = yaml.load(fp)
    else:
        cfg = {}

    current_automerge_value = cfg.get("bot", {}).get("automerge", False)
    if current_automerge_value:
        # already have it
        return False

    # add to conda-forge.yml
    # we do it this way to make room
    # for other keys in the future
    if "bot" not in cfg:
        cfg["bot"] = {}
    cfg["bot"]["automerge"] = True
    with open(cf_yml, "w") as fp:
        yaml.dump(cfg, fp)

    # now commit
    repo.index.add([cf_yml])
    author = Actor(
        "conda-forge-webservices[bot]",
        "121827174+conda-forge-webservices[bot]@users.noreply.github.com",
    )
    repo.index.commit(
        with_action_url("[ci skip] [cf admin skip] ***NO_CI*** added bot automerge"),
        author=author,
    )
    return True


def remove_bot_automerge(repo):
    yaml = _get_yaml_parser()

    cf_yml = os.path.join(repo.working_dir, "conda-forge.yml")
    if os.path.exists(cf_yml):
        with open(cf_yml) as fp:
            cfg = yaml.load(fp)
    else:
        cfg = {}

    current_automerge_value = cfg.get("bot", {}).get("automerge", False)
    if not current_automerge_value:
        # already disabled
        return False

    # remove it from conda-forge.yml
    del cfg["bot"]["automerge"]
    if len(cfg["bot"]) == 0:
        del cfg["bot"]
    with open(cf_yml, "w") as fp:
        yaml.dump(cfg, fp)

    # now commit
    repo.index.add([cf_yml])
    author = Actor(
        "conda-forge-webservices[bot]",
        "121827174+conda-forge-webservices[bot]@users.noreply.github.com",
    )
    repo.index.commit(
        with_action_url("[ci skip] [cf admin skip] ***NO_CI*** removed bot automerge"),
        author=author,
    )
    return True


def make_rerender_dummy_commit(repo):
    # add a dummy commit
    readme_file = os.path.join(repo.working_dir, "README.md")
    with open(readme_file, "a") as fp:
        fp.write("""\

<!-- dummy commit to enable rerendering -->

""")
    repo.index.add([readme_file])
    author = Actor(
        "conda-forge-webservices[bot]",
        "121827174+conda-forge-webservices[bot]@users.noreply.github.com",
    )
    repo.index.commit(
        with_action_url("dummy commit for rerendering"),
        author=author,
    )
    return True


def set_rerender_pr_status(repo, pr_num, status, target_url=None, sha=None):
    if target_url is not None:
        kwargs = {"target_url": target_url}
    else:
        kwargs = {}

    if sha is None:
        pull = repo.get_pull(int(pr_num))
        sha = pull.head.sha
    commit = repo.get_commit(sha)

    if status == "success":
        msg = "Rerendering successful."
    elif status == "failure" or status == "error":
        msg = "Rerendering failed."
    else:
        msg = "Rerendering in progress..."

    commit.create_status(
        status,
        description=msg,
        context="conda-forge-rerendering-service",
        **kwargs,
    )


def rerender(full_name, pr_num):
    gh = get_gh_client()
    repo = gh.get_repo(full_name)
    pull = repo.get_pull(int(pr_num))
    sha = pull.head.sha

    inject_app_token_into_feedstock(full_name, repo=repo)
    inject_app_token_into_feedstock_readonly(full_name, repo=repo)

    _, repo_name = full_name.split("/")
    uid = uuid.uuid4().hex
    ref = __version__.replace("+", ".")
    workflow = gh.get_repo("conda-forge/conda-forge-webservices").get_workflow(
        "webservices-workflow-dispatch.yml"
    )
    running = workflow.create_dispatch(
        ref=ref,
        inputs={
            "task": "rerender",
            "repo": repo_name,
            "pr_number": str(pr_num),
            "container_tag": ref,
            "uuid": uid,
            "sha": sha,
        },
    )
    if running:
        run = get_workflow_run_from_uid(workflow, uid, ref)
        if run:
            target_url = run.html_url
        else:
            target_url = None

        set_rerender_pr_status(repo, pr_num, "pending", target_url=target_url, sha=sha)

    return not running


def update_version(full_name, pr_num, input_ver):
    gh = get_gh_client()
    repo = gh.get_repo(full_name)

    inject_app_token_into_feedstock(full_name, repo=repo)
    inject_app_token_into_feedstock_readonly(full_name, repo=repo)

    uid = uuid.uuid4().hex
    _, repo_name = full_name.split("/")
    ref = __version__.replace("+", ".")
    workflow = gh.get_repo("conda-forge/conda-forge-webservices").get_workflow(
        "webservices-workflow-dispatch.yml"
    )
    running = workflow.create_dispatch(
        ref=ref,
        inputs={
            "task": "version_update",
            "repo": repo_name,
            "pr_number": str(pr_num),
            "container_tag": ref,
            "requested_version": str(input_ver) or "null",
            "uuid": uid,
        },
    )
    return not running


def make_noarch(repo):
    meta_yaml = _determine_recipe_path(repo)
    if meta_yaml is None:
        return False
    with open(meta_yaml) as fh:
        lines = [line for line in fh]
    with open(meta_yaml, "w") as fh:
        build_line = False
        for line in lines:
            if build_line:
                spaces = len(line) - len(line.lstrip())
                line = "{}noarch: python\n{}".format(" " * spaces, line)
            build_line = False
            if line.rstrip() == "build:":
                build_line = True
            fh.write(line)
    repo.index.add([meta_yaml])
    author = Actor(
        "conda-forge-webservices[bot]",
        "121827174+conda-forge-webservices[bot]@users.noreply.github.com",
    )
    repo.index.commit(with_action_url("Add noarch:python option"), author=author)
    return True


def relint(owner, repo_name, pr_num):
    pr = int(pr_num)
    if LINT_VIA_GHA:
        lint_via_github_actions(
            f"{owner}/{repo_name}",
            pr,
        )
    else:
        lint_info = compute_lint_message(
            owner,
            repo_name,
            pr,
            repo_name == "staged-recipes",
        )
        if not lint_info:
            LOGGER.warning("Linting was skipped.")
        else:
            msg = comment_on_pr(owner, repo_name, pr, lint_info["message"], force=True)
            set_pr_status(owner, repo_name, lint_info, target_url=msg.html_url)


def add_bot_rerun_label(repo, pr_num):
    # try to add the label if it does not exist
    # this makes things look nicer but is not needed
    try:
        # color and description are from the bot repo
        repo.create_label(
            "bot-rerun",
            "#191970",
            description=(
                "Apply this label if you want the bot "
                "to retry issuing a particular "
                "pull-request"
            ),
        )
    except github.GithubException:
        # an error here is not fatal so swallow it and
        # move on
        pass

    # now add the label
    # this API call will work even if the label does not
    # exist yet or is already on the PR
    pull = repo.get_pull(int(pr_num))
    pull.add_to_labels("bot-rerun")
