import os
import tempfile
import subprocess
import datetime
import pytz
import dateutil.parser
from ruamel.yaml import YAML
from ruamel.yaml.compat import StringIO
import requests
import json
import logging

import lxml.html
import cachetools

from conda_forge_webservices.tokens import get_app_token_for_webservices_only

LOGGER = logging.getLogger("conda_forge_webservices.status_monitor")

APP_DATA = {
    'azure-pipelines': {
        'repos': cachetools.LRUCache(maxsize=128),
        'rates': cachetools.LRUCache(maxsize=96),
    },
    'travis-ci': {
        'repos': cachetools.LRUCache(maxsize=128),
        'rates': cachetools.LRUCache(maxsize=96),
    },
    'github-actions': {
        'repos': cachetools.LRUCache(maxsize=128),
        'rates': cachetools.LRUCache(maxsize=96),
    },
    'appveyor': {
        'repos': cachetools.LRUCache(maxsize=128),
        'rates': cachetools.LRUCache(maxsize=96),
    },
    'circleci': {
        'repos': cachetools.LRUCache(maxsize=128),
        'rates': cachetools.LRUCache(maxsize=96),
    },
    'drone': {
        'repos': cachetools.LRUCache(maxsize=128),
        'rates': cachetools.LRUCache(maxsize=96),
    },
}

STATUS_UPDATE_DELAY = 60
NOSTATUS = 'No Status Available'
WEBS_STATUS_UPDATED = None
WEBS_STATUS_DATA = {
    'status': NOSTATUS,
    'updated_at': None,
}
START_TIME = datetime.datetime.fromisoformat("2020-01-01T00:00:00+00:00")
TIME_INTERVAL = 60*5  # five minutes


