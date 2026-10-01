#!/bin/zsh
cd -- "${0:A:h}" || exit 1
./.venv/bin/python fishing_menu.py --preset crowded
result=$?
read '?按回车关闭窗口…'
exit "$result"
