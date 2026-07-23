#!/usr/bin/env python3
"""Interactive device query shell for Zentinull API.

Commands:
  <query>           resolve device by name, serial, MAC, IP, or cluster_id
  :history          show field change history
  :sot              show SOT-resolved canonical values
  :drift            show cross-source field audit
  :attachments      show linked records (notes, purchases, employees, account info)
  :anomalies        show cluster review flags for this device
  :sources          list all sources for this device
  :geo              show MDM location data (if available)
  :timeline [h]     show timeline events (default 168h)
  :search <term>    search all devices by name
  :set host <url>   set API host (default http://localhost:8001)
  :help             show this help
  :quit             exit

Example: python3 SCRIPTS/dq.py WS29
If a query is given as an argument, the device is loaded immediately.
Otherwise, the shell starts in interactive mode.
"""

from __future__ import annotations

import cmd
import json
import os
import sys
from urllib.error import URLError
from urllib.request import Request, urlopen


class DeviceQueryShell(cmd.Cmd):
    intro = "Zentinull device query shell. Type :help for commands."
    prompt = "dq> "
    doc_header = "Commands (type :help <command>):"

    def __init__(self, host: str = "http://localhost:8001"):
        super().__init__()
        self.host = host.rstrip("/")
        self._device: dict | str | None = None
        self._device_name: str | None = None

    # ── helpers ──

    def _get(self, path: str) -> dict | list | None:
        try:
            req = Request(f"{self.host}{path}", headers={"Accept": "application/json"})
            with urlopen(req, timeout=10) as r:
                return json.loads(r.read())
        except URLError as e:
            self._echo(f"ERR: {e.reason}")
            return None
        except json.JSONDecodeError:
            self._echo("ERR: invalid JSON response")
            return None

    def _echo(self, msg: str) -> None:
        print(f"\033[90m{msg}\033[0m")

    def _kv(self, key: str, value: str, indent: int = 2) -> str:
        return f"{' ' * indent}\033[1m{key}\033[0m: {value}"

    def _source_tag(self, source: str) -> str:
        colors = {"sp": 33, "me_ec": 35, "me_mdm": 35, "fg": 31, "zbx": 32, "ad": 34, "sdp": 36}
        c = colors.get(source, 37)
        return f"\033[{c}m[{source}]\033[0m"

    # ── flag helper ──

    def _requires_device(self, fn):
        """Decorator: requires a loaded device. Prompts user to query first."""
        if self._device is None or isinstance(self._device, str):
            self._echo("No device loaded. Type a query (name, serial, MAC, IP, or cluster ID).")
            return
        return fn()

    # ── command dispatch ──

    def default(self, line: str) -> None:
        """Any bare text = device query."""
        q = line.strip()
        if not q:
            return
        if q.startswith(":"):
            cmd, _, args = q[1:].partition(" ")
            meth = getattr(self, f"do_{cmd}", None)
            if meth:
                meth(args)
            else:
                self._echo(f"Unknown command: :{cmd}")
            return

        data = self._get(f"/device/{q}")
        if data is None:
            return
        if "error" in data or "detail" in data:
            self._echo(f"Device not found: {q}")
            self._device = q
            self._device_name = None
            return

        self._device = data
        self._device_name = q
        self._show_summary()

    def _show_summary(self) -> None:
        d = self._device
        print(self._kv("name", d.get("device_name", "?")))
        print(self._kv("cluster_id", d.get("cluster_id", "?")))
        print(self._kv("sources", f"{d.get('source_count', 0)} ({', '.join(d.get('sources', []))})"))
        consolidated = d.get("consolidated", {})
        if consolidated:
            key_fields = ["serial_number", "mac_address", "os", "manufacturer", "model", "assigned_user", "ip_address"]
            for k in key_fields:
                v = consolidated.get(k)
                if v and v[0]:
                    print(self._kv(k, v[0]))
        sot = d.get("sot", {})
        if sot:
            name_sot = sot.get("name", {})
            if name_sot:
                print(self._kv("sot_priority", f"{name_sot.get('source', '?')}/{name_sot.get('priority', '?')}"))
        drift = d.get("drift_audit", [])
        mismatches = [a for a in drift if a.get("verdict") == "MISMATCH"]
        if mismatches:
            print(self._kv("drift_mismatches", str(len(mismatches))))

    # ── commands ──

    def do_history(self, _arg: str) -> None:
        """Show field change history."""
        if self._device is None:
            self._echo("No device loaded.")
            return
        q = self._device_name or self._device.get("cluster_id", "")
        data = self._get(f"/device/{q}/history")
        if not data:
            return
        entries = data.get("history", [])
        print(self._kv("history", f"{len(entries)} changes"))
        for e in entries[:20]:
            src = self._source_tag(e.get("source", ""))
            print(f"  {e['changed_at'][:19]} {src}  {e['field']}")
            print(f"    \033[31m-{e['old_value'][:60]}\033[0m")
            print(f"    \033[32m+{e['new_value'][:60]}\033[0m")

    def do_sot(self, _arg: str) -> None:
        """Show SOT-resolved canonical values."""
        if self._device is None:
            self._echo("No device loaded.")
            return
        sot = self._device.get("sot", {})
        for field, info in sot.items():
            src = self._source_tag(info.get("source", ""))
            pri = info.get("priority", "")
            print(f"  \033[1m{field}\033[0m: {info['value']}  {src} \033[90m({pri})\033[0m")

    def do_drift(self, _arg: str) -> None:
        """Show cross-source field audit."""
        if self._device is None:
            self._echo("No device loaded.")
            return
        for a in self._device.get("drift_audit", []):
            v = a["verdict"]
            color = "\033[32m" if v == "MATCH" else "\033[31m" if v == "MISMATCH" else "\033[33m"
            print(f"  \033[1m{a['field']}\033[0m: {color}{v}\033[0m")
            for src, val in a.get("sources", {}).items():
                print(f"    {self._source_tag(src)} {val[:60]}")

    def do_attachments(self, _arg: str) -> None:
        """Show linked records."""
        if self._device is None:
            self._echo("No device loaded.")
            return
        q = self._device_name or self._device.get("cluster_id", "")
        data = self._get(f"/device/{q}/attachments")
        if not data or not isinstance(data, list):
            self._echo("No attachments.")
            return
        for att in data:
            kind = att.get("kind", "?")
            source = att.get("source", "?")
            count = att.get("count", 0)
            print(f"  {self._source_tag(source)} {kind}: {count} records")

    def do_anomalies(self, _arg: str) -> None:
        """Show review flags for this device."""
        if self._device is None:
            self._echo("No device loaded.")
            return
        cid = self._device.get("cluster_id", "")
        data = self._get("/anomalies")
        if not data:
            return
        for r in data.get("review_list", []):
            if r.get("cluster_id") == cid:
                print(f"  \033[31m{r['kind']}\033[0m: {r['field']} — {r.get('detail', '')}")

    def do_sources(self, _arg: str) -> None:
        """List all per-source records."""
        if self._device is None:
            self._echo("No device loaded.")
            return
        for rec in self._device.get("records", []):
            src = self._source_tag(rec.get("source", "?"))
            name = rec.get("name", rec.get("source_id", "?"))[:40]
            print(f"  {src} {name}")

    def do_geo(self, _arg: str) -> None:
        """Show MDM location data."""
        if self._device is None:
            self._echo("No device loaded.")
            return
        consolidated = self._device.get("consolidated", {})
        lat = consolidated.get("mdm_latitude", [None])[0]
        lon = consolidated.get("mdm_longitude", [None])[0]
        if lat and lon:
            acc = consolidated.get("mdm_horizontal_accuracy", ["?"])[0]
            ts = consolidated.get("mdm_located_time", ["?"])[0]
            print(f"  lat: {lat}, lon: {lon}  (±{acc}m)")
            print(f"  located: {ts}")
            # Google Maps link
            url = f"https://maps.google.com/?q={lat},{lon}"
            print(f"  \033[34m{url}\033[0m")
        else:
            self._echo("No MDM location data for this device.")

    def do_timeline(self, arg: str) -> None:
        """Show timeline events. Args: hours (default 168)."""
        if self._device is None:
            self._echo("No device loaded.")
            return
        q = self._device_name or self._device.get("cluster_id", "")
        hours = arg.strip() or "168"
        data = self._get(f"/device/{q}/timeline?hours={hours}")
        if not data:
            return
        print(self._kv("timeline", f"{data.get('count', 0)} events ({hours}h)"))
        for e in data.get("events", [])[:20]:
            ts = e.get("timestamp", "")[:19]
            print(f"  {ts}  \033[90m{e.get('event', '?')}\033[0m  {e.get('detail', '')}")

    def do_search(self, arg: str) -> None:
        """Search devices by name."""
        term = arg.strip()
        if not term:
            self._echo("Usage: :search <term>")
            return
        data = self._get(f"/search?q={term}&limit=20")
        if not data:
            return
        if isinstance(data, list):
            for d in data:
                cid = d.get("cluster_id", "?")
                name = d.get("device_name", "?")
                sources = d.get("source_count", 0)
                print(f"  \033[1m{name}\033[0m  {cid}  ({sources} sources)")
        elif isinstance(data, dict):
            results = data.get("results", [])
            for d in results:
                cid = d.get("cluster_id", "?")
                name = d.get("device_name", "?")
                sources = d.get("source_count", 0)
                print(f"  \033[1m{name}\033[0m  {cid}  ({sources} sources)")

    def do_set(self, arg: str) -> None:
        """Set config: :set host <url>"""
        parts = arg.split()
        if len(parts) == 2 and parts[0] == "host":
            self.host = parts[1].rstrip("/")
            print(f"Host set to: {self.host}")
            # Reload current device
            if self._device_name:
                data = self._get(f"/device/{self._device_name}")
                if data and "error" not in data:
                    self._device = data
        else:
            self._echo("Usage: :set host <url>")

    def do_help(self, arg: str) -> None:
        if arg:
            super().do_help(arg)
            return
        print(__doc__)

    def do_quit(self, _arg: str) -> None:
        return True

    do_EOF = do_quit  # noqa: N815 — cmd.Cmd requires this exact name


# ── scripts/serve data directory helpers ──

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_HOST = os.environ.get("ZENTINULL_API", "http://localhost:8001")


if __name__ == "__main__":
    shell = DeviceQueryShell(host=DEFAULT_HOST)
    if len(sys.argv) > 1:
        # Non-interactive: query and exit
        q = sys.argv[1]
        shell.default(q)
        # If the query returned a device, print full detail
        if isinstance(shell._device, dict):
            print()
            shell.do_sot("")
            print()
            shell.do_drift("")
    else:
        shell.cmdloop()
