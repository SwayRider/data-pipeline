#!/bin/bash
set -e

# Install analysis-icu plugin if not already present
if ! /usr/share/elasticsearch/bin/elasticsearch-plugin list | grep -q analysis-icu; then
    echo "Installing analysis-icu plugin..."
    /usr/share/elasticsearch/bin/elasticsearch-plugin install --batch analysis-icu
else
    echo "analysis-icu plugin already installed"
fi

# Start Elasticsearch
exec /usr/local/bin/docker-entrypoint.sh elasticsearch
