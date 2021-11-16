from git import GitCommandError, Repo, Actor
import github
import os
import re
import subprocess
import time
import json
import shutil
import tempfile
from ruamel.yaml import YAML
import requests
from requests.exceptions import RequestException
import logging
import yaml

# from .utils import tmp_directory
from .linting import compute_lint_message, comment_on_pr, set_pr_status
from .update_teams import update_team
from .circle_ci import update_circle
import textwrap

LOGGER = logging.getLogger("conda_forge_webservices.commands")

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
PING_TEAM = re.compile(pre + r"(please )?ping (?P<team>\S+)", re.I)
RERUN_BOT = re.compile(pre + "(please )?rerun (the )?bot", re.I)
ADD_BOT_AUTOMERGE = re.compile(pre + "(please )?add bot automerge", re.I)
ADD_PY = re.compile(
    pre
    + r"(please )?add (python (?P<verfloat>[0-9]{1}\.[0-9]{1})|py(?P<verint>[0-9]{2}))",
    re.I,
)
ADD_USER = re.compile(pre + r"(please )?add user @(?P<user>\S+)$", re.I)


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

    if not is_staged_recipes:
        gh = github.Github(os.environ['GH_TOKEN'])
        repo = gh.get_repo("{}/{}".format(org_name, repo_name))
        pull = repo.get_pull(int(pr_num))
        if pull.head.repo.full_name.split("/")[0] == "conda-forge":
            message = textwrap.dedent("""
                    Hi! This is the friendly automated conda-forge-webservice.

                    It appears you are making a pull request from a branch in your feedstock and not a fork. This procedure will generate a separate build for each push to the branch and is thus not allowed. See our [documentation](https://conda-forge.org/docs/maintainer/updating_pkgs.html#forking-and-pull-requests) for more details.

                    Please close this pull request and remake it from a fork of this feedstock.

                    Have a great day!
                    """)  # noqa
            pull.create_issue_comment(message)

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
        restart_pull_request_ci(repo, int(pr_num))

    if PING_TEAM.search(comment):
        # get the team
        m = PING_TEAM.search(comment)
        if m.group('team'):
            team = m.group('team').strip()
            if team == 'team':
                team = repo_name.replace('-feedstock', '')
            else:
                if 'conda-forge/' in team:
                    team = team.split('/')[1].strip()
                if team.endswith('-feedstock'):
                    team = team[:-len('-feedstock')]
        else:
            team = repo_name.replace('-feedstock', '')

        gh = github.Github(os.environ['GH_TOKEN'])
        repo = gh.get_repo("{}/{}".format(org_name, repo_name))
        pull = repo.get_pull(int(pr_num))
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

    tmp_dir = None
    try:
        tmp_dir = tempfile.mkdtemp('_recipe')

        feedstock_dir = os.path.join(tmp_dir, repo_name)
        repo_url = "https://x-access-token:{}@github.com/{}/{}.git".format(
            os.environ['GH_TOKEN'], pr_owner, pr_repo)
        repo = Repo.clone_from(repo_url, feedstock_dir, branch=pr_branch, depth=1)

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

    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir)


