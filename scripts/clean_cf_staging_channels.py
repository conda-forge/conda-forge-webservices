import os
import sys
from datetime import timezone, datetime, timedelta

from dateutil.parser import parse
import requests
import urllib
from conda_forge_webservices.utils import parse_conda_pkg


def _clean_channel(base_channel, token_name):
    header = {"Authorization": f"token {os.environ[token_name]}"}
    rc = requests.get(
        f"https://api.anaconda.org/channels/{base_channel}", headers=header
    )

    now = datetime.utcnow()
    now = now.replace(tzinfo=timezone.utc)

    num_del = 0
    for channel in rc.json():
        r = requests.get(
            f"https://api.anaconda.org/channels/{base_channel}/{channel}",
            headers=header,
        )

        for f in r.json()["files"]:
            updt = parse(f["upload_time"])
            dt = now - updt
            if dt > timedelta(hours=2):
                print(
                    "deleting: {}/{}".format(base_channel, f["basename"]),
                    dt,
                    flush=True,
                )
                _, name, version, _ = parse_conda_pkg(f["basename"])
                r = requests.delete(
                    "https://api.anaconda.org/dist/{}/{}/{}/{}".format(
                        base_channel,
                        name,
                        version,
                        urllib.parse.quote(f["basename"], safe=""),
                    ),
                    headers=header,
                )
                num_del += 1
                if num_del > 10000:
                    sys.exit(0)


if __name__ == "__main__":
    for base_channel, token_name in [
        ("cf-staging", "STAGING_BINSTAR_TOKEN"),
        ("cf-post-staging", "POST_STAGING_BINSTAR_TOKEN"),
    ]:
        print("clean channel", base_channel, flush=True)
        _clean_channel(base_channel, token_name)
