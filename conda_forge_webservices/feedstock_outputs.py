"""
This module registers and validates feedstock outputs.
"""
import os
import json
import hmac
import urllib.parse
import logging
import requests
import base64

import scrypt
import github

from binstar_client.utils import get_server_api
from binstar_client import BinstarError
import binstar_client.errors

from .utils import parse_conda_pkg

LOGGER = logging.getLogger("conda_forge_webservices.feedstock_outputs")

STAGING = "cf-staging"
PROD = "conda-forge"


def _get_sharded_path(output):
    chars = [c for c in output if c.isalnum()]
    while len(chars) < 3:
        chars.append("z")

    return os.path.join("outputs", chars[0], chars[1], chars[2], output + ".json")


def is_valid_feedstock_token(user, project, feedstock_token):
    r = requests.get(
        "https://api.github.com/repos/%s/"
        "feedstock-tokens/contents/tokens/%s.json" % (user, project),
        headers={"Authorization": "token %s" % os.environ["GH_TOKEN"]},
    )
    if r.status_code != 200:
        return False
    else:
        data = r.json()
        assert data["encoding"] == "base64"
        token_data = json.loads(
            base64.standard_b64decode(data["content"]).decode('utf-8'))
        salted_token = scrypt.hash(
            feedstock_token,
            bytes.fromhex(token_data["salt"]),
            buflen=256,
        )
        return hmac.compare_digest(
            salted_token, bytes.fromhex(token_data["hashed_token"]),
        )


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


def _make_package(name, ac_prod):
    try:
        pkg_info = requests.get(
            f"https://api.anaconda.org/package/{STAGING}/{name}"
        )
        pkg_info.raise_for_status()
        pkg_info = pkg_info.json()
    except Exception:
        return False

    pkg_attrs = {
        "name": pkg_info["name"],
        "summary": pkg_info["summary"],
        "description": pkg_info["description"],
        "license": pkg_info["license"],
        "license_url": pkg_info["license_url"],
        "license_family": pkg_info.get("license_family"),
        "dev_url": pkg_info["dev_url"],
        "doc_url": pkg_info["doc_url"],
        "home": pkg_info["home"],
        "source_git_url": pkg_info["source_git_url"],
    }

    try:
        ac_prod.add_package(
            PROD,
            name,
            pkg_attrs["summary"],
            pkg_attrs["license"],
            public=True,
            attrs=pkg_attrs,
            license_url=pkg_attrs["license_url"],
            license_family=pkg_attrs["license_family"],
            package_type="conda",
        )
    except Exception:
        return False

    return True


def copy_feedstock_outputs(outputs, channel):
    """Copy outputs from one chanel to another.

    Parameters
    ----------
    outputs : list of str
        A list of outputs to copy. These should be the full names with the
        platform directory, version/build info, and file extension (e.g.,
        `noarch/blah-fa31b0-2020.04.13.15.54.07-py_0.tar.bz2`).
    channel : str
        The source and target channel to use. Pass "main" for the default
        channel.

    Returns
    -------
    copied : dict
        A dict keyed on the output name with True if the copy worked and False
        otherwise.
    """
    ac_prod = _get_ac_api_prod()
    ac_staging = _get_ac_api_staging()

    copied = {o: False for o in outputs}

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
                if not _make_package(name, ac_prod):
                    LOGGER.info(
                        f"    could not make package {name} on {PROD} "
                        "- copying anyways"
                    )

                ac_prod.copy(
                    STAGING,
                    name,
                    version,
                    basename=urllib.parse.quote(dist, safe=""),
                    to_owner=PROD,
                    from_label=channel,
                    to_label=channel,
                )
                copied[dist] = True
                LOGGER.info("    copied: %s", dist)
            except BinstarError:
                LOGGER.info("    did not copy: %s", dist)
                pass

        if (
            copied[dist]
            and _dist_exists(ac_staging, STAGING, dist)
            and "REGRO_GITHUB_TOKEN" not in os.environ
        ):
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


