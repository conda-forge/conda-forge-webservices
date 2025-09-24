import os
import unittest.mock as mock
from pathlib import Path

from git import Repo
import github
import pytest
from requests.exceptions import RequestException

from conda_forge_webservices.commands import (
    pr_detailed_comment as _pr_detailed_comment,
    issue_comment as _issue_comment,
    _find_reactable_comment,
    add_user,
    remove_user,
    _determine_recipe_path,
)


def pr_detailed_comment(
    comment,
    org_name="conda-forge",
    repo_name="python-feedstock",
    pr_repo=None,
    pr_owner="some-user",
    pr_branch="master",
    pr_num=1,
):
    if pr_repo is None:
        pr_repo = repo_name
    return _pr_detailed_comment(
        org_name, repo_name, pr_owner, pr_repo, pr_branch, pr_num, comment
    )


def issue_comment(
    title, comment, issue_num=1, org_name="conda-forge", repo_name="python-feedstock"
):
    return _issue_comment(org_name, repo_name, issue_num, title, comment)


@pytest.fixture
def set_dummy_gh_token():
    if "GH_TOKEN" not in os.environ:
        os.environ["GH_TOKEN"] = "fake"  # github access is mocked anyway
        kill_token = True
    else:
        kill_token = False

    yield

    if kill_token:
        del os.environ["GH_TOKEN"]


@pytest.mark.parametrize(
    "command,on_sr,should,should_not",
    [
        (
            "rerender",
            False,
            [
                "@conda-forge-admin, please rerender",
                "@conda-forge-admin, rerender",
                "@conda-forge-admin, re-render",
                "@conda-forge-admin, please re-render",
                "@conda-forge-admin: PLEASE RERENDER",
                "@conda-forge-admin: RERENDER",
                "something something. @conda-forge-admin: please re-render",
                "something something. @conda-forge-admin: re-render",
            ],
            [
                "@conda-forge admin is pretty cool. please rerender for me?",
                "@conda-forge admin is pretty cool. rerender for me?",
                "@conda-forge-admin, go ahead and rerender for me",
                "please re-render, @conda-forge-admin",
                "re-render, @conda-forge-admin",
                "@conda-forge-linter, please lint",
                "@conda-forge-linter, lint",
            ],
        ),
        (
            "make_noarch",
            False,
            [
                "@conda-forge-admin, please add noarch python",
                "@conda-forge-admin, add noarch python",
                (
                    "@conda-forge-linter, please lint, and @conda-forge-admin, "
                    "please make `noarch: python`"
                ),
                (
                    "@conda-forge-linter, lint, and @conda-forge-admin, make "
                    "`noarch: python`"
                ),
                "@CONDA-FORGE-ADMIN please add `noarch python`",
                "@CONDA-FORGE-ADMIN add `noarch python`",
                "hey @conda-forge-admin : please make noarch: python",
                "hey @conda-forge-admin : make noarch: python",
            ],
            [
                "@conda-forge-linter, please lint",
                "@conda-forge-linter, lint",
                "sure wish @conda-forge-admin would please add noarch python",
                "sure wish @conda-forge-admin would add noarch python",
            ],
        ),
        (
            "relint",
            True,
            [
                "@conda-forge-admin, please lint",
                "@conda-forge-admin, lint",
                "@CONDA-FORGE-LINTER, please relint",
                "@CONDA-FORGE-LINTER, relint",
                "hey @conda-forge-linter please re-lint!",
                "hey @conda-forge-linter re-lint!",
            ],
            [
                "@conda-forge-admin should probably lint again",
            ],
        ),
        (
            "add_bot_rerun_label",
            False,
            [
                "@conda-forge-admin, please rerun the bot",
                "@conda-forge-admin, rerun the bot",
                "@conda-forge-admin, please rerun bot",
                "@conda-forge-admin, rerun bot",
                "@conda-forge-admin: RERUN BOT",
                "something something. @conda-forge-admin: please rerun bot",
            ],
            [
                "@conda-forge admin is pretty cool. please rerun bot for me?",
                "@conda-forge admin is pretty cool. rerun the bot for me?",
                "@conda-forge-admin, go ahead and rerun the bot for me",
                "please rerun the bot, @conda-forge-admin",
                "rerun bot, @conda-forge-admin",
            ],
        ),
    ],
)
@mock.patch("conda_forge_webservices.commands.get_app_token_for_webservices_only")
@mock.patch("conda_forge_webservices.commands.add_bot_rerun_label")
@mock.patch("conda_forge_webservices.commands.rerender")
@mock.patch("conda_forge_webservices.commands.make_noarch")
@mock.patch("conda_forge_webservices.commands.relint")
@mock.patch("conda_forge_webservices.commands.update_team")
@mock.patch("conda_forge_webservices.commands.get_gh_client")
@mock.patch("conda_forge_webservices.commands.Repo")
def test_pr_command_triggers(
    repo,
    gh,
    update_team,
    relint,
    make_noarch,
    rerender,
    add_bot_rerun_label,
    get_app_token_for_webservices_only,
    command,
    on_sr,
    should,
    should_not,
):
    if command == "add_bot_rerun_label":
        command = add_bot_rerun_label
    elif command == "rerender":
        command = rerender
    elif command == "make_noarch":
        command = make_noarch
    elif command == "relint":
        command = relint
    else:
        raise ValueError(f"Unknown command: {command}")

    for msg in should:
        command.reset_mock()
        print(msg, end=" " * 30 + "\r")
        pr_detailed_comment(msg)
        command.assert_called()

        command.reset_mock()
        print(msg, end=" " * 30 + "\r")
        pr_detailed_comment(msg, repo_name="staged-recipes")
        if on_sr:
            command.assert_called()
        else:
            command.assert_not_called()

    for msg in should_not:
        command.reset_mock()
        print(msg, end=" " * 30 + "\r")
        pr_detailed_comment(msg)
        command.assert_not_called()


