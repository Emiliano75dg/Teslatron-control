from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from teslatron_services.cryostat.backends import MercuryResource


DEFAULT_COMMANDS = {
    "itc": [
        "READ:SYS:CAT",
        "READ:DEV:DB8.T1:TEMP:SIG:TEMP?",
        "READ:DEV:MB1.T1:TEMP:SIG:TEMP?",
        "READ:DEV:DB5.P1:PRES:SIG:PRES?",
        "READ:DEV:DB5.P1:PRES:LOOP:PRST?",
        "READ:DEV:DB5.P1:PRES:LOOP:FSET?",
    ],
    "ips": [
        "READ:SYS:CAT",
        "READ:DEV:GRPZ:PSU:SIG:FLD?",
        "READ:DEV:GRPZ:PSU:SIG:CURR?",
        "READ:DEV:GRPZ:PSU:SIG:VOLT?",
        "READ:DEV:GRPZ:PSU:SIG:FSET?",
        "READ:DEV:GRPZ:PSU:SIG:RFLD?",
        "READ:DEV:GRPZ:PSU:SIG:SWHT?",
        "READ:DEV:MB1.T1:TEMP:SIG:TEMP?",
        "READ:DEV:DB8.T1:TEMP:SIG:TEMP?",
        "READ:DEV:DB7.T1:TEMP:SIG:TEMP?",
    ],
}


def query_resource(name: str, address: str, commands: list[str], timeout_ms: int) -> None:
    print(f"## {name} {address}")
    resource = MercuryResource(
        address,
        timeout_ms=timeout_ms,
        read_termination="\n",
        write_termination="\n",
    )
    for command in commands:
        try:
            response = resource.query(command).strip()
            print(f"{command} => {response}")
        except Exception as exc:
            print(f"{command} !! {type(exc).__name__}: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Mercury diagnostics.")
    parser.add_argument("--itc", default="TCPIP0::172.31.109.115::7020::SOCKET")
    parser.add_argument("--ips", default="TCPIP0::172.31.109.116::7020::SOCKET")
    parser.add_argument("--timeout-ms", type=int, default=3000)
    args = parser.parse_args()

    query_resource("itc", args.itc, DEFAULT_COMMANDS["itc"], args.timeout_ms)
    query_resource("ips", args.ips, DEFAULT_COMMANDS["ips"], args.timeout_ms)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
