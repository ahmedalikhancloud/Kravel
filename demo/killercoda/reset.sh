#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
kubectl delete namespace kravel-demo kravel-system --ignore-not-found --wait=true
kubectl delete clusterrole kravel-killercoda-observer --ignore-not-found
kubectl delete clusterrolebinding kravel-killercoda-observer --ignore-not-found
rm -f "${ROOT_DIR}/.kravel-demo-state"
printf 'Removed the Kravel demo namespaces, RBAC objects, and local timestamp state.\n'