@pytest.mark.parametrize(
    "command,should,should_not",
    [
        (
            "add_bot_automerge",
            [
                "@conda-forge-admin, please add bot automerge",
                "@conda-forge-admin, add bot automerge",
                "@conda-forge-admin: PLEASE ADD BOT AUTOMERGE",
                "@conda-forge-admin: ADD BOT AUTOMERGE",
                "something something. @conda-forge-admin: please add bot automerge",
                "something something. @conda-forge-admin: add bot automerge",
            ],
            [
                "@conda-forge admin is pretty cool. please add bot automerge for me?",
                "@conda-forge admin is pretty cool. add bot automerge for me?",
                "@conda-forge-admin, go ahead and add bot automerge for me",
                "please add bot automerge, @conda-forge-admin",
                "add bot automerge, @conda-forge-admin",
            ],
        ),
        (
            "rerender",
            [
                "@conda-forge-admin, please rerender",
                "@conda-forge-admin, rerender",
                "@conda-forge-admin, please re-render",
                "@conda-forge-admin, re-render",
                "@conda-forge-admin: PLEASE RERENDER",
                "@conda-forge-admin: RERENDER",
                "something something. @conda-forge-admin: please re-render",
                "something something. @conda-forge-admin: re-render",
            ],
            [
                "@conda-forge admin is pretty cool. please rerender for me?",
                "@conda-forge admin is pretty cool. rerender for me?",
                "@conda-forge-admin, go ahead and rerender for me",
                "please re-render, @conda-forge-admin",
                "re-render, @conda-forge-admin",
                "@conda-forge-linter, please lint",
                "@conda-forge-linter, lint",
            ],
        ),
        (
            "update_version",
            [
                "@conda-forge-admin, please update version",
                "@conda-forge-admin, update version",
                "@conda-forge-admin: PLEASE UPDATE VERSION",
                "@conda-forge-admin: UPDATE VERSION",
                "something something. @conda-forge-admin: please update version",
                "something something. @conda-forge-admin: update version",
            ],
            [
                "@conda-forge admin is pretty cool. please update version for me?",
                "@conda-forge admin is pretty cool. update version for me?",
                "@conda-forge-admin, go ahead and update version for me",
                "please update version, @conda-forge-admin",
                "update version, @conda-forge-admin",
                "@conda-forge-linter, please lint",
                "@conda-forge-linter, lint",
            ],
        ),
        (
            "make_noarch",
            [
                "@conda-forge-admin, please add noarch python",
                "@conda-forge-admin, add noarch python",
                "@conda-forge-admin, please make `noarch: python`",
                "@conda-forge-admin, make `noarch: python`",
                "@conda-forge-admin please add `noarch python`",
                "@conda-forge-admin add `noarch python`",
                "hey @conda-forge-admin : please make noarch: python",
                "hey @conda-forge-admin : make noarch: python",
            ],
            [
                "@conda-forge-linter, please lint",
                "@conda-forge-linter, lint",
                "sure wish @conda-forge-admin would please add noarch python",
                "sure wish @conda-forge-admin would add noarch python",
            ],
        ),
        (
            "update_team",
            [
                "@conda-forge-admin: please update team",
                "@conda-forge-admin: update team",
                "@conda-forge-admin, please update the team",
                "@conda-forge-admin, update the team",
                "@conda-forge-admin, please refresh team",
                "@conda-forge-admin, refresh team",
            ],
            [
                "@conda-forge-admin please make noarch: python",
                "@conda-forge-admin make noarch: python",
                "@conda-forge-linter, please lint. and can someone refresh the team?",
                "@conda-forge-linter, lint. and can someone refresh the team?",
            ],
        ),
        (
            "add_user",
            [
                "@conda-forge-admin, please add user @blah",
                "@conda-forge-admin, add user @blah",
                "something something. @conda-forge-admin: please add user @blah",
            ],
            [
                "@conda-forge admin is pretty cool. please add user @blah",
                "@conda-forge admin is pretty cool. rerun add user @blah?",
                "@conda-forge-admin, go ahead and rerun add user @blah?",
                "please add user @blah, @conda-forge-admin",
                "add user @blah, @conda-forge-admin",
            ],
        ),
        (
            "remove_user",
            [
                "@conda-forge-admin, please remove user @blah",
                "@conda-forge-admin, remove user @blah",
                "something something. @conda-forge-admin: please remove user @blah",
            ],
            [
                "@conda-forge admin is pretty cool. please remove user @blah",
                "@conda-forge admin is pretty cool. rerun remove user @blah?",
                "@conda-forge-admin, go ahead and rerun remove user @blah?",
                "please remove user @blah, @conda-forge-admin",
                "remove user @blah, @conda-forge-admin",
            ],
        ),
    ],
)
@mock.patch("conda_forge_webservices.commands.get_app_token_for_webservices_only")
@mock.patch("conda_forge_webservices.commands.update_version")
@mock.patch("conda_forge_webservices.commands.add_user")
@mock.patch("conda_forge_webservices.commands.remove_user")
@mock.patch("conda_forge_webservices.commands.make_rerender_dummy_commit")
@mock.patch("conda_forge_webservices.commands.add_bot_automerge")
@mock.patch("conda_forge_webservices.commands.rerender")
@mock.patch("conda_forge_webservices.commands.make_noarch")
@mock.patch("conda_forge_webservices.commands.relint")
@mock.patch("conda_forge_webservices.commands.update_team")
@mock.patch("conda_forge_webservices.commands.github.Github")
@mock.patch("conda_forge_webservices.commands.get_gh_client")
@mock.patch("conda_forge_webservices.commands.Repo")
def test_issue_command_triggers(
    git_repo,
    gh_app,
    gh,
    update_team,
    relint,
    make_noarch,
    rerender,
    add_bot_automerge,
    rerender_dummy_commit,
    remove_user,
    add_user,
    update_version,
    get_app_token_for_webservices_only,
    command,
    should,
    should_not,
    set_dummy_gh_token,
):
    add_user.return_value = True

    if command == "add_bot_automerge":
        command = add_bot_automerge
    elif command == "rerender":
        command = rerender
    elif command == "update_version":
        command = update_version
    elif command == "make_noarch":
        command = make_noarch
    elif command == "update_team":
        command = update_team
    elif command == "add_user":
        command = add_user
    elif command == "remove_user":
        command = remove_user
    else:
        raise ValueError(f"Unknown command: {command}")

    issue = gh_app.return_value.get_repo.return_value.get_issue.return_value
    repo = gh.return_value.get_repo.return_value
    gh.return_value.get_repo.return_value.default_branch = "main"
    for msg in should:
        print(msg, end=" " * 30 + "\r")

        rerender_dummy_commit.reset_mock()
        rerender_dummy_commit.return_value = True
        command.reset_mock()
        issue.reset_mock()
        issue_comment(title="hi", comment=msg)
        command.assert_called()
        issue.edit.assert_not_called()
        if command in (rerender, make_noarch, update_version):
            rerender_dummy_commit.assert_called()
        else:
            rerender_dummy_commit.assert_not_called()
        if command in (add_user, remove_user):
            command.assert_called_with(git_repo.clone_from.return_value, "blah")

        rerender_dummy_commit.reset_mock()
        rerender_dummy_commit.return_value = True
        command.reset_mock()
        issue.reset_mock()
        issue_comment(title=msg, comment=None)
        command.assert_called()
        if command in (
            rerender,
            make_noarch,
            add_bot_automerge,
            add_user,
            remove_user,
            update_version,
        ):
            assert "Fixes #" in repo.create_pull.call_args.kwargs["body"]
        else:
            issue.edit.assert_called_with(state="closed")
        if command in (rerender, make_noarch, update_version):
            rerender_dummy_commit.assert_called()
        else:
            rerender_dummy_commit.assert_not_called()
        if command in (add_user, remove_user):
            command.assert_called_with(git_repo.clone_from.return_value, "blah")

        rerender_dummy_commit.reset_mock()
        rerender_dummy_commit.return_value = True
        command.reset_mock()
        print(msg, end=" " * 30 + "\r")
        issue_comment(msg, msg, repo_name="staged-recipes")
        command.assert_not_called()
        rerender_dummy_commit.assert_not_called()

    for msg in should_not:
        print(msg, end=" " * 30 + "\r")

        command.reset_mock()
        issue.reset_mock()
        issue_comment(title="hi", comment=msg)
        command.assert_not_called()
        issue.edit.assert_not_called()


