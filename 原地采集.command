#!/bin/zsh
cd -- "${0:A:h}" || exit 1
./.venv/bin/python stationary_gather_menu.py
result=$?
read '?按回车关闭窗口…'
exit "$result"
