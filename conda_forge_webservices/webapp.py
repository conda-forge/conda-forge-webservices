import os
import subprocess
import asyncio
import tornado.escape
import tornado.httpserver
import tornado.ioloop
import tornado.web
import tornado.locks
import hmac
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import atexit

# import functools
import logging

import requests
import github
from datetime import datetime, timezone

import conda_forge_webservices.linting as linting
import conda_forge_webservices.feedstocks_service as feedstocks_service
import conda_forge_webservices.update_teams as update_teams
import conda_forge_webservices.commands as commands
from conda_forge_webservices.update_me import WEBSERVICE_PKGS
from conda_forge_webservices.feedstock_outputs import (
    validate_feedstock_outputs,
    copy_feedstock_outputs,
    is_valid_feedstock_token,
    comment_on_outputs_copy,
)
from conda_forge_webservices.utils import ALLOWED_CMD_NON_FEEDSTOCKS
from conda_forge_webservices import status_monitor
from conda_forge_webservices.tokens import get_app_token_for_webservices_only

STATUS_DATA_LOCK = tornado.locks.Lock()

LOGGER = logging.getLogger("conda_forge_webservices")

POOL = None


def _worker_pool():
    global POOL
    if POOL is None:
        if "PYTEST_CURRENT_TEST" in os.environ:
            # needed for mocks in testing
            POOL = ThreadPoolExecutor(max_workers=2)
        else:
            POOL = ProcessPoolExecutor(max_workers=2)
    return POOL


def _shutdown_worker_pool():
    global POOL
    if POOL is not None:
        POOL.shutdown(wait=False)


atexit.register(_shutdown_worker_pool)


THREAD_POOL = None


def _thread_pool():
    global THREAD_POOL
    if THREAD_POOL is None:
        THREAD_POOL = ThreadPoolExecutor(max_workers=2)
    return THREAD_POOL


def _shutdown_thread_pool():
    global THREAD_POOL
    if THREAD_POOL is not None:
        THREAD_POOL.shutdown(wait=False)


atexit.register(_shutdown_thread_pool)


def get_commit_message(full_name, commit):
    return (
        github.Github(os.environ["GH_TOKEN"])
        .get_repo(full_name)
        .get_commit(commit)
        .commit.message
    )


def print_rate_limiting_info_for_token(token):
    # Compute some info about our GitHub API Rate Limit.
    # Note that it doesn't count against our limit to
    # get this info. So, we should be doing this regularly
    # to better know when it is going to run out. Also,
    # this will help us better understand where we are
    # spending it and how to better optimize it.

    # Get GitHub API Rate Limit usage and total
    gh = github.Github(token)
    gh_api_remaining = gh.get_rate_limit().core.remaining
    gh_api_total = gh.get_rate_limit().core.limit

    try:
        user = gh.get_user().login
    except Exception:
        user = "conda-forge-webservices[bot]"

    # Compute time until GitHub API Rate Limit reset
    gh_api_reset_time = gh.get_rate_limit().core.reset
    gh_api_reset_time -= datetime.now(timezone.utc)
    msg = "{user} - remaining {remaining} out of {total}.".format(
        remaining=gh_api_remaining,
        total=gh_api_total,
        user=user,
    )
    LOGGER.info(
        "github api requests: %s - %s",
        msg,
        "Will reset in {time}.".format(time=gh_api_reset_time),
    )


def print_rate_limiting_info():
    d = [os.environ["GH_TOKEN"], get_app_token_for_webservices_only()]

    LOGGER.info("")
    LOGGER.info("GitHub API Rate Limit Info:")
    for k in d:
        print_rate_limiting_info_for_token(k)
    LOGGER.info("")


def valid_request(body, signature):
    our_hash = hmac.new(
        os.environ["CF_WEBSERVICES_TOKEN"].encode("utf-8"),
        body,
        hashlib.sha1,
    ).hexdigest()

    their_hash = signature.split("=")[1]

    return hmac.compare_digest(their_hash, our_hash)


