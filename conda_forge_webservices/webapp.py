import os
import tornado.escape
import tornado.httpserver
import tornado.ioloop
import tornado.web

import requests
import os
from glob import glob
import tempfile
from git import Repo
import textwrap
import github
import conda_smithy.lint_recipe
import shutil
from contextlib import contextmanager


import conda_forge_webservices.linting as linting
import conda_forge_webservices.status as status
import conda_forge_webservices.update_teams as update_teams
import conda_forge_webservices.commands as commands


class RegisterHandler(tornado.web.RequestHandler):
    def get(self):
        token = os.environ.get('GH_TOKEN')
        headers = {'Authorization': 'token {}'.format(token)}

        url = 'https://api.github.com/repos/conda-forge/staged-recipes/hooks'

        payload = {
              "name": "web",
              "active": True,
              "events": [
                "pull_request"
              ],
              "config": {
                "url": "http://conda-linter.herokuapp.com/hook",
                "content_type": "json"
              }
            }

        r1 = requests.post(url, json=payload, headers=headers)

        url = 'https://api.github.com/repos/conda-forge/status/hooks'

        payload = {
              "name": "web",
              "active": True,
              "events": [
                "issues"
              ],
              "config": {
                "url": "http://conda-forge-status.herokuapp.com/hook",
                "content_type": "json"
              }
            }

        r2 = requests.post(url, json=payload, headers=headers)


class LintingHookHandler(tornado.web.RequestHandler):
    def post(self):
        headers = self.request.headers
        event = headers.get('X-GitHub-Event', None)

        if event == 'ping':
            self.write('pong')
        elif event == 'pull_request':
            body = tornado.escape.json_decode(self.request.body)
            repo_name = body['repository']['name']
            repo_url = body['repository']['clone_url']
            owner = body['repository']['owner']['login']
            pr_id = int(body['pull_request']['number'])
            is_open = body['pull_request']['state'] == 'open'

            # Only do anything if we are working with conda-forge, and an open PR.
            if is_open and owner == 'conda-forge':
                lint_info = linting.compute_lint_message(owner, repo_name, pr_id,
                                                         repo_name == 'staged-recipes')
                if lint_info:
                    msg = linting.comment_on_pr(owner, repo_name, pr_id, lint_info['message'])
                    linting.set_pr_status(owner, repo_name, lint_info, target_url=msg.html_url)
        else:
            print('Unhandled event "{}".'.format(event))
            self.set_status(404)
            self.write_error(404)


class StatusHookHandler(tornado.web.RequestHandler):
    def post(self):
        headers = self.request.headers
        event = headers.get('X-GitHub-Event', None)

        if event == 'ping':
            self.write('pong')
        elif event == 'issues' or event == 'issue_comment' or event == 'push':
            body = tornado.escape.json_decode(self.request.body)
            repo_full_name = body['repository']['full_name']

            # Only do something if it involves the status page
            if repo_full_name == 'conda-forge/status':
                status.update()
        else:
            print('Unhandled event "{}".'.format(event))
            self.set_status(404)
            self.write_error(404)


class UpdateTeamHookHandler(tornado.web.RequestHandler):
    def post(self):
        headers = self.request.headers
        event = headers.get('X-GitHub-Event', None)

        if event == 'ping':
            self.write('pong')
        elif event == 'push':
            body = tornado.escape.json_decode(self.request.body)
            repo_name = body['repository']['name']
            owner = body['repository']['owner']['login']
            ref = body['ref']
            # Only do anything if we are working with conda-forge, and a push to master.
            if owner == 'conda-forge' and ref == "refs/heads/master":
                update_teams.update_team(owner, repo_name)
        else:
            print('Unhandled event "{}".'.format(event))
            self.set_status(404)
            self.write_error(404)


class CommandHookHandler(tornado.web.RequestHandler):
    def post(self):
        headers = self.request.headers
        event = headers.get('X-GitHub-Event', None)

        if event == 'ping':
            self.write('pong')
        elif event == 'pull_request_review' or event == 'pull_request' \
            or event == 'pull_request_review_comment':
            body = tornado.escape.json_decode(self.request.body)
            action = body["action"]
            repo_name = body['repository']['name']
            owner = body['repository']['owner']['login']
            # Only do anything if we are working with conda-forge
            if owner != 'conda-forge':
                return
            pr_repo = body['pull_request']['head']['repo']
            pr_owner = pr_repo['owner']['login']
            pr_repo = pr_repo['name']
            pr_branch = body['pull_request']['head']['ref']
            pr_num = body['pull_request']['number']
            comment = None
            if event == 'pull_request_review' and action != 'dismissed':
                comment = body['review']['body']
            elif event == 'pull_request' and action in ['opened', 'edited', 'reopened']:
                comment = body['pull_request']['body']
            elif event == 'pull_request_review_comment' and action != 'deleted':
                comment = body['comment']['body']

            if comment:
                commands.pr_detailed_comment(owner, repo_name, pr_owner, pr_repo, pr_branch, pr_num, comment)

        elif event == 'issue_comment' or event == "issues":
            body = tornado.escape.json_decode(self.request.body)
            action = body["action"]
            repo_name = body['repository']['name']
            owner = body['repository']['owner']['login']
            issue_num = body['issue']['number']

            # Only do anything if we are working with conda-forge
            if owner != 'conda-forge':
                return
            pull_request = False
            if "pull_request" in body["issue"]:
                pull_request = True
            if pull_request and action != 'deleted':
                comment = body['comment']['body']
                commands.pr_comment(owner, repo_name, issue_num, comment)

            if not pull_request and action in ['opened', 'edited', 'created', 'reopened']:
                title = body['issue']['title'] if event == "issues" else ""
                comment = body['issue']['body']
                commands.issue_comment(owner, repo_name, issue_num, title, comment)

        else:
            print('Unhandled event "{}".'.format(event))
            self.set_status(404)
            self.write_error(404)


def create_webapp():
    application = tornado.web.Application([
        (r"/conda-linting/hook", LintingHookHandler),
        (r"/conda-forge-status/hook", StatusHookHandler),
        (r"/conda-forge-teams/hook", UpdateTeamHookHandler),
        (r"/conda-forge-command/hook", CommandHookHandler),
    ])
    return application


def main():
    application = create_webapp()
    http_server = tornado.httpserver.HTTPServer(application, xheaders=True)
    port = int(os.environ.get("PORT", 5000))

    # https://devcenter.heroku.com/articles/optimizing-dyno-usage#python
    n_processes = int(os.environ.get("WEB_CONCURRENCY", 1))

    if n_processes != 1:
        # http://www.tornadoweb.org/en/stable/guide/running.html#processes-and-ports
        http_server.bind(port)
        http_server.start(n_processes)
    else:
        http_server.listen(port)
    tornado.ioloop.IOLoop.instance().start()


if __name__ == "__main__":
    main()
