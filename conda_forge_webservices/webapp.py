import functools
import time
import os
import multiprocessing
import threading
import subprocess
import asyncio
import tornado.escape
import tornado.httpserver
import tornado.ioloop
import tornado.web
import tornado.locks
import hmac
import hashlib
import uuid
import json
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from http.client import responses
import atexit

# import functools
import logging

import requests
import github
from datetime import datetime, timezone

import conda_forge_webservices
import conda_forge_webservices.linting as linting
import conda_forge_webservices.feedstocks_service as feedstocks_service
import conda_forge_webservices.update_teams as update_teams
import conda_forge_webservices.commands as commands
from conda_forge_webservices._version import __version__
from conda_forge_webservices.update_me import WEBSERVICE_PKGS
from conda_forge_webservices.feedstock_outputs import (
    validate_feedstock_outputs,
    is_valid_feedstock_token,
    comment_on_outputs_copy,
    stage_dist_to_prod_and_relabel,
    STAGING_LABEL,
)
from conda_forge_webservices.utils import (
    ALLOWED_CMD_NON_FEEDSTOCKS,
    log_title_and_message_at_level,
)
from conda_forge_webservices import status_monitor
from conda_forge_webservices.tokens import (
    get_app_token_for_webservices_only,
    get_gh_client,
    inject_app_token_into_feedstock,
    inject_app_token_into_feedstock_readonly,
)

STATUS_DATA_LOCK = tornado.locks.Lock()

LOGGER = logging.getLogger("conda_forge_webservices")

COMMAND_POOL = None
COPYLOCK = None
UPLOAD_POOL = None


def _init_upload_pool_processes(lock):
    global COPYLOCK
    COPYLOCK = lock


def _worker_pool(kind):
    global COMMAND_POOL
    global UPLOAD_POOL
    global COPYLOCK

    if kind == "command":
        if COMMAND_POOL is None:
            if "PYTEST_CURRENT_TEST" in os.environ:
                # needed for mocks in testing
                COMMAND_POOL = ThreadPoolExecutor(max_workers=2)
            else:
                COMMAND_POOL = ProcessPoolExecutor(max_workers=2)
        return COMMAND_POOL
    elif kind == "upload":
        if UPLOAD_POOL is None:
            if "PYTEST_CURRENT_TEST" in os.environ:
                # needed for mocks in testing
                COPYLOCK = threading.Lock()
                UPLOAD_POOL = ThreadPoolExecutor(
                    max_workers=4,
                    initializer=_init_upload_pool_processes,
                    initargs=(COPYLOCK,),
                )
            else:
                COPYLOCK = multiprocessing.Lock()
                UPLOAD_POOL = ProcessPoolExecutor(
                    max_workers=4,
                    initializer=_init_upload_pool_processes,
                    initargs=(COPYLOCK,),
                )
        return UPLOAD_POOL
    else:
        raise ValueError(f"Unknown pool kind: {kind}")


def _shutdown_worker_pools():
    global COMMAND_POOL
    global UPLOAD_POOL
    for pool in [COMMAND_POOL, UPLOAD_POOL]:
        if pool is not None:
            pool.shutdown(wait=False)


atexit.register(_shutdown_worker_pools)


THREAD_POOL = None


def _thread_pool():
    global THREAD_POOL
    if THREAD_POOL is None:
        THREAD_POOL = ThreadPoolExecutor(max_workers=4)
    return THREAD_POOL


def _shutdown_thread_pool():
    global THREAD_POOL
    if THREAD_POOL is not None:
        THREAD_POOL.shutdown(wait=False)


atexit.register(_shutdown_thread_pool)


def get_commit_message(full_name, commit):
    return (
        github.Github(auth=github.Auth.Token(os.environ["GH_TOKEN"]))
        .get_repo(full_name)
        .get_commit(commit)
        .commit.message
    )


def _get_rate_limiting_info_for_token(token):
    # Compute some info about our GitHub API Rate Limit.
    # Note that it doesn't count against our limit to
    # get this info. So, we should be doing this regularly
    # to better know when it is going to run out. Also,
    # this will help us better understand where we are
    # spending it and how to better optimize it.

    # Get GitHub API Rate Limit usage and total
    gh = github.Github(auth=github.Auth.Token(token))
    gh_api_remaining = gh.get_rate_limit().core.remaining
    gh_api_total = gh.get_rate_limit().core.limit

    try:
        user = gh.get_user().login
    except Exception:
        user = "conda-forge-webservices[bot]"

    # Compute time until GitHub API Rate Limit reset
    gh_api_reset_time = gh.get_rate_limit().core.reset
    gh_api_reset_time -= datetime.now(timezone.utc)
    msg = f"{user} - remaining {gh_api_remaining} out of {gh_api_total}."
    msg = f"github api requests: {msg} - Will reset in {gh_api_reset_time}."
    return msg


