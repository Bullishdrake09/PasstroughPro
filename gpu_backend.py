"""
PassthroughPro — Backend Logic
Handles all kernel-level operations for VFIO switching for any GPU.
"""

import subprocess
import os
import re
import time
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class GPUMode(Enum):
    HOST = "host"
    VM = "vm"
    UNKNOWN = "unknown"
    TRANSITIONING = "transitioning"


class SwitchError(Exception):
    pass


@dataclass
class PCIDevice:
    address: str       # e.g. "0000:01:00.0"
    vendor_id: str     # e.g. "10de"
    device_id: str     # e.g. "1b81"
    description: str
    current_driver: Optional[str] = None
    is_grub_listed: bool = False

    @property
    def id_pair(self) -> str:
        return f"{self.vendor_id}:{self.device_id}"

    @property
    def sysfs_path(self) -> Path:
        return Path(f"/sys/bus/pci/devices/{self.address}")

    @property
    def driver_path(self) -> Optional[Path]:
        link = self.sysfs_path / "driver"
        if link.exists():
            return Path(os.readlink(link)).name
        return None


@dataclass
class SwitchResult:
    success: bool
    mode: GPUMode
    messages: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    errors: list = field(default_factory=list)


def run_cmd(cmd: list[str], check=True, capture=True) -> subprocess.CompletedProcess:
    """Run a shell command, return result."""
    result = subprocess.run(
        cmd, capture_output=capture, text=True,
        timeout=30
    )
    if check and result.returncode != 0:
        raise SwitchError(f"Command failed: {' '.join(cmd)}\n{result.stderr.strip()}")
    return result


def write_sysfs(path: str, value: str) -> bool:
    """Write a value to a sysfs file. Returns True on success."""
    try:
        with open(path, "w") as f:
            f.write(value)
        return True
    except OSError as e:
        raise SwitchError(f"Cannot write to {path}: {e}")


# ──────────────────────────────────────────────
# GRUB Inspection
# ──────────────────────────────────────────────

def get_grub_vfio_ids() -> list[str]:
    """
    Parse /etc/default/grub (or /etc/grub.d) for vfio-pci.ids=... entries.
    Returns list of 'vendor:device' strings found there.
    """
    ids = []
    grub_files = [
        Path("/etc/default/grub"),
        Path("/etc/kernel/cmdline"),
        Path("/proc/cmdline"),
    ]
    grub_d = Path("/etc/default/grub.d")
    if grub_d.exists():
        grub_files += list(grub_d.glob("*.cfg"))

    pattern = re.compile(r"vfio[-_]pci\.ids=([^\s\"']+)")
    for gf in grub_files:
        try:
            text = gf.read_text()
            for match in pattern.finditer(text):
                for pair in match.group(1).split(","):
                    pair = pair.strip()
                    if re.match(r"[0-9a-fA-F]{4}:[0-9a-fA-F]{4}", pair):
                        ids.append(pair.lower())
        except (OSError, PermissionError):
            pass

    return list(dict.fromkeys(ids))


def is_device_grub_listed(device: PCIDevice, grub_ids: list[str]) -> bool:
    return device.id_pair.lower() in [g.lower() for g in grub_ids]


# ──────────────────────────────────────────────
# PCI Device Discovery
# ──────────────────────────────────────────────

def discover_gpu_devices() -> list[PCIDevice]:
    """
    Enumerate GPU PCI devices (and their associated functions on the same slot) using lspci.
    Returns list of PCIDevice objects.
    """
    devices = []
    try:
        result = run_cmd(["lspci", "-Dnn"], check=True)
    except (FileNotFoundError, SwitchError):
        return devices

    # Step 1: Find all base slots (Domain:Bus:Slot) that contain a VGA/3D controller
    gpu_slots = set()
    for line in result.stdout.splitlines():
        if "VGA compatible controller" in line or "3D controller" in line or "Display controller" in line:
            # Extract "0000:01:00" from "0000:01:00.0 VGA..."
            slot_id = line.split()[0][:12] 
            gpu_slots.add(slot_id)

    # Step 2: Extract all devices that belong to those slots (grabs the GPU, its Audio, USB, etc.)
    device_re = re.compile(r"^([\da-fA-F]{4}:[\da-fA-F]{2}:[\da-fA-F]{2}\.\d)\s+(.+?)\[(\w{4}):(\w{4})\]")
    for line in result.stdout.splitlines():
        m = device_re.match(line)
        if m:
            addr = m.group(1)
            slot_id = addr[:12]
            
            if slot_id in gpu_slots:
                desc = m.group(2).strip()
                vendor = m.group(3).lower()
                dev_id = m.group(4).lower()
                pcidev = PCIDevice(
                    address=addr,
                    vendor_id=vendor,
                    device_id=dev_id,
                    description=desc,
                )
                
                driver_link = Path(f"/sys/bus/pci/devices/{addr}/driver")
                if driver_link.exists():
                    pcidev.current_driver = Path(os.readlink(driver_link)).name
                devices.append(pcidev)

    return devices


