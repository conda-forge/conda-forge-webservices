"""
This module registers and validates feedstock outputs.
"""

import os
import json
import hmac
import urllib.parse
import logging
import base64
import time

import requests
import scrypt
import github

from binstar_client.utils import get_server_api
from binstar_client import BinstarError
from conda_forge_metadata.feedstock_outputs import (
    package_to_feedstock,
    feedstock_outputs_config,
)
from conda_forge_metadata.feedstock_outputs import sharded_path as _get_sharded_path
import binstar_client.errors

from .utils import parse_conda_pkg, _test_and_raise_besides_file_not_exists
from conda_forge_webservices.tokens import (
    get_app_token_for_webservices_only,
    get_gh_client,
)

LOGGER = logging.getLogger("conda_forge_webservices.feedstock_outputs")

STAGING = "cf-staging"
PROD = "conda-forge"


def is_valid_feedstock_token(user, project, feedstock_token, provider=None):
    gh_token = get_app_token_for_webservices_only()
    r = requests.get(
        f"https://api.github.com/repos/{user}/"
        f"feedstock-tokens/contents/tokens/{project}.json",
        headers={"Authorization": f"Bearer {gh_token}"},
    )
    if r.status_code == 200:
        data = r.json()
        assert data["encoding"] == "base64"
        token_data = json.loads(
            base64.standard_b64decode(data["content"]).decode("utf-8")
        )
        if "tokens" not in token_data:
            token_data = {"tokens": [token_data]}

        now = time.time()
        for td in token_data["tokens"]:
            td_provider = td.get("provider", None)
            td_expires_at = td.get("expires_at", None)
            if ((td_provider is None) or (td_provider == provider)) and (
                (td_expires_at is None) or (td_expires_at > now)
            ):
                salted_token = scrypt.hash(
                    feedstock_token,
                    bytes.fromhex(td["salt"]),
                    buflen=256,
                )

                if hmac.compare_digest(
                    salted_token,
                    bytes.fromhex(td["hashed_token"]),
                ):
                    return True

    return False


def _get_ac_api_prod():
    """wrap this a function so we can more easily mock it when testing"""
    return get_server_api(token=os.environ["PROD_BINSTAR_TOKEN"])


def _get_ac_api_staging():
    """wrap this a function so we can more easily mock it when testing"""
    return get_server_api(token=os.environ["STAGING_BINSTAR_TOKEN"])


def _dist_exists(ac, channel, dist):
    try:
        _, name, version, _ = parse_conda_pkg(dist)
    except RuntimeError:
        return False

    try:
        ac.distribution(
            channel,
            name,
            version,
            basename=urllib.parse.quote(dist, safe=""),
        )
        return True
    except binstar_client.errors.NotFound:
        return False


def _delete_dist(ac, channel, dist):
    try:
        _, name, version, _ = parse_conda_pkg(dist)
    except RuntimeError:
        return False

    try:
        ac.remove_dist(
            channel,
            name,
            version,
            basename=urllib.parse.quote(dist, safe=""),
        )
    except BinstarError:
        return False

    return True


def _add_label_dist(ac, channel, dist, label):
    try:
        _, name, version, _ = parse_conda_pkg(dist)
    except RuntimeError:
        return False

    try:
        ac.add_channel(
            label,
            channel,
            package=name,
            version=version,
            filename=urllib.parse.quote(dist, safe=""),
        )
    except BinstarError:
        return False

    return True


def _remove_label_dist(ac, channel, dist, label):
    try:
        _, name, version, _ = parse_conda_pkg(dist)
    except RuntimeError:
        return False

    try:
        ac.remove_channel(
            label,
            channel,
            package=name,
            version=version,
            filename=urllib.parse.quote(dist, safe=""),
        )
    except BinstarError:
        return False

    return True


