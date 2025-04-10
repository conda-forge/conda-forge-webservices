"""
This module registers and validates feedstock outputs.
"""

import os
import json
import hmac
import urllib.parse
import functools
import logging
import base64
import time

import requests
import requests.exceptions
import scrypt
import github

from binstar_client.utils import get_server_api
from binstar_client import BinstarError
from conda_forge_metadata.feedstock_outputs import (
    package_to_feedstock,
    feedstock_outputs_config,
)
from conda_forge_metadata.feedstock_outputs import sharded_path as _get_sharded_path

from .utils import parse_conda_pkg, _test_and_raise_besides_file_not_exists
from conda_forge_webservices.tokens import (
    get_app_token_for_webservices_only,
    get_gh_client,
)

LOGGER = logging.getLogger("conda_forge_webservices.feedstock_outputs")

STAGING = "cf-staging"
PROD = "conda-forge"
STAGING_LABEL = "cf-staging-do-not-use"


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


def _get_ac_api_with_timeout(token, timeout=10):
    # see https://stackoverflow.com/a/59317604/1745538
    ac = get_server_api(token=token)
    ac.session.request = functools.partial(ac.session.request, timeout=timeout)
    return ac


@functools.lru_cache(maxsize=1)
def _get_ac_api_prod():
    """wrap this a function so we can more easily mock it when testing"""
    return _get_ac_api_with_timeout(token=os.environ["PROD_BINSTAR_TOKEN"])


@functools.lru_cache(maxsize=1)
def _get_ac_api_staging():
    """wrap this a function so we can more easily mock it when testing"""
    return _get_ac_api_with_timeout(token=os.environ["STAGING_BINSTAR_TOKEN"])


def _get_dist(ac, channel, dist):
    try:
        _, name, version, _ = parse_conda_pkg(dist)
    except RuntimeError as e:
        LOGGER.critical(
            "    could not parse dist for existence check: %s",
            dist,
            exc_info=e,
        )
        return None

    try:
        data = ac.distribution(
            channel,
            name,
            version,
            basename=urllib.parse.quote(dist, safe=""),
        )
        return data
    except (BinstarError, requests.exceptions.ReadTimeout):
        return None


def _dist_exists(ac, channel, dist):
    if _get_dist(ac, channel, dist) is not None:
        return True
    else:
        return False


def _dist_has_label(ac, channel, dist, label):
    data = _get_dist(ac, channel, dist)
    if data is None:
        return False
    else:
        return label in data.get("labels", [])


def _add_label_dist(ac, channel, dist, label, src_label):
    try:
        _, name, version, _ = parse_conda_pkg(dist)
    except RuntimeError as e:
        LOGGER.critical(
            "    could not parse dist for adding label %s: %s",
            label,
            dist,
            exc_info=e,
        )
        return False

    if _dist_has_label(ac, channel, dist, label):
        return True
    else:
        try:
            ac.copy(
                channel,
                name,
                version,
                basename=urllib.parse.quote(dist, safe=""),
                to_owner=channel,
                from_label=src_label,
                to_label=label,
                update=False,
                replace=True,
            )
        except (BinstarError, requests.exceptions.ReadTimeout) as e:
            LOGGER.critical(
                "    could not add label %s: %s",
                label,
                dist,
                exc_info=e,
            )
            return False

    return True


def _remove_label_dist(ac, channel, dist, label):
    try:
        _, name, version, _ = parse_conda_pkg(dist)
    except RuntimeError as e:
        LOGGER.critical(
            "    could not parse dist for removing label %s: %s",
            label,
            dist,
            exc_info=e,
        )
        return False

    if not _dist_has_label(ac, channel, dist, label):
        return True
    else:
        try:
            ac.remove_channel(
                label,
                channel,
                package=name,
                version=version,
                filename=urllib.parse.quote(dist, safe=""),
            )
        except (BinstarError, requests.exceptions.ReadTimeout) as e:
            LOGGER.critical(
                "    could not remove label %s: %s",
                label,
                dist,
                exc_info=e,
            )
            return False

    return True


