#!/usr/bin/env bash

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

if [[ "${retvale}" == "0" && "${retvalc}" == "0" ]]; then
  exit 0
else
  exit 1
fi
