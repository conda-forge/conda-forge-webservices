from __future__ import annotations

import contextlib
import datetime
import logging
import os
import random
import subprocess
import tempfile
import time
from typing import TYPE_CHECKING

from github import GithubException
from ruamel.yaml import YAML

if TYPE_CHECKING:
    from github.PullRequest import PullRequest
    from github.Repository import Repository

LOGGER = logging.getLogger(__name__)

ALLOWED_USERS = ["regro-cf-autotick-bot"]

IGNORED_CHECKS: list[str] = []

# sets of states that indicate good / bad / neutral in the github API
NEUTRAL_STATES = ["pending"]
BAD_STATES = [
    # for statuses
    "failure",
    "error",
    # for checks
    "action_required",
    "canceled",
    "timed_out",
    "failed",
    "neutral",
]


# https://stackoverflow.com/questions/6194499/pushd-through-os-system
@contextlib.contextmanager
def pushd(new_dir):
    previous_dir = os.getcwd()
    os.chdir(new_dir)
    try:
        yield
    finally:
        os.chdir(previous_dir)


def _run_git_command(*args):
    try:
        c = subprocess.run(
            ["git", *list(args)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except subprocess.CalledProcessError as e:
        print(c.stdout)
        raise e


def _get_conda_forge_config(pr):
    """get the conda-forge.yml from upstream master

    We always do this to make sure we use the maintainer settings and not
    any from a fork.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        _run_git_command("clone", pr.base.repo.clone_url, tmpdir)
        with pushd(tmpdir):
            _run_git_command("checkout", pr.base.ref)
            with open("conda-forge.yml") as fp:
                cfg = YAML().load(fp)
    return cfg


def _automerge_me(cfg):
    """Compute if feedstock allows automerges from `conda-forge.yml`"""
    return cfg.get("bot", {}).get("automerge", False)


def _get_checks(repo, pr):
    checks = []
    commit = repo.get_commit(pr.head.sha)
    for check in commit.get_check_suites():
        _check = {}
        _check["app"] = {"slug": check.app.slug}
        _check["status"] = check.status
        _check["conclusion"] = check.conclusion
        # for gha we check the runs to ensure we have the right check
        if check.status == "completed" and check.app.slug == "github-actions":
            _check["runs"] = [run.name for run in check.get_check_runs()]
        else:
            _check["runs"] = None
        checks.append(_check)
    return checks


def _get_github_checks(repo, pr):
    """Get all of the github checks associated with a PR.

    Parameters
    ----------
    repo : github.Repository.Repository
        A `Repository` object for the given repo from the PyGithub package.
    pr : github.PullRequest.PullRequest
        A `PullRequest` object for the given PR from the PyGithub package.

    Returns
    -------
    check_states : dict of bool or None
        A dictionary mapping each check to its state.
    """

    check_states = {}
    checks = _get_checks(repo, pr)
    for check in checks:
        name = check["app"]["slug"]
        if name not in IGNORED_CHECKS:
            if check["status"] != "completed":
                check_states[name] = None
            else:
                if name == "github-actions":
                    if (
                        check["conclusion"] == "success"
                        and check["runs"]
                        and (not any(run == "automerge" for run in check["runs"]))
                    ):
                        check_states[name] = True
                    else:
                        check_states[name] = False
                else:
                    if check["conclusion"] == "success":
                        check_states[name] = True
                    else:
                        check_states[name] = False

    for name, good in check_states.items():
        LOGGER.info("check: name|state = %s|%s", name, good)

    return check_states


def _get_github_statuses(repo, pr):
    """Get all of the github statuses associated with a PR.

    Parameters
    ----------
    repo : github.Repository.Repository
        A `Repository` object for the given repo from the PyGithub package.
    pr : github.PullRequest.PullRequest
        A `PullRequest` object for the given PR from the PyGithub package.

    Returns
    -------
    status_states : dict of bool or None
        A dictionary mapping each status to its state.
    """
    # github emits all of the statuses with a time stamp as events
    # you have to keep the latest one
    # so this is why we compare the times below

    commit = repo.get_commit(pr.head.sha)
    statuses = commit.get_statuses()

    oldest_time = None
    status_states = {}
    for status in statuses:
        if oldest_time is None:
            if status.updated_at.tzinfo is None:
                oldest_time = datetime.datetime.now() - datetime.timedelta(weeks=10000)
            else:
                oldest_time = datetime.datetime.now(
                    datetime.timezone.utc
                ) - datetime.timedelta(weeks=10000)

        if status.context not in status_states:
            # init with really old time
            status_states[status.context] = (None, oldest_time)

        if status.state in NEUTRAL_STATES:
            if status.updated_at > status_states[status.context][1]:
                status_states[status.context] = (None, status.updated_at)
        elif status.state in BAD_STATES:
            if status.updated_at > status_states[status.context][1]:
                status_states[status.context] = (False, status.updated_at)
        else:
            if status.updated_at > status_states[status.context][1]:
                status_states[status.context] = (True, status.updated_at)

    for context, val in status_states.items():
        LOGGER.info("status: name|state = %s|%s", context, val[0])

    return {k: v[0] for k, v in status_states.items()}


def _circle_is_active():
    """check if circle is active"""
    if os.path.exists(".circleci/checkout_merge_commit.sh"):
        return True

    if os.path.exists(".circleci/fast_finish_ci_pr_build.sh"):
        return True

    # we now look for this sentinel text
    #      filters:
    #        branches:
    #          ignore:
    #            - /.*/
    with open(".circleci/config.yml") as fp:
        start = False
        ind = 0
        sentinels = ["filters:", "branches:", "ignore:", "- /.*/"]
        found_sentinels = [False] * len(sentinels)
        for line in fp.readlines():
            if line.strip() == "filters:":
                start = True
            if start and ind < len(sentinels):
                if line.strip() == sentinels[ind]:
                    found_sentinels[ind] = True
                ind += 1

    if all(found_sentinels):
        return False
    else:
        return True


def _get_required_checks_and_statuses(pr, cfg):
    """return a list of required statuses and checks"""
    ignored_statuses = (
        cfg.get("bot", {}).get("automerge_options", {}).get("ignored_statuses", [])
    )
    required = ["linter"]

    with tempfile.TemporaryDirectory() as tmpdir:
        _run_git_command("clone", pr.head.repo.clone_url, tmpdir)
        with pushd(tmpdir):
            _run_git_command("checkout", pr.head.sha)

            if os.path.exists("appveyor.yml") or os.path.exists(".appveyor.yml"):
                required.append("appveyor")

            if os.path.exists(".drone.yml"):
                required.append("drone")

            if os.path.exists(".travis.yml"):
                required.append("travis")

            if os.path.exists("azure-pipelines.yml"):
                required.append("azure")

            if os.path.exists(".github/workflows/conda-build.yml"):
                required.append("github-actions")

            # smithy writes this config even if circle is off, but we can check
            # for other things
            if os.path.exists(".circleci/config.yml") and _circle_is_active():
                required.append("circle")

    return [
        r.lower()
        for r in required
        if not any(r.lower() in _i for _i in ignored_statuses)
    ]


def _all_statuses_and_checks_ok(status_states, check_states, req_checks_and_states):
    """check all of the required statuses are OK and return their states"""
    final_states = {r: None for r in req_checks_and_states}
    for req in req_checks_and_states:
        found_state = False
        for k, s in status_states.items():
            if req in k.lower():
                if not found_state:
                    found_state = True
                    state = s
                else:
                    state = state and s

        for k, s in check_states.items():
            if req in k.lower():
                if not found_state:
                    found_state = True
                    state = s
                else:
                    state = state and s

        final_states[req] = None if not found_state else state
        LOGGER.info("final status: name|state = %s|%s", req, final_states[req])

    if "conda-forge-rerendering-service" in status_states:
        final_states["rerender called"] = status_states[
            "conda-forge-rerendering-service"
        ]

    return all(v for v in final_states.values()), final_states


def _comment_on_pr_with_race(pr, comment, check_slug, check_race=2):
    # check for a PR comment with a given slug
    # turn check_race > 1 to check more than once
    last_comment = None
    i = 0
    while last_comment is None and i < check_race:
        for cmnt in pr.get_issue_comments():
            if check_slug in cmnt.body:
                last_comment = cmnt
        time.sleep(random.uniform(0.5, 1.5))
        i += 1

    if last_comment is None:
        pr.create_issue_comment(comment)
    else:
        last_comment.edit(comment)


def _no_extra_pr_commits(pr):
    """check that no commits were made after a PR has the automerge label added"""
    events = [e for e in pr.as_issue().get_timeline()]
    dts = [e.created_at for e in events if e.created_at is not None]
    if len(dts) > 1:
        for i in range(1, len(dts)):
            if dts[i] < dts[i - 1]:
                LOGGER.warning("events are out of order!")
                return None

    label_ind = None
    for i, e in enumerate(events):
        if e.event == "labeled" and e.raw_data["label"]["name"] == "automerge":
            label_ind = i

    if label_ind is None:
        LOGGER.warning("could not find 'automerge' label in events!")
        return None

    return all(e.event != "committed" for e in events[label_ind + 1 :])


def _check_pr(
    pr: PullRequest, pr_for_admin: PullRequest, cfg
) -> tuple[bool, str | None]:
    """make sure a PR is ok to automerge"""

    pr_has_automerge_label = any(label.name == "automerge" for label in pr.get_labels())

    # If the automerge label is present, then we can proceed as long as no commits
    # have since been added.
    if pr_has_automerge_label:
        _no_commits = _no_extra_pr_commits(pr)
        if _no_commits is None:
            return False, "could not determine if extra commits were made to PR"
        else:
            if _no_commits:
                return True, None
            else:
                pr.remove_from_labels("automerge")
                pr_for_admin.create_issue_comment(
                    """\
Hi! This is the friendly conda-forge automerge bot!

Commits were made to this PR after the `automerge` label was added. For \
security reasons, I have disabled automerge by removing the `automerge` label. \
Please add the `automerge` label again (or ask a maintainer to do so) if you'd \
like to enable automerge again!
"""
                )
                return False, "commits were made after the automerge label was added"
    else:  # The PR has no automerge label, so proceed only if titled "[bot-automerge]"
        # only allowed users
        if pr.user.login not in ALLOWED_USERS:
            return (
                False,
                f"user {pr.user.login} cannot automerge and no automerge label found",
            )

        # only if [bot-automerge] is in the pr title
        if "[bot-automerge]" not in pr.title:
            return False, "PR does not have the '[bot-automerge]' slug in the title"

        # only if only ALLOWED_USERS have commits
        committers = {getattr(c.author, "login", None) for c in pr.get_commits()}
        if not all(c in ALLOWED_USERS for c in committers):
            _comment_on_pr_with_race(
                pr_for_admin,
                """\
Hi! This is the friendly conda-forge automerge bot!

It appears that not all commits to this PR were made by the bot. Thus this PR is \
not being automatically merged. Please add the `automerge` label again (or ask a \
maintainer to do so) if you'd like to enable automerge again!
""",
                "not all commits to this PR were made by the bot",
            )
            return False, "non-bot commits on a bot PR with the automerge slug"

        # can we automerge in this feedstock?
        if not _automerge_me(cfg):
            return False, "automated bot merges are turned off for this feedstock"

        return True, None


def _comment_on_pr(pr, stats, msg):
    # do not comment if pending
    if any(v is None for v in stats.values()):
        return

    comment = """\
Hi! This is the friendly conda-forge automerge bot!

I considered the following status checks when analyzing this PR:
"""
    for k, v in stats.items():
        if v:
            _v = "passed"
        elif v is None:
            _v = "pending"
        else:
            _v = "failed"
        comment = comment + f" - **{k}**: {_v}\n"

    comment = comment + f"\n\nThus the PR was {msg}"

    # the times at which PR statuses return are correlated and so this code
    # can race when posting failures
    # thus we can turn up check_race to say 10
    # in that case to try and randomize to avoid double posting comments
    # I considered using app slugs (e.g. require the failed check to be triggered
    # by the same app as a failed one in the final statuses). However, some apps
    # post more than one message (e.g., circle) so that would not work if they both
    # fail.
    # I also thought about using timestamps, but github check events don't come
    # with one.
    check_slug = "I considered the following status checks when analyzing this PR:"
    _comment_on_pr_with_race(pr, comment, check_slug)


def _automerge_pr(
    repo: Repository,
    pr: PullRequest,
    pr_for_admin: PullRequest,
) -> tuple[bool, str | None]:
    cfg = _get_conda_forge_config(pr)
    allowed, msg = _check_pr(pr, pr_for_admin, cfg)

    if not allowed:
        return False, msg

    # get checks and statuses
    status_states = _get_github_statuses(repo, pr)
    check_states = _get_github_checks(repo, pr)

    # get which ones are required
    req_checks_and_states = _get_required_checks_and_statuses(pr, cfg)
    if len(req_checks_and_states) == 0:
        return False, "At least one status or check must be required"

    ok, final_statuses = _all_statuses_and_checks_ok(
        status_states, check_states, req_checks_and_states
    )
    if not ok:
        _comment_on_pr(pr_for_admin, final_statuses, "not passing and not merged.")
        return False, "PR has failing or pending statuses/checks"

    # make sure PR is mergeable and not already merged
    # we have to get the PR again to ensure we have updated mergeable status
    pr = repo.get_pull(pr.number)

    if pr.is_merged():
        return False, "PR has already been merged"

    if pr.mergeable is None or not pr.mergeable:
        _comment_on_pr(
            pr_for_admin,
            final_statuses,
            f"passing, but not in a mergeable state (mergeable={pr.mergeable}).",
        )
        return False, f"PR merge issue: mergeable={pr.mergeable}"

    # we're good - now merge
    try:
        merge_status = pr_for_admin.merge(
            commit_message="automerged PR by conda-forge/automerge-action",
            commit_title=f"{pr.title} (#{pr.number})",
            merge_method="merge",
            sha=pr.head.sha,
        )
        merge_status_merged = merge_status.merged
        if not merge_status_merged:
            merge_status_message = merge_status.message
        else:
            merge_status_message = None
    except GithubException as e:
        merge_status_merged = False
        merge_status_message = "API error in PUT to merge"
        LOGGER.exception(merge_status_message + ":")
        if e.data is not None and "message" in e.data:
            merge_status_message += f" -- '{e.data['message']}'"
    except Exception:
        merge_status_merged = False
        merge_status_message = "Unexpected error while attempting to merge."
        LOGGER.exception(merge_status_message)
        merge_status_message += " Check Actions logs for stack trace."

    if not merge_status_merged:
        _comment_on_pr(
            pr_for_admin,
            final_statuses,
            f"passing, but could not be merged (error={merge_status_message}).",
        )
        return (False, f"PR could not be merged: {merge_status_message}")
    else:
        # use a smaller check_race here to make sure this one is prompt
        _comment_on_pr(
            pr_for_admin, final_statuses, "passing and merged! Have a great day!"
        )
        return True, "all is well :)"


def automerge_pr(
    repo: Repository, pr: PullRequest, pr_for_admin: PullRequest
) -> tuple[bool, str | None]:
    """Possibly automerge a PR.

    Parameters
    ----------
    repo : github.Repository.Repository
        A `Repository` object for the given repo from the PyGithub package.
    pr : github.PullRequest.PullRequest
        A `PullRequest` object for the given PR from the PyGithub package
        that is used for API-request heavy operations.
    pr_for_admin : github.PullRequest.PullRequest
        A `PullRequest` object for the given PR from the PyGithub package
        that is used only to comment on and/or merge the PR.

    Returns
    -------
    did_merge : bool
        If `True`, the merge was done, `False` if not.
    reason : str
        The reason the merge worked or did not work.
    """
    did_merge, reason = _automerge_pr(repo, pr, pr_for_admin)

    if did_merge:
        LOGGER.info("MERGED PR %s on %s: %s", pr.number, repo.full_name, reason)
    else:
        LOGGER.info("DID NOT MERGE PR %s on %s: %s", pr.number, repo.full_name, reason)

    return did_merge, reason
