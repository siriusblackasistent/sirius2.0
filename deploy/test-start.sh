#!/bin/sh
set -eu
# Temporary switch only: do not change enable/disable state of legacy services.
systemctl stop irene-telebot.service irene-webapi.service
if ! systemctl start sirius2.0-server.service sirius2.0-telegram.service; then
    systemctl stop sirius2.0-telegram.service sirius2.0-server.service
    systemctl start irene-webapi.service irene-telebot.service
    exit 1
fi
