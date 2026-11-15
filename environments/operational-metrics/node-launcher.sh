#!/bin/sh
# The solver starts with a minimal environment. Set image-owned tool defaults at
# the Node entry point; explicit caller overrides remain intact.
: "${PLAYWRIGHT_CHROME_EXECUTABLE_PATH:=/usr/local/share/chrome-for-testing/chrome-linux-arm64/chrome}"
: "${FONTCONFIG_PATH:=/usr/local/share/chrome-for-testing/fontconfig}"
: "${FONTCONFIG_FILE:=fonts.conf}"
: "${PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD:=1}"
export PLAYWRIGHT_CHROME_EXECUTABLE_PATH FONTCONFIG_PATH FONTCONFIG_FILE PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD
exec /usr/local/lib/node-runtime/bin/node "$@"