@mock.patch("conda_forge_webservices.commands.get_app_token_for_webservices_only")
@mock.patch("conda_forge_webservices.commands.rerender")
@mock.patch("conda_forge_webservices.commands.make_noarch")
@mock.patch("conda_forge_webservices.commands.relint")
@mock.patch("conda_forge_webservices.commands.update_team")
@mock.patch("conda_forge_webservices.commands.get_gh_client")
@mock.patch("conda_forge_webservices.commands.Repo")
def test_rerender_failure(
    repo,
    gh,
    update_team,
    relint,
    make_noarch,
    rerender,
    get_app_token_for_webservices_only,
):
    rerender.side_effect = RequestException

    repo = gh.return_value.get_repo.return_value
    pull_create_issue = repo.get_pull.return_value.create_issue_comment

    msg = "@conda-forge-admin, please rerender"

    pr_detailed_comment(msg)

    rerender.assert_called()

    assert "ran into an issue with" in pull_create_issue.call_args[0][0]
    assert (
        "conda-forge/core for further assistance" in pull_create_issue.call_args[0][0]
    )


@mock.patch("conda_forge_webservices.commands._sync_default_branch")
@mock.patch("conda_forge_webservices.commands.get_app_token_for_webservices_only")
@mock.patch("conda_forge_webservices.commands.make_rerender_dummy_commit")
@mock.patch("conda_forge_webservices.commands.update_version")
@mock.patch("conda_forge_webservices.commands.make_noarch")
@mock.patch("conda_forge_webservices.commands.relint")
@mock.patch("conda_forge_webservices.commands.update_team")
@mock.patch("conda_forge_webservices.commands.get_gh_client")
@mock.patch("github.Github")
@mock.patch("conda_forge_webservices.commands.Repo")
def test_update_version_failure(
    repo,
    gh,
    gh_app,
    update_team,
    relint,
    make_noarch,
    update_version,
    rrdc,
    gatfwo,
    sdb,
    set_dummy_gh_token,
):
    update_version.side_effect = RequestException

    repos = [mock.MagicMock(), mock.MagicMock(), mock.MagicMock()]
    for repo in repos:
        repo.default_branch = "main"
    gh.return_value.get_repo.side_effect = repos
    pull_create_issue = repos[0].create_pull.return_value.create_issue_comment

    msg = "@conda-forge-admin, please update version"

    issue_comment(title=msg, comment=None)

    update_version.assert_called()

    assert "ran into an issue with" in pull_create_issue.call_args[0][0]
    assert (
        "conda-forge/core for further assistance" in pull_create_issue.call_args[0][0]
    )