def get_current_mode(devices: list[PCIDevice]) -> GPUMode:
    """Determine current GPU binding state."""
    if not devices:
        return GPUMode.UNKNOWN
    drivers = set(d.current_driver for d in devices if d.current_driver)
    
    if "vfio-pci" in drivers and len(drivers) == 1:
        return GPUMode.VM
    elif "vfio-pci" not in drivers and len(drivers) > 0:
        return GPUMode.HOST
    
    return GPUMode.UNKNOWN


# ──────────────────────────────────────────────
# VM Detection
# ──────────────────────────────────────────────

def detect_running_vms() -> list[str]:
    """Return list of running QEMU/KVM VM names."""
    vms = []
    try:
        r = run_cmd(["virsh", "list", "--name", "--state-running"], check=False)
        for line in r.stdout.splitlines():
            name = line.strip()
            if name:
                vms.append(name)
    except FileNotFoundError:
        pass

    try:
        r = run_cmd(["pgrep", "-a", "qemu-system"], check=False)
        for line in r.stdout.splitlines():
            if "qemu-system" in line:
                m = re.search(r"-name\s+(\S+)", line)
                name = m.group(1) if m else f"qemu-pid-{line.split()[0]}"
                if name not in vms:
                    vms.append(name)
    except FileNotFoundError:
        pass

    return vms


# ──────────────────────────────────────────────
# Driver Operations
# ──────────────────────────────────────────────

def unbind_driver(device: PCIDevice, log) -> None:
    if not device.current_driver:
        log(f"  {device.address}: no driver bound, skipping unbind")
        return
    unbind_path = f"/sys/bus/pci/devices/{device.address}/driver/unbind"
    log(f"  Unbinding {device.address} from {device.current_driver}…")
    write_sysfs(unbind_path, device.address)
    time.sleep(0.3)
    log(f"  ✓ Unbound {device.address}")


def bind_to_driver(device: PCIDevice, driver: str, log) -> None:
    new_id_path = f"/sys/bus/pci/drivers/{driver}/new_id"
    override_path = f"/sys/bus/pci/devices/{device.address}/driver_override"
    bind_path = f"/sys/bus/pci/drivers/{driver}/bind"

    log(f"  Binding {device.address} → {driver}…")

    try:
        write_sysfs(override_path, driver)
        write_sysfs(bind_path, device.address)
        log(f"  ✓ Bound {device.address} to {driver} (override method)")
        return
    except SwitchError:
        pass

    try:
        write_sysfs(new_id_path, f"{device.vendor_id} {device.device_id}")
        time.sleep(0.2)
        write_sysfs(bind_path, device.address)
        log(f"  ✓ Bound {device.address} to {driver} (new_id method)")
    except SwitchError as e:
        raise SwitchError(f"Failed to bind {device.address} to {driver}: {e}")


def load_module(module: str, log) -> bool:
    try:
        run_cmd(["modprobe", module])
        log(f"  ✓ Module {module} loaded")
        return True
    except SwitchError:
        return False


def unload_module(module: str, log) -> bool:
    try:
        run_cmd(["modprobe", "-r", module])
        log(f"  ✓ Module {module} unloaded")
        return True
    except SwitchError:
        return False


def refresh_driver_state(devices: list[PCIDevice]) -> None:
    for d in devices:
        link = d.sysfs_path / "driver"
        if link.exists():
            d.current_driver = Path(os.readlink(link)).name
        else:
            d.current_driver = None


# ──────────────────────────────────────────────
# High-Level Switch Operations
# ──────────────────────────────────────────────

HOST_MODULES = [
    "nvidia_drm", "nvidia_modeset", "nvidia_uvm", "nvidia", 
    "amdgpu", "radeon", "nouveau", "i915", "xe"
]