def _copy_dist_if_not_exists(
    ac_src,
    channel_src,
    label_src,
    dist,
    ac_dest,
    channel_dest,
    label_dest,
    update_metadata=False,
    replace_metadata=False,
):
    try:
        _, name, version, _ = parse_conda_pkg(dist)
    except RuntimeError:
        return False

    if _dist_exists(ac_dest, channel_dest, dist):
        return True
    else:
        try:
            ac_dest.copy(
                channel_src,
                name,
                version,
                basename=urllib.parse.quote(dist, safe=""),
                to_owner=channel_dest,
                from_label=label_src,
                to_label=label_dest,
                update=update_metadata,
                replace=replace_metadata,
            )
        except BinstarError:
            return False

    return True


def _is_dist_hash_valid(ac, channel, dist, hash_type, hash_value):
    try:
        _, name, version, _ = parse_conda_pkg(dist)
    except RuntimeError:
        return False

    try:
        data = ac.distribution(
            channel,
            name,
            version,
            basename=urllib.parse.quote(dist, safe=""),
        )
        return hmac.compare_digest(data[hash_type], hash_value)
    except BinstarError:
        return False


def copy_feedstock_outputs(outputs, channel, delete=True):
    """Copy outputs from one chanel to another.

    Parameters
    ----------
    outputs : list of str
        A list of outputs to copy. These should be the full names with the
        platform directory, version/build info, and file extension (e.g.,
        `noarch/blah-fa31b0-2020.04.13.15.54.07-py_0.conda`).
    channel : str
        The source and target channel to use. Pass "main" for the default
        channel.
    delete : bool, optional
        If True, delete the artifact from STAGING if the copy is successful.
        Default is True.

    Returns
    -------
    copied : dict
        A dict keyed on the output name with True if the copy worked and False
        otherwise.
    """
    ac_prod = _get_ac_api_prod()
    ac_staging = _get_ac_api_staging()

    copied = dict.fromkeys(outputs, False)

    for dist in outputs:
        try:
            _, name, version, _ = parse_conda_pkg(dist)
        except RuntimeError:
            continue

        # if we already have it, then we mark it copied
        # this matches the old behavior where outputs are never
        # replaced once pushed
        if _dist_exists(ac_prod, PROD, dist):
            copied[dist] = True
        else:
            try:
                ac_prod.copy(
                    STAGING,
                    name,
                    version,
                    basename=urllib.parse.quote(dist, safe=""),
                    to_owner=PROD,
                    from_label=channel,
                    to_label=channel,
                    update=True,
                )
                copied[dist] = True
                LOGGER.info("    copied: %s", dist)
            except BinstarError as e:
                LOGGER.info("    did not copy: %s (%s)", dist, repr(e))
                pass

        if copied[dist] and _dist_exists(ac_staging, STAGING, dist) and delete:
            try:
                ac_staging.remove_dist(
                    STAGING,
                    name,
                    version,
                    basename=urllib.parse.quote(dist, safe=""),
                )
                LOGGER.info("    removed: %s", dist)
            except BinstarError:
                LOGGER.info("    could not remove: %s", dist)
                pass
    return copied


def relabel_feedstock_outputs(outputs, src_label, dest_label, remove_src_label=True):
    """Relabel outputs on a conda channel.

    Parameters
    ----------
    outputs : list of str
        A list of outputs to relabel. These should be the full names with the
        platform directory, version/build info, and file extension (e.g.,
        `noarch/blah-fa31b0-2020.04.13.15.54.07-py_0.conda`).
    src_label : str
        The source label for the packages on the channel.
    dest_label : str
        the destination label for the packages on the channel.
    remove_src_label : bool, optional
        If True, remove the source label from the artifacts. Default is True.

    Returns
    -------
    relabeled : dict
        A dict keyed on the output name with True if the relabel worked and False
        otherwise.
    """
    ac_prod = _get_ac_api_prod()

    relabeled = dict.fromkeys(outputs, False)

    for dist in outputs:
        if _add_label_dist(ac_prod, PROD, dist, dest_label):
            relabeled[dist] = True
            LOGGER.info("    relabeled: %s", dist)
        else:
            LOGGER.info("    did not relabel: %s", dist)

        if remove_src_label and relabeled[dist]:
            if _remove_label_dist(ac_prod, PROD, dist, src_label):
                LOGGER.info("    removed label: %s", dist)
            else:
                LOGGER.info("    did not remove label: %s", dist)

    return relabeled