def _is_valid_output_hash(outputs):
    """Test if a set of outputs have valid hashes on the staging channel.

    Parameters
    ----------
    outputs : dict
        A dictionary mapping each output to its md5 hash. The keys should be the
        full names with the platform directory, version/build info, and file extension
        (e.g., `noarch/blah-fa31b0-2020.04.13.15.54.07-py_0.tar.bz2`).

    Returns
    -------
    valid : dict
        A dict keyed on full output names with True if it is valid and False
        otherwise.
    """
    ac = get_server_api()

    valid = {o: False for o in outputs}

    for dist, md5hash in outputs.items():
        try:
            _, name, version, _ = parse_conda_pkg(dist)
        except RuntimeError:
            continue

        try:
            data = ac.distribution(
                STAGING,
                name,
                version,
                basename=urllib.parse.quote(dist, safe=""),
            )
            valid[dist] = hmac.compare_digest(data["md5"], md5hash)
            LOGGER.info("    did hash comp: %s", dist)
        except BinstarError:
            LOGGER.info("    did not do hash comp: %s", dist)
            pass

    return valid


def _is_valid_feedstock_output(
    project, outputs, register=True, must_explicitly_exist=False
):
    """Test if feedstock outputs are valid (i.e., the outputs are allowed for that
    feedstock). Optionally register them if they do not exist.

    Parameters
    ----------
    project : str
        The GitHub repo.
    outputs : list of str
        A list of ouputs top validate. The list entries should be the
        full names with the platform directory, version/build info, and file extension
        (e.g., `noarch/blah-fa31b0-2020.04.13.15.54.07-py_0.tar.bz2`).
    register : bool, optional
        If True, attempt to register any outputs that do not exist by pushing
        the proper json blob to `output_repo`. Default is True.
    must_explicitly_exist : bool, optional
        If True, the output must be already registered and exist for it to be
        valid. This option is used for appveyor-only uploads where we cannot
        verify the request.

    Returns
    -------
    valid : dict
        A dict keyed on output name with True if it is valid and False
        otherwise.
    """
    if project.endswith("-feedstock"):
        feedstock = project[:-len("-feedstock")]
    else:
        feedstock = project

    valid = {o: False for o in outputs}

    unique_names = set()
    for dist in outputs:
        try:
            _, o, _, _ = parse_conda_pkg(dist)
        except RuntimeError:
            continue
        unique_names.add(o)

    unique_names_valid = {o: False for o in unique_names}
    for un in unique_names:
        un_sharded_path = _get_sharded_path(un)
        r = requests.get(
            "https://api.github.com/repos/conda-forge/"
            "feedstock-outputs/contents/%s" % un_sharded_path,
            headers={"Authorization": "token %s" % os.environ["GH_TOKEN"]}
        )
        if r.status_code != 200:
            # it failed, but we need to know if it failed due to the API or
            # if the file is not there
            if r.status_code == 404 and not must_explicitly_exist:
                unique_names_valid[un] = True

                LOGGER.info(
                    "    does not exist|valid: %s|%s" % (un, unique_names_valid[un]))
                if register:
                    data = {"feedstocks": [feedstock]}
                    edata = base64.standard_b64encode(
                        json.dumps(data).encode("utf-8")).decode("ascii")

                    r = requests.put(
                        "https://api.github.com/repos/conda-forge/"
                        "feedstock-outputs/contents/%s" % un_sharded_path,
                        headers={"Authorization": "token %s" % os.environ["GH_TOKEN"]},
                        json={
                            "message": (
                                "[ci skip] [skip ci] [cf admin skip] ***NO_CI*** added "
                                "output %s for conda-forge/%s" % (un, feedstock)),
                            "content": edata,
                            "branch": "master",
                        }
                    )
                    if r.status_code != 201:
                        LOGGER.info(
                            "    output %s not created for "
                            "feedstock conda-forge/%s" % (un, feedstock)
                        )
                        r.raise_for_status()
        else:
            data = r.json()
            assert data["encoding"] == "base64"
            data = json.loads(
                base64.standard_b64decode(data["content"]).decode('utf-8'))
            unique_names_valid[un] = feedstock in data["feedstocks"]
            LOGGER.info("    checked|valid: %s|%s" % (un, unique_names_valid[un]))

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
    win_only,
):
    """Validate feedstock outputs on the staging channel.

    Parameters
    ----------
    project : str
        The name of the feedstock.
    outputs : dict
        A dictionary mapping each output to its md5 hash. The keys should be the
        full names with the platform directory, version/build info, and file extension
        (e.g., `noarch/blah-fa31b0-2020.04.13.15.54.07-py_0.tar.bz2`).
    win_only : bool
        If True, only outputs in the win-64 subdir will be allowed. This option
        is used for appveyor only uploads.

    Returns
    -------
    valid : dict
        A dict keyed on the keys in `outputs` with values True in the output
        is valid and False otherwise.
    errors : list of str
        A list of any errors encountered.
    """
    valid = {o: False for o in outputs}

    errors = []

    correctly_formatted = {}
    for o in outputs:
        try:
            parse_conda_pkg(o)
            correctly_formatted[o] = True
        except RuntimeError:
            correctly_formatted[o] = False
            errors.append(
                "output '%s' is not correctly formatted (it must be the fully "
                "qualified name w/ extension, `noarch/blah-fa31b0-2020.04.13.15"
                ".54.07-py_0.tar.bz2`)" % o
            )

    outputs_to_test = {o: v for o, v in outputs.items() if correctly_formatted[o]}

    valid_outputs = _is_valid_feedstock_output(
        project,
        outputs_to_test,
        # for win-only uploads on appveyor we must have already registered
        # the output and we cannot register new outputs
        register=not win_only,
        must_explicitly_exist=win_only,
    )

    valid_hashes = _is_valid_output_hash(outputs_to_test)

    for o in outputs_to_test:
        _errors = []
        if not valid_outputs[o]:
            _errors.append(
                "output %s not allowed for conda-forge/%s" % (o, project)
            )
        if not valid_hashes[o]:
            _errors.append("output %s does not have a valid md5 checksum" % o)

        if len(_errors) > 0:
            errors.extend(_errors)
        else:
            valid[o] = True

    # this has to come last
    if win_only:
        for o in outputs_to_test:
            plat, _, _, _ = parse_conda_pkg(o)
            if plat != "win-64":
                valid[o] = False
                errors.append(
                    "output %s is not allowed for win-64-only copies" % o
                )

    return valid, errors


