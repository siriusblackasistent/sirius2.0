#!/bin/sh
set -eu
systemctl stop sirius2.0-telegram.service sirius2.0-server.service
systemctl start irene-webapi.service irene-telebot.service