@pytest.mark.parametrize(
    "number,comment_id,review_id",
    [
        (5, -1, None),  # PR description
        (5, 2178557279, None),  # Normal PR comment
        (5, None, 2128210901),  # Submitted review
        (5, None, 1646160070),  # Comment in a submitted review
        (5, None, 1646411494),  # Reply in a review comment
        (5, None, 1646163741),  # Single comment review
        (4, -1, None),  # Issue description
        (4, 2178549014, None),  # Issue comment
    ],
)
def test_find_reactable_comment(number, comment_id, review_id):
    """
    In this PR there are several comments that we could have reacted to:
    https://github.com/conda-forge/conda-pypi-feedstock/pull/5

    - https://github.com/conda-forge/conda-pypi-feedstock/pull/5#issue-2362214690
        This is the PR description.
    - https://github.com/conda-forge/conda-pypi-feedstock/pull/5#issuecomment-2178557279
        A 'normal' comment in the PR discussion.
    - https://github.com/conda-forge/conda-pypi-feedstock/pull/5#pullrequestreview-2128210901
        The summary of a submitted review (e.g. "Approved").
    - https://github.com/conda-forge/conda-pypi-feedstock/pull/5#discussion_r1646160070
        A comment that is part of a submitted review.
    - https://github.com/conda-forge/conda-pypi-feedstock/pull/5#discussion_r1646411494
        A reply to one of those comments.
    - https://github.com/conda-forge/conda-pypi-feedstock/pull/5#discussion_r1646163741
        A single-comment review (comment on a diff line).

    In this issue we have:
    https://github.com/conda-forge/conda-pypi-feedstock/issues/4

    - https://github.com/conda-forge/conda-pypi-feedstock/issues/4#issue-2362214456
        The issue description
    - https://github.com/conda-forge/conda-pypi-feedstock/issues/4#issuecomment-2178549014
        A comment in the discussion
    """
    gh = github.Github()
    repo = gh.get_repo("conda-forge/conda-pypi-feedstock")
    comment = _find_reactable_comment(repo, number, comment_id, review_id)
    assert hasattr(comment, "create_reaction")