def _print_rate_limiting_info():
    d = [
        os.environ["GH_TOKEN"],
        get_app_token_for_webservices_only(),
    ]
    if "AUTOTICK_BOT_GH_TOKEN" in os.environ:
        d.append(os.environ["AUTOTICK_BOT_GH_TOKEN"])

    msg = []
    for k in d:
        msg.append(_get_rate_limiting_info_for_token(k))
    msg = "\n".join(msg)
    log_title_and_message_at_level(
        level="info",
        title="GitHub API Rate Limit Info",
        msg=msg,
    )


def valid_request(body, signature):
    our_hash = hmac.new(
        os.environ["CF_WEBSERVICES_TOKEN"].encode("utf-8"),
        body,
        hashlib.sha1,
    ).hexdigest()

    their_hash = signature.split("=")[1]

    return hmac.compare_digest(their_hash, our_hash)


class WriteErrorAsJSONRequestHandler(tornado.web.RequestHandler):
    """The write_error method below was pulled from jupyter under
    the license below.

    https://github.com/jupyter-server/jupyter_server/blob/132cf044cc969fb70063666919b4d9ad3349c5d1/jupyter_server/base/handlers.py#L756-L776

    BSD 3-Clause License

    - Copyright (c) 2001-2015, IPython Development Team
    - Copyright (c) 2015-, Jupyter Development Team

    All rights reserved.

    Redistribution and use in source and binary forms, with or without
    modification, are permitted provided that the following conditions are met:

    1. Redistributions of source code must retain the above copyright notice, this
    list of conditions and the following disclaimer.

    2. Redistributions in binary form must reproduce the above copyright notice,
    this list of conditions and the following disclaimer in the documentation
    and/or other materials provided with the distribution.

    3. Neither the name of the copyright holder nor the names of its
    contributors may be used to endorse or promote products derived from
    this software without specific prior written permission.

    THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
    AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
    IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
    DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
    FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
    DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
    SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
    CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
    OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
    OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
    """

    def write_error(self, status_code: int, **kwargs) -> None:
        """APIHandler errors are JSON, not human pages"""
        self.set_header("Content-Type", "application/json")
        message = responses.get(status_code, "Unknown HTTP Error")
        reply: dict[str, str | None] = {
            "message": message,
        }
        exc_info = kwargs.get("exc_info")
        if exc_info:
            e = exc_info[1]
            if isinstance(e, tornado.web.HTTPError):
                reply["message"] = e.log_message or message
                reply["reason"] = e.reason
            else:
                reply["message"] = "Unhandled error"
                reply["reason"] = None
                # backward-compatibility: traceback field is present,
                # but always empty
                reply["traceback"] = ""
        self.finish(json.dumps(reply))


class LintingHookHandler(WriteErrorAsJSONRequestHandler):
    async def post(self):
        headers = self.request.headers
        event = headers.get("X-GitHub-Event", None)

        if not valid_request(
            self.request.body,
            headers.get("X-Hub-Signature", ""),
        ):
            self.set_status(403)
            self.write_error(403)
            return

        if event == "ping":
            self.write("pong")
        elif event == "pull_request":
            body = tornado.escape.json_decode(self.request.body)
            repo_name = body["repository"]["name"]
            owner = body["repository"]["owner"]["login"]
            pr_id = int(body["pull_request"]["number"])
            is_open = body["pull_request"]["state"] == "open"

            if owner != "conda-forge" or not (
                repo_name == "staged-recipes" or repo_name.endswith("-feedstock")
            ):
                self.set_status(404)
                self.write_error(404)
                return

            if body["action"] not in ["opened", "reopened", "synchronize", "unlocked"]:
                return

            if repo_name == "staged-recipes":
                stale = any(
                    label["name"] == "stale" for label in body["pull_request"]["labels"]
                )
            else:
                stale = False

            # Only do anything if we are working with conda-forge,
            # and an open PR.
            if is_open and owner == "conda-forge" and not stale:
                log_title_and_message_at_level(
                    level="info",
                    title=f"linting: {body['repository']['full_name']}",
                )

                if linting.LINT_VIA_GHA:
                    linting.lint_via_github_actions(
                        body["repository"]["full_name"],
                        pr_id,
                    )
                else:
                    lint_info = await tornado.ioloop.IOLoop.current().run_in_executor(
                        _worker_pool("command"),
                        linting.compute_lint_message,
                        owner,
                        repo_name,
                        pr_id,
                        repo_name == "staged-recipes",
                    )
                    if lint_info:
                        msg = linting.comment_on_pr(
                            owner,
                            repo_name,
                            pr_id,
                            lint_info["message"],
                            search="conda-forge-linting service",
                        )
                        linting.set_pr_status(
                            owner,
                            repo_name,
                            lint_info,
                            target_url=msg.html_url,
                        )
        else:
            LOGGER.info(f'Unhandled event "{event}".')
            self.set_status(404)
            self.write_error(404)


