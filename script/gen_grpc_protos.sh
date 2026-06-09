#!/usr/bin/env bash
# Regenerate gRPC/protobuf Python files from core.proto.
#
# Usage: script/gen_grpc_protos.sh
#
# Requirements:
#   pip install grpcio-tools==<version matching grpcio in package_constraints.txt>
#
# The generated *_grpc.py file uses a bare "import core_pb2" which breaks when
# the module is loaded as part of the homeassistant package.  This script
# patches the import to the fully-qualified form automatically.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
PROTO_DIR="${REPO_ROOT}/homeassistant/core_grpc/protos"
PROTO_FILE="${PROTO_DIR}/core.proto"
GRPC_FILE="${PROTO_DIR}/core_pb2_grpc.py"
PACKAGE="homeassistant.core_grpc.protos"

# ---------------------------------------------------------------------------
# Version check: grpcio-tools must match the grpcio pin in package_constraints
# ---------------------------------------------------------------------------
REQUIRED_VERSION=$(grep '^grpcio==' "${REPO_ROOT}/homeassistant/package_constraints.txt" \
    | head -n1 | cut -d= -f3)

INSTALLED_VERSION=$(pip show grpcio-tools 2>/dev/null | awk '/^Version:/{print $2}')

if [ -z "${INSTALLED_VERSION}" ]; then
    echo "ERROR: grpcio-tools is not installed."
    echo "       Run: pip install grpcio-tools==${REQUIRED_VERSION}"
    exit 1
fi

if [ "${INSTALLED_VERSION}" != "${REQUIRED_VERSION}" ]; then
    echo "ERROR: grpcio-tools ${INSTALLED_VERSION} is installed but ${REQUIRED_VERSION} is required"
    echo "       (must match grpcio pin in homeassistant/package_constraints.txt)"
    echo "       Run: pip install grpcio-tools==${REQUIRED_VERSION}"
    exit 1
fi

echo "grpcio-tools ${INSTALLED_VERSION} — OK"

# ---------------------------------------------------------------------------
# Generate
# ---------------------------------------------------------------------------
echo "Generating from ${PROTO_FILE} ..."

python -m grpc_tools.protoc \
    -I "${PROTO_DIR}" \
    --python_out="${PROTO_DIR}" \
    --grpc_python_out="${PROTO_DIR}" \
    "${PROTO_FILE}"

# ---------------------------------------------------------------------------
# Fix import: replace bare "import core_pb2" with fully-qualified package import
# ---------------------------------------------------------------------------
echo "Patching import in ${GRPC_FILE} ..."

sed -i "s|^import core_pb2 as core__pb2$|from ${PACKAGE} import core_pb2 as core__pb2|" \
    "${GRPC_FILE}"

# Verify the patch was applied
if ! grep -q "from ${PACKAGE} import core_pb2" "${GRPC_FILE}"; then
    echo "ERROR: import patch was not applied — check the generated file manually."
    exit 1
fi

echo "Done. Generated files:"
echo "  ${PROTO_DIR}/core_pb2.py"
echo "  ${GRPC_FILE}"

# ---------------------------------------------------------------------------
# Quick sanity check
# ---------------------------------------------------------------------------
echo "Verifying import ..."
python -c "from homeassistant.core_grpc.protos import core_pb2, core_pb2_grpc; print('Import OK')"