def comment_on_outputs_copy(feedstock, git_sha, errors, valid, copied):
    """Comment on a git commit if the feedstock output copy failed.

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
    gh = github.Github(os.environ['GH_TOKEN'])

    message = """\
Hi @conda-forge/%s! This is the friendly automated conda-forge-webservice!

It appears that one or more of your feedstock's outputs did not copy from the
staging channel to the conda-forge production channels. :(

This failure can happen for a lot of reasons, including an outdated feedstock
token. Below we have put some information about the failure to help you debug it.

If you have any issues or questions, you can find us on gitter in the
community [chat room](https://gitter.im/conda-forge/conda-forge.github.io) or you can bump us right in this comment.
""" % (feedstock[:-len("-feedstock")])  # noqa

    if len(valid) > 0:
        valid_msg = "output validation (is this output allowed for your feedstock?):\n"
        for o, v in valid.items():
            valid_msg += " - **%s**: %s\n" % (o, v)

        message += "\n\n"
        message += valid_msg

    if len(copied) > 0:
        copied_msg = "copied (did this output get copied to the production channel?):\n"
        for o, v in copied.items():
            copied_msg += " - **%s**: %s\n" % (o, v)

        message += "\n\n"
        message += copied_msg

    if len(errors) > 0:
        error_msg = "error messages:\n"
        for err in errors:
            error_msg += " - %s" % err

        message += "\n\n"
        message += error_msg

    repo = gh.get_repo("conda-forge/%s" % feedstock)
    commit = repo.get_commit(git_sha)
    commit.create_comment(message)
