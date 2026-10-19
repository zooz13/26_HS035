from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


SAMPLE_WINDOW = {
    "window_id": "win_test_001",
    "device_group_id": "room_1",
    "device_ids": ["esp32_1", "esp32_2"],
    "start_time": "2026-06-13T15:24:08",
    "end_time": "2026-06-13T15:24:10",
    "data_type": "window_matrix",
    "shape": [128, 192],
    "data": [[0.05 if (row + col) % 17 == 0 else 0.0 for col in range(192)] for row in range(128)],
    "sampling_rate_hz": 50,
    "window_size": 128,
    "preprocess_version": "preprocess_v1",
    "normalization_version": "norm_v1_log_amp_empty_robust_zscore",
    "feature_version": "stage1_window_features_v1",
    "baseline_version": "baseline_v1",
    "source": "test",
    "metadata": {},
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a sample ProcessedWindow to the V1 model server.")
    parser.add_argument("--url", default="http://127.0.0.1:8000/api/window")
    args = parser.parse_args()

    body = json.dumps(SAMPLE_WINDOW).encode("utf-8")
    request = urllib.request.Request(
        args.url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0
    except urllib.error.URLError as exc:
        print(f"Could not reach model server at {args.url}: {exc}", file=sys.stderr)
        print("Start it with: python v1/scripts/run_model_server.py", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
