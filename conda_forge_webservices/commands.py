from git import GitCommandError, Repo, Actor
import github
import os
import re
import subprocess
import time
import json
import shutil
from ruamel.yaml import YAML
import requests
from requests.exceptions import RequestException

from .utils import tmp_directory
from .linting import compute_lint_message, comment_on_pr, set_pr_status
from .update_teams import update_team
from .circle_ci import update_circle
import textwrap


pre = r"@conda-forge-(admin|linter)\s*[,:]?\s*"
COMMAND_PREFIX = re.compile(pre, re.I)
ADD_NOARCH_MSG = re.compile(pre + "(please )?(add|make) `?noarch:? python`?", re.I)
RERENDER_MSG = re.compile(pre + "(please )?re-?render", re.I)
RESTART_CI = re.compile(pre + "(please )?restart (build|builds|ci)", re.I)
LINT_MSG = re.compile(pre + "(please )?(re-?)?lint", re.I)
UPDATE_TEAM_MSG = re.compile(pre + "(please )?(update|refresh) (the )?team", re.I)
UPDATE_CIRCLECI_KEY_MSG = re.compile(
    pre + "(please )?(update|refresh) (the )?circle", re.I)
UPDATE_CB3_MSG = re.compile(
    pre + "(please )?update (for )?(cb|conda[- ]build)[- ]?3", re.I)
PING_TEAM = re.compile(pre + "(please )?ping team", re.I)
RERUN_BOT = re.compile(pre + "(please )?rerun (the )?bot", re.I)
ADD_BOT_AUTOMERGE = re.compile(pre + "(please )?add bot automerge", re.I)


def pr_comment(org_name, repo_name, issue_num, comment):
    if not COMMAND_PREFIX.search(comment):
        return
    gh = github.Github(os.environ['GH_TOKEN'])
    repo = gh.get_repo("{}/{}".format(org_name, repo_name))
    pr = repo.get_pull(int(issue_num))
    pr_detailed_comment(
        org_name,
        repo_name,
        pr.head.user.login,
        pr.head.repo.name,
        pr.head.ref,
        issue_num,
        comment,
    )


