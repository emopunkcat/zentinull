"""
Run all 6 source ingestors.
"""

from zentinull.ingestors import ad, fortigate, manageengine, servicedeskplus, sharepoint, zabbix
from zentinull.logging_config import get_logger, setup

SOURCES = [
    ("SharePoint", sharepoint.ingest),
    ("ManageEngine", manageengine.ingest),
    ("FortiGate", fortigate.ingest),
    ("Zabbix", zabbix.ingest),
    ("Active Directory", ad.ingest),
    ("ServiceDesk Plus", servicedeskplus.ingest),
]
setup()
log = get_logger("run_ingest")


def main():
    totals = {}
    for name, fn in SOURCES:
        log.info({"event": "ingesting", "source": name})
        try:
            n = fn()
            totals[name] = n
        except Exception as e:
            log.error({"event": "ingest_failed", "source": name, "error": str(e)})
            totals[name] = -1

    log.info({"event": "summary"})
    for name, n in totals.items():
        status = "OK" if n >= 0 else "FAILED"
        log.info({"event": "result", "source": name, "status": status, "rows": n})


if __name__ == "__main__":
    main()
