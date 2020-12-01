import os
import tornado.escape
import tornado.httpserver
import tornado.ioloop
import tornado.web
import hmac
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import atexit
# import functools
import logging

import requests
import github
from datetime import datetime

import conda_forge_webservices.linting as linting
import conda_forge_webservices.feedstocks_service as feedstocks_service
import conda_forge_webservices.update_teams as update_teams
import conda_forge_webservices.commands as commands
from conda_forge_webservices.update_me import get_current_versions
from conda_forge_webservices.feedstock_outputs import (
    validate_feedstock_outputs,
    copy_feedstock_outputs,
    is_valid_feedstock_token,
    comment_on_outputs_copy,
)

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
        github.Github(os.environ['GH_TOKEN'])
        .get_repo(full_name)
        .get_commit(commit)
        .commit
        .message)


def print_rate_limiting_info_for_token(token, user):
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

    # Compute time until GitHub API Rate Limit reset
    gh_api_reset_time = gh.get_rate_limit().core.reset
    gh_api_reset_time -= datetime.utcnow()
    msg = "{user} - remaining {remaining} out of {total}.".format(
        remaining=gh_api_remaining,
        total=gh_api_total, user=user,
    )
    LOGGER.info(
        "github api requests: %s - %s",
        msg,
        "Will reset in {time}.".format(time=gh_api_reset_time)
    )


def print_rate_limiting_info():

    d = [(os.environ['GH_TOKEN'], "conda-forge-linter")]

    LOGGER.info("")
    LOGGER.info("GitHub API Rate Limit Info:")
    for k, v in d:
        print_rate_limiting_info_for_token(k, v)
    LOGGER.info("")


def valid_request(body, signature):
    our_hash = hmac.new(
        os.environ['CF_WEBSERVICES_TOKEN'].encode('utf-8'),
        body,
        hashlib.sha1,
    ).hexdigest()

    their_hash = signature.split("=")[1]

    return hmac.compare_digest(their_hash, our_hash)