class UpdateFeedstockHookHandler(WriteErrorAsJSONRequestHandler):
    async def post(self):
        headers = self.request.headers
        event = headers.get("X-GitHub-Event", None)

        if not valid_request(
            self.request.body,
            headers.get("X-Hub-Signature", ""),
        ):
            self.set_status(403)
            self.write_error(403)
            return

        if event == "ping":
            self.write("pong")
            return
        elif event == "push":
            body = tornado.escape.json_decode(self.request.body)
            repo_name = body["repository"]["name"]
            owner = body["repository"]["owner"]["login"]
            ref = body["ref"]
            commit_msg = (body.get("head_commit", None) or {}).get("message", "")

            # Only do anything if we are working with conda-forge, and a
            # push to main.
            if (
                # this weird thing happens with master to main branch changes maybe?
                body["after"] != "0000000000000000000000000000000000000000"
                and owner == "conda-forge"
                and (ref == "refs/heads/master" or ref == "refs/heads/main")
                and "[cf admin skip feedstocks]" not in commit_msg
                and "[cf admin skip]" not in commit_msg
                and repo_name.endswith("-feedstock")
            ):
                log_title_and_message_at_level(
                    level="info",
                    title=f"feedstocks service: {body['repository']['full_name']}",
                )
                handled = await tornado.ioloop.IOLoop.current().run_in_executor(
                    _worker_pool("command"),
                    feedstocks_service.handle_feedstock_event,
                    owner,
                    repo_name,
                )
                if handled:
                    return
        else:
            LOGGER.info(f'Unhandled event "{event}".')
        self.set_status(404)
        self.write_error(404)


class UpdateTeamHookHandler(WriteErrorAsJSONRequestHandler):
    async def post(self):
        headers = self.request.headers
        event = headers.get("X-GitHub-Event", None)

        if not valid_request(
            self.request.body,
            headers.get("X-Hub-Signature", ""),
        ):
            self.set_status(403)
            self.write_error(403)
            return

        if event == "ping":
            self.write("pong")
            return
        elif event == "push":
            body = tornado.escape.json_decode(self.request.body)
            repo_name = body["repository"]["name"]
            owner = body["repository"]["owner"]["login"]
            ref = body["ref"]
            commit = (body.get("head_commit", None) or {}).get("id", None)
            commit_msg = (body.get("head_commit", None) or {}).get("message", "")

            # Only do anything if we are working with conda-forge,
            # and a push to main.
            if (
                # this weird thing happens with master to main branch changes maybe?
                body["after"] != "0000000000000000000000000000000000000000"
                and owner == "conda-forge"
                and repo_name.endswith("-feedstock")
                and (ref == "refs/heads/master" or ref == "refs/heads/main")
                and "[cf admin skip teams]" not in commit_msg
                and "[cf admin skip]" not in commit_msg
            ):
                log_title_and_message_at_level(
                    level="info",
                    title=f"update teams: {body['repository']['full_name']}",
                )
                await tornado.ioloop.IOLoop.current().run_in_executor(
                    _thread_pool(),  # always threads due to expensive lru_cache
                    update_teams.update_team,
                    owner,
                    repo_name,
                    commit,
                )
                return
        else:
            LOGGER.info(f'Unhandled event "{event}".')

        self.set_status(404)
        self.write_error(404)


