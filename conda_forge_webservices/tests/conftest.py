import glob
import os
import shutil

import pytest

TOKENS = [
    "CF_WEBSERVICES_APP_ID",
    "CF_WEBSERVICES_TOKEN",
]
MISSING_TOKENS = any(token not in os.environ for token in TOKENS)


@pytest.fixture(scope="session", autouse=True)
def prep_test_recipes():
    recipe_data = os.path.join(os.path.dirname(__file__), "linting", "data")
    # fix up any weird state from previous runs
    recipes = glob.glob(os.path.join(recipe_data, "*.yaml"))
    for recipe in recipes:
        shutil.move(recipe, recipe + ".skipme")

    # now we move the files from .skipme to the normal state
    recipes = glob.glob(os.path.join(recipe_data, "*.yaml.skipme"))
    for recipe in recipes:
        shutil.move(recipe, recipe.replace(".skipme", ""))

    yield

    # then move them back
    for recipe in recipes:
        shutil.move(recipe.replace(".skipme", ""), recipe)


def pytest_report_teststatus(report, config):
    if report.when == "call" and report.outcome == "no tokens":
        return report.outcome, "-", "no tokens"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_logreport(report):
    if report.when == "call" and report.failed:
        # Ok, so we have a failure, let's see if it a failure we expect
        message = report.longrepr.reprcrash.message
        if any([t in message for t in TOKENS]) and MISSING_TOKENS:
            report.wasxfail = "no tokens - {}".format(" ".join(message.splitlines()))
            report.outcome = "no tokens"

    yield report


@pytest.fixture
def skip_if_no_tokens():
    if not MISSING_TOKENS:
        yield
    else:
        pytest.skip("No conda-forge-webservices app tokens available for testing!")
