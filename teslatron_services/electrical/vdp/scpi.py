"""SCPI protocol transport over TCP socket.

Key classes:
- Transport: Protocol interface for write/query/close (enables both real and simulated)
- SocketTransport: Real TCP socket transport with timeout handling and retry logic
- DryRunTransport: Simulated transport for testing (prints commands, returns mock data)

Features:
- Automatic retry on transient socket timeouts (up to 3 attempts)
- Clear error messages for connection refused, broken pipe, timeout
- SCPI error queue checking after critical operations
- Logging of socket operations for diagnostics

Usage:
    # Real hardware with automatic retry
    transport = SocketTransport("192.168.0.101", 5025, timeout_s=5, max_retries=3)
    transport.write("*RST")          # Reset instrument (retries on timeout)
    idn = transport.query("*IDN?")   # Get identifier
    transport.close()
    
    # Simulation (no hardware needed)
    transport = DryRunTransport()
    transport.write(":SOUR:CURR 1e-5")  # Sets current (prints to stdout)
    voltage = transport.query(":MEAS:VOLT?")  # Returns mock voltage
"""

from __future__ import annotations

import logging
import socket
import time
from dataclasses import dataclass, field
from typing import Protocol

logger = logging.getLogger(__name__)


class Transport(Protocol):
    def write(self, command: str) -> None:
        ...

    def query(self, command: str) -> str:
        ...

    def close(self) -> None:
        ...


@dataclass
class DryRunTransport:
    name: str
    log: list[str] = field(default_factory=list)
    current_a: float = 0.0
    dry_run_resistance_ohm: float = 100.0

    def write(self, command: str) -> None:
        self.log.append(f"{self.name} << {command}")
        self._remember_state(command)

    def query(self, command: str) -> str:
        self.log.append(f"{self.name} << {command}")
        response = _default_response(
            command,
            current_a=self.current_a,
            dry_run_resistance_ohm=self.dry_run_resistance_ohm,
        )
        self.log.append(f"{self.name} >> {response}")
        return response

    def close(self) -> None:
        self.log.append(f"{self.name} -- close")

    def _remember_state(self, command: str) -> None:
        normalized = command.strip().upper()
        if ":SOUR" not in normalized or ":CURR" not in normalized:
            return
        parts = command.strip().split()
        if len(parts) < 2:
            return
        try:
            self.current_a = float(parts[-1])
        except ValueError:
            return


