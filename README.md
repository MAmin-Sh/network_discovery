# 🌐 Enterprise Network Operations Console

[![Version](https://img.shields.io/badge/version-2.6.1-blue.svg)](https://github.com/MAmin-Sh/network_discovery/releases/tag/v2.6.1)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey.svg)](#)
[![Python](https://img.shields.io/badge/python-3.8%2B-brightgreen.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

An enterprise-grade, high-performance desktop application for internal network discovery, host diagnostics, latency monitoring, and subnet analytics. Built with Python and modern GUI components to offer an effortless operational workflow for network administrators and IT specialists.

---

## Downloads & Latest Release

Download the standalone, single-file executable directly from the GitHub Releases page—no installation or external Python setup required:

📦 **[Download NetworkOpsConsole v2.6.1 Executable](https://github.com/MAmin-Sh/network_discovery/releases/tag/v2.6.1)**

| Asset | Target OS | Details |
| :--- | :--- | :--- |
| **`NetworkScanner.exe`** | Windows (64-bit) | Portable single-file executable (`--onefile`) |

---

## Key Features

* **Subnet Scanning:** Asynchronous IPv4 subnet (`/24`) host discovery via ICMP ping sweeps.
* **Diagnostics & Routing:** Embedded Traceroute diagnostics window and quick single-host latency checks.
* **Public & Local IP Detection:** One-click refresh for immediate inspection of local subnet endpoints and public routing IP addresses.
* **Real-Time KPIs & Analytics:** Live tracking of total discovered hosts, response times (ms), active scan timers, and percentage progression.
* **Data Filtering & Export:** Dynamic full-text filtering across hostnames, IPs, and statuses with structured export to **CSV** and **JSON**.
* **Modern Enterprise UI:** Dark theme layout with centered dynamic tables, non-intrusive toast notification alerts, custom DPI scaling, and desktop shortcuts.

---

## Installation & Setup

### Running from Source

1. **Clone the Repository:**
   ```bash
   git clone [https://github.com/MAmin-Sh/network_discovery.git](https://github.com/MAmin-Sh/network_discovery.git)
   cd network_discovery