class CommandHookHandler(WriteErrorAsJSONRequestHandler):
    async def post(self):
        """
        See https://docs.github.com/en/webhooks/webhook-events-and-payloads
        for the event payloads.
        """
        headers = self.request.headers
        event = headers.get("X-GitHub-Event", None)

        if not valid_request(
            self.request.body,
            headers.get("X-Hub-Signature", ""),
        ):
            self.set_status(403)
            self.write_error(403)
            return

        if event == "ping":
            self.write("pong")
            return
        elif (
            event == "pull_request_review"
            or event == "pull_request"
            or event == "pull_request_review_comment"
        ):
            body = tornado.escape.json_decode(self.request.body)
            action = body["action"]
            repo_name = body["repository"]["name"]
            owner = body["repository"]["owner"]["login"]
            # Only do anything if we are working with conda-forge
            if owner != "conda-forge" or not (
                repo_name in ALLOWED_CMD_NON_FEEDSTOCKS
                or repo_name.endswith("-feedstock")
            ):
                self.set_status(404)
                self.write_error(404)
                return

            pr_repo = body["pull_request"]["head"]["repo"]
            pr_owner = pr_repo["owner"]["login"]
            pr_repo = pr_repo["name"]
            pr_branch = body["pull_request"]["head"]["ref"]
            pr_num = body["pull_request"]["number"]
            comment = None
            comment_id = None
            review_id = None
            if event == "pull_request_review" and action != "dismissed":
                comment = body["review"]["body"]
                review_id = body["review"]["id"]
            elif event == "pull_request" and action in ["opened", "edited", "reopened"]:
                comment = body["pull_request"]["body"]
                comment_id = -1  # will react on description for issue/PR #pr_num
            elif event == "pull_request_review_comment" and action != "deleted":
                comment = body["comment"]["body"]
                review_id = body["comment"]["id"]

            if comment:
                log_title_and_message_at_level(
                    level="info",
                    title=f"PR command: {body['repository']['full_name']}",
                )

                await tornado.ioloop.IOLoop.current().run_in_executor(
                    _worker_pool("command"),
                    commands.pr_detailed_comment,
                    owner,
                    repo_name,
                    pr_owner,
                    pr_repo,
                    pr_branch,
                    pr_num,
                    comment,
                    comment_id,
                    review_id,
                )
                return

        elif event == "issue_comment" or event == "issues":
            body = tornado.escape.json_decode(self.request.body)
            action = body["action"]
            repo_name = body["repository"]["name"]
            owner = body["repository"]["owner"]["login"]
            issue_num = body["issue"]["number"]

            # Only do anything if we are working with conda-forge
            if owner != "conda-forge" or not (
                repo_name in ALLOWED_CMD_NON_FEEDSTOCKS
                or repo_name.endswith("-feedstock")
            ):
                self.set_status(404)
                self.write_error(404)
                return
            pull_request = False
            if "pull_request" in body["issue"]:
                pull_request = True
            if pull_request and action != "deleted":
                comment = body["comment"]["body"]
                comment_id = body["comment"]["id"]
                log_title_and_message_at_level(
                    level="info",
                    title=f"PR command: {body['repository']['full_name']}",
                )

                await tornado.ioloop.IOLoop.current().run_in_executor(
                    _worker_pool("command"),
                    commands.pr_comment,
                    owner,
                    repo_name,
                    issue_num,
                    comment,
                    comment_id,
                )
                return

            if not pull_request and action in [
                "opened",
                "edited",
                "created",
                "reopened",
            ]:
                title = body["issue"]["title"] if event == "issues" else ""
                if "comment" in body:
                    comment = body["comment"]["body"]
                    comment_id = body["comment"]["id"]
                else:
                    comment = body["issue"]["body"]
                    comment_id = -1  # will react to issue/PR description #issue_num

                log_title_and_message_at_level(
                    level="info",
                    title=f"issue command: {body['repository']['full_name']}",
                )

                await tornado.ioloop.IOLoop.current().run_in_executor(
                    _worker_pool("command"),
                    commands.issue_comment,
                    owner,
                    repo_name,
                    issue_num,
                    title,
                    comment,
                    comment_id,
                )
                return

        else:
            LOGGER.info(f'Unhandled event "{event}".')

        self.set_status(404)
        self.write_error(404)


def _get_current_versions():
    r = subprocess.run(
        ["conda", "list", "--json"],
        capture_output=True,
        check=True,
        encoding="utf-8",
    )
    out = json.loads(r.stdout)
    vers = {}
    for item in out:
        if item["name"] in WEBSERVICE_PKGS:
            vers[item["name"]] = item["version"]
    return vers


class UpdateWebservicesVersionsHandler(WriteErrorAsJSONRequestHandler):
    async def get(self):
        self.write(json.dumps(_get_current_versions()))


def _repo_exists(feedstock):
    r = requests.get(f"https://github.com/conda-forge/{feedstock}")
    if r.status_code != 200:
        return False
    else:
        return True


class OutputsValidationHandler(WriteErrorAsJSONRequestHandler):
    """This is a stub that we keep around so that old CI jobs still work
    if they have not bveen rerendered. We should remove it eventually."""

    async def post(self):
        self.write(json.dumps({"deprecated": True}))


def _dist_exists_on_prod_with_label_and_hash(dist, dest_label, hash_type, hash_value):
    import hmac
    import urllib.parse

    import binstar_client
    from conda_forge_webservices.feedstock_outputs import _get_ac_api_prod, PROD
    from conda_forge_webservices.utils import parse_conda_pkg

    ac = _get_ac_api_prod()

    try:
        _, name, version, _ = parse_conda_pkg(dist)
    except RuntimeError as e:
        LOGGER.critical(
            "    could not parse dist for existence check: %s",
            dist,
            exc_info=e,
        )
        return False

    try:
        data = ac.distribution(
            PROD,
            name,
            version,
            basename=urllib.parse.quote(dist, safe=""),
        )
        return (dest_label in data.get("labels", ())) and hmac.compare_digest(
            data[hash_type], hash_value
        )
    except binstar_client.errors.NotFound:
        return False


