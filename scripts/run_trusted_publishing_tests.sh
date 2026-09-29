#!/usr/bin/env bash

python -u -m conda_forge_webservices.webapp --local &
echo "waiting for the server..."
sleep 5

echo "running the tests..."
pushd scripts
pytest -vvs test_trusted_publishing_endpoint.py
retval=$?
kill $(jobs -p)
popd

exit $retval
