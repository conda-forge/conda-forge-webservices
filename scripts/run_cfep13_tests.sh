#!/usr/bin/env bash

python delete_staged_recipes_token.py
mkdir staged-recipes
conda smithy generate-feedstock-token --feedstock_directory staged-recipes
conda smithy register-feedstock-token \
  --without-circle \
  --without-drone \
  --without-azure \
  --without-travis \
  --without-github-actions \
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

python delete_staged_recipes_token.py

if [[ "${retvale}" == "0" && "${retvalc}" == "0" ]]; then
  exit 0
else
  exit 1
fi
