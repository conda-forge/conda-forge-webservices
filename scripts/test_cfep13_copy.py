"""
To run these tests

1. start the web server locally via

   python -u -m conda_forge_webservices.webapp --local

2. Make sure you have a github token in the GH_TOKEN environment variable.

3. Make sure you have the STAGING_BINSTAR_TOKEN and PROD_BINSTAR_TOKEN environment
   variables defined.

4. Run these tests via pytest -vvs test_cfep13_copy.py
"""

import os
import tempfile
import json
import subprocess
import uuid
import glob
import hashlib
import contextlib
import shutil
import secrets
import urllib
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import requests
from binstar_client import BinstarError
import binstar_client.errors
import yaml

import pytest
from flaky import flaky

from conda_forge_webservices.utils import pushd, with_action_url
from conda_forge_webservices.feedstock_outputs import (
    _get_ac_api_prod,
    _get_ac_api_staging,
)

RNG = secrets.SystemRandom()

OUTPUTS_REPO = (
    "https://x-access-token:${GH_TOKEN}@github.com/conda-forge/feedstock-outputs.git"
)

try:
    token_path = "${HOME}/.conda-smithy/conda-forge_staged-recipes.token"
    with open(os.path.expandvars(token_path)) as fp:
        sr_token = fp.read().strip()

    headers: dict[str, str] | None = {
        "FEEDSTOCK_TOKEN": sr_token,
    }
except Exception:
    headers = None


def _run_git_command(*args):
    subprocess.run(
        " ".join(["git", *list(args)]),
        check=True,
        shell=True,
    )


def _clone_and_remove(repo, file_to_remove):
    with tempfile.TemporaryDirectory() as tmpdir:
        with pushd(tmpdir):
            _run_git_command("clone", "--depth=1", repo)

            with pushd(os.path.split(repo)[1].replace(".git", "")):
                _run_git_command(
                    "remote",
                    "set-url",
                    "--push",
                    "origin",
                    repo,
                )
                if os.path.exists(file_to_remove):
                    print(f"    repo {repo}: removed file {file_to_remove}")
                    _run_git_command("rm", file_to_remove)
                    msg = with_action_url(f"removed {file_to_remove} for testing")
                    _run_git_command(
                        "commit",
                        "-m",
                        f"'{msg}'",
                    )
                    _run_git_command("pull", "--rebase", "--commit")
                    _run_git_command("push")


def _build_recipe(uid):
    os.makedirs("recipe", exist_ok=True)

    with open("recipe/meta.yaml", "w") as fp:
        fp.write("""\
{% set version = datetime.datetime.utcnow().strftime('%Y.%m.%d.%H.%M.%S') %}
{% set name = "blah-" ~ uuid %}

package:
  name: {{ name|lower }}
  version: {{ version }}

source:
  path: .

build:
  number: 0
  noarch: python

requirements:
  host:
    - python
    - pip
  run:
    - python

test:
  commands:
    - echo "works!"

about:
  home: https://github.com/conda-forge/conda-forge-webservices
  license: BSD-3-Clause
  license_family: BSD
  license_file: LICENSE
  summary: testing package for the conda forge webservices

extra:
  recipe-maintainers:
    - conda-forge/core
""")

    with open("recipe/LICENSE", "w") as fp:
        fp.write("""\
BSD 3-clause
Copyright (c) conda-forge
All rights reserved.

Redistribution and use in source and binary forms, with or without modification,
are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice,
   this list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation and/or
   other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its contributors
   may be used to endorse or promote products derived from this software without
   specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT,
INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT
NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA,
OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY,
WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
POSSIBILITY OF SUCH DAMAGE.
""")

    with open("recipe/conda_build_config.yaml", "w") as fp:
        fp.write(f"""\
uuid:
  - {uid}
""")
    subprocess.run(
        "mkdir -p built_dists "
        "&& rm -rf built_dists/* "
        '&& export CONDA_BLD_PATH="$(pwd)/built_dists" '
        "&& conda-build recipe",
        check=True,
        shell=True,
    )


def _split_pkg(pkg):
    if pkg.endswith(".tar.bz2"):
        pkg = pkg[: -len(".tar.bz2")]
    elif pkg.endswith(".conda"):
        pkg = pkg[: -len(".conda")]
    else:
        raise RuntimeError("Can only process packages that end in .tar.bz2 or .conda!")
    plat, pkg_name = pkg.split(os.path.sep)
    name_ver, build = pkg_name.rsplit("-", 1)
    name, ver = name_ver.rsplit("-", 1)
    return plat, name, ver, build


def _compute_local_info(dist, croot, hash_type):
    h = getattr(hashlib, hash_type)()

    with open(os.path.join(croot, dist), "rb") as fp:
        chunk = 0
        while chunk != b"":
            chunk = fp.read(1024)
            h.update(chunk)

    md5 = h.hexdigest()

    return {dist: md5}


@contextlib.contextmanager
def _get_temp_token(token):
    dn = tempfile.mkdtemp()
    fn = os.path.join(dn, "binstar.token")
    with open(fn, "w") as fh:
        fh.write(token)
    yield fn
    shutil.rmtree(dn)


def _remove_dist(ac, channel, dist):
    _, name, version, _ = _split_pkg(dist)
    try:
        ac.remove_dist(
            channel,
            name,
            version,
            basename=urllib.parse.quote(dist, safe=""),
        )
        return True
    except BinstarError:
        return False