class LintingHookHandler(tornado.web.RequestHandler):
    async def post(self):
        headers = self.request.headers
        event = headers.get('X-GitHub-Event', None)

        if not valid_request(
            self.request.body,
            headers.get('X-Hub-Signature', ''),
        ):
            self.set_status(403)
            self.write_error(403)
            return

        if event == 'ping':
            self.write('pong')
        elif event == 'pull_request':
            body = tornado.escape.json_decode(self.request.body)
            repo_name = body['repository']['name']
            owner = body['repository']['owner']['login']
            pr_id = int(body['pull_request']['number'])
            is_open = body['pull_request']['state'] == 'open'

            if (
                owner != 'conda-forge' or
                not (
                    repo_name == 'staged-recipes' or
                    repo_name.endswith("-feedstock")
                )
            ):
                self.set_status(404)
                self.write_error(404)
                return

            if body["action"] not in ["opened", "reopened", "synchronize", "unlocked"]:
                return

            if repo_name == 'staged-recipes':
                stale = any(
                    label['name'] == 'stale'
                    for label in body['pull_request']['labels']
                )
            else:
                stale = False

            # Only do anything if we are working with conda-forge,
            # and an open PR.
            if is_open and owner == 'conda-forge' and not stale:
                LOGGER.info("")
                LOGGER.info("===================================================")
                LOGGER.info("linting: %s", body['repository']['full_name'])
                LOGGER.info("===================================================")

                lint_info = await tornado.ioloop.IOLoop.current().run_in_executor(
                    _worker_pool(),
                    linting.compute_lint_message,
                    owner,
                    repo_name,
                    pr_id,
                    repo_name == 'staged-recipes',
                )
                if lint_info:
                    msg = linting.comment_on_pr(
                        owner,
                        repo_name,
                        pr_id,
                        lint_info['message'],
                        search='conda-forge-linting service',
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
        event = headers.get('X-GitHub-Event', None)

        if not valid_request(
            self.request.body,
            headers.get('X-Hub-Signature', ''),
        ):
            self.set_status(403)
            self.write_error(403)
            return

        if event == 'ping':
            self.write('pong')
            return
        elif event == 'push':
            body = tornado.escape.json_decode(self.request.body)
            repo_name = body['repository']['name']
            owner = body['repository']['owner']['login']
            ref = body['ref']
            commit = body.get('head_commit', {}).get('id', None)

            if commit:
                commit_msg = get_commit_message(
                    body['repository']['full_name'],
                    commit,
                )
            else:
                commit_msg = ""

            # Only do anything if we are working with conda-forge, and a
            # push to master.
            if (
                owner == 'conda-forge' and
                ref == "refs/heads/master" and
                "[cf admin skip feedstocks]" not in commit_msg and
                "[cf admin skip]" not in commit_msg
            ):
                LOGGER.info("")
                LOGGER.info("===================================================")
                LOGGER.info("feedstocks service: %s", body['repository']['full_name'])
                LOGGER.info("===================================================")
                handled = await tornado.ioloop.IOLoop.current().run_in_executor(
                    _worker_pool(),
                    feedstocks_service.handle_feedstock_event,
                    owner,
                    repo_name,
                )
                if handled:
                    print_rate_limiting_info()
                    return
        else:
            LOGGER.info('Unhandled event "{}".'.format(event))
        self.set_status(404)
        self.write_error(404)


class UpdateTeamHookHandler(tornado.web.RequestHandler):
    async def post(self):
        headers = self.request.headers
        event = headers.get('X-GitHub-Event', None)

        if not valid_request(
            self.request.body,
            headers.get('X-Hub-Signature', ''),
        ):
            self.set_status(403)
            self.write_error(403)
            return

        if event == 'ping':
            self.write('pong')
            return
        elif event == 'push':
            body = tornado.escape.json_decode(self.request.body)
            repo_name = body['repository']['name']
            owner = body['repository']['owner']['login']
            ref = body['ref']
            commit = body.get('head_commit', {}).get('id', None)

            if commit:
                commit_msg = get_commit_message(
                    body['repository']['full_name'],
                    commit,
                )
            else:
                commit_msg = ""

            # Only do anything if we are working with conda-forge,
            # and a push to master.
            if (
                owner == 'conda-forge' and
                repo_name.endswith("-feedstock") and
                ref == "refs/heads/master" and
                "[cf admin skip teams]" not in commit_msg and
                "[cf admin skip]" not in commit_msg
            ):
                LOGGER.info("")
                LOGGER.info("===================================================")
                LOGGER.info("updating team: %s", body['repository']['full_name'])
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
        headers = self.request.headers
        event = headers.get('X-GitHub-Event', None)

        if not valid_request(
            self.request.body,
            headers.get('X-Hub-Signature', ''),
        ):
            self.set_status(403)
            self.write_error(403)
            return

        if event == 'ping':
            self.write('pong')
            return
        elif (
            event == 'pull_request_review' or
            event == 'pull_request' or
            event == 'pull_request_review_comment'
        ):
            body = tornado.escape.json_decode(self.request.body)
            action = body["action"]
            repo_name = body['repository']['name']
            owner = body['repository']['owner']['login']
            # Only do anything if we are working with conda-forge
            if (
                owner != 'conda-forge' or
                not (
                    repo_name == "staged-recipes" or
                    repo_name.endswith("-feedstock")
                )
            ):
                self.set_status(404)
                self.write_error(404)
                return

            pr_repo = body['pull_request']['head']['repo']
            pr_owner = pr_repo['owner']['login']
            pr_repo = pr_repo['name']
            pr_branch = body['pull_request']['head']['ref']
            pr_num = body['pull_request']['number']
            comment = None
            if event == 'pull_request_review' and action != 'dismissed':
                comment = body['review']['body']
            elif (
                event == 'pull_request' and
                action in ['opened', 'edited', 'reopened']
            ):
                comment = body['pull_request']['body']
            elif (
                event == 'pull_request_review_comment' and
                action != 'deleted'
            ):
                comment = body['comment']['body']

            if comment:
                LOGGER.info("")
                LOGGER.info("===================================================")
                LOGGER.info("PR command: %s", body['repository']['full_name'])
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
                )
                print_rate_limiting_info()
                return

        elif event == 'issue_comment' or event == "issues":
            body = tornado.escape.json_decode(self.request.body)
            action = body["action"]
            repo_name = body['repository']['name']
            owner = body['repository']['owner']['login']
            issue_num = body['issue']['number']

            # Only do anything if we are working with conda-forge
            if (
                owner != 'conda-forge' or
                not (
                    repo_name == "staged-recipes" or
                    repo_name.endswith("-feedstock")
                )
            ):
                self.set_status(404)
                self.write_error(404)
                return
            pull_request = False
            if "pull_request" in body["issue"]:
                pull_request = True
            if pull_request and action != 'deleted':
                comment = body['comment']['body']
                LOGGER.info("")
                LOGGER.info("===================================================")
                LOGGER.info("PR command: %s", body['repository']['full_name'])
                LOGGER.info("===================================================")

                await tornado.ioloop.IOLoop.current().run_in_executor(
                    _worker_pool(),
                    commands.pr_comment,
                    owner,
                    repo_name,
                    issue_num,
                    comment,
                )
                print_rate_limiting_info()
                return

            if (
                not pull_request and
                action in ['opened', 'edited', 'created', 'reopened']
            ):
                title = body['issue']['title'] if event == "issues" else ""
                if 'comment' in body:
                    comment = body['comment']['body']
                else:
                    comment = body['issue']['body']

                LOGGER.info("")
                LOGGER.info("===================================================")
                LOGGER.info("issue command: %s", body['repository']['full_name'])
                LOGGER.info("===================================================")

                await tornado.ioloop.IOLoop.current().run_in_executor(
                    _worker_pool(),
                    commands.issue_comment,
                    owner,
                    repo_name,
                    issue_num,
                    title,
                    comment,
                )
                print_rate_limiting_info()
                return

        else:
            LOGGER.info('Unhandled event "{}".'.format(event))

        self.set_status(404)
        self.write_error(404)