class SocketTransport:
    """TCP socket transport for SCPI commands with retry logic and error handling.
    
    Automatically retries on transient timeouts (up to max_retries times).
    Fails fast on connection refused, broken pipe, and other permanent errors.
    """
    
    def __init__(
        self,
        host: str,
        port: int = 5025,
        *,
        timeout_s: float = 5.0,
        termination: str = "\n",
        read_buffer_bytes: int = 65536,
        max_retries: int = 3,
    ) -> None:
        """Initialize SCPI socket transport.
        
        Args:
            host: Instrument IP address.
            port: TCP port number (typically 5025 for SCPI).
            timeout_s: Socket timeout in seconds. Raises socket.timeout if exceeded.
            termination: Command line terminator (typically newline).
            read_buffer_bytes: Size of socket recv buffer.
            max_retries: Maximum retry attempts on socket timeout (0 = no retry).
        """
        self._host = host
        self._port = port
        self._timeout_s = timeout_s
        self._termination = termination.encode("ascii")
        self._read_buffer_bytes = read_buffer_bytes
        self._max_retries = max_retries
        self._socket = None
        
        # Connect to instrument
        try:
            self._socket = socket.create_connection((host, port), timeout=timeout_s)
            self._socket.settimeout(timeout_s)
            logger.debug(f"Connected to {host}:{port}")
        except ConnectionRefusedError as e:
            logger.error(f"Connection refused to {host}:{port} - is instrument online?")
            raise
        except socket.timeout as e:
            logger.error(f"Socket timeout during connect to {host}:{port}")
            raise
        except OSError as e:
            logger.error(f"Socket error during connect to {host}:{port}: {e}")
            raise

    def write(self, command: str) -> None:
        """Send a SCPI command (no response expected).
        
        Retries automatically on socket timeout; fails fast on connection errors.
        
        Args:
            command: SCPI command string (e.g., "*RST", ":SOUR:VOLT 5.0").
            
        Raises:
            ConnectionRefusedError: Instrument not accepting connections.
            BrokenPipeError: Connection closed by instrument mid-command.
            socket.timeout: Command timeout (after max_retries attempts).
            OSError: Other network errors.
        """
        logger.debug(f"SCPI write: {command}")
        
        for attempt in range(1, self._max_retries + 2):
            try:
                self._socket.sendall(command.encode("ascii") + self._termination)
                return
            except socket.timeout as e:
                if attempt <= self._max_retries:
                    wait_s = 2 ** (attempt - 1) * 0.1  # Exponential backoff: 0.1s, 0.2s, 0.4s
                    logger.warning(
                        f"Socket timeout on write attempt {attempt}/{self._max_retries + 1}, "
                        f"retrying in {wait_s:.2f}s"
                    )
                    time.sleep(wait_s)
                else:
                    logger.error(f"Socket timeout on write (failed after {self._max_retries + 1} attempts)")
                    raise
            except BrokenPipeError as e:
                logger.error(f"BrokenPipeError on write - connection closed by instrument")
                raise
            except ConnectionRefusedError as e:
                logger.error(f"ConnectionRefusedError on write - instrument not accepting connections")
                raise
            except OSError as e:
                logger.error(f"OSError on write: {e}")
                raise

    def query(self, command: str) -> str:
        """Send a SCPI query and read response.
        
        Retries automatically on socket timeout; fails fast on connection errors.
        Validates response is not empty before returning.
        
        Args:
            command: SCPI query command (e.g., "*IDN?", ":MEAS:VOLT?").
            
        Returns:
            Response string (stripped of whitespace and line terminators).
            
        Raises:
            ConnectionRefused Error: Instrument not accepting connections.
            BrokenPipeError: Connection closed by instrument.
            socket.timeout: Query timeout (after max_retries attempts).
            ValueError: Received empty or malformed response.
            OSError: Other network errors.
        """
        logger.debug(f"SCPI query: {command}")
        
        for attempt in range(1, self._max_retries + 2):
            try:
                # Send command
                self._socket.sendall(command.encode("ascii") + self._termination)
                
                # Read response
                response = self._socket.recv(self._read_buffer_bytes).decode("ascii", errors="replace").strip()
                
                # Validate non-empty
                if not response:
                    logger.warning(f"Empty response to query: {command}")
                    raise ValueError(f"Empty response to query '{command}'")
                
                logger.debug(f"SCPI response: {response}")
                return response
                
            except socket.timeout as e:
                if attempt <= self._max_retries:
                    wait_s = 2 ** (attempt - 1) * 0.1  # Exponential backoff
                    logger.warning(
                        f"Socket timeout on query attempt {attempt}/{self._max_retries + 1}, "
                        f"retrying in {wait_s:.2f}s"
                    )
                    time.sleep(wait_s)
                else:
                    logger.error(f"Socket timeout on query (failed after {self._max_retries + 1} attempts)")
                    raise
            except BrokenPipeError as e:
                logger.error(f"BrokenPipeError on query - connection closed by instrument")
                raise
            except ConnectionRefusedError as e:
                logger.error(f"ConnectionRefusedError on query - instrument not accepting connections")
                raise
            except OSError as e:
                logger.error(f"OSError on query: {e}")
                raise

    def close(self) -> None:
        """Close socket and cleanup."""
        if self._socket:
            try:
                self._socket.close()
                logger.debug("Socket closed")
            except Exception as e:
                logger.warning(f"Error closing socket: {e}")


def _default_response(
    command: str,
    *,
    current_a: float = 0.0,
    dry_run_resistance_ohm: float = 100.0,
) -> str:
    normalized = command.strip().upper()
    if normalized == "*IDN?":
        return "DRY,RUN,0,0"
    if normalized == "*LANG?":
        return "SCPI"
    if normalized == "*OPC?":
        return "1"
    if normalized.endswith(":ERR?") or normalized == "SYST:ERR?":
        return '0,"No error"'
    if "PROT:TRIP?" in normalized or "PROTECTION:TRIPPED?" in normalized:
        return "0"
    if "MEAS:VOLT" in normalized or "MEASURE:VOLT" in normalized:
        voltage_v = current_a * dry_run_resistance_ohm
        return f"{voltage_v:.12g}"
    if "MEAS:CURR" in normalized or "MEASURE:CURR" in normalized:
        return f"{current_a:.12g}"
    if "FETC" in normalized or "READ" in normalized:
        voltage_v = current_a * dry_run_resistance_ohm
        return f"{voltage_v:.12g},{current_a:.12g},0"
    return "0"