@pytest.fixture
def pillow_feedstock(tmp_path):
    yield Repo.clone_from(
        "https://github.com/conda-forge/pillow-feedstock.git",
        tmp_path,
        depth=1,
    )


def _read_codeowners_words(repo):
    return Path(repo.working_dir, ".github", "CODEOWNERS").read_text().split()


def _read_recipe_stripped_lines(repo):
    recipe_path = _determine_recipe_path(repo)
    if recipe_path:
        return [line.strip() for line in Path(recipe_path).read_text().splitlines()]
    return []


def test_add_and_remove_user(pillow_feedstock):
    assert remove_user(pillow_feedstock, "doesnotexist") is False
    assert "@doesnotexist" not in _read_codeowners_words(pillow_feedstock)
    assert "- doesnotexist" not in _read_recipe_stripped_lines(pillow_feedstock)

    assert add_user(pillow_feedstock, "doesnotexist") is True
    assert "@doesnotexist" in _read_codeowners_words(pillow_feedstock)
    assert "- doesnotexist" in _read_recipe_stripped_lines(pillow_feedstock)

    assert remove_user(pillow_feedstock, "doesnotexist") is True
    assert "@doesnotexist" not in _read_codeowners_words(pillow_feedstock)
    assert "- doesnotexist" not in _read_recipe_stripped_lines(pillow_feedstock)

    os.rename(
        os.path.join(pillow_feedstock.working_dir, "recipe"),
        os.path.join(pillow_feedstock.working_dir, "recipe-moved"),
    )
    assert remove_user(pillow_feedstock, "doesnotexist") is None
    assert "@doesnotexist" not in _read_codeowners_words(pillow_feedstock)
    assert "- doesnotexist" not in _read_recipe_stripped_lines(pillow_feedstock)
