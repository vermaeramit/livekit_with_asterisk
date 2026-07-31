#!/bin/sh
set -e
cp -f /conf-override/*.conf /etc/asterisk/ 2>/dev/null || true
exec asterisk -f -vvv
