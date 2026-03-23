#!/usr/bin/env python3
"""
PassthroughPro — CLI Interface
Universal GPU Edition
Usage:
    sudo python3 gpu_cli.py status
    sudo python3 gpu_cli.py switch host
    sudo python3 gpu_cli.py switch vm
    sudo python3 gpu_cli.py devices
    sudo python3 gpu_cli.py grub-check
"""

import sys
import os
import argparse
from datetime import datetime

# Allow running from any directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gpu_backend import (
    discover_gpu_devices, get_current_mode, get_grub_vfio_ids,
    is_device_grub_listed, switch_to_vm_mode, switch_to_host_mode,
    detect_running_vms, get_system_info, refresh_driver_state, GPUMode
)

# ANSI colours
RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
WHITE  = "\033[97m"
DIM    = "\033[2m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def ts():
    return f"{DIM}[{datetime.now().strftime('%H:%M:%S')}]{RESET}"

def log(msg):
    print(f"{ts()} {msg}")

def print_banner():
    print(f"""
{CYAN}╔══════════════════════════════════════════════════╗
║  PASSTHROUGHPRO  · UNIVERSAL GPU ·  v2.0 ║
╚══════════════════════════════════════════════════╝{RESET}""")

def cmd_status():
    devices = discover_gpu_devices()
    grub_ids = get_grub_vfio_ids()
    mode = get_current_mode(devices)
    mode_str = mode.value if hasattr(mode, 'value') else str(mode)
    vms = detect_running_vms()
    info = get_system_info()

    mode_color = GREEN if mode_str == "host" else (CYAN if mode_str == "vm" else YELLOW)
    print(f"\n{BOLD}Current Mode:{RESET}  {mode_color}{mode_str.upper()}{RESET}")
    print(f"{BOLD}Running VMs:{RESET}   {', '.join(vms) if vms else 'None'}")
    print(f"{BOLD}Kernel:{RESET}        {info.get('kernel', '?')}")
    print(f"{BOLD}IOMMU:{RESET}         {'✓ Enabled' if info.get('iommu_enabled') else '✗ Disabled'}")
    print(f"{BOLD}VFIO module:{RESET}   {'✓ Loaded' if info.get('vfio_loaded') else 'Not loaded'}")
    print(f"{BOLD}Host Driver:{RESET}   {'✓ Loaded' if info.get('host_driver_loaded') else 'Not loaded'}")
    print(f"{BOLD}GRUB IDs:{RESET}      {', '.join(grub_ids) if grub_ids else 'None found'}")

    print(f"\n{BOLD}Devices:{RESET}")
    for d in devices:
        in_grub = is_device_grub_listed(d, grub_ids)
        drv = d.current_driver or 'none'
        drv_color = CYAN if drv == "vfio-pci" else (GREEN if drv != "none" else DIM)
        grub_flag = f"{GREEN}[GRUB✓]{RESET}" if in_grub else f"{YELLOW}[!GRUB]{RESET}"
        print(f"  {CYAN}{d.address}{RESET}  {d.description[:50]}")
        print(f"    ID: {d.id_pair}  Driver: {drv_color}{drv}{RESET}  {grub_flag}")

def cmd_devices():
    devices = discover_gpu_devices()
    grub_ids = get_grub_vfio_ids()
    for d in devices:
        in_grub = is_device_grub_listed(d, grub_ids)
        print(f"{CYAN}{d.address}{RESET}  {d.id_pair}  {d.description}")
        print(f"  Driver: {d.current_driver or 'none'}")
        print(f"  In GRUB vfio-pci.ids: {'Yes' if in_grub else 'NO — add to GRUB for persistence'}")

def cmd_grub_check():
    ids = get_grub_vfio_ids()
    devices = discover_gpu_devices()
    print(f"\n{BOLD}GRUB vfio-pci.ids entries found:{RESET}")
    if ids:
        for i in ids:
            print(f"  {GREEN}✓{RESET}  {i}")
    else:
        print(f"  {YELLOW}⚠  None found in /etc/default/grub or /proc/cmdline{RESET}")
        print(f"\n  To enable persistence, add to GRUB_CMDLINE_LINUX in /etc/default/grub:")
        for d in devices:
            print(f"  {CYAN}vfio-pci.ids={d.id_pair}{RESET}  ({d.description})")
        print(f"\n  Then run: {BOLD}sudo update-grub{RESET}")

    print(f"\n{BOLD}GPU devices:{RESET}")
    for d in devices:
        in_grub = is_device_grub_listed(d, ids)
        flag = f"{GREEN}✓ listed{RESET}" if in_grub else f"{YELLOW}✗ NOT listed{RESET}"
        print(f"  {d.address}  {d.id_pair}  {flag}")

def cmd_switch(target: str):
    if os.geteuid() != 0:
        print(f"{RED}ERROR: This command must be run as root.{RESET}")
        sys.exit(1)

    devices = discover_gpu_devices()
    if not devices:
        print(f"{RED}No GPU devices found.{RESET}")
        sys.exit(1)

    grub_ids = get_grub_vfio_ids()
    for d in devices:
        d.is_grub_listed = is_device_grub_listed(d, grub_ids)

    if target == "vm":
        result = switch_to_vm_mode(devices, log)
    else:
        result = switch_to_host_mode(devices, log)

    for w in result.warnings:
        print(f"{YELLOW}⚠  {w}{RESET}")
    for e in result.errors:
        print(f"{RED}✗  {e}{RESET}")
    for m in result.messages:
        print(f"{GREEN}✓  {m}{RESET}")

    if result.success:
        print(f"\n{GREEN}{BOLD}Switch successful.{RESET}")
        sys.exit(0)
    else:
        print(f"\n{RED}{BOLD}Switch failed. Check output above.{RESET}")
        sys.exit(1)


def main():
    print_banner()
    parser = argparse.ArgumentParser(description="PassthroughPro CLI (Universal)")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status",     help="Show current GPU and system status")
    sub.add_parser("devices",    help="List GPU PCI devices")
    sub.add_parser("grub-check", help="Check GRUB vfio-pci.ids configuration")

    sw = sub.add_parser("switch", help="Switch GPU mode")
    sw.add_argument("target", choices=["host", "vm"], help="Target mode")

    args = parser.parse_args()

    if args.command == "status":
        cmd_status()
    elif args.command == "devices":
        cmd_devices()
    elif args.command == "grub-check":
        cmd_grub_check()
    elif args.command == "switch":
        cmd_switch(args.target)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()