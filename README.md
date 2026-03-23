# PassthroughPro

Switch a video card between your host OS and a QEMU/KVM virtual machine — with a polished GUI and full CLI support.

---

## Features

| Feature | Detail |
|---|---|
| **Host Mode** | Unbinds from VFIO, loads NVIDIA driver, makes GPU available to Xorg/Wayland |
| **VM Mode** | Unloads NVIDIA driver, binds GPU to `vfio-pci` for QEMU/KVM passthrough |
| **GRUB Check** | Detects `vfio-pci.ids=` entries in GRUB; warns if your devices aren't listed |
| **VM Detection** | Queries `virsh` and `pgrep` — blocks host-mode switch if a VM is still running |
| **Live Log** | Color-coded operation log with timestamps in the GUI |
| **Auto-Refresh** | GUI polls device state every 8 seconds and updates automatically |
| **Safety Guards** | Warns if Xorg is active, refuses switch if VMs are running |
| **CLI Mode** | Headless switching for scripts, SSH sessions, startup hooks |
| **Installer** | Sets up sudoers rules, `.desktop` entry, CLI wrapper |

---

## Requirements

- Linux kernel ≥ 5.x (tested on 6.x)
- IOMMU enabled in BIOS + GRUB (`intel_iommu=on` or `amd_iommu=on`)
- Python ≥ 3.10
- PyQt6: `pip install PyQt6`
- Root access for driver operations
- Optional: `libvirt` / `virsh` for VM state detection

---

## Quick Start

### 1. Install
```bash
sudo bash install.sh
```

### 2. Run GUI
```bash
sudo passthroughpro gui
# or directly:
sudo python3 gpu_switcher_gui.py
```

### 3. Run CLI
```bash
sudo passthroughpro status
sudo passthroughpro devices
sudo passthroughpro grub-check
sudo passthroughpro switch vm
sudo passthroughpro switch host
```

---

## GRUB Configuration

For the GPU binding to survive reboots, add to `/etc/default/grub`:

```
GRUB_CMDLINE_LINUX="... intel_iommu=on iommu=pt vfio-pci.ids=10de:1b81,10de:10f0"
```

Replace the IDs with your actual GPU device IDs (visible in `passthroughpro devices`).
Replace with amd_iommu=on if your CPU is an amd CPU
Then:
```bash
sudo update-grub
sudo reboot
```

The application will warn you if your devices are **not** found in GRUB.

---

## /etc/modules (optional but recommended)

To pre-load VFIO modules at boot:
```
vfio
vfio_iommu_type1
vfio_pci
```

---

## File Structure

```
gpu_switcher/
├── gpu_backend.py        # All kernel/sysfs/driver operations
├── gpu_switcher_gui.py   # PyQt6 graphical interface
├── gpu_cli.py            # Command-line interface
├── install.sh            # Installer script
└── README.md             # This file
```

---

## How It Works

### VM Mode (Host → VFIO)
1. Warn if Xorg is running
2. Unload `nvidia_drm`, `nvidia_modeset`, `nvidia_uvm`, `nvidia`
3. Unbind GPU PCI devices from their current driver
4. Load `vfio`, `vfio_iommu_type1`, `vfio_pci`
5. Bind GPU devices to `vfio-pci`

### Host Mode (VFIO → NVIDIA)
1. Abort if any QEMU/KVM VM is still running
2. Unbind GPU PCI devices from `vfio-pci`
3. Clear `driver_override` on each device
4. Unload VFIO modules
5. Load `nvidia`, `nvidia_modeset`, `nvidia_uvm`, `nvidia_drm`
6. Bind GPU to `nvidia` driver
7. Remind user to restart their display manager

---

## Safety Notes

- **Never switch while a VM is actively using the GPU** — this causes kernel panics.
- **Switching away from Xorg** will crash your display session. Run the GUI over SSH or a secondary display.
- This tool writes to `/sys/bus/pci` and calls `modprobe` — **root access is required**.
- The GRUB check is advisory only — if your IDs aren't in GRUB, you'll need to switch manually each boot.

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `vfio-pci: No such file` | Install/load vfio-pci: `modprobe vfio-pci` |
| Bind fails after unbind | Try `echo 1 > /sys/bus/pci/rescan` |
| NVIDIA won't load after switch | Run `nvidia-smi`; if still failing, reboot |
| No devices detected | Check `lspci -Dnn | grep 10de` |
| IOMMU not showing | Enable `intel_iommu=on` in GRUB and reboot |
