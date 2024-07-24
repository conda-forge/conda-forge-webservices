import os
import sys
from datetime import timezone, datetime, timedelta

from dateutil.parser import parse
import requests
import urllib
from conda_forge_webservices.utils import parse_conda_pkg

if __name__ == "__main__":
    header = {"Authorization": "token {}".format(os.environ["STAGING_BINSTAR_TOKEN"])}
    rc = requests.get("https://api.anaconda.org/channels/cf-staging", headers=header)

    now = datetime.utcnow()
    now = now.replace(tzinfo=timezone.utc)

    num_del = 0
    for channel in rc.json():
        r = requests.get(
            f"https://api.anaconda.org/channels/cf-staging/{channel}",
            headers=header,
        )

        for f in r.json()["files"]:
            updt = parse(f["upload_time"])
            dt = now - updt
            if dt > timedelta(hours=2):
                print("deleting:", f["basename"], dt)
                _, name, version, _ = parse_conda_pkg(f["basename"])
                r = requests.delete(
                    "https://api.anaconda.org/dist/cf-staging/{}/{}/{}".format(
                        name, version, urllib.parse.quote(f["basename"], safe="")
                    ),
                    headers=header,
                )
                num_del += 1
                if num_del > 10000:
                    sys.exit(0)
