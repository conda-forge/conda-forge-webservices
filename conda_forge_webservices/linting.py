from contextlib import contextmanager
from glob import glob
import os
import shutil
import tempfile
import textwrap

import requests
from git import Repo
import github
import conda_smithy.lint_recipe


@contextmanager
def tmp_directory():
    tmp_dir = tempfile.mkdtemp('recipe_')
    yield tmp_dir
    shutil.rmtree(tmp_dir)


def compute_lint_message(repo_owner, repo_name, pr_id):
    gh = github.Github(os.environ['GH_TOKEN'])

    owner = gh.get_user(repo_owner)
    repo = owner.get_repo(repo_name)

    issue = repo.get_issue(pr_id)

    with tmp_directory() as tmp_dir:
        repo = Repo.clone_from(repo.clone_url, tmp_dir)
        repo.remotes.origin.fetch('pull/{pr}/head:pr/{pr}'.format(pr=pr_id))
        repo.refs['pr/{}'.format(pr_id)].checkout()
        sha = str(repo.head.object.hexsha)
        recipes = [y for x in os.walk(tmp_dir)
                   for y in glob(os.path.join(x[0], 'meta.yaml'))]
        all_pass = True
        messages = []
        recipe_dirs = [os.path.dirname(recipe) for recipe in recipes
                       if os.path.basename(os.path.dirname(recipe)) != 'example']

        # Sort the recipes for consistent linting order (which glob doesn't give us).
        recipe_dirs = sorted(recipe_dirs)

        rel_recipe_dirs = []
        for recipe_dir in recipe_dirs:
            rel_path = os.path.relpath(recipe_dir, tmp_dir)
            rel_recipe_dirs.append(rel_path)
            lints = conda_smithy.lint_recipe.main(recipe_dir)
            if lints:
                all_pass = False
                messages.append("\nFor **{}**:\n\n{}".format(rel_path,
                                                             '\n'.join(' * {}'.format(lint) for lint in lints)))

    # Put the recipes in the form "```recipe/a```, ```recipe/b```".
    recipe_code_blocks = ', '.join('```{}```'.format(r) for r in rel_recipe_dirs)

    good = textwrap.dedent("""
    Hi! This is the friendly conda-forge-admin automated user.

    I just wanted to let you know that I linted all conda-recipes in your PR ({}) and found it was in an excellent condition.

    """.format(recipe_code_blocks))

    bad = textwrap.dedent("""
    Hi! This is the friendly conda-forge-admin automated user.

    I wanted to let you know that I linted all conda-recipes in your PR ({}) and found some lint.

    Here's what I've got...

    {{}}
    """.format(recipe_code_blocks)).format('\n'.join(messages))

    if not recipe_dirs:
        message = textwrap.dedent("""
            Hi! This is the friendly conda-forge-admin automated user.
            
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

    lint_info = {'message': message,
                 'status': status,
                 'sha': sha}

    return lint_info


def comment_on_pr(owner, repo_name, pr_id, message):
    gh = github.Github(os.environ['GH_TOKEN'])

    user = gh.get_user(owner)
    repo = user.get_repo(repo_name)
    issue = repo.get_issue(pr_id)
    # TODO: Only message if the lint was different.

    comments = list(issue.get_comments())

    comment_owners = [comment.user.login for comment in comments]

    my_login = gh.get_user().login

    my_last_comment = None
    if my_login in comment_owners:
        my_last_comment = [comment for comment in comments
                           if comment.user.login == my_login][-1]

    # Only comment if we haven't before, or if the message we have is different.
    if my_last_comment is None or my_last_comment.body != message:
        my_last_comment = issue.create_comment(message)

    return my_last_comment


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

    if args.enable_commenting:
        comment_on_pr(owner, repo_name, args.pr, lint_info['message'])

        gh = github.Github(os.environ['GH_TOKEN'])

        user = gh.get_user(owner)
        repo = user.get_repo(repo_name)
        commit = repo.get_commit(lint_info['sha'])
        if lint_info['status'] == 'good':
            commit.create_status("success", description="All recipes are excellent.",
                                 context="conda-linter-bot")
        else:
            commit.create_status("failure", description="Some recipes need some changes.",
                                 context="conda-linter-bot")

    else:
        print('Comments not published, but the following would have been the message:\n{}'.format(lint_info['message']))


if __name__ == '__main__':
    main()