def _do_copy(
    feedstock,
    outputs,
    dest_label,
    git_sha,
    comment_on_error,
    hash_type,
    staging_label,
    start_time,
):
    valid, errors = validate_feedstock_outputs(
        feedstock,
        outputs,
        hash_type,
        dest_label,
    )

    outputs_to_copy = {k: v for k, v in outputs.items() if valid[k]}

    copied = {}
    if outputs_to_copy:
        for dist, hash_value in outputs_to_copy.items():
            with COPYLOCK:
                dist_copied, dist_errors = stage_dist_to_prod_and_relabel(
                    dist, dest_label, staging_label, hash_type, hash_value
                )
                errors.extend(dist_errors)
                copied[dist] = dist_copied
                if not dist_copied:
                    valid[dist] = False
                    errors.append(
                        f"failed to stage {dist} to "
                        f"conda-forge/label/{staging_label} and "
                        f"relabel to conda-forge/label/{dest_label}"
                    )

    for o in outputs:
        if o not in copied:
            copied[o] = False
        if o not in valid:
            valid[o] = False

    if not all(copied[o] for o in outputs) and comment_on_error:
        comment_on_outputs_copy(feedstock, git_sha, errors, valid, copied)

    return valid, errors, copied, time.time() - start_time


class OutputsCopyHandler(WriteErrorAsJSONRequestHandler):
    async def post(self):
        headers = self.request.headers
        feedstock_token = headers.get("FEEDSTOCK_TOKEN", None)
        data = tornado.escape.json_decode(self.request.body)
        feedstock = data.get("feedstock", None)
        outputs = data.get("outputs", None)
        # the anaconda-client calls labels (e.g., "main") "channels" internally
        # we did adopt that nomenclature in the API here, but that was a mistake
        # looking back on it. We have kept the API name, but internally in the code
        # we will use "channel" to mean a channel (e.g., conda-forge) and "label"
        # to mean a label (e.g., "main", "broken", etc.)
        label = data.get("channel", None)
        git_sha = data.get("git_sha", None)
        hash_type = data.get("hash_type", "md5")
        provider = data.get("provider", None)
        # the old default was to comment only if the git sha was not None
        # so we keep that here
        comment_on_error = data.get("comment_on_error", git_sha is not None)

        # uncomment this to turn off uploads
        # if feedstock not in [
        #     "staged-recipes",
        #     "cf-autotick-bot-test-package-feedstock",
        # ]:
        #     self.set_status(403)
        #     self.write_error(403)

        log_title_and_message_at_level(
            level="info",
            title=f"copy started for outputs for feedstock '{feedstock}'",
        )

        if feedstock is not None and len(feedstock) > 0:
            feedstock_exists = _repo_exists(feedstock)
        else:
            feedstock_exists = False

        valid_token = False
        if (
            feedstock_exists
            and feedstock_token is not None
            and len(feedstock_token) > 0
            and is_valid_feedstock_token(
                "conda-forge",
                feedstock,
                feedstock_token,
                provider=provider,
            )
        ):
            valid_token = True

        if (
            (not feedstock_exists)
            or outputs is None
            or label is None
            or (not valid_token)
            or hash_type not in ["md5", "sha256"]
        ):
            log_title_and_message_at_level(
                level="warning",
                title=f"invalid outputs copy request for feedstock '{feedstock}'",
                msg=f"""    feedstock exists: {feedstock_exists}
    outputs: {outputs}
    label: {label}
    valid token: {valid_token}
    hash type: {hash_type}
    provider: {provider}
""",
            )
            err_msgs = []
            if outputs is None:
                err_msgs.append("no outputs data sent for copy")
            if label is None:
                err_msgs.append("no label sent for copy")
            if not valid_token:
                err_msgs.append("invalid feedstock token")
            if hash_type not in ["md5", "sha256"]:
                err_msgs.append("invalid hash type")

            if feedstock_exists and comment_on_error:
                comment_on_outputs_copy(feedstock, git_sha, err_msgs, {}, {})

            self.set_status(403)
            self.write_error(403)
        else:
            staging_label = STAGING_LABEL + "-h" + uuid.uuid4().hex
            (
                valid,
                errors,
                copied,
                run_time,
            ) = await tornado.ioloop.IOLoop.current().run_in_executor(
                _worker_pool("upload"),
                _do_copy,
                feedstock,
                outputs,
                label,
                git_sha,
                comment_on_error,
                hash_type,
                staging_label,
                time.time(),
            )

            if not all(v for v in copied.values()):
                self.set_status(403)

            self.write(
                json.dumps(
                    {
                        "errors": errors,
                        "valid": valid,
                        "copied": copied,
                        "run_time": run_time,
                    }
                )
            )

            log_title_and_message_at_level(
                level="info",
                title=f"copy finished for outputs for feedstock '{feedstock}'",
                msg=f"""    feedstock exists: {feedstock_exists}
    errors: {errors}
    valid: {valid}
    copied: {copied}
    provider: {provider}
    run time: {run_time} (s)
""",
            )

        return

        # code to pass everything through
        # not used but can be to turn it all off if we need to
        # if outputs is not None and channel is not None:
        #     copied = await tornado.ioloop.IOLoop.current().run_in_executor(
        #         _worker_pool("upload"),
        #         copy_feedstock_outputs,
        #         outputs,
        #         dest_label,
        #     )
        #
        #     if not all(v for v in copied.values()):
        #         self.set_status(403)
        #
        #     if git_sha is not None and not all(copied[o] for o in outputs):
        #         comment_on_outputs_copy(
        #             feedstock, git_sha, ["some outputs did not copy"], {}, copied)
        #
        #     self.write(json.dumps(
        #         {"errors": ["some outputs did not copy"],
        #          "valid": {},
        #          "copied": copied}))
        #
        #     LOGGER.info("    errors: %s", ["some outputs did not copy"])
        #     LOGGER.info("    valid: %s", {})
        #     LOGGER.info("    copied: %s", copied)
        #
        # else:
        #     if git_sha is not None and feedstock is not None:
        #         comment_on_outputs_copy(
        #             feedstock, git_sha,
        #             ["invalid copy request (either bad data or bad feedstock token)"],
        #             {}, {}
        #         )
        #     self.set_status(403)
        #     self.write_error(403)
        #
        # return


