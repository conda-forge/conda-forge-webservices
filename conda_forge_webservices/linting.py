from contextlib import contextmanager
from glob import glob
import os
import shutil
import tempfile
import textwrap
import time

import requests
from git import Repo
import github
import conda_smithy.lint_recipe


@contextmanager
def tmp_directory():
    tmp_dir = tempfile.mkdtemp('_recipe')
    yield tmp_dir
    shutil.rmtree(tmp_dir)

def find_recipes(a_dir):
    return [os.path.dirname(y) for x in os.walk(a_dir)
            for y in glob(os.path.join(x[0], 'meta.yaml'))]

def compute_lint_message(repo_owner, repo_name, pr_id):
    gh = github.Github(os.environ['GH_TOKEN'])

    owner = gh.get_user(repo_owner)
    remote_repo = owner.get_repo(repo_name)

    mergeable = None
    while mergeable is None:
        time.sleep(0.1)
        pull_request = remote_repo.get_pull(pr_id)
        if pull_request.state != "open":
            return {}
        mergeable = pull_request.mergeable

    with tmp_directory() as tmp_dir:
        repo = Repo.clone_from(remote_repo.clone_url, tmp_dir)

        # Checkout the PR head.
        repo.remotes.origin.fetch('pull/{pr}/head:pr/{pr}'.format(pr=pr_id))
        repo.refs['pr/{}'.format(pr_id)].checkout()
        sha = str(repo.head.object.hexsha)

        # Raise an error if the PR is not mergeable.
        if not mergeable:
            message = textwrap.dedent("""
                Hi! This is the friendly automated conda-forge-linting service.
                
                I was trying to look for recipes to lint for you, but it appears we have a merge conflict.
                Please try to merge or rebase with the base branch to resolve this conflict.
                
                Please ping the 'conda-forge/core' team (using the @ notation in a comment) if you believe this is a bug.
                """)
            status = 'merge_conflict'

            lint_info = {'message': message,
                         'status': status,
                         'sha': sha}

            return lint_info

        # Get the list of recipes and prep for linting.
        recipes = find_recipes(tmp_dir)
        all_pass = True
        messages = []

        # Exclude some things from our list of recipes.
        recipe_dirs = [recipe for recipe in recipes
                       if os.path.basename(recipe) != 'example']

        # Sort the recipes for consistent linting order (which glob doesn't give us).
        recipe_dirs = sorted(recipe_dirs)

        rel_recipe_dirs = []
        for recipe_dir in recipe_dirs:
            rel_path = os.path.relpath(recipe_dir, tmp_dir)
            rel_recipe_dirs.append(rel_path)
            try:
                lints = conda_smithy.lint_recipe.main(recipe_dir)
            except Exception as err:
                print('ERROR:', err)
                lints = ['Failed to even lint the recipe (might be a conda-smithy bug) :cry:']
            if lints:
                all_pass = False
                messages.append("\nFor **{}**:\n\n{}".format(rel_path,
                                                             '\n'.join(' * {}'.format(lint) for lint in lints)))

    # Put the recipes in the form "```recipe/a```, ```recipe/b```".
    recipe_code_blocks = ', '.join('```{}```'.format(r) for r in rel_recipe_dirs)

    good = textwrap.dedent("""
    Hi! This is the friendly automated conda-forge-linting service.

    I just wanted to let you know that I linted all conda-recipes in your PR ({}) and found it was in an excellent condition.

    """.format(recipe_code_blocks))

    bad = textwrap.dedent("""
    Hi! This is the friendly automated conda-forge-linting service.

    I wanted to let you know that I linted all conda-recipes in your PR ({}) and found some lint.

    Here's what I've got...

    {{}}
    """.format(recipe_code_blocks)).format('\n'.join(messages))

    if not recipe_dirs:
        message = textwrap.dedent("""
            Hi! This is the friendly automated conda-forge-linting service.
            
            I was trying to look for recipes to lint for you, but couldn't find any.
            Please ping the 'conda-forge/core' team (using the @ notation in a comment) if you believe this is a bug.
            """)
        status = 'no recipes'
    elif all_pass:
        message = good
        status = 'good'
    else:
        message = bad
        status = 'bad'

    pull_request = remote_repo.get_pull(pr_id)
    if pull_request.state == "open":
        lint_info = {'message': message,
                     'status': status,
                     'sha': sha}
    else:
        lint_info = {}

    return lint_info


def comment_on_pr(owner, repo_name, pr_id, message):
    gh = github.Github(os.environ['GH_TOKEN'])
    my_login = gh.get_user().login

    user = gh.get_user(owner)
    repo = user.get_repo(repo_name)
    issue = repo.get_issue(pr_id)

    comments = list(issue.get_comments())
    comment_owners = [comment.user.login for comment in comments]

    my_last_comment = None
    if my_login in comment_owners:
        my_last_comment = [comment for comment in comments
                           if comment.user.login == my_login][-1]

    # Only comment if we haven't before, or if the message we have is different.
    if my_last_comment is None or my_last_comment.body != message:
        my_last_comment = issue.create_comment(message)

    return my_last_comment


def set_pr_status(owner, repo_name, lint_info, target_url=None):
    gh = github.Github(os.environ['GH_TOKEN'])

    user = gh.get_user(owner)
    repo = user.get_repo(repo_name)
    if lint_info:
        commit = repo.get_commit(lint_info['sha'])
        if lint_info['status'] == 'good':
            commit.create_status("success", description="All recipes are excellent.",
                                 context="conda-forge-linter", target_url=target_url)
        else:
            commit.create_status("failure", description="Some recipes need some changes.",
                                 context="conda-forge-linter", target_url=target_url)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('repo')
    parser.add_argument('pr', type=int)
    parser.add_argument('--enable-commenting', help='Turn on PR commenting',
                        action='store_true')

    args = parser.parse_args()
    owner, repo_name = args.repo.split('/')

    lint_info = compute_lint_message(owner, repo_name, args.pr)

    if not lint_info:
        print('Linting was skipped.')
    elif args.enable_commenting:
        msg = comment_on_pr(owner, repo_name, args.pr, lint_info['message'])
        set_pr_status(owner, repo_name, lint_info, target_url=msg.html_url)
    else:
        print('Comments not published, but the following would have been the message:\n{}'.format(lint_info['message']))


if __name__ == '__main__':
    main()
