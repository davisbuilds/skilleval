#!/usr/bin/env bash
set -euo pipefail
if [ "$#" -ne 1 ]; then
  echo 'usage: build-gitleaks.sh OUTPUT_BINARY' >&2
  exit 2
fi

export GOENV=off GOWORK=off GOTOOLCHAIN=local CGO_ENABLED=0 GOFLAGS=
export GOPROXY=https://proxy.golang.org GOSUMDB=sum.golang.org
export GOPRIVATE='' GONOPROXY='' GONOSUMDB=''
if [ "$(go env GOVERSION)" != go1.27.3 ]; then
  echo 'The pinned scanner build requires Go 1.27.3' >&2
  exit 2
fi
tool_root=$(cd -- "$(dirname -- "$0")/gitleaks" && pwd)
go -C "$tool_root" mod verify
go -C "$tool_root" build -p 2 -mod=readonly -trimpath -buildvcs=false \
  -ldflags '-X=github.com/zricethezav/gitleaks/v8/version.Version=8.30.1-skilleval.20261029' \
  -o "$1" github.com/zricethezav/gitleaks/v8