class UpdateWebservicesVersionsHandler(tornado.web.RequestHandler):
    def get(self):
        self.write(json.dumps(get_current_versions()))


def _repo_exists(feedstock):
    r = requests.get("https://github.com/conda-forge/%s" % feedstock)
    if r.status_code != 200:
        return False
    else:
        return True


class OutputsValidationHandler(tornado.web.RequestHandler):
    """This is a stub that we keep around so that old CI jobs still work
    if they have not bveen rerendered. We should remove it eventually."""
    def post(self):
        self.write(json.dumps({"deprecated": True}))
        return


def _do_copy(feedstock, outputs, channel, git_sha):
    valid, errors = validate_feedstock_outputs(
        feedstock,
        outputs,
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

        # send artifact to be uploaded
        if "REGRO_GITHUB_TOKEN" in os.environ:
            try:
                gh = github.Github(os.environ["REGRO_GITHUB_TOKEN"])
                repo = gh.get_repo("regro/releases")
                for dist in copied:
                    if not copied[dist]:
                        continue

                    _subdir, _pkg = os.path.split(dist)

                    if channel == "main":
                        _url = f"https://conda.anaconda.org/cf-staging/{dist}"
                    else:
                        _url = (
                            "https://conda.anaconda.org/cf-staging/label/"
                            + f"{channel}/{dist}"
                        )

                    repo.create_repository_dispatch(
                        "release",
                        {
                            "subdir": _subdir,
                            "package": _pkg,
                            "url": _url,
                            "feedstock": feedstock,
                            "label": channel,
                            "md5": outputs_to_copy[dist],
                        }
                    )
            except Exception as e:
                LOGGER.info(e)

        # send for artifact validation
        try:
            gh = github.Github(os.environ["GH_TOKEN"])
            repo = gh.get_repo("conda-forge/artifact-validation")
            for dist in copied:
                if not copied[dist]:
                    continue

                if channel == "main":
                    _url = f"https://conda.anaconda.org/cf-staging/{dist}"
                else:
                    _url = (
                        "https://conda.anaconda.org/cf-staging/label/"
                        + f"{channel}/{dist}"
                    )

                repo.create_repository_dispatch(
                    "validate",
                    {
                        "artifact_url": _url,
                        "md5": outputs_to_copy[dist],
                    }
                )
        except Exception as e:
            LOGGER.info(e)
    else:
        copied = {}

    for o in outputs:
        if o not in copied:
            copied[o] = False

    if not all(copied[o] for o in outputs):
        comment_on_outputs_copy(
            feedstock, git_sha, errors, valid, copied)

    return valid, errors, copied


class OutputsCopyHandler(tornado.web.RequestHandler):
    async def post(self):
        headers = self.request.headers
        feedstock_token = headers.get('FEEDSTOCK_TOKEN', None)
        data = tornado.escape.json_decode(self.request.body)
        feedstock = data.get("feedstock", None)
        outputs = data.get("outputs", None)
        channel = data.get("channel", None)
        git_sha = data.get("git_sha", None)

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
                    "conda-forge", feedstock, feedstock_token
            )
        ):
            valid_token = True

        if (
            (not feedstock_exists)
            or outputs is None
            or channel is None
            or (not valid_token)
        ):
            LOGGER.warning('    invalid outputs copy request for %s!' % feedstock)
            LOGGER.warning('    feedstock exists: %s' % feedstock_exists)
            LOGGER.warning('    outputs: %s' % outputs)
            LOGGER.warning('    channel: %s' % channel)
            LOGGER.warning('    valid token: %s' % valid_token)

            err_msgs = []
            if outputs is None:
                err_msgs.append("no outputs data sent for copy")
            if channel is None:
                err_msgs.append("no channel sent for copy")
            if not valid_token:
                err_msgs.append("invalid feedstock token")

            if feedstock_exists:
                comment_on_outputs_copy(
                    feedstock, git_sha,
                    err_msgs,
                    {}, {}
                )

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
                git_sha
            )

            if not all(v for v in copied.values()):
                self.set_status(403)

            self.write(json.dumps(
                {"errors": errors, "valid": valid, "copied": copied}))

            LOGGER.info("    errors: %s", errors)
            LOGGER.info("    valid: %s", valid)
            LOGGER.info("    copied: %s", copied)

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


