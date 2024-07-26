import tempfile
import subprocess
import os

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
        with tempfile.TemporaryDirectory() as tmpdir:
            subprocess.check_call(
                "git clone https://x-access-token:${GH_TOKEN}@github.com/conda-forge/"
                "feedstock-tokens.git",
                cwd=tmpdir,
                shell=True,
            )

            subprocess.check_call(
                "git remote set-url --push origin "
                "https://x-access-token:${GH_TOKEN}@github.com/conda-forge/"
                "feedstock-tokens.git",
                cwd=os.path.join(tmpdir, "feedstock-tokens"),
                shell=True,
            )

            subprocess.check_call(
                f"git rm tokens/{feedstock_name}.json",
                cwd=os.path.join(tmpdir, "feedstock-tokens"),
                shell=True,
            )

            msg = with_action_url(
                "[ci skip] [skip ci] [cf admin skip] ***NO_CI*** "
                f"removing token for {feedstock_name}"
            )
            subprocess.check_call(
                f"git commit --allow-empty -am '{msg}'",
                cwd=os.path.join(tmpdir, "feedstock-tokens"),
                shell=True,
            )

            subprocess.check_call(
                "git pull",
                cwd=os.path.join(tmpdir, "feedstock-tokens"),
                shell=True,
            )

            subprocess.check_call(
                "git push",
                cwd=os.path.join(tmpdir, "feedstock-tokens"),
                shell=True,
            )
