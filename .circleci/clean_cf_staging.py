import os
import sys
from datetime import timezone, datetime, timedelta

from dateutil.parser import parse
import requests
import urllib


def parse_conda_pkg(pkg):
    """Parse a conda package into its parts.
    code due to Isuru F. and CJ Wright
    Returns platform, name, version and build string
    """
    if not pkg.endswith(".tar.bz2"):
        raise RuntimeError("Package must end with .tar.bz2!")
    pkg = pkg[:-8]
    plat, pkg_name = pkg.split(os.path.sep)
    name_ver, build = pkg_name.rsplit('-', 1)
    name, ver = name_ver.rsplit('-', 1)
    return plat, name, ver, build


if __name__ == "__main__":
    header = {'Authorization': 'token {}'.format(os.environ["STAGING_BINSTAR_TOKEN"])}
    rc = requests.get(
        "https://api.anaconda.org/channels/cf-staging",
        headers=header
    )

    now = datetime.utcnow()
    now = now.replace(tzinfo=timezone.utc)

    num_del = 0
    for channel in rc.json():
        r = requests.get(
            "https://api.anaconda.org/channels/cf-staging/main",
            headers=header,
        )

        for f in r.json()['files']:
            updt = parse(f["upload_time"])
            dt = now - updt
            if dt > timedelta(days=5):
                print("deleting:", f['basename'], dt)
                _, name, version, _ = parse_conda_pkg(f["basename"])
                r = requests.delete(
                    "https://api.anaconda.org/dist/cf-staging/%s/%s/%s" % (
                        name,
                        version,
                        urllib.parse.quote(f["basename"], safe="")
                    ),
                    headers=header,
                )
                num_del += 1
                if num_del > 10:
                    sys.exit(0)
