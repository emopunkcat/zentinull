"""Splink shim — delegates to profile-driven splink_runner."""

from zentinull.config import PATHS
from zentinull.manifest import load_manifest
from zentinull.resolve.splink_runner import run

manifest = load_manifest()
profile = manifest.profiles["device"]
csv_path = str(PATHS.csv_dir / "devices.csv")
labels_path = str(PATHS.splink_output_dir / "training_labels.csv")
run(profile, csv_path, labels_path=labels_path)
