from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

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


def query_resource(
    name: str,
    address: str,
    commands: list[str],
    timeout_ms: int,
) -> None:
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


def query_all(args: argparse.Namespace) -> None:
    print(time.strftime("# %Y-%m-%d %H:%M:%S"))
    query_resource("itc", args.itc, DEFAULT_COMMANDS["itc"], args.timeout_ms)
    query_resource("ips", args.ips, DEFAULT_COMMANDS["ips"], args.timeout_ms)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Mercury diagnostics.")
    parser.add_argument("--itc", default="TCPIP0::172.31.109.115::7020::SOCKET")
    parser.add_argument("--ips", default="TCPIP0::172.31.109.116::7020::SOCKET")
    parser.add_argument("--timeout-ms", type=int, default=3000)
    parser.add_argument(
        "--watch",
        type=float,
        metavar="SECONDS",
        help="Repeat diagnostics every SECONDS until interrupted.",
    )
    parser.add_argument(
        "--count",
        type=int,
        help="Stop after COUNT acquisitions. Implies repeated mode when --watch is set.",
    )
    args = parser.parse_args()

    if args.watch is None:
        query_all(args)
        return 0

    if args.watch <= 0:
        parser.error("--watch must be greater than zero")
    if args.count is not None and args.count <= 0:
        parser.error("--count must be greater than zero")

    acquired = 0
    try:
        while args.count is None or acquired < args.count:
            if acquired:
                print()
            query_all(args)
            acquired += 1
            if args.count is None or acquired < args.count:
                time.sleep(args.watch)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
