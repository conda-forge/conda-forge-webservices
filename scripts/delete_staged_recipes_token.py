import os

import github
import requests

from conda_forge_webservices.utils import with_action_url


def feedstock_token_exists(organization, name):
    r = requests.get(
        f"https://api.github.com/repos/{organization}/"
        f"feedstock-tokens/contents/tokens/{name}.json",
        headers={"Authorization": "token {}".format(os.environ["GH_TOKEN"])},
    )
    if r.status_code != 200:
        return False
    else:
        return True


if __name__ == "__main__":
    feedstock_name = "staged-recipes"

    if feedstock_token_exists("conda-forge", feedstock_name):
        gh = github.Github(auth=github.Auth.Token(os.environ["GH_TOKEN"]))
        repo = gh.get_repo("conda-forge/feedstock-tokens")
        file = repo.get_contents(f"tokens/{feedstock_name}.json")
        repo.delete_file(
            path=file.path,
            message=with_action_url(
                "[ci skip] [skip ci] [cf admin skip] ***NO_CI*** "
                f"removing token for {feedstock_name}"
            ),
            sha=file.sha,
        )
