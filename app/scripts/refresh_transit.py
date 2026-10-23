from __future__ import annotations

import argparse

from app.travel.transit.stream_ingestion import ingest_gtfs_file


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate and activate a local GTFS feed safely."
    )
    parser.add_argument(
        "--provider",
        required=True,
        choices=("tgsrtc", "hmrl"),
    )
    parser.add_argument(
        "--file",
        required=True,
        help="Path to the downloaded GTFS ZIP file.",
    )
    args = parser.parse_args()

    feed_id = ingest_gtfs_file(args.provider, args.file)
    print(f"Activated feed {feed_id} for {args.provider}.")


if __name__ == "__main__":
    main()