@functools.lru_cache(maxsize=1)
def _cached_bot_workflow():
    if "AUTOTICK_BOT_GH_TOKEN" not in os.environ:
        return None

    gh = github.Github(auth=github.Auth.Token(os.environ["AUTOTICK_BOT_GH_TOKEN"]))
    repo = gh.get_repo("regro/cf-scripts")
    return repo.get_workflow("bot-events.yml")


def _dispatch_autotickbot_job(event, uid):
    wf = _cached_bot_workflow()
    if wf is None:
        LOGGER.info(
            "    autotick bot job dispatch skipped: event|uid = %s|%s - no token",
            event,
            uid,
        )
        return

    running = wf.create_dispatch(
        "main",
        inputs={
            "event": str(event),
            "uid": str(uid),
        },
    )
    LOGGER.info(
        "    autotick bot job dispatched: event|uid|running = %s|%s|%s",
        event,
        uid,
        running,
    )


class AutotickBotPayloadHookHandler(WriteErrorAsJSONRequestHandler):
    async def post(self):
        headers = self.request.headers
        event = headers.get("X-GitHub-Event", None)

        if not valid_request(
            self.request.body,
            headers.get("X-Hub-Signature", ""),
        ):
            self.set_status(403)
            self.write_error(403)
            return

        if event == "ping":
            self.write("pong")
            return

        body = tornado.escape.json_decode(self.request.body)
        if event == "pull_request":
            head_owner = body["pull_request"]["head"]["repo"]["full_name"]

            if (
                body["repository"]["full_name"].endswith("-feedstock")
                and (body["action"] in ["closed", "labeled"])
                and head_owner.startswith("regro-cf-autotick-bot/")
            ):
                log_title_and_message_at_level(
                    level="info",
                    title=f"autotick bot PR: {body['repository']['full_name']}",
                )
                _dispatch_autotickbot_job(
                    "pr",
                    body["pull_request"]["id"],
                )
            return
        elif event == "push":
            log_title_and_message_at_level(
                level="info",
                title=f"autotick bot push: {body['repository']['full_name']}",
            )

            repo_name = body["repository"]["name"]
            owner = body["repository"]["owner"]["login"]
            ref = body["ref"]
            commit_msg = (body.get("head_commit", None) or {}).get("message", "")

            # Only do anything if we are working with conda-forge, and a
            # push to main.
            if (
                # this weird thing happens with master to main branch changes maybe?
                body["after"] != "0000000000000000000000000000000000000000"
                and owner == "conda-forge"
                and (ref == "refs/heads/master" or ref == "refs/heads/main")
                and "[cf admin skip feedstocks]" not in commit_msg
                and "[cf admin skip]" not in commit_msg
                and repo_name.endswith("-feedstock")
            ):
                _dispatch_autotickbot_job(
                    "push",
                    repo_name.split("-feedstock")[0],
                )

            return
        else:
            LOGGER.info(f'Unhandled event "{event}".')

        self.set_status(404)
        self.write_error(404)


