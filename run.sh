#!/bin/sh
# Дождь Matrix на панели Turing 8.8". Файл пишет generate.py: он повторяет
# настройки, с которыми собран assets/matrix.h264.
exec "${BEZEL:-$HOME/.local/bin/bezel}" stream \
  "$(dirname "$0")/assets/matrix.h264" --fps 20