def pr_detailed_comment(
    org_name,
    repo_name,
    pr_owner,
    pr_repo,
    pr_branch,
    pr_num,
    comment,
):
    is_staged_recipes = (repo_name == "staged-recipes")
    if not (repo_name.endswith("-feedstock") or is_staged_recipes):
        return

    if not is_staged_recipes and UPDATE_CIRCLECI_KEY_MSG.search(comment):
        update_circle(org_name, repo_name)

        gh = github.Github(os.environ['GH_TOKEN'])
        repo = gh.get_repo("{}/{}".format(org_name, repo_name))
        pull = repo.get_pull(int(pr_num))
        message = textwrap.dedent("""
                Hi! This is the friendly automated conda-forge-webservice.

                I just wanted to let you know that I updated the circle-ci
                deploy key and followed the project.
                """)
        pull.create_issue_comment(message)

    if RESTART_CI.search(comment):
        gh = github.Github(os.environ['GH_TOKEN'])
        repo = gh.get_repo("{}/{}".format(org_name, repo_name))
        pull = repo.get_pull(int(pr_num))
        pull.edit(state='closed')
        time.sleep(1)  # wait a bit to be sure things are ok
        pull.edit(state='open')

    if PING_TEAM.search(comment):
        gh = github.Github(os.environ['GH_TOKEN'])
        repo = gh.get_repo("{}/{}".format(org_name, repo_name))
        pull = repo.get_pull(int(pr_num))
        team = repo_name.replace('-feedstock', '')
        message = textwrap.dedent("""
            Hi! This is the friendly automated conda-forge-webservice.

            I was asked to ping @conda-forge/%s and so here I am doing that.
            """ % team)
        pull.create_issue_comment(message)

    if not is_staged_recipes and RERUN_BOT.search(comment):
        gh = github.Github(os.environ['GH_TOKEN'])
        repo = gh.get_repo("{}/{}".format(org_name, repo_name))
        add_bot_rerun_label(repo, pr_num)

    pr_commands = [LINT_MSG]
    if not is_staged_recipes:
        pr_commands += [ADD_NOARCH_MSG, RERENDER_MSG, UPDATE_CB3_MSG]

    if not any(command.search(comment) for command in pr_commands):
        return

    with tmp_directory() as tmp_dir:
        feedstock_dir = os.path.join(tmp_dir, repo_name)
        repo_url = "https://{}@github.com/{}/{}.git".format(
            os.environ['GH_TOKEN'], pr_owner, pr_repo)
        repo = Repo.clone_from(repo_url, feedstock_dir, branch=pr_branch)

        if LINT_MSG.search(comment):
            relint(org_name, repo_name, pr_num)

        changed_anything = False
        expected_changes = []
        extra_msg = ''
        if not is_staged_recipes:
            do_noarch = do_cb3 = do_rerender = False
            if ADD_NOARCH_MSG.search(comment):
                do_noarch = do_rerender = True
                expected_changes.append('add noarch')
            if UPDATE_CB3_MSG.search(comment):
                do_cb3 = do_rerender = True
                expected_changes.append('update for conda-build 3')
            if RERENDER_MSG.search(comment):
                do_rerender = True

            if do_noarch:
                changed_anything |= make_noarch(repo)

            if do_cb3:
                c, cb3_changes = update_cb3(repo)
                changed_anything |= c
                extra_msg += '\n\n' + cb3_changes

        message = None
        if expected_changes:
            if len(expected_changes) > 1:
                expected_changes[-1] = 'and ' + expected_changes[-1]
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
                rerender_error = rerender(org_name + '/' + repo_name, int(pr_num))
            except RequestException:
                rerender_error = True

        if rerender_error:
            doc_url = (
                'https://conda-forge.org/docs/maintainer/updating_pkgs.html'
                '#rerendering-with-conda-smithy-locally'
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
            gh = github.Github(os.environ['GH_TOKEN'])
            gh_repo = gh.get_repo("{}/{}".format(org_name, repo_name))
            pull = gh_repo.get_pull(int(pr_num))
            pull.create_issue_comment(message)


def issue_comment(org_name, repo_name, issue_num, title, comment):
    if not repo_name.endswith("-feedstock"):
        return

    text = comment + title

    issue_commands = [UPDATE_TEAM_MSG, ADD_NOARCH_MSG, UPDATE_CIRCLECI_KEY_MSG,
                      RERENDER_MSG, UPDATE_CB3_MSG, ADD_BOT_AUTOMERGE]
    send_pr_commands = [
        ADD_NOARCH_MSG, RERENDER_MSG, UPDATE_CB3_MSG, ADD_BOT_AUTOMERGE]

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
                """)  # noqa
        issue.create_comment(message)

    if UPDATE_CIRCLECI_KEY_MSG.search(text):
        update_circle(org_name, repo_name)
        if UPDATE_CIRCLECI_KEY_MSG.search(title):
            issue.edit(state="closed")
        message = textwrap.dedent("""
                Hi! This is the friendly automated conda-forge-webservice.

                I just wanted to let you know that I updated the circle-ci deploy key and followed the project.
                """)  # noqa
        issue.create_comment(message)

    if any(command.search(text) for command in send_pr_commands):
        forked_user_gh = gh.get_user()
        forked_user = forked_user_gh.login

        # make the fork if it does not exist
        try:
            gh.get_repo("{}/{}.git".format(forked_user, repo_name))
        except github.UnknownObjectException:
            forked_user_gh.create_fork(gh.get_repo('{}/{}'.format(
                org_name,
                repo_name)))

        with tmp_directory() as tmp_dir:
            feedstock_dir = os.path.join(tmp_dir, repo_name)
            repo_url = "https://{}@github.com/{}/{}.git".format(
                os.environ['GH_TOKEN'], forked_user, repo_name)
            upstream_repo_url = "https://{}@github.com/{}/{}.git".format(
                os.environ['GH_TOKEN'], org_name, repo_name)
            git_repo = Repo.clone_from(repo_url, feedstock_dir)
            forked_repo_branch = 'conda_forge_admin_{}'.format(issue_num)
            upstream = git_repo.create_remote('upstream', upstream_repo_url)
            upstream.fetch()
            new_branch = git_repo.create_head(forked_repo_branch, upstream.refs.master)
            new_branch.checkout()

            changed_anything = False
            check_bump_build = True
            do_rerender = False
            is_rerender = False
            extra_msg = ""
            if UPDATE_CB3_MSG.search(text):
                pr_title = "MNT: Update for conda-build 3"
                comment_msg = "updated the recipe for conda-build 3"
                to_close = UPDATE_CB3_MSG.search(title)

                if ADD_NOARCH_MSG.search(text):
                    changed_anything |= make_noarch(git_repo)
                    pr_title += ' and add noarch: python'
                    comment_msg += ' and added `noarch: python`'

                c, cb3_changes = update_cb3(git_repo)
                changed_anything |= c
                if not c:
                    cb3_changes = "There weren't any changes to make for conda-build 3."
                extra_msg = '\n\n' + cb3_changes

                do_rerender = True
                changed_anything |= make_rerender_dummy_commit(git_repo)
            elif ADD_NOARCH_MSG.search(text):
                pr_title = "MNT: Add noarch: python"
                comment_msg = "made the recipe `noarch: python`"
                to_close = ADD_NOARCH_MSG.search(title)

                changed_anything |= make_noarch(git_repo)
                do_rerender = True
                changed_anything |= make_rerender_dummy_commit(git_repo)
            elif RERENDER_MSG.search(text):
                pr_title = "MNT: rerender"
                comment_msg = "rerendered the recipe"
                to_close = RERENDER_MSG.search(title)

                do_rerender = True
                is_rerender = True
                changed_anything |= make_rerender_dummy_commit(git_repo)

            elif ADD_BOT_AUTOMERGE.search(text):
                pr_title = "[ci skip] [cf admin skip] ***NO_CI*** adding bot automerge"
                comment_msg = "added bot automerge"
                to_close = ADD_BOT_AUTOMERGE.search(title)
                check_bump_build = False
                extra_msg = "\n\nMerge this PR to enable bot automerging.\n"

                changed_anything |= add_bot_automerge(git_repo)

            if changed_anything or is_rerender:
                git_repo.git.push("origin", forked_repo_branch)
                pr_message = textwrap.dedent("""
                        Hi! This is the friendly automated conda-forge-webservice.

                        I've {} as instructed in #{}.{}
                        """).format(comment_msg, issue_num, extra_msg)
                if check_bump_build:
                    pr_message += textwrap.dedent("""

                        Here's a checklist to do before merging.
                        - [ ] Bump the build number if needed.
                        """)

                if to_close:
                    pr_message += "\nFixes #{}".format(issue_num)

                pr = repo.create_pull(
                    pr_title, pr_message,
                    "master", "{}:{}".format(forked_user, forked_repo_branch))

                message = textwrap.dedent("""
                        Hi! This is the friendly automated conda-forge-webservice.

                        I just wanted to let you know that I {} in {}/{}#{}.
                        """).format(comment_msg, org_name, repo_name, pr.number)
                issue.create_comment(message)

                if do_rerender:
                    rerender_error = False
                    try:
                        rerender_error = rerender(
                            org_name + '/' + repo_name,
                            pr.number,
                        )
                    except RequestException:
                        rerender_error = True

                    if rerender_error:
                        doc_url = (
                            'https://conda-forge.org/docs/maintainer/updating_pkgs.html'
                            '#rerendering-with-conda-smithy-locally'
                        )
                        message = textwrap.dedent("""
                            Hi! This is the friendly automated conda-forge-webservice.

                            I tried to rerender for you but ran into an issue with kicking GitHub Actions to do the rerender.
                            Please ping conda-forge/core for further assistance. You can also try [re-rendering locally]({}).
                            """).format(doc_url)  # noqa

                        pr.create_issue_comment(message)

            else:
                message = textwrap.dedent("""
                        Hi! This is the friendly automated conda-forge-webservice.

                        I've {} as requested, but nothing actually changed.
                        """).format(comment_msg)
                issue.create_comment(message)
                if to_close:
                    issue.edit(state="closed")


def add_bot_automerge(repo):
    yaml = YAML()

    cf_yml = os.path.join(repo.working_dir, "conda-forge.yml")
    if os.path.exists(cf_yml):
        with open(cf_yml, 'r') as fp:
            cfg = yaml.load(fp)
    else:
        cfg = {}

    if cfg.get('bot', {}).get('automerge', False):
        # already have it
        return False

    # add to conda-forge.yml
    # we do it this way to make room
    # for other keys in the future
    if 'bot' not in cfg:
        cfg['bot'] = {}
    cfg['bot']['automerge'] = True
    with open(cf_yml, 'w') as fp:
        yaml.dump(cfg, fp)

    # copy in the workflow def from smithy
    from conda_smithy.configure_feedstock import conda_forge_content

    workflows_dir = os.path.join(repo.working_dir, ".github", "workflows")
    os.makedirs(workflows_dir, exist_ok=True)
    dest_main_yml = os.path.join(workflows_dir, "main.yml")
    src_main_yml = os.path.join(
        conda_forge_content,
        "feedstock_content",
        ".github",
        "workflows",
        "main.yml",
    )
    shutil.copyfile(src_main_yml, dest_main_yml)

    # now commit
    repo.index.add([dest_main_yml, cf_yml])
    author = Actor("conda-forge-admin", "pelson.pub+conda-forge@gmail.com")
    repo.index.commit(
        "[ci skip] ***NO_CI*** added bot automerge", author=author)
    return True


def make_rerender_dummy_commit(repo):
    # add a dummy commit
    readme_file = os.path.join(repo.working_dir, "README.md")
    with open(readme_file, "a") as fp:
        fp.write("""\

<!-- dummy commit to enable rerendering -->

""")
    repo.index.add([readme_file])
    author = Actor("conda-forge-admin", "pelson.pub+conda-forge@gmail.com")
    repo.index.commit(
        "[ci skip] [cf admin skip] dummy commit for rerendering",
        author=author,
    )
    return True


def rerender(full_name, pr_num):
    # dispatch events do not seem to be in the pygithub API
    # so we are rolling the API request by hand
    headers = {
        "authorization": "Bearer %s" % os.environ['GH_TOKEN'],
        'content-type': 'application/json',
    }
    r = requests.post(
        "https://api.github.com/repos/%s/dispatches" % full_name,
        data=json.dumps({"event_type": "rerender", "client_payload": {"pr": pr_num}}),
        headers=headers,
    )
    return r.status_code != 204


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
    return True


def update_cb3(repo):
    output = subprocess.check_output(
        ["conda", "smithy", "update-cb3"],
        cwd=repo.working_dir,
    )
    output = output.decode('utf-8')
    repo.git.add(A=True)
    if repo.is_dirty():
        author = Actor("conda-forge-admin", "pelson.pub+conda-forge@gmail.com")
        repo.index.commit("Update for conda-build 3", author=author)
        return True, output
    else:
        return False, output


def relint(owner, repo_name, pr_num):
    pr = int(pr_num)
    lint_info = compute_lint_message(
        owner,
        repo_name,
        pr,
        repo_name == 'staged-recipes',
    )
    if not lint_info:
        print('Linting was skipped.')
    else:
        msg = comment_on_pr(owner, repo_name, pr, lint_info['message'], force=True)
        set_pr_status(owner, repo_name, lint_info, target_url=msg.html_url)


def add_bot_rerun_label(repo, pr_num):
    # try to add the label if it does not exist
    # this makes things look nicer but is not needed
    try:
        # color and description are from the bot repo
        repo.create_label(
            'bot-rerun',
            '#191970',
            description=(
                'Apply this label if you want the bot '
                'to retry issuing a particular '
                'pull-request'))
    except github.GithubException:
        # an error here is not fatal so swallow it and
        # move on
        pass

    # now add the label
    # this API call will work even if the label does not
    # exist yet or is already on the PR
    pull = repo.get_pull(int(pr_num))
    pull.add_to_labels("bot-rerun")
