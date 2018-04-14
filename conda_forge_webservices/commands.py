from git import GitCommandError, Repo, Actor
from conda_build.metadata import MetaData
import github
import os
import re
import subprocess
from .utils import tmp_directory
from .linting import compute_lint_message, comment_on_pr, set_pr_status
from .update_teams import update_team
from .circle_ci import update_circle
from conda_smithy import __version__ as conda_smithy_version
import textwrap


pre = r"@conda-forge-(admin|linter)\s*[,:]?\s*"
COMMAND_PREFIX = re.compile(pre, re.I)
ADD_NOARCH_MSG = re.compile(pre + "please (add|make) `?noarch:? python`?", re.I)
RERENDER_MSG = re.compile(pre + "please re-?render", re.I)
LINT_MSG = re.compile(pre + "please (re-?)?lint", re.I)
UPDATE_TEAM_MSG = re.compile(pre + "please (update|refresh) (the )?team", re.I)
UPDATE_CIRCLECI_KEY_MSG = re.compile(pre + "please (update|refresh) (the )?circle", re.I)


def pr_comment(org_name, repo_name, issue_num, comment):
    if not COMMAND_PREFIX.search(comment):
        return
    gh = github.Github(os.environ['GH_TOKEN'])
    repo = gh.get_repo("{}/{}".format(org_name, repo_name))
    pr = repo.get_pull(int(issue_num))
    pr_detailed_comment(org_name, repo_name, pr.head.user.login, pr.head.repo.name, pr.head.ref, issue_num, comment)


def pr_detailed_comment(org_name, repo_name, pr_owner, pr_repo, pr_branch, pr_num, comment):
    is_staged_recipes = (repo_name == "staged-recipes")
    if not (repo_name.endswith("-feedstock") or is_staged_recipes):
        return

    pr_commands = [LINT_MSG]
    if not is_staged_recipes:
        pr_commands += [ADD_NOARCH_MSG, RERENDER_MSG]

    if not any(command.search(comment) for command in pr_commands):
        return

    with tmp_directory() as tmp_dir:
        feedstock_dir = os.path.join(tmp_dir, repo_name)
        repo_url = "https://{}@github.com/{}/{}.git".format(os.environ['GH_TOKEN'],
            pr_owner, pr_repo)
        repo = Repo.clone_from(repo_url, feedstock_dir, branch=pr_branch)

        if not is_staged_recipes:
            if ADD_NOARCH_MSG.search(comment):
                make_noarch(repo)
                rerender(repo, pr_num)
            if RERENDER_MSG.search(comment):
                rerender(repo, pr_num)
        if LINT_MSG.search(comment):
            relint(org_name, repo_name, pr_num)

        repo.remotes.origin.push()


def issue_comment(org_name, repo_name, issue_num, title, comment):
    if not repo_name.endswith("-feedstock"):
        return

    text = comment + title

    issue_commands = [UPDATE_TEAM_MSG, ADD_NOARCH_MSG, UPDATE_CIRCLECI_KEY_MSG]
    send_pr_commands = [ADD_NOARCH_MSG]

    if not any(command.search(text) for command in issue_commands):
        return

    gh = github.Github(os.environ['GH_TOKEN'])
    repo = gh.get_repo("{}/{}".format(org_name, repo_name))
    issue = repo.get_issue(int(issue_num))

    if UPDATE_TEAM_MSG.search(text):
        update_team(org_name, repo_name)
        if UPDATE_TEAM_MSG.search(title):
            issue.edit(state="closed")
        message = textwrap.dedent("""
                Hi! This is the friendly automated conda-forge-webservice.

                I just wanted to let you know that I updated the team with maintainers from master.
                """)
        issue.create_comment(message)

    if UPDATE_CIRCLECI_KEY_MSG.search(text):
        update_circle(org_name, repo_name)
        if UPDATE_CIRCLECI_KEY_MSG.search(title):
            issue.edit(state="closed")
        message = textwrap.dedent("""
                Hi! This is the friendly automated conda-forge-webservice.

                I just wanted to let you know that I updated the circle-ci deploy key and followed the project.
                """)
        issue.create_comment(message)

    if any(command.search(text) for command in send_pr_commands):
        forked_user = gh.get_user().login
        forked_repo = gh.get_user().create_fork(repo)

        with tmp_directory() as tmp_dir:
            feedstock_dir = os.path.join(tmp_dir, repo_name)
            repo_url = "https://{}@github.com/{}/{}.git".format(os.environ['GH_TOKEN'],
                forked_user, repo_name)
            git_repo = Repo.clone_from(repo_url, feedstock_dir)
            forked_repo_branch = 'conda_forge_admin_{}'.format(issue_num)
            new_branch = git_repo.create_head(forked_repo_branch)
            new_branch.checkout()

            if ADD_NOARCH_MSG.search(text):
                make_noarch(git_repo)
                rerender(git_repo, issue_num)
                git_repo.git.push("origin", forked_repo_branch)
                msg = "MNT: Add noarch: python"
                pr = repo.create_pull(msg, "As instructed in #{}".format(issue_num),
                        "master", "{}:{}".format(forked_user, forked_repo_branch))

                if ADD_NOARCH_MSG.search(title):
                    issue.edit(state="closed")

                message = textwrap.dedent("""
                        Hi! This is the friendly automated conda-forge-webservice.

                        I just wanted to let you know that I made the recipe noarch: python in {}/{}#{}.
                        """.format(org_name, repo_name, pr.number))
                issue.create_comment(message)


def rerender(repo, pr_num):
    subprocess.call(["conda", "smithy", "rerender"], cwd=repo.working_dir)
    if repo.is_dirty():
        author = Actor("conda-forge-admin", "pelson.pub+conda-forge@gmail.com")
        repo.index.commit("MNT: Re-rendered with conda-smithy {}".format(conda_smithy_version), author=author, committer=author)
    else:
        message = textwrap.dedent("""
                Hi! This is the friendly automated conda-forge-webservice.

                I rerendered the feedstock and it seems to be already up-to-date.
                """)
        repo.get_issue(pr_num).create_comment(message)


def make_noarch(repo):
    meta_yaml = os.path.join(repo.working_dir, "recipe", "meta.yaml")
    with open(meta_yaml, 'r') as fh:
        lines = [line for line in fh]
    with open(meta_yaml, 'w') as fh:
        build_line = False
        for line in lines:
            if build_line:
                spaces = len(line) - len(line.lstrip())
                line = "{}noarch: python\n{}".format(" "*spaces, line)
            build_line = False
            if line.rstrip() == 'build:':
                build_line = True
            fh.write(line)
    repo.index.add([meta_yaml])
    author = Actor("conda-forge-admin", "pelson.pub+conda-forge@gmail.com")
    repo.index.commit("Add noarch:python option", author=author)


def relint(owner, repo_name, pr_num):
    pr = int(pr_num)
    lint_info = compute_lint_message(owner, repo_name, pr, repo_name == 'staged-recipes')
    if not lint_info:
        print('Linting was skipped.')
    else:
        msg = comment_on_pr(owner, repo_name, pr, lint_info['message'], force=True)
        set_pr_status(owner, repo_name, lint_info, target_url=msg.html_url)

