conda-forge recipe linting GitHub webhook
-----------------------------------------

This repository is the source for the Heroku hosted webapp which lints conda-forge's recipes.
The linting itself comes from conda-smithy (https://github.com/conda-forge/conda-smithy) - this web
service is simply a webapp for the linting functionality on conda-forge pull requests.

Rather than using OAuth, this app is using a pre-determined "personal access token" which has
appropriate conda-forge permissions. It has been configured with:

    heroku config:set GH_TOKEN=<token>

The service deploys to the "conda-forge" heroku project: https://dashboard.heroku.com/apps/conda-forge/resources

It is then a case of adding the appropriate webhook to trigger the service on ``pull_request``.

The buildpack for this repo comes from https://github.com/pl31/heroku-buildpack-conda, which allows a conda
environment to be deployed as part of the slug.
 
