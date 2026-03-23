#!/usr/bin/env bash
# PassthroughPro — Installer (Universal GPU)
# Run as root: sudo bash install.sh

set -e
INSTALL_DIR="/opt/passthroughpro"
BIN_DIR="/usr/local/bin"
DESKTOP_DIR="/usr/share/applications"
SYSTEMD_DIR="/etc/systemd/system"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'; BOLD='\033[1m'

if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}This script must be run as root.${NC}" 
   exit 1
fi

echo -e "${CYAN}${BOLD}"
echo "  ╔══════════════════════════════════════════╗"
echo "  ║  PassthroughPro — Installer (Universal)  ║"
echo "  ╚══════════════════════════════════════════╝"
echo -e "${NC}"

# ── Check dependencies
echo -e "${CYAN}Checking dependencies…${NC}"
for pkg in python3 python3-pip lspci pgrep awk xargs; do
    if ! command -v "$pkg" &>/dev/null; then
        echo -e "  ${YELLOW}⚠ $pkg not found — installing…${NC}"
        apt-get install -y "$(echo $pkg | sed 's/lspci/pciutils/;s/pgrep/procps/;s/awk/gawk/;s/xargs/findutils/')" 2>/dev/null || true
    else
        echo -e "  ${GREEN}✓ $pkg${NC}"
    fi
done

# ── PyQt6
echo -e "${CYAN}Installing Python dependencies…${NC}"
pip3 install PyQt6 psutil --break-system-packages -q && echo -e "  ${GREEN}✓ PyQt6, psutil${NC}"

# ── Copy files
echo -e "${CYAN}Installing application files to ${INSTALL_DIR}…${NC}"
mkdir -p "$INSTALL_DIR"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "$SCRIPT_DIR/gpu_backend.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/gpu_switcher_gui.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/gpu_cli.py" "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/gpu_cli.py"
echo -e "  ${GREEN}✓ Files installed${NC}"

# ── CLI wrapper
cat > "$BIN_DIR/passthroughpro" << 'EOF'
#!/usr/bin/env bash
# PassthroughPro — wrapper
if [[ "$1" == "gui" ]]; then
    exec python3 /opt/passthroughpro/gpu_switcher_gui.py
else
    exec python3 /opt/passthroughpro/gpu_cli.py "$@"
fi
EOF
chmod +x "$BIN_DIR/passthroughpro"
echo -e "  ${GREEN}✓ CLI wrapper installed: passthroughpro${NC}"

# ── Sudoers entry (allow running CLI as root without password for the specific script)
SUDOERS_FILE="/etc/sudoers.d/passthroughpro"
cat > "$SUDOERS_FILE" << 'EOF'
# Allow users in 'video' group to run GPU passthrough manager
%video ALL=(root) NOPASSWD: /usr/local/bin/passthroughpro
%sudo  ALL=(root) NOPASSWD: /usr/local/bin/passthroughpro
EOF
chmod 440 "$SUDOERS_FILE"
echo -e "  ${GREEN}✓ Sudoers rule installed${NC}"

# ── .desktop entry
cat > "$DESKTOP_DIR/passthroughpro.desktop" << 'EOF'
[Desktop Entry]
Name=PassthroughPro
Comment=Switch GPU between Host and QEMU/KVM VM
Exec=sudo passthroughpro gui
Icon=video-display
Terminal=false
Type=Application
Categories=System;Settings;HardwareSettings;
Keywords=GPU;VFIO;NVIDIA;AMD;Intel;passthrough;KVM;QEMU;
EOF
echo -e "  ${GREEN}✓ Desktop entry installed${NC}"

# ── VFIO module loading hints
echo ""
echo -e "${CYAN}${BOLD}Post-installation checklist:${NC}"
echo -e "${YELLOW}1. IOMMU must be enabled in BIOS and GRUB:${NC}"
echo -e "   Intel: add  ${BOLD}intel_iommu=on iommu=pt${NC}  to GRUB_CMDLINE_LINUX"
echo -e "   AMD:   add  ${BOLD}amd_iommu=on iommu=pt${NC}   to GRUB_CMDLINE_LINUX"
echo ""

# Auto-detect GPU IDs for the entire slot
if command -v lspci &>/dev/null; then
    # Grab IDs of all VGA/3D controllers and their associated devices on the same slot
    GPU_IDS=$(lspci -Dnn | awk '/VGA|3D/{print substr($1,1,12)}' | sort -u | xargs -I{} lspci -s {}* -nn | grep -oP '\[\K[0-9a-f]{4}:[0-9a-f]{4}(?=\])' | tr '\n' ',' | sed 's/,$//')
    if [[ -n "$GPU_IDS" ]]; then
        echo -e "${YELLOW}2. For persistence, add to GRUB_CMDLINE_LINUX:${NC}"
        echo -e "   ${BOLD}vfio-pci.ids=${GPU_IDS}${NC}"
        echo -e "   Then run: ${BOLD}sudo update-grub && sudo reboot${NC}"
    fi
fi

echo ""
echo -e "${YELLOW}3. Ensure vfio modules load early. Add to /etc/modules:${NC}"
echo -e "   ${BOLD}vfio\nvfio_iommu_type1\nvfio_pci${NC}"
echo ""
echo -e "${GREEN}${BOLD}Installation complete!${NC}"
echo -e "  GUI:  ${BOLD}sudo passthroughpro gui${NC}"
echo -e "  CLI:  ${BOLD}sudo passthroughpro status${NC}"
echo -e "  CLI:  ${BOLD}sudo passthroughpro switch vm${NC}"
echo -e "  CLI:  ${BOLD}sudo passthroughpro switch host${NC}"