def issue_comment(org_name, repo_name, issue_num, title, comment):
    if not repo_name.endswith("-feedstock"):
        return
    if comment is None:
        comment = ""
    if title is None:
        title = ""

    text = comment + title

    issue_commands = [UPDATE_TEAM_MSG, ADD_NOARCH_MSG, UPDATE_CIRCLECI_KEY_MSG,
                      RERENDER_MSG, UPDATE_CB3_MSG, ADD_BOT_AUTOMERGE, ADD_PY,
                      ADD_USER]
    send_pr_commands = [
        ADD_NOARCH_MSG, RERENDER_MSG, UPDATE_CB3_MSG, ADD_BOT_AUTOMERGE, ADD_PY,
        ADD_USER]

    if not any(command.search(text) for command in issue_commands):
        return

    # sometimes the webhook outpaces other bits of the API so we try a bit
    for i in range(5):
        try:
            gh = github.Github(os.environ['GH_TOKEN'])
            repo = gh.get_repo("{}/{}".format(org_name, repo_name))
            default_branch = repo.default_branch
            issue = repo.get_issue(int(issue_num))
            break
        except Exception as e:
            if i < 4:
                time.sleep(0.050 * 2**i)
                continue
            else:
                raise e

    if UPDATE_TEAM_MSG.search(text):
        update_team(org_name, repo_name)
        if UPDATE_TEAM_MSG.search(title):
            issue.edit(state="closed")
        message = textwrap.dedent("""
                Hi! This is the friendly automated conda-forge-webservice.

                I just wanted to let you know that I updated the team with maintainers from %s.
                """ % default_branch)  # noqa
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
            forked_user_repo = gh.get_repo("{}/{}".format(forked_user, repo_name))
        except github.UnknownObjectException:
            forked_user_gh.create_fork(gh.get_repo('{}/{}'.format(
                org_name,
                repo_name)))
            # we have to wait since the call above is async
            for i in range(5):
                try:
                    forked_user_repo = gh.get_repo("{}/{}".format(
                        forked_user, repo_name)
                    )
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
                    repo_name, forked_user, forked_user_repo.default_branch,
                    default_branch, gh
                )

            feedstock_dir = os.path.join(tmp_dir, repo_name)
            repo_url = "https://x-access-token:{}@github.com/{}/{}.git".format(
                os.environ['GH_TOKEN'], forked_user, repo_name)
            upstream_repo_url = "https://x-access-token:{}@github.com/{}/{}.git".format(
                os.environ['GH_TOKEN'], org_name, repo_name)
            git_repo = Repo.clone_from(repo_url, feedstock_dir, depth=1)
            forked_repo_branch = 'conda_forge_admin_{}'.format(issue_num)
            upstream = git_repo.create_remote('upstream', upstream_repo_url)
            upstream.fetch()
            new_branch = git_repo.create_head(
                forked_repo_branch,
                getattr(upstream.refs, default_branch)
            )
            new_branch.checkout()

            err_msg = None
            changed_anything = False
            check_bump_build = True
            do_rerender = False
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
                changed_anything |= make_rerender_dummy_commit(git_repo)

            elif ADD_BOT_AUTOMERGE.search(text):
                pr_title = "[ci skip] [cf admin skip] ***NO_CI*** adding bot automerge"
                comment_msg = "added bot automerge"
                to_close = ADD_BOT_AUTOMERGE.search(title)
                check_bump_build = False
                extra_msg = "\n\nMerge this PR to enable bot automerging.\n"

                changed_anything |= add_bot_automerge(git_repo)
            elif ADD_PY.search(text):
                m = ADD_PY.search(text)
                if m.group('verfloat'):
                    pyver = m.group('verfloat')
                elif m.group('verint'):
                    verint = m.group('verint')
                    pyver = verint[0] + '.' + verint[1]
                else:
                    pyver = None

                if pyver is None:
                    err_msg = (
                        "the Python version could not be found "
                        "from the issue text"
                    )
                else:
                    pr_title = "ENH adding python %s" % pyver
                    comment_msg = "added python %s" % pyver
                    to_close = ADD_PY.search(title)

                    extra_msg = (
                        "\n\nMerge this PR to enable Python %s. Note that you "
                        "may need to merge this PR into a new branch "
                        "on the feedstock to enable Python %s while also keeping "
                        "`win`, `aarch64`, `ppc64le`, or other Python builds "
                        "working.\n" % (pyver, pyver)
                    )

                    if pyver == "2.7":
                        extra_msg += (
                            "\n**WARNING: Python 2.7 reached end-of-life on "
                            "2020-01-01. `conda-forge` provides no support for "
                            "Python 2.7 builds and all existing builds are provided "
                            "on an \"as-is\" basis. Python 2.7 builds on the `win` "
                            "platform are not possible since we do not build against "
                            "`vs2008` in our CI providers. We also do not support "
                            "Python 2.7 builds on the `aarch64` or `ppc64le` "
                            "platforms.**\n"
                        )

                    do_rerender = True
                    changed_anything |= add_py(git_repo, pyver)
                    changed_anything |= make_rerender_dummy_commit(git_repo)
            elif ADD_USER.search(text):
                if ADD_USER.search(title):
                    m = ADD_USER.search(title)
                    user = m.group('user')
                elif ADD_USER.search(comment):
                    m = ADD_USER.search(comment)
                    user = m.group('user')
                else:
                    user = None
                comment_msg = "added user @%s" % user

                if user is None:
                    err_msg = (
                        "the user to add to the feedstock could not be found "
                        "from the issue title or text"
                    )
                    to_close = False
                else:
                    _changed_anything = add_user(git_repo, user)
                    if _changed_anything is None:
                        err_msg = (
                            "the recipe meta.yaml and/or CODEOWNERS file could "
                            "not be found or parsed properly when adding "
                            "user @%s to the feedstock" % user
                        )
                        to_close = False
                    else:
                        if not _changed_anything:
                            err_msg = (
                                "the recipe already has maintainer @%s" % user
                            )
                            to_close = True
                        else:
                            do_rerender = False
                            check_bump_build = False
                            pr_title = "[ci skip] adding user @%s" % user
                            to_close = ADD_USER.search(title)
                            extra_msg = (
                                "\n\nMerge this PR to add the user. Please do not rerender "  # noqa
                                "this PR or change it in any way. It has `[ci skip]` in "  # noqa
                                "the commit message to avoid pushing a new build and so "  # noqa
                                "the build configuration in the feedstock should not be "  # noqa
                                "changed.\n\nPlease contact [conda-forge/core](https://"  # noqa
                                "conda-forge.org/docs/maintainer/maintainer_faq.html"  # noqa
                                "#mfaq-contact-core) to have this PR merged, in case the "  # noqa
                                "maintainer is not around anymore."  # noqa
                            )
                            changed_anything |= _changed_anything

            if changed_anything:
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
                    default_branch, "{}:{}".format(forked_user, forked_repo_branch))

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
                issue.create_comment(message)
                if to_close:
                    issue.edit(state="closed")

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
        }
    )
    # ignore no such branch errors?
    if r.status_code != 404:
        r.raise_for_status()

    # poll until ready since this call is async
    for i in range(5):
        try:
            new_forked_default_branch = gh.get_repo("{}/{}".format(
                forked_user, repo_name)
            ).default_branch
            if new_forked_default_branch == default_branch:
                break
            else:
                raise RuntimeError(
                    "Forked repo branch %s could not be renamed to %s for repo %s" % (
                        forked_default_branch, default_branch, repo_name,
                    )
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
            "/api/repos/conda-forge/%s/builds/%s" % (
                repo.name, drone_build
            ),
        )

    pull.edit(state='closed')
    time.sleep(1)  # wait a bit to be sure things are ok
    pull.edit(state='open')