def _dispatch_automerge_job(repo, sha):
    gh = get_gh_client()

    skip_test_pr = False
    if repo == "cf-autotick-bot-test-package-feedstock":
        gh_repo = gh.get_repo("conda-forge/cf-autotick-bot-test-package-feedstock")
        for pr in gh_repo.get_pulls():
            if pr.head.sha == sha:
                if pr.head.ref.startswith("automerge-live-test-head-branch-"):
                    skip_test_pr = True
                break

    if not skip_test_pr:
        uid = uuid.uuid4().hex
        ref = __version__.replace("+", ".")
        workflow = gh.get_repo("conda-forge/conda-forge-webservices").get_workflow(
            "automerge.yml"
        )
        running = workflow.create_dispatch(
            ref=ref,
            inputs={
                "repo": repo,
                "sha": sha,
                "uuid": uid,
            },
        )

        if running:
            LOGGER.info(
                "    automerge job dispatched: conda-forge/%s@%s [uuid=%s]",
                repo,
                sha,
                uid,
            )
        else:
            LOGGER.info("    automerge job dispatch failed")
    else:
        LOGGER.info(
            "    automerge job dispatch skipped for testing: conda-forge/%s@%s",
            repo,
            sha,
        )


class StatusMonitorPayloadHookHandler(WriteErrorAsJSONRequestHandler):
    async def post(self):
        headers = self.request.headers
        event = headers.get("X-GitHub-Event", None)

        if not valid_request(
            self.request.body,
            headers.get("X-Hub-Signature", ""),
        ):
            self.set_status(403)
            self.write_error(403)
            return

        if event == "ping":
            self.write("pong")
            return

        body = tornado.escape.json_decode(self.request.body)
        if event == "check_run":
            log_title_and_message_at_level(
                level="info",
                title=f"check run: {body['repository']['full_name']}",
            )
            inject_app_token_into_feedstock(body["repository"]["full_name"])
            inject_app_token_into_feedstock_readonly(body["repository"]["full_name"])
            async with STATUS_DATA_LOCK:
                status_monitor.update_data_check_run(body)

            return
        elif event == "check_suite":
            inject_app_token_into_feedstock(body["repository"]["full_name"])
            inject_app_token_into_feedstock_readonly(body["repository"]["full_name"])

            log_title_and_message_at_level(
                level="info",
                title=f"check suite: {body['repository']['full_name']}",
            )

            if body["action"] == "completed" and body["repository"][
                "full_name"
            ].endswith("-feedstock"):
                _dispatch_automerge_job(
                    body["repository"]["name"],
                    body["check_suite"]["head_sha"],
                )

            return
        elif event == "status":
            log_title_and_message_at_level(
                level="info",
                title=f"status: {body['repository']['full_name']}",
            )
            inject_app_token_into_feedstock(body["repository"]["full_name"])
            inject_app_token_into_feedstock_readonly(body["repository"]["full_name"])
            async with STATUS_DATA_LOCK:
                status_monitor.update_data_status(body)

            if body["repository"]["full_name"].endswith("-feedstock"):
                _dispatch_automerge_job(
                    body["repository"]["name"],
                    body["sha"],
                )

            return
        elif event in ["pull_request", "pull_request_review"]:
            log_title_and_message_at_level(
                level="info",
                title=(
                    "pull request/pull request review: "
                    f"{body['repository']['full_name']}"
                ),
            )

            if body["repository"]["full_name"].endswith("-feedstock"):
                _dispatch_automerge_job(
                    body["repository"]["name"],
                    body["pull_request"]["head"]["sha"],
                )
            return
        else:
            LOGGER.info(f'Unhandled event "{event}".')

        self.set_status(404)
        self.write_error(404)


class StatusMonitorAzureHandler(WriteErrorAsJSONRequestHandler):
    async def get(self):
        self.add_header("Access-Control-Allow-Origin", "*")
        self.write(status_monitor.get_azure_status())


class StatusMonitorOpenGPUServerHandler(WriteErrorAsJSONRequestHandler):
    async def get(self):
        self.add_header("Access-Control-Allow-Origin", "*")
        self.write(status_monitor.get_open_gpu_server_status())


class StatusMonitorDockerHandler(WriteErrorAsJSONRequestHandler):
    async def get(self):
        self.add_header("Access-Control-Allow-Origin", "*")
        self.write(status_monitor.get_docker_status())


class StatusMonitorDBHandler(WriteErrorAsJSONRequestHandler):
    async def get(self):
        self.add_header("Access-Control-Allow-Origin", "*")
        self.write(status_monitor.dump_report_data())


class StatusMonitorReportHandler(WriteErrorAsJSONRequestHandler):
    async def get(self, name):
        self.add_header("Access-Control-Allow-Origin", "*")
        self.write(status_monitor.dump_report_data(name=name))