def create_webapp():
    application = tornado.web.Application([
        (r"/conda-linting/org-hook", LintingHookHandler),
        (r"/conda-forge-feedstocks/org-hook", UpdateFeedstockHookHandler),
        (r"/conda-forge-teams/org-hook", UpdateTeamHookHandler),
        (r"/conda-forge-command/org-hook", CommandHookHandler),
        (r"/conda-webservice-update/versions", UpdateWebservicesVersionsHandler),
        (r"/feedstock-outputs/validate", OutputsValidationHandler),
        (r"/feedstock-outputs/copy", OutputsCopyHandler),
    ])
    return application


def main():
    # start logging and reset the log format to make it a bit easier to read
    tornado.log.enable_pretty_logging()
    from tornado.log import LogFormatter
    my_log_formatter = LogFormatter(fmt='%(message)s', color=True)
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

    # https://devcenter.heroku.com/articles/optimizing-dyno-usage#python
    n_processes = int(os.environ.get("WEB_CONCURRENCY", 1))

    LOGGER.info("starting server w/ %d processes", n_processes)

    if args.local:
        LOGGER.info(
            "server address: http://127.0.0.1:5000/conda-webservice-update/versions")
        http_server.listen(5000, address='127.0.0.1')
    else:
        if n_processes != 1:
            # http://www.tornadoweb.org/en/stable/guide/running.html#processes-and-ports
            http_server.bind(port)
            http_server.start(n_processes)
        else:
            http_server.listen(port)

    tornado.ioloop.IOLoop.instance().start()


if __name__ == "__main__":
    main()
