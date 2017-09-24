from git import GitCommandError, Repo, Actor
from conda_build.metadata import MetaData
import github
import os
import subprocess
from .utils import tmp_directory
from .linting import compute_lint_message, comment_on_pr, set_pr_status
from .update_teams import update_team
from conda_smithy import __version__ as conda_smithy_version


def check_for_bot(comment):
    return "@conda-forge-admin" in comment or "@conda-forge-linter" in comment


def pr_comment(org_name, repo_name, issue_num, comment):
    if not check_for_bot(comment):
        return
    gh = github.Github(os.environ['GH_TOKEN'])
    repo = gh.get_repo("{}/{}".format(org_name, repo_name))
    pr = repo.get_pull(int(issue_num))
    pr_detailed_comment(org_name, repo_name, pr.head.user.login, pr.head.repo.name, pr.head.ref, issue_num, comment)


def pr_detailed_comment(org_name, repo_name, pr_owner, pr_repo, pr_branch, pr_num, comment):
    if not repo_name.endswith("-feedstock"):
        return

    if not check_for_bot(comment):
        return

    with tmp_directory() as tmp_dir:
        feedstock_dir = os.path.join(tmp_dir, repo_name)
        repo_url = "https://{}@github.com/{}/{}.git".format(os.environ['GH_TOKEN'],
            pr_owner, pr_repo)
        repo = Repo.clone_from(repo_url, feedstock_dir, branch=pr_branch)

        if "please add noarch: python" in comment.lower():
            make_noarch(repo)
            rerender(repo)
        if "please rerender" in comment.lower():
            rerender(repo)
        if "please lint" in comment.lower():
            relint(org_name, repo_name, pr_num)
    
        repo.remotes.origin.push()


def issue_comment(org_name, repo_name, issue_num, title, comment):
    if not repo_name.endswith("-feedstock"):
        return

    if not check_for_bot(comment + title):
        return

    if "please update team" in comment + title:
        update_team(org_name, repo_name, issue_num, title, comment)


def issue_team_update(org_name, repo_name, issue_num, title, comment):
    update_team(org_name, repo_name)
    gh = github.Github(os.environ['GH_TOKEN'])
    repo = gh.get_repo("{}/{}".format(org_name, repo_name))
    issue = repo.get_issue(int(issue_num))
    if "please update team" in title:
        issue.edit(state="closed")
        issue.create_comment("Hi, I updated the team and closed this issue")
    else:
        issue.create_comment("Hi, I updated the team.")


def rerender(repo):
    subprocess.call(["conda", "smithy", "rerender"], cwd=repo.working_dir)
    if repo.is_dirty():
        author = Actor("conda-forge-admin", "pelson.pub+conda-forge@gmail.com")
        repo.index.commit("MNT: Re-rendered with conda-smithy {}".format(conda_smithy_version), author=author)


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
    lint_info = compute_lint_message(owner, repo_name, pr, False)
    if not lint_info:
        print('Linting was skipped.')
    else:
        msg = comment_on_pr(owner, repo_name, pr, lint_info['message'])
        set_pr_status(owner, repo_name, lint_info, target_url=msg.html_url)

