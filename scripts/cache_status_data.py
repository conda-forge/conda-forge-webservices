#!/usr/bin/env python
import os
import json
import tempfile
import subprocess
import datetime

import requests


# first pull down the data
latest_data = requests.get(
    "https://services.conda-forge.org/status-monitor/db"
).json()

# now update the repo
with tempfile.TemporaryDirectory() as tmpdir:
    os.chdir(tmpdir)
    subprocess.run(
        ("git clone --depth=1 "
         "https://github.com/conda-forge/conda-forge-status-monitor.git"),
        shell=True,
        check=True,
    )

    os.chdir("conda-forge-status-monitor")

    subprocess.run(
        "git remote set-url --push origin "
        "https://${GH_TOKEN}@github.com/conda-forge/conda-forge-status-monitor.git",
        shell=True,
        check=True,
    )

    os.makedirs("data", exist_ok=True)

    with open("data/latest.json", "w") as fp:
        json.dump(latest_data, fp, indent=2)

    subprocess.run(
        ["git add data/latest.json"],
        shell=True,
        check=True,
    )

    stat = subprocess.run(
        ["git status"],
        shell=True,
        check=True,
        capture_output=True,
    )
    status = stat.stdout.decode('utf-8')
    print(status)

    if "nothing to commit" not in status:
        subprocess.run(
            "git commit -m '[ci skip] [skip ci] [cf admin skip] ***NO_CI*** "
            "status data update %s'" % datetime.datetime.utcnow().isoformat(),
            shell=True,
            check=True,
        )

        subprocess.run(
            ["git push"],
            shell=True,
            check=True,
        )
