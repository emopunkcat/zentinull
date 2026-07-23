"""Splink shim — delegates to profile-driven splink_runner."""

from zentinull.config import get_paths
from zentinull.manifest import load_manifest
from zentinull.resolve.splink_runner import run

manifest = load_manifest()
paths = get_paths()
profile = manifest.profiles["device"]
csv_path = paths.csv_dir / "devices.csv"
tmp_path = paths.csv_dir / "devices.csv.tmp"
if tmp_path.exists() and (not csv_path.exists() or tmp_path.stat().st_mtime > csv_path.stat().st_mtime):
    csv_path = tmp_path
csv_path = str(csv_path)
labels_path = str(paths.splink_output_dir / "training_labels.csv")
run(profile, csv_path, labels_path=labels_path)