def _dist_exists(ac, channel, dist):
    _, name, version, _ = _split_pkg(dist)
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


def _print_step(title):
    step = f"""\
\n=========================================================
{title}
========================================================="""
    print(step, flush=True)


def _upload_to_staging(ac_staging, outputs):
    _print_step("uploading to cf-staging")
    with _get_temp_token(os.environ["STAGING_BINSTAR_TOKEN"]) as fn:
        for output in outputs:
            pth = os.path.join("built_dists", output)
            subprocess.run(
                " ".join(
                    [
                        "anaconda",
                        "--quiet",
                        "-t",
                        fn,
                        "upload",
                        pth,
                        "--user=cf-staging",
                        "--channel=main",
                    ]
                ),
                check=True,
                shell=True,
            )

    _print_step("sleeping for 10 seconds")
    time.sleep(10)

    _print_step("checking that dists exist on staging")
    for dist in outputs:
        assert _dist_exists(ac_staging, "cf-staging", dist)
        print(f"    cf-staging: dist {dist} exists", flush=True)


def _post_copy_request(headers, json_data):
    r = requests.post(
        "http://127.0.0.1:5000/feedstock-outputs/copy",
        headers=headers,
        json=json_data,
    )
    return r


def _post_and_check_copy_requests(headers, json_data, should_fail):
    n_try = 10
    with ProcessPoolExecutor(max_workers=n_try) as exc:
        futs = [
            exc.submit(_post_copy_request, headers, json_data) for _ in range(n_try)
        ]
        results = []
        for fut in as_completed(futs):
            r = fut.result()
            results.append(r.status_code == 200)
            rmsg = yaml.dump(r.json(), default_flow_style=False, indent=2)
            print(f"\n>>> copy API response:\n{rmsg}\n", flush=True)

    if should_fail:
        assert all([not res for res in results])
    else:
        assert any([res for res in results])


def _attempt_copy_prod(outputs, hash_type, should_fail):
    _print_step("making copy calls to admin server")
    json_data = {
        "feedstock": "staged-recipes",
        "outputs": outputs,
        "channel": "main",
    }
    if hash_type is not None:
        json_data["hash_type"] = hash_type
    _post_and_check_copy_requests(headers, json_data, should_fail)

    _print_step("sleeping for 10 seconds for copy")
    time.sleep(10)


@pytest.fixture(scope="session")
def build_test_package():
    uid = uuid.uuid4().hex[0:6]

    _print_step("running conda build")
    _build_recipe(uid)
    dists = glob.glob("built_dists/noarch/blah-*.conda")
    assert len(dists) == 1
    dists = [os.path.relpath(dist, start="built_dists") for dist in dists]

    return uid, dists


@pytest.mark.skipif(headers is None, reason="No feedstock token for testing!")
@pytest.mark.parametrize("should_fail", [False, True])
@pytest.mark.parametrize("hash_type", [None, "md5", "sha256"])
@flaky
def test_feedstock_outputs_copy_works(build_test_package, should_fail, hash_type):
    uid, dists = build_test_package

    ac_prod = _get_ac_api_prod()
    ac_staging = _get_ac_api_staging()

    _print_step("computing dist info")
    outputs = {}
    for dist in dists:
        hash_value = _compute_local_info(dist, "built_dists", hash_type or "md5")[dist]
        if should_fail:  # scramble the hash
            hash_value = list(hash_value)
            RNG.shuffle(hash_value)
            hash_value = "".join(hash_value)
        outputs[dist] = hash_value

    omsg = yaml.dump(outputs, default_flow_style=False, indent=2)
    print(f"outputs:\n{omsg}", flush=True)

    try:
        _upload_to_staging(ac_staging, outputs)
        _attempt_copy_prod(outputs, hash_type, should_fail)

        _print_step("checking that dists exist on prod")
        for dist in outputs:
            de = _dist_exists(ac_prod, "conda-forge", dist)
            if should_fail:
                assert not de
                print(f"    conda-forge: dist {dist} does not exist", flush=True)
            else:
                assert de
                print(f"    conda-forge: dist {dist} exists", flush=True)

        _print_step("checking the new outputs")
        output_fname = f"outputs/b/l/a/blah-{uid}.json"
        with tempfile.TemporaryDirectory() as tmpdir:
            with pushd(tmpdir):
                _run_git_command("clone", "--depth=1", OUTPUTS_REPO)

                with pushd(os.path.split(OUTPUTS_REPO)[1].replace(".git", "")):
                    if should_fail:
                        assert not os.path.exists(output_fname)
                    else:
                        assert os.path.exists(output_fname)
                        with open(output_fname) as fp:
                            data = json.load(fp)
                            assert data["feedstocks"] == ["staged-recipes"]

    finally:
        _print_step("cleaning up repos and dists")
        for dist in outputs:
            if _dist_exists(ac_staging, "cf-staging", dist):
                if _remove_dist(ac_staging, "cf-staging", dist):
                    print(f"cf-staging: removed {dist}")
            if _dist_exists(ac_prod, "conda-forge", dist):
                if _remove_dist(ac_prod, "conda-forge", dist):
                    print(f"cond-forge: removed {dist}")

        _clone_and_remove(OUTPUTS_REPO, f"outputs/b/l/a/blah-{uid}.json")
        print(" ", flush=True)