def switch_to_vm_mode(devices: list[PCIDevice], log) -> SwitchResult:
    result = SwitchResult(success=False, mode=GPUMode.TRANSITIONING)
    warnings = result.warnings
    messages = result.messages

    log("═══ Switching GPU to VM (VFIO) mode ═══")

    display_check = run_cmd(["pgrep", "-x", "Xorg"], check=False)
    if display_check.returncode == 0:
        warnings.append("Xorg is running. Switching GPU while display is active may crash your session.")
        log("  ⚠ WARNING: Xorg is running on this GPU")

    eligible = [d for d in devices if d.current_driver != "vfio-pci"]
    if not eligible:
        messages.append("All GPU devices are already bound to vfio-pci.")
        result.success = True
        result.mode = GPUMode.VM
        return result

    log("Step 1: Unloading Host driver modules…")
    for mod in HOST_MODULES:
        unload_module(mod, log)

    log("Step 2: Unbinding PCI devices…")
    for dev in eligible:
        refresh_driver_state([dev])
        try:
            unbind_driver(dev, log)
        except SwitchError as e:
            result.errors.append(str(e))
            log(f"  ✗ {e}")

    log("Step 3: Loading vfio-pci module…")
    try:
        if not load_module("vfio", log): pass
        if not load_module("vfio_iommu_type1", log): pass
        if not load_module("vfio_pci", log): raise SwitchError("Could not load vfio_pci")
    except SwitchError as e:
        result.errors.append(str(e))
        log(f"  ✗ {e}")
        return result

    log("Step 4: Binding devices to vfio-pci…")
    success_count = 0
    for dev in eligible:
        try:
            bind_to_driver(dev, "vfio-pci", log)
            success_count += 1
        except SwitchError as e:
            result.errors.append(str(e))
            log(f"  ✗ {e}")

    time.sleep(0.5)
    refresh_driver_state(devices)

    if success_count == len(eligible):
        result.success = True
        result.mode = GPUMode.VM
        messages.append(f"Successfully bound {success_count} device(s) to vfio-pci.")
        log("═══ VM mode switch complete ✓ ═══")
    else:
        result.mode = GPUMode.UNKNOWN
        log("═══ VM mode switch FAILED ═══")

    return result


def switch_to_host_mode(devices: list[PCIDevice], log) -> SwitchResult:
    result = SwitchResult(success=False, mode=GPUMode.TRANSITIONING)
    warnings = result.warnings
    messages = result.messages

    log("═══ Switching GPU to Host mode ═══")

    running_vms = detect_running_vms()
    if running_vms:
        err = f"Cannot switch: VM(s) still running: {', '.join(running_vms)}"
        result.errors.append(err)
        log(f"  ✗ {err}")
        return result

    eligible = [d for d in devices if d.current_driver == "vfio-pci" or d.current_driver is None]
    if not eligible:
        messages.append("GPU devices appear to already be in host mode.")
        result.success = True
        result.mode = GPUMode.HOST
        return result

    log("Step 1: Unbinding from vfio-pci…")
    for dev in eligible:
        refresh_driver_state([dev])
        try:
            unbind_driver(dev, log)
        except SwitchError as e:
            result.errors.append(str(e))
            log(f"  ✗ {e}")

    log("Step 2: Clearing driver overrides…")
    for dev in eligible:
        override_path = f"/sys/bus/pci/devices/{dev.address}/driver_override"
        try:
            write_sysfs(override_path, "\n")
        except Exception:
            pass

    log("Step 3: Unloading vfio-pci module…")
    for mod in ["vfio_pci", "vfio_iommu_type1", "vfio"]:
        unload_module(mod, log)

    log("Step 4: Loading Host driver modules…")
    for mod in HOST_MODULES:
        load_module(mod, log)

    log("Step 5: Triggering PCI re-probe…")
    try:
        write_sysfs("/sys/bus/pci/rescan", "1")
        log("  ✓ Triggered PCI bus rescan")
    except Exception as e:
        log(f"  ✗ Rescan failed: {e}")

    time.sleep(1.0)
    refresh_driver_state(devices)

    bound_host = sum(1 for d in devices if d.current_driver not in ["vfio-pci", None])
    if bound_host > 0:
        result.success = True
        result.mode = GPUMode.HOST
        messages.append(f"{bound_host} device(s) now bound to host driver.")
        log("═══ Host mode switch complete ✓ ═══")
        warnings.append("You may need to restart your display manager (GDM/SDDM/LightDM) or reboot for Xorg to use the GPU.")
    else:
        result.mode = GPUMode.UNKNOWN
        log("═══ Host mode switch may need manual steps ═══")
        warnings.append("Modules loaded but automatic binding failed. Your system may require a reboot or specific Xorg configuration.")
        result.success = True

    return result


# ──────────────────────────────────────────────
# System Info
# ──────────────────────────────────────────────

def get_system_info() -> dict:
    info = {}

    try:
        r = run_cmd(["uname", "-r"], check=False)
        info["kernel"] = r.stdout.strip()
    except Exception:
        info["kernel"] = "unknown"

    cmdline = Path("/proc/cmdline").read_text() if Path("/proc/cmdline").exists() else ""
    info["iommu_enabled"] = "iommu=on" in cmdline or "intel_iommu=on" in cmdline or "amd_iommu=on" in cmdline

    try:
        r = run_cmd(["lsmod"], check=False)
        info["vfio_loaded"] = "vfio_pci" in r.stdout
        info["host_driver_loaded"] = any(mod in r.stdout for mod in ["nvidia ", "amdgpu ", "radeon ", "nouveau ", "i915 ", "xe "])
    except Exception:
        info["vfio_loaded"] = False
        info["host_driver_loaded"] = False

    info["grub_vfio_ids"] = get_grub_vfio_ids()
    info["running_vms"] = detect_running_vms()

    return info