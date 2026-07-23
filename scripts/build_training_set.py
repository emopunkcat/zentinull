"""Fast training set builder — pre-indexed, no DataFrame scans."""

import random
from collections import defaultdict

import pandas as pd

from zentinull.config import get_paths
from zentinull.logging_config import get_logger, setup

random.seed(42)
setup()
log = get_logger("training")
paths = get_paths()

CSV = str(paths.csv_dir / "devices.csv")
OUT = str(paths.splink_output_dir / "training_labels.csv")

df = pd.read_csv(CSV)
df.columns = [c.strip() for c in df.columns]
df["unique_id"] = df["source"] + "::" + df["source_id"].astype(str)

# Pre-index everything into dicts
uid2row = {}
name2uids = defaultdict(lambda: defaultdict(list))
mac2uids = defaultdict(lambda: defaultdict(list))
ser2uids = defaultdict(lambda: defaultdict(list))

for _i, r in df.iterrows():
    uid = r["unique_id"]
    src = r["source"]
    uid2row[uid] = r

    # Name
    nm = str(r["name"]).strip().lower().split(".")[0] if pd.notna(r["name"]) else None
    if nm and nm != "nan":
        name2uids[nm][src].append(uid)

    # MAC
    mac = str(r["mac_address"]).strip().lower() if pd.notna(r["mac_address"]) else ""
    mac = mac.split(",")[0].replace(":", "").replace("-", "").replace(".", "")
    if len(mac) == 12:
        mac2uids[mac][src].append(uid)

    # Serial
    ser = str(r["serial_number"]).strip().lower() if pd.notna(r["serial_number"]) else ""
    if ser and ser != "nan":
        ser2uids[ser][src].append(uid)

log.info({"event": "indexed", "records": len(uid2row)})

# ── MATCH pairs ────────────────────────────────────────────────────────────
pairs = set()  # (uid_l, uid_r) with uid_l < uid_r


def add_cross_pairs(src_map):
    """Add one pair per source-pair per entity."""
    sources = list(src_map.keys())
    for i in range(len(sources)):
        for j in range(i + 1, len(sources)):
            a, b = src_map[sources[i]][0], src_map[sources[j]][0]
            if a > b:
                a, b = b, a
            pairs.add((a, b))


# Name matches
for _nm, sm in name2uids.items():
    if len(sm) >= 2:
        add_cross_pairs(sm)
log.info({"event": "pairs", "type": "name", "count": len(pairs)})

# MAC matches
for _mac, sm in mac2uids.items():
    if len(sm) >= 2:
        add_cross_pairs(sm)
log.info({"event": "pairs", "type": "mac", "count": len(pairs)})

# Serial matches
for _ser, sm in ser2uids.items():
    if len(sm) >= 2:
        add_cross_pairs(sm)
log.info({"event": "pairs", "type": "serial", "count": len(pairs)})

# ── Build labels ───────────────────────────────────────────────────────────
labels = []
for a, b in pairs:
    labels.append(
        {
            "unique_id_l": a,
            "unique_id_r": b,
            "source_dataset_l": "__splink__df_predict",
            "source_dataset_r": "__splink__df_predict",
            "label": 1,
        }
    )

# Non-matches: random cross-source pairs NOT in existing pairs
all_uids = list(uid2row.keys())
target = len(labels) * 2
attempts = 0
while len(labels) < len(pairs) + target and attempts < 50000:
    a = random.choice(all_uids)
    b = random.choice(all_uids)
    if a == b:
        continue
    if uid2row[a]["source"] == uid2row[b]["source"]:
        continue  # must be cross-source
    key = (a, b) if a < b else (b, a)
    if key in pairs:
        continue
    labels.append(
        {
            "unique_id_l": a,
            "unique_id_r": b,
            "source_dataset_l": "__splink__df_predict",
            "source_dataset_r": "__splink__df_predict",
            "label": 0,
        }
    )
    pairs.add(key)
    attempts += 1

pos = [lb for lb in labels if lb["label"] == 1]
neg = [lb for lb in labels if lb["label"] == 0]
log.info({"event": "final", "match": len(pos), "non_match": len(neg), "total": len(labels)})

# Source distribution
ps = defaultdict(int)
for lb in pos:
    rl = uid2row[lb["unique_id_l"]]
    rr = uid2row[lb["unique_id_r"]]
    ps[f"{rl['source']}↔{rr['source']}"] += 1
for k, v in sorted(ps.items(), key=lambda x: -x[1])[:12]:
    log.info({"event": "source_dist", "pair": k, "count": v})

# Top devices
dev_srcs = defaultdict(set)
for lb in pos:
    rl = uid2row[lb["unique_id_l"]]
    rr = uid2row[lb["unique_id_r"]]
    nm = (rl.get("name") and str(rl["name"]).split(".")[0].lower()) or "?"
    dev_srcs[nm].add(rl["source"])
    dev_srcs[nm].add(rr["source"])
log.info({"event": "top_devices_header"})
for nm in sorted(dev_srcs, key=lambda n: -len(dev_srcs[n]))[:10]:
    log.info(
        {
            "event": "top_device",
            "name": nm.strip(),
            "sources": len(dev_srcs[nm]),
            "source_list": ", ".join(sorted(dev_srcs[nm])),
        }
    )

pd.DataFrame(labels).to_csv(OUT, index=False)
log.info({"event": "saved", "labels": len(labels), "path": OUT})
