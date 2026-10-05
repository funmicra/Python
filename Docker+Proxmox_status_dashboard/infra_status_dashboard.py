#!/usr/bin/env python3
"""
infra_status_dashboard.py
A lightweight terminal dashboard showing Docker containers and Proxmox VMs/LXCs status.



Requirements:
  pip install requests docker tabulate

Configuration:
  - Set PROXMOX_HOST, USERNAME, PASSWORD (or API token)
  - Optionally store them in environment variables for safety
"""

import os
import sys
import time
import requests
import docker
from tabulate import tabulate

# ---- CONFIGURATION ----
PROXMOX_HOST = os.getenv("PROXMOX_HOST", "https://192.168.88.10:8006")
PROXMOX_USER = os.getenv("PROXMOX_USER", "root@pam")
PROXMOX_PASS = os.getenv("PROXMOX_PASS", "yourpassword")  # or use API token
VERIFY_SSL = False  # set to True if you have valid certs
REFRESH_INTERVAL = 30  # seconds between updates

# ------------------------

def proxmox_login():
    """Login to Proxmox API and return authentication ticket and CSRF token."""
    url = f"{PROXMOX_HOST}/api2/json/access/ticket"
    try:
        resp = requests.post(url, data={"username": PROXMOX_USER, "password": PROXMOX_PASS}, verify=VERIFY_SSL)
        resp.raise_for_status()
        data = resp.json()["data"]
        ticket = data["ticket"]
        csrf = data["CSRFPreventionToken"]
        return ticket, csrf
    except Exception as e:
        print(f"[!] Proxmox login failed: {e}")
        return None, None

def proxmox_list_vms(ticket):
    """Get list of VMs and LXCs from Proxmox."""
    cookies = {"PVEAuthCookie": ticket}
    try:
        resp = requests.get(f"{PROXMOX_HOST}/api2/json/cluster/resources", cookies=cookies, verify=VERIFY_SSL)
        resp.raise_for_status()
        vms = [x for x in resp.json()["data"] if x["type"] in ("qemu", "lxc")]
        return vms
    except Exception as e:
        print(f"[!] Failed to get Proxmox resources: {e}")
        return []

def docker_list_containers():
    """List running Docker containers and their stats."""
    try:
        client = docker.from_env()
        containers = client.containers.list(all=True)
        result = []
        for c in containers:
            stats = c.stats(stream=False)
            name = c.name
            image = c.image.tags[0] if c.image.tags else "<none>"
            status = c.status
            cpu = calc_cpu_percent(stats)
            mem = round(stats["memory_stats"]["usage"] / (1024**2), 1)
            result.append((name, image, status, f"{cpu:.1f}%", f"{mem} MB"))
        return result
    except Exception as e:
        print(f"[!] Docker connection error: {e}")
        return []

def calc_cpu_percent(stats):
    """Rough CPU usage percent calculation for Docker container stats."""
    try:
        cpu_delta = stats["cpu_stats"]["cpu_usage"]["total_usage"] - stats["precpu_stats"]["cpu_usage"]["total_usage"]
        system_delta = stats["cpu_stats"]["system_cpu_usage"] - stats["precpu_stats"]["system_cpu_usage"]
        percpu = len(stats["cpu_stats"]["cpu_usage"]["percpu_usage"])
        cpu_percent = (cpu_delta / system_delta) * percpu * 100.0 if system_delta > 0 else 0.0
        return cpu_percent
    except Exception:
        return 0.0

def print_dashboard(docker_data, vm_data):
    os.system("clear")
    print("🛰️  Homelab Infrastructure Dashboard")
    print("=" * 60)

    print("\n🐳 Docker Containers")
    if docker_data:
        print(tabulate(docker_data, headers=["Name", "Image", "Status", "CPU", "Mem"], tablefmt="fancy_grid"))
    else:
        print("No containers found.")

    print("\n🖥️  Proxmox VMs / LXCs")
    if vm_data:
        rows = []
        for vm in vm_data:
            rows.append((
                vm.get("vmid"),
                vm.get("name", "-"),
                vm.get("node"),
                vm.get("status"),
                f"{vm.get('maxmem',0)//(1024**3)} GB",
                f"{vm.get('maxdisk',0)//(1024**3)} GB"
            ))
        print(tabulate(rows, headers=["ID", "Name", "Node", "Status", "RAM", "Disk"], tablefmt="fancy_grid"))
    else:
        print("No VM/LXC data available.")

def main():
    print("[+] Logging in to Proxmox...")
    ticket, csrf = proxmox_login()
    if not ticket:
        sys.exit(1)

    while True:
        docker_data = docker_list_containers()
        vm_data = proxmox_list_vms(ticket)
        print_dashboard(docker_data, vm_data)
        print(f"\nUpdated: {time.strftime('%Y-%m-%d %H:%M:%S')} (refresh every {REFRESH_INTERVAL}s)")
        time.sleep(REFRESH_INTERVAL)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nExiting dashboard.")
