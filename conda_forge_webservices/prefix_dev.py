"""
Copy packages that were copied to conda-forge into a channel on prefix.dev.

Enabled by setting `PREFIX_DEV_TOKEN` and `PREFIX_DEV_CHANNEL` (as
`namespace/name`). prefix.dev downloads the files from `conda-web.anaconda.org`,
which is not behind a CDN, and checks them against the SHA-256 we send.
Errors are logged and never raised.
"""

import logging
import os

import requests

LOGGER = logging.getLogger("conda_forge_webservices.prefix_dev")

PREFIX_DEV_URL = "https://prefix.dev/api/graphql"
DOWNLOAD_URL = "https://conda-web.anaconda.org/conda-forge"

COPY_MUTATION = """
mutation CopyPackagesFromUrls(
  $channelName: String!
  $packages: [CopyPackageUrlInput!]!
) {
  copyPackagesFromUrls(channelName: $channelName, packages: $packages) {
    id
  }
}
"""


def copy_to_prefix_dev(outputs):
    """Ask prefix.dev to copy packages from conda-forge.

    `outputs` maps each dist (`<subdir>/<filename>`) to its SHA-256.
    """
    token = os.environ.get("PREFIX_DEV_TOKEN")
    channel = os.environ.get("PREFIX_DEV_CHANNEL")
    if not (token and channel and outputs):
        return

    packages = [
        {"url": f"{DOWNLOAD_URL}/{dist}", "sha256": sha256}
        for dist, sha256 in sorted(outputs.items())
    ]
    try:
        resp = requests.post(
            PREFIX_DEV_URL,
            json={
                "query": COPY_MUTATION,
                "variables": {"channelName": channel, "packages": packages},
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=(5, 20),
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("errors"):
            raise RuntimeError(data["errors"])
        LOGGER.info(
            "    prefix.dev copy job scheduled: %s",
            data["data"]["copyPackagesFromUrls"]["id"],
        )
    except Exception as e:
        LOGGER.warning(
            "    prefix.dev copy failed for %s", ", ".join(sorted(outputs)), exc_info=e
        )
