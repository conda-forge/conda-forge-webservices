"""
Mirror packages copied to conda-forge into a channel on prefix.dev.

After a package has been validated and copied from `cf-staging` to `conda-forge`,
we ask prefix.dev to copy the same file into a (shadow) channel. prefix.dev
downloads the file from `cf-staging`, which serves it immediately, unlike the
`conda-forge` channel on anaconda.org, which is behind a CDN that can lag for
hours. The SHA-256 that we already validated is sent along, and prefix.dev
refuses any file that does not match it.

The feature is enabled by setting `PREFIX_DEV_TOKEN` and `PREFIX_DEV_CHANNEL`.
Failures here are logged but never fail the copy to conda-forge.
"""

import logging
import os

import requests

LOGGER = logging.getLogger("conda_forge_webservices.prefix_dev")

DEFAULT_PREFIX_DEV_URL = "https://prefix.dev"
STAGING_DOWNLOAD_URL = "https://conda.anaconda.org/cf-staging"

# prefix.dev rejects larger requests
MAX_PACKAGES_PER_REQUEST = 500

COPY_MUTATION = """
mutation CopyPackagesFromUrls(
  $channelName: String!
  $packages: [CopyPackageUrlInput!]!
) {
  copyPackagesFromUrls(channelName: $channelName, packages: $packages) {
    id
    status
  }
}
"""


def prefix_dev_channel():
    """The prefix.dev channel to copy packages to, or None if disabled."""
    if not os.environ.get("PREFIX_DEV_TOKEN"):
        return None
    return os.environ.get("PREFIX_DEV_CHANNEL") or None


def is_prefix_dev_copy_enabled(label, hash_type):
    """Whether packages copied to `label` should also be copied to prefix.dev.

    Only the `main` label is mirrored, since prefix.dev channels have no labels.
    prefix.dev pins packages by SHA-256, so other hash types are skipped.
    """
    return (
        prefix_dev_channel() is not None and label == "main" and hash_type == "sha256"
    )


def _staging_url(dist):
    # `dist` is `<subdir>/<filename>`. Package files on cf-staging are served
    # directly without a redirect, which prefix.dev requires.
    return f"{STAGING_DOWNLOAD_URL}/{dist}"


def request_prefix_dev_copy(outputs):
    """Ask prefix.dev to copy the given packages from cf-staging.

    Parameters
    ----------
    outputs : dict
        Maps each dist (`<subdir>/<filename>`) to its SHA-256 hex digest.

    Returns
    -------
    job_ids : list of str
        The ids of the prefix.dev background jobs that were scheduled.
    errors : list of str
        Errors that occurred while scheduling the copy. These are not fatal.
    """
    channel = prefix_dev_channel()
    if channel is None or not outputs:
        return [], []

    base_url = os.environ.get("PREFIX_DEV_URL", DEFAULT_PREFIX_DEV_URL).rstrip("/")
    headers = {"Authorization": f"Bearer {os.environ['PREFIX_DEV_TOKEN']}"}

    items = sorted(outputs.items())
    job_ids = []
    errors = []
    for start in range(0, len(items), MAX_PACKAGES_PER_REQUEST):
        batch = items[start : start + MAX_PACKAGES_PER_REQUEST]
        packages = [
            {"url": _staging_url(dist), "sha256": sha256} for dist, sha256 in batch
        ]
        try:
            resp = requests.post(
                f"{base_url}/api/graphql",
                json={
                    "query": COPY_MUTATION,
                    "variables": {"channelName": channel, "packages": packages},
                },
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("errors"):
                raise RuntimeError(
                    "; ".join(e.get("message", str(e)) for e in data["errors"])
                )
            job_ids.append(data["data"]["copyPackagesFromUrls"]["id"])
        except Exception as e:
            dists = ", ".join(dist for dist, _ in batch)
            LOGGER.warning(
                "    prefix.dev copy request failed for %s", dists, exc_info=e
            )
            errors.append(f"prefix.dev copy request failed for {dists}: {e!r}")

    for job_id in job_ids:
        LOGGER.info("    prefix.dev copy job scheduled: %s", job_id)

    return job_ids, errors
