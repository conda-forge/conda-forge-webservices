import tempfile
import subprocess
import os

import requests


def feedstock_token_exists(organization, name):
    r = requests.get(
        "https://api.github.com/repos/%s/"
        "feedstock-tokens/contents/tokens/%s.json" % (organization, name),
        headers={"Authorization": "token %s" % os.environ["GITHUB_TOKEN"]},
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
                "git rm tokens/%s.json" % feedstock_name,
                cwd=os.path.join(tmpdir, "feedstock-tokens"),
                shell=True,
            )

            subprocess.check_call(
                "git commit --allow-empty -am "
                "'[ci skip] [skip ci] [cf admin skip] ***NO_CI*** removing "
                "token for %s'" % feedstock_name,
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
