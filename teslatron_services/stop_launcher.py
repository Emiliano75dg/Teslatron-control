from __future__ import annotations

import argparse
import socket
import subprocess
import sys
from typing import Iterable

DEFAULT_PORTS = [8501, 8765, 8766, 8767, 8775]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Stop Teslatron-related local services by closing their known ports."
    )
    parser.add_argument(
        "--include-log-viewer",
        action="store_true",
        help="Also stop the Streamlit log reader on port 8501.",
    )
    parser.add_argument(
        "--ports",
        nargs="*",
        type=int,
        help="Override the default set of ports to stop.",
    )
    return parser


def is_local_service_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def chosen_ports(args: argparse.Namespace) -> list[int]:
    if args.ports:
        return sorted(set(args.ports))
    ports = [8765, 8766, 8767, 8775]
    if args.include_log_viewer:
        ports.insert(0, 8501)
    return ports


def stop_ports(ports: Iterable[int]) -> int:
    command = r"""
$ports = @(__PORTS__);
$processIds = @();
foreach ($port in $ports) {
  $lines = netstat -ano | Select-String ":$port";
  foreach ($line in $lines) {
    $parts = ($line.ToString() -split '\s+') | Where-Object { $_ -ne '' };
    if ($parts.Length -ge 5 -and $parts[3] -eq 'LISTENING') {
      $processId = [int]$parts[4];
      if ($processId -ne 0 -and $processIds -notcontains $processId) {
        $processIds += $processId;
      }
    }
  }
}
foreach ($processId in $processIds) {
  try { Stop-Process -Id $processId -Force -ErrorAction Stop } catch {}
}
"""
    ps_command = command.replace("__PORTS__", ",".join(str(port) for port in ports))
    return subprocess.call(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            ps_command,
        ]
    )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    ports = chosen_ports(args)

    print("Stopping Teslatron services")
    print("Ports  :", ", ".join(str(port) for port in ports))

    exit_code = stop_ports(ports)

    open_ports = [port for port in ports if is_local_service_port_open(port)]
    if open_ports:
        print("Still open:", ", ".join(str(port) for port in open_ports), file=sys.stderr)
        raise SystemExit(exit_code or 1)

    print("All requested Teslatron ports are closed.")


if __name__ == "__main__":
    main()
