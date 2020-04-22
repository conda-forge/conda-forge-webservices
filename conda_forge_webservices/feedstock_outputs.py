"""
This module registers and validates feedstock outputs.
"""
import os
import json
import hmac
import urllib.parse
import subprocess
import shutil
import tempfile

from binstar_client.utils import get_server_api
from binstar_client import BinstarError
import binstar_client.errors

from conda_smithy.feedstock_tokens import is_valid_feedstock_token

from .utils import parse_conda_pkg

STAGING = "cf-staging"
PROD = "conda-forge"
OUTPUTS_REPO = "https://${GH_TOKEN}@github.com/conda-forge/feedstock-outputs.git"
TOKENS_REPO = "https://${GH_TOKEN}@github.com/conda-forge/feedstock-tokens.git"


def _run_git_command(*args, cwd=None):
    subprocess.run(
        " ".join(["git"] + list(args)),
        check=True,
        shell=True,
        cwd=cwd,
    )


def _run_smithy_command(*args, cwd=None):
    subprocess.run(
        ["conda-smithy"] + list(args),
        check=True,
        cwd=cwd,
    )


def register_feedstock_token_handler(feedstock):
    """Generate and register feedstock tokens.

    Parameters
    ----------
    feedstock : str
        The name of the feedstock.

    Returns
    -------
    error : bool
        True if there is an error, False otherwise.
    """
    if feedstock.endswith("-feedstock"):
        feedstock_name = feedstock[:-len("-feedstock")]
    else:
        feedstock_name = feedstock

    feedstock_url = "https://github.com/conda-forge/%s-feedstock.git" % feedstock_name
    token_path = os.path.expanduser(
        "~/.conda-smithy/conda-forge_%s_feedstock.token" % feedstock_name
    )

    tmpdir = None
    try:
        tmpdir = tempfile.mkdtemp('_recipe')
        fspath = os.path.join(tmpdir, "%s-feedstock" % feedstock_name)
        try:
            _run_git_command(
                "clone", "--depth=1",
                feedstock_url, fspath,
            )
        except subprocess.CalledProcessError:
            print("    could not clone the feedstock")
            return True

        try:
            _run_smithy_command("generate-feedstock-token", cwd=fspath)
        except subprocess.CalledProcessError:
            print("    could not generate feedstock token")
            return True

        try:
            _run_smithy_command("register-feedstock-token", cwd=fspath)
        except subprocess.CalledProcessError:
            print("    could not register feedstock token")
            return True
    finally:
        if tmpdir is not None:
            shutil.rmtree(tmpdir)

        try:
            os.remove(token_path)
        except Exception:
            pass

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
                print("    copied:", dist)
            except BinstarError:
                print("    did not copy:", dist)
                pass

        if (
            copied[dist]
            and _dist_exists(ac_staging, STAGING, dist)
        ):
            try:
                ac_staging.remove_dist(
                    STAGING,
                    name,
                    version,
                    basename=urllib.parse.quote(dist, safe=""),
                )
                print("    removed:", dist)
            except BinstarError:
                print("    could not remove:", dist)
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
            print("    did hash comp:", dist)
        except BinstarError:
            print("    did not do hash comp:", dist)
            pass

    return valid


def is_valid_feedstock_output(project, outputs, register=True):
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
    register : bool
        If True, attempt to register any outputs that do not exist by pushing
        the proper json blob to `output_repo`. Default is True.

    Returns
    -------
    valid : dict
        A dict keyed on output name with True if it is valid and False
        otherwise.
    """
    feedstock = project.replace("-feedstock", "")

    valid = {o: False for o in outputs}
    made_commit = False

    tmpdir = None
    try:
        tmpdir = tempfile.mkdtemp('_recipe')
        repo_path = os.path.join(tmpdir, "feedstock-outputs")

        _run_git_command("clone", "--depth=1", OUTPUTS_REPO, repo_path)

        _run_git_command(
            "remote",
            "set-url",
            "--push",
            "origin",
            OUTPUTS_REPO,
            cwd=repo_path)

        for dist in outputs:
            try:
                _, o, _, _ = parse_conda_pkg(dist)
            except RuntimeError:
                continue

            pth = os.path.join(repo_path, "outputs", o + ".json")

            if not os.path.exists(pth):
                # no output exists, so we can add it
                valid[dist] = True

                print("    does not exist|valid: %s|%s" % (o, valid[dist]))
                if register:
                    print("    registered:", o)
                    with open(pth, "w") as fp:
                        json.dump({"feedstocks": [feedstock]}, fp)
                    _run_git_command("add", pth, cwd=repo_path)
                    _run_git_command(
                        "commit",
                        "-m",
                        "'added output %s for conda-forge/%s'" % (o, feedstock),
                        cwd=repo_path
                    )
                    made_commit = True
            else:
                # make sure feedstock is ok
                with open(pth, "r") as fp:
                    data = json.load(fp)
                valid[dist] = feedstock in data["feedstocks"]
                print("    checked|valid: %s|%s" % (o, valid[dist]))

        if register and made_commit:
            _run_git_command("pull", "--commit", "--rebase", cwd=repo_path)
            _run_git_command("push", cwd=repo_path)

    finally:
        if tmpdir is not None:
            shutil.rmtree(tmpdir)

    return valid


def validate_feedstock_outputs(
    project,
    outputs,
    feedstock_token,
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
    feedstock_token : str
        The secret token used to validate that this feedstock is who it says
        it is.

    Returns
    -------
    valid : dict
        A dict keyed on the keys in `outputs` with values True in the output
        is valid and False otherwise.
    errors : list of str
        A list of any errors encountered.
    """
    valid = {o: False for o in outputs}

    if not is_valid_feedstock_token(
        "conda-forge", project, feedstock_token, TOKENS_REPO
    ):
        return valid, ["invalid feedstock token"]

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

    valid_outputs = is_valid_feedstock_output(
        project,
        outputs_to_test,
        register=True,
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

    return valid, errors