def _copy_dist_if_not_exists(
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
    except RuntimeError as e:
        LOGGER.critical(
            "    could not parse dist for copy: %s",
            dist,
            exc_info=e,
        )
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
        except (BinstarError, requests.exceptions.ReadTimeout) as e:
            LOGGER.critical(
                "    could not copy dist: %s",
                dist,
                exc_info=e,
            )
            return False

    return True


def _dist_has_only_staging_labels(ac, channel, dist):
    data = _get_dist(ac, channel, dist)
    if data is None:
        return False
    else:
        labels = data.get("labels", [])
        if all(label.startswith(STAGING_LABEL) for label in labels):
            return True
        else:
            LOGGER.info(
                "    dist %s has labels %s, not all of which are staging labels",
                dist,
                labels,
            )
            return False


def _remove_dist(ac, channel, dist):
    try:
        _, name, version, _ = parse_conda_pkg(dist)
    except RuntimeError as e:
        LOGGER.critical(
            "    could not parse dist for removing: %s",
            dist,
            exc_info=e,
        )
        return False

    try:
        ac.remove_dist(
            channel,
            name,
            version,
            basename=urllib.parse.quote(dist, safe=""),
        )
        LOGGER.info("    removed from %s: %s", channel, dist)
    except (BinstarError, requests.exceptions.ReadTimeout) as e:
        LOGGER.info("    could not remove from %s: %s", channel, dist, exc_info=e)
        pass


def _copy_feedstock_outputs_from_staging_to_prod(
    outputs, src_label, dest_label, delete=True
):
    """Copy outputs from one chanel to another.

    Parameters
    ----------
    outputs : list of str
        A list of outputs to copy. These should be the full names with the
        platform directory, version/build info, and file extension (e.g.,
        `noarch/blah-fa31b0-2020.04.13.15.54.07-py_0.conda`).
    src_label : str
        The source label for the packages on the STAGING channel.
    dest_label : str
        The destination label for the packages on the PROD channel.
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
            copied[dist] = _copy_dist_if_not_exists(
                STAGING,
                src_label,
                dist,
                ac_prod,
                PROD,
                dest_label,
                update_metadata=False,
                replace_metadata=False,
            )
        except (BinstarError, requests.exceptions.ReadTimeout) as e:
            LOGGER.info("    did not copy: %s", dist, exc_info=e)
            pass

        if copied[dist] and delete:
            _remove_dist(ac_staging, STAGING, dist)

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
        if _add_label_dist(ac_prod, PROD, dist, dest_label, src_label):
            relabeled[dist] = True
            LOGGER.info("    relabeled: %s", dist)

            if remove_src_label:
                if _remove_label_dist(ac_prod, PROD, dist, src_label):
                    LOGGER.info("    removed label: %s", dist)
                else:
                    LOGGER.info("    did not remove label: %s", dist)
        else:
            LOGGER.info("    did not relabel: %s", dist)

    return relabeled


def _is_valid_output_hash(outputs, hash_type, channel, label):
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
        The channel to check the hashes on. Should be one of
        `cf-staging` or `conda-forge`.
    label : str
        The label for the packages to validate. The packages must have this label and
        only this labvel to be considered valid.

    Returns
    -------
    valid : dict
        A dict keyed on full output names with True if it is valid and False
        otherwise.
    """
    valid = dict.fromkeys(outputs, False)

    if channel == PROD:
        ac = _get_ac_api_prod()
    elif channel == STAGING:
        ac = _get_ac_api_staging()
    else:
        LOGGER.critical(
            "    channel must be one of conda-forge or cf-staging: %s", channel
        )
        return valid

    for dist, hashsum in outputs.items():
        try:
            data = _get_dist(ac, channel, dist)
            if data is None:
                LOGGER.info(
                    "    did not do hash comp due to dist not existing: %s",
                    dist,
                )
                continue

            if set(data.get("labels", [])) != set([label]):
                LOGGER.info(
                    "    did not do hash comp due to dist"
                    " not having only the label %s: %s",
                    label,
                    dist,
                )
                continue

            valid[dist] = hmac.compare_digest(data[hash_type], hashsum)
            LOGGER.info("    did hash comp: %s", dist)
        except (BinstarError, requests.exceptions.ReadTimeout) as e:
            LOGGER.info("    did not do hash comp: %s", dist, exc_info=e)
            pass

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


def _run_with_backoff(func, *args, n_try=10):
    for i in range(n_try):
        try:
            return func(*args)
        except Exception as e:
            if i == n_try - 1:
                raise e
            time.sleep(1.5**i)


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
        except RuntimeError as e:
            LOGGER.critical(
                "    could not parse dist for output validation: %s",
                dist,
                exc_info=e,
            )
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
                _run_with_backoff(
                    _add_feedstock_output,
                    feedstock,
                    un,
                )

    for dist in outputs:
        try:
            _, o, _, _ = parse_conda_pkg(dist)
        except RuntimeError as e:
            LOGGER.critical(
                "    could not parse dist for output validation: %s",
                dist,
                exc_info=e,
            )
            continue

        valid[dist] = unique_names_valid[o]

    return valid


def validate_feedstock_outputs(
    project,
    outputs,
    hash_type,
    dest_label,
    # staging_label,
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
    dest_label : str
        The destination label for the packages. The packages must also have
        this label on the staging channel `cf-staging`.
    # staging_label : str
    #     The label to use for the staging dists to the prod channel.

    Returns
    -------
    valid : dict
        A dict keyed on the keys in `outputs` with values True in the output
        is valid and False otherwise.
    errors : list of str
        A list of any errors encountered.
    """
    valid = dict.fromkeys(outputs, False)

    # if dest_label == staging_label:
    #     LOGGER.critical(
    #         "    destination label must be different "
    #         "from staging label: dest=%s staging=%s",
    #         dest_label,
    #         staging_label,
    #     )
    #     return valid, ["destination label must be different from staging label"]

    errors = []

    # first ensure that the outputs are correctly formatted
    # do not pass any incorrectly formatted outputs to the
    # rest of the functions
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

    # next ensure the outputs have valid hashes on the staging channel
    # again do not pass any invalid outputs to the rest of the functions
    valid_hashes_staging = _is_valid_output_hash(
        outputs_to_test, hash_type, STAGING, dest_label
    )
    for o in outputs_to_test:
        if not valid_hashes_staging[o]:
            errors.append(
                f"output {o} does not have a valid checksum or "
                f"correct label on {STAGING}"
            )
    outputs_to_test = {
        o: v for o, v in outputs_to_test.items() if valid_hashes_staging[o]
    }

    # next ensure the outputs are valid for the feedstock
    # again do not pass any invalid outputs to the rest of the functions
    valid_outputs = _is_valid_feedstock_output(
        project,
        outputs_to_test,
        # to turn this off, set the value in the config.json blob in
        # conda-forge/feedstock-outputs
        register=feedstock_outputs_config().get("auto_register_all", False),
    )
    for o in outputs_to_test:
        if not valid_outputs[o]:
            errors.append(f"output {o} not allowed for conda-forge/{project}")
    # outputs_to_test = {o: v for o, v in outputs_to_test.items() if valid_outputs[o]}

    # # next copy outputs to the production channel under the staging label
    # # again do not pass any invalid outputs to the rest of the functions
    # copied = _copy_feedstock_outputs_from_staging_to_prod(
    #     outputs_to_test, dest_label, staging_label, delete=True
    # )
    # for o in outputs_to_test:
    #     if not copied[o]:
    #         errors.append(
    #             f"output {o} did not copy to {PROD} under "
    #             f"staging label {staging_label}"
    #         )
    # outputs_to_test = {o: v for o, v in outputs_to_test.items() if copied[o]}

    # # finally validate the hashes on the production channel
    # valid_hashes_prod = _is_valid_output_hash(
    #     outputs_to_test, hash_type, PROD, staging_label
    # )
    # for o in outputs_to_test:
    #     if not valid_hashes_prod[o]:
    #         errors.append(f"output {o} does not have a valid checksum on {PROD}")

    # combine all validations
    for o in outputs:
        if (
            correctly_formatted.get(o)
            and valid_outputs.get(o)
            and valid_hashes_staging.get(o)
            # and copied.get(o)
            # and valid_hashes_prod.get(o)
        ):
            valid[o] = True
        else:
            valid[o] = False

    return valid, errors


def stage_dist_to_prod_and_relabel(
    dist, dest_label, staging_label, hash_type, hash_value
):
    ac_prod = _get_ac_api_prod()
    outputs_to_copy = {dist: hash_value}
    copied = False
    relabeled = False
    errors = []
    try:
        # remove any failed copies
        if _dist_has_only_staging_labels(ac_prod, PROD, dist):
            _remove_dist(ac_prod, PROD, dist)

        # copy to the staging label
        copied = _copy_feedstock_outputs_from_staging_to_prod(
            outputs_to_copy, dest_label, staging_label, delete=True
        )[dist]
        if copied:
            # check the hash
            valid_hash_prod = _is_valid_output_hash(
                outputs_to_copy, hash_type, PROD, staging_label
            )[dist]

            # relabel if the hash is valid
            if valid_hash_prod:
                relabeled = relabel_feedstock_outputs(
                    [dist], staging_label, dest_label, remove_src_label=True
                )[dist]
            else:
                errors.append(
                    f"output {dist} does not have a valid checksum "
                    f"and staging label on {PROD}"
                )
        else:
            errors.append(f"output {dist} did not copy to {PROD} under staging label")
    finally:
        # remove the dist if it was copied and not relabeled
        if copied and not relabeled:
            _remove_dist(ac_prod, PROD, dist)

    return copied and relabeled, errors


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
- Request that any new packages be added to the allowed outputs for the feedstock
  via our [admin-requests repo](https://github.com/conda-forge/admin-requests?tab=readme-ov-file#add-a-package-output-to-a-feedstock).
- In rare cases, the package name may change regularly in a well defined way (e.g., `libllvm18`, `libllvm19`, etc.).
  In this case, you can use our [admin-requests repo](https://github.com/conda-forge/admin-requests?tab=readme-ov-file#add-a-package-output-to-a-feedstock)
  to add a glob pattern that matches the new package name pattern. We use the Python `fnmatch` module syntax.
  Output packages that match these patterns will be automatically registered for your feedstock.
- Request a feedstock token reset via our [admin-requests repo](https://github.com/conda-forge/admin-requests?tab=readme-ov-file#reset-your-feedstock-token).

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