def _is_valid_output_hash(outputs, hash_type, channel, staging_label):
    """Test if a set of outputs have valid hashes on the staging channel.

    Parameters
    ----------
    outputs : dict
        A dictionary mapping each output to its md5 hash. The keys should be the
        full names with the platform directory, version/build info, and file extension
        (e.g., `noarch/blah-fa31b0-2020.04.13.15.54.07-py_0.conda`).
    hash_type : str
        The hash key to look for. One of sha256 or md5.
    channel : str
        The source label for the packages on the staging channel.
    staging_label : str
        The label to use for staging the dists to the prod channel.

    Returns
    -------
    valid : dict
        A dict keyed on full output names with True if it is valid and False
        otherwise.
    """
    ac_prod = _get_ac_api_prod()
    ac_staging = _get_ac_api_staging()

    valid = dict.fromkeys(outputs, False)

    try:
        for dist, hashsum in outputs.items():
            try:
                if _is_dist_hash_valid(
                    ac_staging, STAGING, dist, hash_type, hashsum
                ) and _copy_dist_if_not_exists(
                    ac_staging,
                    STAGING,
                    channel,
                    dist,
                    ac_prod,
                    PROD,
                    staging_label,
                    update_metadata=False,
                    replace_metadata=False,
                ):
                    valid[dist] = _is_dist_hash_valid(
                        ac_prod,
                        PROD,
                        dist,
                        hash_type,
                        hashsum,
                    )
                    LOGGER.info("    did hash comp: %s", dist)
                else:
                    LOGGER.info(
                        "    did not do hash comp due to failed staging copy: %s",
                        dist,
                    )
            except BinstarError:
                LOGGER.info("    did not do hash comp: %s", dist)
                pass
    finally:
        for dist, v in valid.items():
            if not v and _dist_exists(ac_prod, PROD, dist):
                if _delete_dist(ac_prod, PROD, dist):
                    LOGGER.info("    invalid dist hash - deleted from prod: %s", dist)
                else:
                    LOGGER.info(
                        "    invalid dist hash - could not delete from prod: %s",
                        dist,
                    )

    return valid


def _add_feedstock_output(
    feedstock: str,
    pkg_name: str,
):
    gh = get_gh_client()
    repo = gh.get_repo("conda-forge/feedstock-outputs")
    try:
        contents = repo.get_contents(_get_sharded_path(pkg_name))
    except github.GithubException as e:
        _test_and_raise_besides_file_not_exists(e)
        contents = None

    if contents is None:
        data = {"feedstocks": [feedstock]}
        repo.create_file(
            _get_sharded_path(pkg_name),
            f"[cf admin skip] ***NO_CI*** add output {pkg_name} for "
            f"conda-forge/{feedstock}-feedstock",
            json.dumps(data),
        )
        LOGGER.info(
            f"    output {pkg_name} added for feedstock "
            f"conda-forge/{feedstock}-feedstock"
        )
    else:
        data = json.loads(contents.decoded_content.decode("utf-8"))
        if feedstock not in data["feedstocks"]:
            data["feedstocks"].append(feedstock)
            repo.update_file(
                contents.path,
                f"[cf admin skip] ***NO_CI*** add output {pkg_name} "
                f"for conda-forge/{feedstock}-feedstock",
                json.dumps(data),
                contents.sha,
            )
            LOGGER.info(
                f"    output {pkg_name} added for feedstock "
                f"conda-forge/{feedstock}-feedstock"
            )
        else:
            LOGGER.info(
                f"    output {pkg_name} already exists for feedstock "
                f"conda-forge/{feedstock}-feedstock"
            )


