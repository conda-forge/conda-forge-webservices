#!/usr/bin/env bash

echo "blah-not-a-token" > ~/.conda-smithy/anaconda.token

python scripts/delete_staged_recipes_token.py
echo "waiting for github to remove the token..."
sleep 10
mkdir staged-recipes
rm -f ~/.conda-smithy/conda-forge_staged-recipes.token
conda smithy generate-feedstock-token --feedstock_directory staged-recipes
conda smithy register-feedstock-token \
  --without-circle \
  --without-drone \
  --without-azure \
  --without-travis \
  --without-github-actions \
  --token_repo='https://x-access-token:${GH_TOKEN}@github.com/conda-forge/feedstock-tokens' \
  --feedstock_directory staged-recipes

python -u -m conda_forge_webservices.webapp --local &
echo "waiting for the server..."
sleep 5

echo "running the tests..."
pushd scripts
pytest -vvs test_cfep13_endpoints.py
retvale=$?
pytest -vvs test_cfep13_copy.py
retvalc=$?
kill $(jobs -p)
popd

rm -f ~/.conda-smithy/conda-forge_staged-recipes.token
python scripts/delete_staged_recipes_token.py

if [[ "${retvale}" == "0" && "${retvalc}" == "0" ]]; then
  exit 0
else
  exit 1
fi