class LintingHookHandler(tornado.web.RequestHandler):
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
                LOGGER.info("")
                LOGGER.info("===================================================")
                LOGGER.info("linting: %s", body["repository"]["full_name"])
                LOGGER.info("===================================================")

                lint_info = await tornado.ioloop.IOLoop.current().run_in_executor(
                    _worker_pool(),
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
            print_rate_limiting_info()
        else:
            LOGGER.info('Unhandled event "{}".'.format(event))
            self.set_status(404)
            self.write_error(404)


class UpdateFeedstockHookHandler(tornado.web.RequestHandler):
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
                LOGGER.info("")
                LOGGER.info("===================================================")
                LOGGER.info("feedstocks service: %s", body["repository"]["full_name"])
                LOGGER.info("===================================================")
                handled = await tornado.ioloop.IOLoop.current().run_in_executor(
                    _worker_pool(),
                    feedstocks_service.handle_feedstock_event,
                    owner,
                    repo_name,
                )
                if handled:
                    return
        else:
            LOGGER.info('Unhandled event "{}".'.format(event))
        self.set_status(404)
        self.write_error(404)


class UpdateTeamHookHandler(tornado.web.RequestHandler):
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
                LOGGER.info("")
                LOGGER.info("===================================================")
                LOGGER.info("updating team: %s", body["repository"]["full_name"])
                LOGGER.info("===================================================")
                await tornado.ioloop.IOLoop.current().run_in_executor(
                    _thread_pool(),  # always threads due to expensive lru_cache
                    update_teams.update_team,
                    owner,
                    repo_name,
                    commit,
                )
                print_rate_limiting_info()
                return
        else:
            LOGGER.info('Unhandled event "{}".'.format(event))

        self.set_status(404)
        self.write_error(404)


class CommandHookHandler(tornado.web.RequestHandler):
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
                LOGGER.info("")
                LOGGER.info("===================================================")
                LOGGER.info("PR command: %s", body["repository"]["full_name"])
                LOGGER.info("===================================================")

                await tornado.ioloop.IOLoop.current().run_in_executor(
                    _worker_pool(),
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
                print_rate_limiting_info()
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
                LOGGER.info("")
                LOGGER.info("===================================================")
                LOGGER.info("PR command: %s", body["repository"]["full_name"])
                LOGGER.info("===================================================")

                await tornado.ioloop.IOLoop.current().run_in_executor(
                    _worker_pool(),
                    commands.pr_comment,
                    owner,
                    repo_name,
                    issue_num,
                    comment,
                    comment_id,
                )
                print_rate_limiting_info()
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

                LOGGER.info("")
                LOGGER.info("===================================================")
                LOGGER.info("issue command: %s", body["repository"]["full_name"])
                LOGGER.info("===================================================")

                await tornado.ioloop.IOLoop.current().run_in_executor(
                    _worker_pool(),
                    commands.issue_comment,
                    owner,
                    repo_name,
                    issue_num,
                    title,
                    comment,
                    comment_id,
                )
                print_rate_limiting_info()
                return

        else:
            LOGGER.info('Unhandled event "{}".'.format(event))

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


class UpdateWebservicesVersionsHandler(tornado.web.RequestHandler):
    async def get(self):
        self.write(json.dumps(_get_current_versions()))


def _repo_exists(feedstock):
    r = requests.get("https://github.com/conda-forge/%s" % feedstock)
    if r.status_code != 200:
        return False
    else:
        return True


class OutputsValidationHandler(tornado.web.RequestHandler):
    """This is a stub that we keep around so that old CI jobs still work
    if they have not bveen rerendered. We should remove it eventually."""

    async def post(self):
        self.write(json.dumps({"deprecated": True}))


def _do_copy(feedstock, outputs, channel, git_sha, comment_on_error, hash_type):
    valid, errors = validate_feedstock_outputs(
        feedstock,
        outputs,
        hash_type,
    )

    outputs_to_copy = {}
    for o in valid:
        if valid[o]:
            outputs_to_copy[o] = outputs[o]

    if outputs_to_copy:
        copied = copy_feedstock_outputs(
            outputs_to_copy,
            channel,
            delete=False,
        )

        # send for github releases copy
        # if False:
        #     try:
        #         gh = github.Github(os.environ["GH_TOKEN"])
        #         repo = gh.get_repo("conda-forge/repodata-shards")
        #         for dist in copied:
        #             if not copied[dist]:
        #                 continue
        #
        #             _subdir, _pkg = os.path.split(dist)
        #
        #             if channel == "main":
        #                 _url = f"https://conda.anaconda.org/cf-staging/{dist}"
        #             else:
        #                 _url = (
        #                     "https://conda.anaconda.org/cf-staging/label/"
        #                     + f"{channel}/{dist}"
        #                 )
        #
        #             repo.create_repository_dispatch(
        #                 "release",
        #                 {
        #                     "artifact_url": _url,
        #                     "md5": outputs_to_copy[dist],
        #                     "subdir": _subdir,
        #                     "package": _pkg,
        #                     "url": _url,
        #                     "feedstock": feedstock,
        #                     "label": channel,
        #                     "git_sha": git_sha,
        #                     "comment_on_error": comment_on_error,
        #                 }
        #             )
        #             LOGGER.info("    artifact %s sent for copy", dist)
        #     except Exception as e:
        #         LOGGER.info(
        #             "    repo dispatch for artifact copy failed: %s", repr(e)
        #         )
    else:
        copied = {}

    for o in outputs:
        if o not in copied:
            copied[o] = False

    if not all(copied[o] for o in outputs) and comment_on_error:
        comment_on_outputs_copy(feedstock, git_sha, errors, valid, copied)

    return valid, errors, copied


class OutputsCopyHandler(tornado.web.RequestHandler):
    async def post(self):
        headers = self.request.headers
        feedstock_token = headers.get("FEEDSTOCK_TOKEN", None)
        data = tornado.escape.json_decode(self.request.body)
        feedstock = data.get("feedstock", None)
        outputs = data.get("outputs", None)
        channel = data.get("channel", None)
        git_sha = data.get("git_sha", None)
        hash_type = data.get("hash_type", "md5")
        provider = data.get("provider", None)
        # the old default was to comment only if the git sha was not None
        # so we keep that here
        comment_on_error = data.get("comment_on_error", git_sha is not None)

        LOGGER.info("")
        LOGGER.info("===================================================")
        LOGGER.info("copy outputs for feedstock '%s'" % feedstock)
        LOGGER.info("===================================================")

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
            or channel is None
            or (not valid_token)
            or hash_type not in ["md5", "sha256"]
        ):
            LOGGER.warning("    invalid outputs copy request for %s!" % feedstock)
            LOGGER.warning("    feedstock exists: %s" % feedstock_exists)
            LOGGER.warning("    outputs: %s" % outputs)
            LOGGER.warning("    channel: %s" % channel)
            LOGGER.warning("    valid token: %s" % valid_token)
            LOGGER.warning("    hash type: %s" % hash_type)
            LOGGER.warning("    provider: %s" % provider)

            err_msgs = []
            if outputs is None:
                err_msgs.append("no outputs data sent for copy")
            if channel is None:
                err_msgs.append("no channel sent for copy")
            if not valid_token:
                err_msgs.append("invalid feedstock token")
            if hash_type not in ["md5", "sha256"]:
                err_msgs.append("invalid hash type")

            if feedstock_exists and comment_on_error:
                comment_on_outputs_copy(feedstock, git_sha, err_msgs, {}, {})

            self.set_status(403)
            self.write_error(403)
        else:
            (
                valid,
                errors,
                copied,
            ) = await tornado.ioloop.IOLoop.current().run_in_executor(
                _worker_pool(),
                _do_copy,
                feedstock,
                outputs,
                channel,
                git_sha,
                comment_on_error,
                hash_type,
            )

            if not all(v for v in copied.values()):
                self.set_status(403)

            self.write(json.dumps({"errors": errors, "valid": valid, "copied": copied}))

            LOGGER.info("    errors: %s", errors)
            LOGGER.info("    valid: %s", valid)
            LOGGER.info("    copied: %s", copied)
            LOGGER.info("    provider: %s" % provider)

        print_rate_limiting_info()

        return

        # code to pass everything through
        # not used but can be to turn it all off if we need to
        # if outputs is not None and channel is not None:
        #     copied = await tornado.ioloop.IOLoop.current().run_in_executor(
        #         _worker_pool(),
        #         copy_feedstock_outputs,
        #         outputs,
        #         channel,
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


class StatusMonitorPayloadHookHandler(tornado.web.RequestHandler):
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
            LOGGER.info("")
            LOGGER.info("===================================================")
            LOGGER.info("check run: %s", body["repository"]["full_name"])
            LOGGER.info("===================================================")
            async with STATUS_DATA_LOCK:
                status_monitor.update_data_check_run(body)

            return
        elif event == "check_suite":
            self.write(event)
            return
        elif event == "status":
            LOGGER.info("")
            LOGGER.info("===================================================")
            LOGGER.info("status: %s", body["repository"]["full_name"])
            LOGGER.info("===================================================")
            async with STATUS_DATA_LOCK:
                status_monitor.update_data_status(body)

            return
        else:
            LOGGER.info('Unhandled event "{}".'.format(event))

        self.set_status(404)
        self.write_error(404)


class StatusMonitorAzureHandler(tornado.web.RequestHandler):
    async def get(self):
        self.add_header("Access-Control-Allow-Origin", "*")
        self.write(status_monitor.get_azure_status())


class StatusMonitorOpenGPUServerHandler(tornado.web.RequestHandler):
    async def get(self):
        self.add_header("Access-Control-Allow-Origin", "*")
        self.write(status_monitor.get_open_gpu_server_status())


class StatusMonitorDBHandler(tornado.web.RequestHandler):
    async def get(self):
        self.add_header("Access-Control-Allow-Origin", "*")
        self.write(status_monitor.dump_report_data())


class StatusMonitorReportHandler(tornado.web.RequestHandler):
    async def get(self, name):
        self.add_header("Access-Control-Allow-Origin", "*")
        self.write(status_monitor.dump_report_data(name=name))


class StatusMonitorHandler(tornado.web.RequestHandler):
    async def get(self):
        self.write(status_monitor.render_status_index())


class AliveHandler(tornado.web.RequestHandler):
    async def get(self):
        self.add_header("Access-Control-Allow-Origin", "*")
        self.write(json.dumps({"status": "operational"}))


def create_webapp():
    application = tornado.web.Application(
        [
            (r"/conda-linting/org-hook", LintingHookHandler),
            (r"/conda-forge-feedstocks/org-hook", UpdateFeedstockHookHandler),
            (r"/conda-forge-teams/org-hook", UpdateTeamHookHandler),
            (r"/conda-forge-command/org-hook", CommandHookHandler),
            (r"/conda-webservice-update/versions", UpdateWebservicesVersionsHandler),
            (r"/feedstock-outputs/validate", OutputsValidationHandler),
            (r"/feedstock-outputs/copy", OutputsCopyHandler),
            (r"/status-monitor/payload", StatusMonitorPayloadHookHandler),
            (r"/status-monitor/azure", StatusMonitorAzureHandler),
            (r"/status-monitor/open-gpu-server", StatusMonitorOpenGPUServerHandler),
            (r"/status-monitor/db", StatusMonitorDBHandler),
            (r"/status-monitor/report/(.*)", StatusMonitorReportHandler),
            (r"/status-monitor", StatusMonitorHandler),
            (r"/alive", AliveHandler),
        ]
    )
    return application


async def _cache_data():
    if "CF_WEBSERVICES_TEST" not in os.environ:
        LOGGER.info("")
        LOGGER.info("===================================================")
        LOGGER.info("caching status data")
        LOGGER.info("===================================================")
        async with STATUS_DATA_LOCK:
            await tornado.ioloop.IOLoop.current().run_in_executor(
                _thread_pool(),
                status_monitor.cache_status_data,
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

    LOGGER.info("starting server")

    if args.local:
        LOGGER.info("server address: http://127.0.0.1:5000/")
        http_server.listen(5000, address="127.0.0.1")
    else:
        http_server.listen(port)

    pcb = tornado.ioloop.PeriodicCallback(
        lambda: asyncio.create_task(_cache_data()),
        status_monitor.TIME_INTERVAL * 1000,
    )
    pcb.start()

    tornado.ioloop.IOLoop.instance().start()


if __name__ == "__main__":
    main()