def _is_valid_feedstock_output(
    project,
    outputs,
    register=False,
):
    """Test if feedstock outputs are valid (i.e., the outputs are allowed for that
    feedstock). Optionally register them if they do not exist.

    Parameters
    ----------
    project : str
        The GitHub repo.
    outputs : list of str
        A list of outputs top validate. The list entries should be the
        full names with the platform directory, version/build info, and file extension
        (e.g., `noarch/blah-fa31b0-2020.04.13.15.54.07-py_0.conda`).
    register : bool, optional
        If True, attempt to register any outputs that do not exist by pushing
        the proper json blob to `output_repo`. Default is False.
        ** DO NOT TURN TO TRUE UNLESS YOU KNOW WHAT YOU ARE DOING. **

    Returns
    -------
    valid : dict
        A dict keyed on output name with True if it is valid and False
        otherwise.
    """
    gh_token = get_app_token_for_webservices_only()

    if project.endswith("-feedstock"):
        feedstock = project[: -len("-feedstock")]
    else:
        feedstock = project

    valid = dict.fromkeys(outputs, False)

    unique_names = set()
    for dist in outputs:
        try:
            _, o, _, _ = parse_conda_pkg(dist)
        except RuntimeError:
            continue
        unique_names.add(o)

    unique_names_valid = dict.fromkeys(unique_names, False)
    for un in unique_names:
        try:
            registered_feedstocks = package_to_feedstock(un)
        except requests.exceptions.HTTPError:
            registered_feedstocks = []

        if registered_feedstocks:
            # if we find any, we check
            unique_names_valid[un] = feedstock in registered_feedstocks
            LOGGER.info(f"    checked|valid: {un}|{unique_names_valid[un]}")
        else:
            # otherwise it is only valid if we are registering on the fly
            unique_names_valid[un] = register
            LOGGER.info(f"    does not exist|valid: {un}|{unique_names_valid[un]}")

        # make the output if we need to
        if unique_names_valid[un]:
            un_sharded_path = _get_sharded_path(un)
            r = requests.get(
                "https://api.github.com/repos/conda-forge/"
                f"feedstock-outputs/contents/{un_sharded_path}",
                headers={"Authorization": f"Bearer {gh_token}"},
            )
            if r.status_code == 404:
                _add_feedstock_output(feedstock, un)

    for dist in outputs:
        try:
            _, o, _, _ = parse_conda_pkg(dist)
        except RuntimeError:
            continue

        valid[dist] = unique_names_valid[o]

    return valid


def validate_feedstock_outputs(
    project,
    outputs,
    hash_type,
    channel,
    staging_label,
):
    """Validate feedstock outputs on the staging channel.

    Parameters
    ----------
    project : str
        The name of the feedstock.
    outputs : dict
        A dictionary mapping each output to its md5 hash. The keys should be the
        full names with the platform directory, version/build info, and file extension
        (e.g., `noarch/blah-fa31b0-2020.04.13.15.54.07-py_0.conda`).
    hash_type : str
        The hash key to look for. One of sha256 or md5.
    channel : str
        The source label for the packages on the staging channel.
    staging_label : str
        The label to use for the staging dists to the prod channel.

    Returns
    -------
    valid : dict
        A dict keyed on the keys in `outputs` with values True in the output
        is valid and False otherwise.
    errors : list of str
        A list of any errors encountered.
    """
    valid = dict.fromkeys(outputs, False)

    errors = []

    correctly_formatted = {}
    for o in outputs:
        try:
            parse_conda_pkg(o)
            correctly_formatted[o] = True
        except RuntimeError:
            correctly_formatted[o] = False
            errors.append(
                f"output '{o}' is not correctly formatted (it must be the fully "
                "qualified name w/ extension, `noarch/blah-fa31b0-2020.04.13.15"
                ".54.07-py_0.conda`)"
            )

    outputs_to_test = {o: v for o, v in outputs.items() if correctly_formatted[o]}

    valid_outputs = _is_valid_feedstock_output(
        project,
        outputs_to_test,
        # to turn this off, set the value in the config.json blob in
        # conda-forge/feedstock-outputs
        register=feedstock_outputs_config().get("auto_register_all", False),
    )

    valid_hashes = _is_valid_output_hash(
        outputs_to_test, hash_type, channel, staging_label
    )

    for o in outputs_to_test:
        p_errors = []
        if not valid_outputs[o]:
            p_errors.append(f"output {o} not allowed for conda-forge/{project}")
        if not valid_hashes[o]:
            p_errors.append(f"output {o} does not have a valid md5 checksum")

        if len(p_errors) > 0:
            errors.extend(p_errors)
        else:
            valid[o] = True

    return valid, errors


