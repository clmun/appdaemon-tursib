#!/bin/bash
# update_appdaemon.sh

cd /addon_configs/a0d7b954_appdaemon/apps

# dacă nu există fișierul, clonează repo-ul
if [ ! -d "appdaemon-tursib" ]; then
  git clone https://github.com/clmun/appdaemon-tursib.git
fi

# copiază ultima versiune din GitHub peste fișierul din addon
cp appdaemon-tursib/tursib.py ./tursib.py

# restart AppDaemon
ha addons restart a0d7b954_appdaemon