def add_user(repo, user):
    # a feedstock has user names in three spots as of 2021/06/19
    # 1. the recipe maintainers section
    # 2. the CODEOWNERS file
    # 3. the README
    #
    # The location in the README is subject to change and so we won't adjust it.
    # However, the recipe and the CODEOWNERS file are structured and so we can
    # adjust those easily enough.
    # Those happen to also be the only locations where adding a user really matters.

    recipe_path = os.path.join(repo.working_dir, "recipe", "meta.yaml")
    co_path = os.path.join(repo.working_dir, ".github", "CODEOWNERS")
    if os.path.exists(recipe_path):
        # get the current maintainers - if user is in them, return False
        with open(recipe_path, "r") as fp:
            lines = fp.readlines()
            keep_lines = []
            skip = 0
            for line in lines:
                if line.strip().startswith("extra:"):
                    skip += 1
                if skip > 0:
                    keep_lines.append(line)
            assert skip == 1, "team update failed due to > 1 'extra:' sections"
        data = yaml.safe_load("\n".join(keep_lines))
        curr_users = data["extra"]["recipe-maintainers"]
        if user in curr_users:
            return False
        else:
            if os.path.exists(co_path):
                # do code owners first
                with open(co_path, "r") as fp:
                    lines = [ln.strip() for ln in fp.readlines()]
                lines.append("* @%s" % user)
                with open(co_path, "w") as fp:
                    fp.write("\n".join(lines))

            # now the recipe
            # we cannot use yaml because sometimes reading a recipe via the yaml
            # is impossible or lossy
            # so we have to parse it directly :/
            with open(recipe_path, "r") as fp:
                lines = fp.read().splitlines()
            new_lines = []
            found_extra = False
            found_rm = False
            added_user = False
            for line in lines:
                if line.strip().startswith("extra:"):
                    found_extra = True
                    new_lines.append(line)
                elif line.strip().startswith("recipe-maintainers:"):
                    found_rm = True
                    new_lines.append(line)
                elif found_extra and found_rm and not added_user:
                    added_user = True
                    dashind = line.find("-")
                    if dashind == -1:
                        return None
                    head = line[:dashind]
                    new_lines.append(head + "- " + user)
                    new_lines.append(line)
                else:
                    new_lines.append(line)

            if not added_user:
                return None

            with open(recipe_path, "w") as fp:
                fp.write("\n".join(new_lines))

            # and commit
            repo.index.add([recipe_path])
            if os.path.exists(co_path):
                repo.index.add([co_path])
            author = Actor("conda-forge-admin", "pelson.pub+conda-forge@gmail.com")
            # do not @-mention users in commit messages - it causes lots of
            # extra notifications
            repo.index.commit("[ci skip] added user %s" % user, author=author)

            return True
    else:
        return None


def add_py(repo, pyver):
    pystr = "%s.* *_cpython" % pyver

    yaml = YAML()
    yaml.indent(mapping=2, sequence=4, offset=2)
    cbc_pth = os.path.join(repo.working_dir, "recipe", "conda_build_config.yaml")

    if os.path.exists(cbc_pth):
        with open(cbc_pth, "r") as fp:
            cbc = yaml.load(fp)
    else:
        cbc = {}

    if "python" in cbc:
        cbc["python"].append(pystr)
    else:
        cbc["python"] = [pystr]

    with open(cbc_pth, "w") as fp:
        yaml.dump(cbc, fp)

    # need to apply the selector - being very lazy here
    with open(cbc_pth, "r") as fp:
        lines = fp.readlines()
    with open(cbc_pth, "w") as fp:
        for line in lines:
            if pystr in line:
                line = line.replace(
                    pystr,
                    "%s  # [not (aarch64 or ppc64le or win)]]" % pystr
                )
            fp.write(line)

    # commit
    repo.index.add([cbc_pth])
    author = Actor("conda-forge-admin", "pelson.pub+conda-forge@gmail.com")
    repo.index.commit("added python %s" % pyver, author=author)

    return True


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

    # now commit
    repo.index.add([cf_yml])
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
        "dummy commit for rerendering",
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
        LOGGER.warning('Linting was skipped.')
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