def _make_time_key(uptime):
    dt = uptime.timestamp() - START_TIME.timestamp()
    return int(dt // TIME_INTERVAL)


# reload the cache
RELOAD_CACHE = True


def _reload_cache():
    print(" ", flush=True)
    print("!!!!!!!!!!!!!! RELOADING THE CACHE !!!!!!!!!!!!!!", flush=True)

    global APP_DATA

    try:
        data = requests.get(
            ("https://raw.githubusercontent.com/conda-forge/"
             "conda-forge-status-monitor/"
             "main/data/latest.json")).json()
    except Exception as e:
        print(e, flush=True)
        data = None

    if data is not None:
        for slug in APP_DATA:
            print('reloading data for %s' % slug, flush=True)

            if slug not in data:
                continue
            else:
                _data = data[slug]

            for repo in _data['repos']:
                APP_DATA[slug]['repos'][repo] = _data['repos'][repo]

            for ts in _data['rates']:
                t = datetime.datetime.fromisoformat(ts).astimezone(pytz.UTC)
                key = _make_time_key(t)
                APP_DATA[slug]['rates'][key] = _data['rates'][ts]

            print("    reloaded %d repos" % len(APP_DATA[slug]['repos']), flush=True)
            print("    reloaded %d rates" % len(APP_DATA[slug]['rates']), flush=True)
    else:
        print("could not get app cache!", flush=True)
    print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!", flush=True)
    print(" ", flush=True)


if RELOAD_CACHE:
    _reload_cache()
    RELOAD_CACHE = False


class MyYAML(YAML):
    """dump yaml as string rippd from docs"""
    def dump(self, data, stream=None, **kw):
        inefficient = False
        if stream is None:
            inefficient = True
            stream = StringIO()
        YAML.dump(self, data, stream, **kw)
        if inefficient:
            return stream.getvalue()


def _make_est_from_time_key(key, iso=False):
    est = pytz.timezone('US/Eastern')
    fmt = '%Y-%m-%d %H:%M:%S %Z%z'
    dt = datetime.timedelta(seconds=key * TIME_INTERVAL)
    t = dt + START_TIME
    t = t.astimezone(est)
    if iso:
        return t.isoformat()
    else:
        return t.strftime(fmt)


def _make_report_data(iso=False):
    now = datetime.datetime.utcnow().replace(tzinfo=pytz.UTC)
    know = _make_time_key(now)

    report = {}
    for key in APP_DATA:
        rates = {}
        for k in range(know, know-96, -1):
            tstr = _make_est_from_time_key(k, iso=iso)
            rates[tstr] = APP_DATA[key]['rates'].get(k, 0)

        total = sum(v for v in rates.values())

        report[key] = {
            'total': total,
            'rates': rates,
            'repos': {
                k: v
                for k, v in
                sorted(APP_DATA[key]['repos'].items(), key=lambda x: x[1])[::-1]
            },
        }

    return report


def render_status_index():
    yaml = MyYAML()
    shell = """\
<!DOCTYPE html>
<html>
    <head>
        <title>conda-forge status data</title>
    </head>
    <body>
        <h2>conda-forge status data</h2>
        <pre>
%s
        </pre>
  </body>
</html>
""" % yaml.dump(_make_report_data(iso=False))
    return shell


def dump_report_data(name=None):
    data = _make_report_data(iso=True)
    if name is None:
        return json.dumps(data)
    else:
        return json.dumps(data[name])


def get_azure_status():
    status_data = {}

    # always update azure
    try:
        r = requests.get('https://status.dev.azure.com', timeout=2)
        if r.status_code != 200:
            status_data['azure'] = NOSTATUS
        else:
            s = json.loads(
                lxml
                .html
                .fromstring(r.content)
                .get_element_by_id('dataProviders')
                .text
            )

            def _rec_search(d):
                if isinstance(d, dict):
                    if 'health' in d and 'message' in d:
                        return d['message']
                    else:
                        for v in d.values():
                            if isinstance(v, dict):
                                val = _rec_search(v)
                                if val is not None:
                                    return val
                        return None
                else:
                    return None

            stat = _rec_search(s)

            if stat is None:
                stat = NOSTATUS

            status_data['status'] = stat
    except requests.exceptions.RequestException:
        status_data['status'] = NOSTATUS

    fmt = '%Y-%m-%d %H:%M:%S %Z%z'
    status_data['updated_at'] = (
        datetime.datetime.now().astimezone(pytz.UTC).strftime(fmt)
    )

    return json.dumps(status_data)


def get_open_gpu_server_status():
    status_data = {}
    try:
        r = requests.get(
            "https://api.openstatus.dev/public/status/open-gpu-server",
            timeout=2,
        )
        if r.status_code != 200:
            status_data["status"] = NOSTATUS
        else:
            status_data["status"] = r.json()["status"]
    except requests.exceptions.RequestException:
        status_data['status'] = NOSTATUS

    fmt = '%Y-%m-%d %H:%M:%S %Z%z'
    status_data['updated_at'] = (
        datetime.datetime.now().astimezone(pytz.UTC).strftime(fmt)
    )

    return json.dumps(status_data)


def update_data_status(event_data):
    global APP_DATA

    repo = event_data['repository']['full_name']

    if 'circleci' in event_data['context']:
        slug = 'circleci'
    elif 'appveyor' in event_data['context']:
        slug = 'appveyor'
    elif 'travis' in event_data['context']:
        slug = 'travis-ci'
    elif 'drone' in event_data['context']:
        slug = 'drone'
    else:
        LOGGER.warning("    context not found: %s", event_data['context'])
        return

    LOGGER.debug("    repo: %s", repo)
    LOGGER.debug("    app: %s", slug)
    LOGGER.debug("    state: %s", event_data['state'])

    if event_data['state'] in ['success', 'failure', 'error']:

        LOGGER.debug("    updated_at: %s", event_data['updated_at'])

        uptime = dateutil.parser.isoparse(event_data['updated_at'])
        interval = _make_time_key(uptime)
        if interval not in APP_DATA[slug]['rates']:
            APP_DATA[slug]['rates'][interval] = 0
        APP_DATA[slug]['rates'][interval] = (
            APP_DATA[slug]['rates'][interval] + 1)

        if repo not in APP_DATA[slug]['repos']:
            APP_DATA[slug]['repos'][repo] = 0
        APP_DATA[slug]['repos'][repo] = APP_DATA[slug]['repos'][repo] + 1


def update_data_check_run(event_data):
    global APP_DATA

    repo = event_data['repository']['full_name']
    cs = event_data['check_run']

    LOGGER.debug("    repo: %s", repo)
    LOGGER.debug("    app: %s", cs['app']['slug'])
    LOGGER.debug("    action: %s", event_data['action'])
    LOGGER.debug("    status: %s", cs['status'])
    LOGGER.debug("    conclusion: %s", cs['conclusion'])

    if (
        cs['app']['slug'] in APP_DATA and
        cs['status'] == 'completed'
    ):
        LOGGER.debug("    completed_at: %s", cs['completed_at'])
        key = cs['app']['slug']

        uptime = dateutil.parser.isoparse(cs['completed_at'])
        interval = _make_time_key(uptime)
        if interval not in APP_DATA[key]['rates']:
            APP_DATA[key]['rates'][interval] = 0
        APP_DATA[key]['rates'][interval] = (
            APP_DATA[key]['rates'][interval]
            + 1
        )

        if repo not in APP_DATA[key]['repos']:
            APP_DATA[key]['repos'][repo] = 0
        APP_DATA[key]['repos'][repo] = (
            APP_DATA[key]['repos'][repo]
            + 1
        )


def cache_status_data():
    if "CF_WEBSERVICES_TEST" in os.environ:
        return
    try:
        gh_token = get_app_token_for_webservices_only()

        # first pull down the data
        latest_data = requests.get(
            "https://services.conda-forge.org/status-monitor/db"
        ).json()

        # now update the repo
        with tempfile.TemporaryDirectory() as tmpdir:
            subprocess.run(
                ("cd %s && git clone --depth=1 "
                 "https://github.com/conda-forge/conda-forge-status-monitor.git" %
                 tmpdir
                 ),
                shell=True,
                check=True,
                capture_output=True,
            )

            pth = os.path.join(tmpdir, "conda-forge-status-monitor")

            subprocess.run(
                "cd %s && git remote set-url --push origin "
                "https://x-access-token:%s@github.com/"
                "conda-forge/conda-forge-status-monitor.git" % (
                    pth, gh_token
                ),
                shell=True,
                check=True,
                capture_output=True,
            )

            os.makedirs(os.path.join(pth, "data"), exist_ok=True)

            with open(os.path.join(pth, "data", "latest.json"), "w") as fp:
                json.dump(latest_data, fp, indent=2)

            subprocess.run(
                "cd %s && git add data/latest.json" % pth,
                shell=True,
                check=True,
                capture_output=True,
            )

            stat = subprocess.run(
                "cd %s && git status" % pth,
                shell=True,
                check=True,
                capture_output=True,
            )
            status = stat.stdout.decode('utf-8')
            LOGGER.debug("    cache git status: %s", status)

            if "nothing to commit" not in status:
                LOGGER.info("    making status data commit")
                subprocess.run(
                    "cd %s && git commit -m '[ci skip] "
                    "[skip ci] [cf admin skip] ***NO_CI*** "
                    "status data update %s'" % (
                        pth, datetime.datetime.utcnow().isoformat()
                    ),
                    shell=True,
                    check=True,
                    capture_output=True,
                )

                subprocess.run(
                    "cd %s && git push" % pth,
                    shell=True,
                    check=True,
                    capture_output=True,
                )
            else:
                LOGGER.info("    no status data to commit")
    except Exception as e:
        LOGGER.warning("    caching status data failed: %s" % repr(e))