def comment_on_outputs_copy(feedstock, git_sha, errors, valid, copied):
    """Make an issue or comment if the feedstock output copy failed.

    Parameters
    ----------
    feedstock : str
        The name of the feedstock.
    git_sha : str
        The git SHA of the commit.
    errors : list of str
        A list of errors, if any.
    valid : dict
        A dictionary mapping outputs to where or not they were valid for the
        feedstock.
    copied : dict
        A dictionary mapping outputs to whether or not they were copied.
    """
    if not feedstock.endswith("-feedstock"):
        return None

    gh = get_gh_client()

    team_name = feedstock[: -len("-feedstock")]

    message = f"""\
Hi @conda-forge/{team_name}! This is the friendly automated conda-forge-webservice!

It appears that one or more of your feedstock's packages did not copy from the
staging channel (cf-staging) to the production channel (conda-forge). :(

This failure can happen for a lot of reasons, including an outdated feedstock
token or your feedstock not having permissions to upload the given package.
Below we have put some information about the failure to help you debug it.

Common ways to fix this problem include:

- First check the [conda-forge status page](https://conda-forge.org/status/) for any infrastructure outages.
- Retry the package build and upload by pushing an empty commit to the feedstock.
- Rerender the feedstock in a PR from a fork of the feedstock and merge.
- Request a feedstock token reset via our [admin-requests repo](https://github.com/conda-forge/admin-requests?tab=readme-ov-file#reset-your-feedstock-token).
- Request that any new packages be added to the allowed outputs for the feedstock
  via our [admin-requests repo](https://github.com/conda-forge/admin-requests?tab=readme-ov-file#add-a-package-output-to-a-feedstock).
- In rare cases, the package name may change regularly in a well defined way (e.g., `libllvm18`, `libllvm19`, etc.).
  In this case, you can use our [admin-requests repo](https://github.com/conda-forge/admin-requests?tab=readme-ov-file#add-a-package-output-to-a-feedstock)
  to add a glob pattern that matches the new package name pattern. We use the Python `fnmatch` module syntax.
  Output packages that match these patterns will be automatically registered for your feedstock.

If you have any issues or questions, you can find us on Zulip in the
community [channel](https://conda-forge.zulipchat.com/#narrow/channel/457337-general) or you can bump us right here.
"""  # noqa

    is_all_valid = True
    if len(valid) > 0:
        valid_msg = "output validation (is this package allowed for your feedstock?):\n"
        for o, v in valid.items():
            valid_msg += f" - **{o}**: {v}\n"
            is_all_valid &= v

        message += "\n\n"
        message += valid_msg

    if len(copied) > 0:
        copied_msg = (
            "copied (did this package get copied to the production channel?):\n"
        )
        for o, v in copied.items():
            copied_msg += f" - **{o}**: {v}\n"

        message += "\n\n"
        message += copied_msg

    if len(errors) > 0:
        error_msg = "error messages:\n"
        for err in errors:
            is_all_valid &= "not allowed for" not in err
            error_msg += f" - {err}"

        message += "\n\n"
        message += error_msg

    if not is_all_valid:
        message += (
            "\n\n"
            "To fix package package output validation errors, follow the "
            "instructions above to add "
            "new package outputs to your feedstock or to add your "
            "feedstock+packages to the allow list for automatic "
            "registration."
        )

    repo = gh.get_repo(f"conda-forge/{feedstock}")
    issue = None
    for _issue in repo.get_issues(state="all"):
        if (git_sha is not None and git_sha in _issue.title) or (
            "[warning] failed package validation and/or copy" in _issue.title
        ):
            issue = _issue
            break

    if issue is None:
        if git_sha is not None:
            issue = repo.create_issue(
                f"[warning] failed package validation and/or copy for commit {git_sha}",
                body=message,
            )
        else:
            issue = repo.create_issue(
                "[warning] failed package validation and/or copy",
                body=message,
            )
    else:
        if issue.state == "closed":
            issue.edit(state="open")
        issue.create_comment(message)
