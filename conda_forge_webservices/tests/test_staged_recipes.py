import unittest.mock as mock

from conda_forge_webservices.staged_recipes import (
    label_pr,
    REVIEW_REQ_LABEL,
    AWAITING_AUTH_LABEL,
)


@mock.patch("conda_forge_webservices.staged_recipes.get_gh_client")
def test_staged_recipes_label_pr_comment_help_python(
    gh,
):
    repo_full_name = "conda-forge/staged-recipes"
    pr_id = 10
    action = "blahblah"
    curr_label_names = ["blah"]
    comment = "@conda-forge/help-python"
    label = None

    label_pr(
        repo_full_name,
        pr_id,
        action,
        curr_label_names,
        comment=comment,
        label=label,
    )

    gh.assert_called()
    gh.return_value.get_repo.assert_called_once_with(repo_full_name)
    repo = gh.return_value.get_repo.return_value
    pr = repo.get_pull.return_value
    repo.get_pull.assert_called_once_with(pr_id)
    pr.add_to_labels.assert_any_call("python")
    pr.add_to_labels.assert_any_call(REVIEW_REQ_LABEL)
    pr.as_issue.assert_not_called()
    pr.remove_from_labels.assert_not_called()


@mock.patch("conda_forge_webservices.staged_recipes.get_gh_client")
def test_staged_recipes_label_pr_comment_staged_recipes(
    gh,
):
    repo_full_name = "conda-forge/staged-recipes"
    pr_id = 10
    action = "blahblah"
    curr_label_names = ["blah"]
    comment = "@conda-forge/staged-recipes"
    label = None

    label_pr(
        repo_full_name,
        pr_id,
        action,
        curr_label_names,
        comment=comment,
        label=label,
    )

    gh.assert_called()
    gh.return_value.get_repo.assert_called_once_with(repo_full_name)
    repo = gh.return_value.get_repo.return_value
    pr = repo.get_pull.return_value
    repo.get_pull.assert_called_once_with(pr_id)
    pr.add_to_labels.assert_any_call("staged-recipes")
    pr.add_to_labels.assert_any_call(REVIEW_REQ_LABEL)
    pr.as_issue.assert_called_once()
    pr.as_issue.return_value.create_comment.assert_called_once()
    pr.remove_from_labels.assert_not_called()


@mock.patch("conda_forge_webservices.staged_recipes.get_gh_client")
def test_staged_recipes_label_pr_review_req_label(
    gh,
):
    repo_full_name = "conda-forge/staged-recipes"
    pr_id = 10
    action = "labeled"
    curr_label_names = ["blah", REVIEW_REQ_LABEL]
    comment = None
    label = AWAITING_AUTH_LABEL

    label_pr(
        repo_full_name,
        pr_id,
        action,
        curr_label_names,
        comment=comment,
        label=label,
    )

    gh.assert_called()
    gh.return_value.get_repo.assert_called_once_with(repo_full_name)
    repo = gh.return_value.get_repo.return_value
    pr = repo.get_pull.return_value
    repo.get_pull.assert_called_once_with(pr_id)
    pr.remove_from_labels.assert_called_once_with(REVIEW_REQ_LABEL)
    pr.as_issue.assert_not_called()
    pr.add_to_labels.assert_not_called()


@mock.patch("conda_forge_webservices.staged_recipes.get_gh_client")
def test_staged_recipes_label_pr_awaiting_auth_label(
    gh,
):
    repo_full_name = "conda-forge/staged-recipes"
    pr_id = 10
    action = "unlabeled"
    curr_label_names = ["blah"]
    comment = None
    label = REVIEW_REQ_LABEL

    label_pr(
        repo_full_name,
        pr_id,
        action,
        curr_label_names,
        comment=comment,
        label=label,
    )

    gh.assert_called()
    gh.return_value.get_repo.assert_called_once_with(repo_full_name)
    repo = gh.return_value.get_repo.return_value
    pr = repo.get_pull.return_value
    repo.get_pull.assert_called_once_with(pr_id)
    pr.add_to_labels.assert_called_once_with(AWAITING_AUTH_LABEL)
    pr.as_issue.assert_not_called()
    pr.remove_from_labels.assert_not_called()
