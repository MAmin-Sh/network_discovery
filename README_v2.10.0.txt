NETWORK OPERATIONS CONSOLE | VERSION 2.10.0
===========================================

Release focus
- Persistent Ping Monitor samples across application restarts (last 5,000 samples).
- Persistent host availability state-change events (last 2,000 events).
- Sample-based availability and packet-loss metrics in reports.
- Alert-event section in PDF/HTML/Markdown/TXT/JSON and a dedicated Excel worksheet.
- Scan snapshots now include observed open TCP ports; scan comparison reports newly observed and no-longer-observed open ports.
- Report cards have increased height and a taller description region to prevent clipped descriptions and hidden controls.
- Fixed report host reachability counting: latency-grade labels are not interpreted as online/offline state.

Files
- main.py: desktop application
- reporting.py: PDF, Excel, HTML, JSON, CSV, Markdown, TXT and ZIP exporters
- tests/test_reporting.py: offline report-generation smoke tests
- requirements.txt: runtime dependencies

Install / run (Windows PowerShell)
  python -m pip install -r requirements.txt
  python -m py_compile main.py reporting.py
  python -m unittest discover -s tests -v
  python main.py

Persistent data is stored locally in the user's home folder:
- .network_operations_console_history.json
- .network_operations_console_monitor.json
- .network_operations_console_alerts.json

Important notes
- Availability is calculated from saved Ping Monitor samples, not a guarantee of end-to-end service uptime.
- ICMP non-response does not prove a device is offline.
- Open-port changes are observations from scans, not proof of a vulnerability.
- Network scans should only target systems the operator is authorized to assess.
- Automated report tests are run offline with synthetic data. Full GUI rendering and live network behavior must still be verified on the target Windows machine before release/push.
