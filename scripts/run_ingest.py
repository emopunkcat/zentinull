"""
Run all 6 source ingestors.
"""

from zentinull.ingest_adapter import run_ingest
from zentinull.logging_config import get_logger, setup
from zentinull.manifest import load_manifest

setup()
log = get_logger("run_ingest")


def main():
    manifest = load_manifest()
    totals = run_ingest(manifest)

    log.info({"event": "summary"})
    for name, n in totals.items():
        status = "OK" if n >= 0 else "FAILED"
        log.info({"event": "result", "source": name, "status": status, "rows": n})


if __name__ == "__main__":
    main()
