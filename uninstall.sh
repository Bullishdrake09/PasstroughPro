#!/usr/bin/env bash
if [[ $EUID -ne 0 ]]; then
   echo "Please run as root (sudo)"
   exit 1
fi

rm -rf /opt/passthroughpro
rm -f /usr/local/bin/passthroughpro
rm -f /usr/share/applications/passthroughpro.desktop
rm -f /etc/sudoers.d/passthroughpro

echo "PassthroughPro has been uninstalled."
