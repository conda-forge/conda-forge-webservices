import re

from conda_forge_webservices.tokens import (
    get_gh_client,
)

AWAITING_REV_LABEL = "Awaiting author contribution"
REVIEW_REQ_LABEL = "review-requested"
ALL_TEAMS = [
    "@conda-forge/staged-recipes",
    "@conda-forge/help-c-cpp",
    "@conda-forge/help-cdts",
    "@conda-forge/help-go",
    "@conda-forge/help-java",
    "@conda-forge/help-julia",
    "@conda-forge/help-nodejs",
    "@conda-forge/help-perl",
    "@conda-forge/help-python",
    "@conda-forge/help-python-c",
    "@conda-forge/help-r",
    "@conda-forge/help-ruby",
    "@conda-forge/help-rust",
]
ALL_REGEXES = [re.compile(team + "[^\\w-]|" + team + "$") for team in ALL_TEAMS]


def label_pr(
    repo_full_name: str,
    pr_id: int,
    action: str,
    curr_label_names: set,
    comment: str | None = None,
    label: str | None = None,
):
    gh = get_gh_client()
    repo = gh.get_repo(repo_full_name)
    pr = repo.get_pull(pr_id)

    if comment is not None:
        # check all help- teams + staged-recipes in comment
        # if we find one of them, then add review-requested label
        # if we find only staged-recipes, then comment on issue
        # asking for more specific team

        found_non_sr_team = False
        found_sr_team = False
        for team, team_regex in zip(ALL_TEAMS, ALL_REGEXES):
            label = team.replace("@conda-forge/", "").replace("help-", "")
            if team_regex.search(comment):
                if label not in curr_label_names:
                    pr.add_to_labels(label)

                if label == "staged-recipes":
                    found_sr_team = True
                else:
                    found_non_sr_team = True

        found_team = found_non_sr_team or found_sr_team

        if found_sr_team and not found_non_sr_team:
            pr.as_issue().create_comment(
                "To help direct your pull request to the best reviewers, "
                + "please mention a topic-specifc team if your "
                + "recipe matches any of the following: {"
                + ", ".join(team_.replace("@", "") for team_ in ALL_TEAMS)
                + "}. Thanks!"
            )

        # remove awaiting author contribution label if it is there
        if AWAITING_REV_LABEL in curr_label_names and found_team:
            pr.remove_from_labels(AWAITING_REV_LABEL)

    if (
        label == REVIEW_REQ_LABEL
        and action == "unlabeled"
        and AWAITING_REV_LABEL not in curr_label_names
    ):
        pr.add_to_labels(AWAITING_REV_LABEL)

    if (
        label == AWAITING_REV_LABEL
        and action == "labeled"
        and REVIEW_REQ_LABEL in curr_label_names
    ):
        pr.remove_from_labels(REVIEW_REQ_LABEL)