class StatusMonitorHandler(WriteErrorAsJSONRequestHandler):
    async def get(self):
        self.write(status_monitor.render_status_index())


class AliveHandler(WriteErrorAsJSONRequestHandler):
    async def get(self):
        self.add_header("Access-Control-Allow-Origin", "*")
        self.write(json.dumps({"status": "operational"}))


class UpdateTeamsEndpointHandler(WriteErrorAsJSONRequestHandler):
    async def post(self):
        headers = self.request.headers
        true_token = os.environ["CF_WEBSERVICES_TOKEN"].encode("utf-8")
        header_token = headers.get("CF_WEBSERVICES_TOKEN", None)

        if header_token is not None and hmac.compare_digest(
            header_token.encode("utf-8"), true_token
        ):
            data = tornado.escape.json_decode(self.request.body)
            feedstock = data.get("feedstock", None)

            if feedstock is not None:
                log_title_and_message_at_level(
                    level="info",
                    title=f"update teams endpoint: conda-forge/{feedstock}",
                )

                await tornado.ioloop.IOLoop.current().run_in_executor(
                    _thread_pool(),  # always threads due to expensive lru_cache
                    update_teams.update_team,
                    "conda-forge",
                    feedstock,
                    None,
                )
                return

        self.set_status(404)
        self.write_error(404)


def create_webapp():
    application = tornado.web.Application(
        [
            (r"/conda-linting/org-hook", LintingHookHandler),
            (r"/conda-forge-feedstocks/org-hook", UpdateFeedstockHookHandler),
            (r"/conda-forge-teams/org-hook", UpdateTeamHookHandler),
            (r"/conda-forge-teams/update", UpdateTeamsEndpointHandler),
            (r"/conda-forge-command/org-hook", CommandHookHandler),
            (r"/conda-webservice-update/versions", UpdateWebservicesVersionsHandler),
            (r"/feedstock-outputs/validate", OutputsValidationHandler),
            (r"/feedstock-outputs/copy", OutputsCopyHandler),
            (r"/autotickbot/payload", AutotickBotPayloadHookHandler),
            (r"/status-monitor/payload", StatusMonitorPayloadHookHandler),
            (r"/status-monitor/azure", StatusMonitorAzureHandler),
            (r"/status-monitor/open-gpu-server", StatusMonitorOpenGPUServerHandler),
            (r"/status-monitor/docker", StatusMonitorDockerHandler),
            (r"/status-monitor/db", StatusMonitorDBHandler),
            (r"/status-monitor/report/(.*)", StatusMonitorReportHandler),
            (r"/status-monitor", StatusMonitorHandler),
            (r"/alive", AliveHandler),
        ]
    )
    return application


async def _cache_data():
    if "CF_WEBSERVICES_TEST" not in os.environ:
        log_title_and_message_at_level(
            level="info",
            title="caching status data",
        )
        async with STATUS_DATA_LOCK:
            await tornado.ioloop.IOLoop.current().run_in_executor(
                _thread_pool(),
                status_monitor.cache_status_data,
            )


async def _print_token_info():
    await tornado.ioloop.IOLoop.current().run_in_executor(
        _thread_pool(),
        _print_rate_limiting_info,
    )


def main():
    # start logging and reset the log format to make it a bit easier to read
    tornado.log.enable_pretty_logging()
    from tornado.log import LogFormatter

    my_log_formatter = LogFormatter(fmt="%(message)s", color=True)
    root_logger = logging.getLogger()
    root_streamhandler = root_logger.handlers[0]
    root_streamhandler.setFormatter(my_log_formatter)

    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--local",
        help="run the webserver locally on 127.0.0.1:5000",
        action="store_true",
    )
    args = parser.parse_args()

    application = create_webapp()
    http_server = tornado.httpserver.HTTPServer(application, xheaders=True)
    port = int(os.environ.get("PORT", 5000))

    LOGGER.info(
        "starting server - conda-forge-webservices "
        f"version {conda_forge_webservices._version.__version__}"
    )

    if args.local:
        LOGGER.info("server address: http://127.0.0.1:5000/")
        http_server.listen(5000, address="127.0.0.1")
    else:
        http_server.listen(port)

    pcb = tornado.ioloop.PeriodicCallback(
        lambda: asyncio.create_task(_cache_data()),
        status_monitor.TIME_INTERVAL * 1000,  # in ms
    )
    pcb.start()

    ptk = tornado.ioloop.PeriodicCallback(
        lambda: asyncio.create_task(_print_token_info()),
        60 * 5 * 1000,  # five minutes in ms
    )
    ptk.start()

    tornado.ioloop.IOLoop.instance().start()


if __name__ == "__main__":
    main()
