#!/bin/sh
set -eu

mkdir -p /opt/meshcentral/meshcentral-data /opt/meshcentral/meshcentral-files
cp /opt/meshcentral/config-template.json /opt/meshcentral/meshcentral-data/config.json
chown -R node:node /opt/meshcentral/meshcentral-data /opt/meshcentral/meshcentral-files
exec su-exec node "$@"
