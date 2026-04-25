"""Feature 015 — US3 reference-video regression runner (T017 + T020).

Exit code:
    0 = every video's kb_items count landed inside its expected range
    1 = at least one failure (HTTP error, timeout, or count out of range)

Typical usage on a deployment host::

    # US3 — manifest-driven regression
    python specs/015-kb-pipeline-real-algorithms/scripts/run_reference_regression.py \
        --manifest specs/015-kb-pipeline-real-algorithms/reference_videos.json \
        --output specs/015-kb-pipeline-real-algorithms/verification.md

    # US4 — also record extraction_jobs wallclock vs Feature-002 baseline
    python specs/015-kb-pipeline-real-algorithms/scripts/run_reference_regression.py \
        --manifest specs/015-kb-pipeline-real-algorithms/reference_videos.json \
        --output specs/015-kb-pipeline-real-algorithms/verification.md \
        --measure-wallclock

    # Opportunistic sampling (no manifest — any 10 already-classified videos,
    # default expected range [1, 50])
    python specs/015-kb-pipeline-real-algorithms/scripts/run_reference_regression.py \
        --random-sample 10 \
        --output /tmp/verification.md

The module also exposes ``run_regression(...)`` so the T018 integration test
can drive the same logic with a MockTransport-backed HTTP client.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://127.0.0.1:8080/api/v1"
DEFAULT_POLL_INTERVAL_S = 5.0
DEFAULT_TIMEOUT_S = 3600.0            # 1 h per video (for 10-min clips)
TERMINAL_STATUSES = {"success", "failed", "canceled"}
SAMPLE_MIN_DEFAULT = 1
SAMPLE_MAX_DEFAULT = 50


# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class ReferenceVideo:
    name: str
    cos_object_key: str
    tech_category: str
    expected_items_min: int
    expected_items_max: int
    has_speech: bool = False
    baseline_f002_seconds: float | None = None
    notes: str = ""


@dataclass
class VideoRunResult:
    name: str
    cos_object_key: str
    status: str                         # 'success' / 'failed' / 'timeout' / 'http_error'
    duration_s: float | None
    visual_items: int
    audio_items: int
    visual_plus_audio_items: int
    total_items: int
    expected_min: int
    expected_max: int
    passed: bool
    error_message: str = ""
    baseline_f002_seconds: float | None = None

    @property
    def wallclock_ratio(self) -> float | None:
        if self.baseline_f002_seconds and self.duration_s:
            return self.duration_s / self.baseline_f002_seconds
        return None


# ── Manifest loading ─────────────────────────────────────────────────────────


def load_manifest(path: Path) -> list[ReferenceVideo]:
    data = json.loads(path.read_text(encoding="utf-8"))
    videos = data.get("videos") or []
    out: list[ReferenceVideo] = []
    for entry in videos:
        out.append(ReferenceVideo(
            name=str(entry["name"]),
            cos_object_key=str(entry["cos_object_key"]),
            tech_category=str(entry["tech_category"]),
            expected_items_min=int(entry["expected_items_min"]),
            expected_items_max=int(entry["expected_items_max"]),
            has_speech=bool(entry.get("has_speech", False)),
            baseline_f002_seconds=(
                float(entry["baseline_f002_seconds"])
                if entry.get("baseline_f002_seconds") not in (None, "")
                else None
            ),
            notes=str(entry.get("notes", "")),
        ))
    return out


# ── Core HTTP interactions ───────────────────────────────────────────────────


def _submit_job(client: httpx.Client, base_url: str, video: ReferenceVideo) -> str:
    """POST /tasks/kb-extraction → return the job_id (== task_id's extraction_job)."""
    resp = client.post(
        f"{base_url}/tasks/kb-extraction",
        json={"cos_object_key": video.cos_object_key, "force": True},
    )
    resp.raise_for_status()
    body = resp.json()
    items = body.get("items") or []
    if not items or not items[0].get("task_id"):
        raise RuntimeError(
            f"submission for {video.name} returned no task_id: {body}"
        )
    task_id = items[0]["task_id"]

    # Resolve analysis_task → extraction_job_id via the GET /tasks/{id} endpoint.
    task_resp = client.get(f"{base_url}/tasks/{task_id}")
    task_resp.raise_for_status()
    tbody = task_resp.json()
    job_id = tbody.get("extraction_job_id")
    if not job_id:
        raise RuntimeError(
            f"task {task_id} has no extraction_job_id yet — Worker may not "
            "have picked up the submission"
        )
    return str(job_id)


def _poll_job(
    client: httpx.Client,
    base_url: str,
    job_id: str,
    *,
    poll_interval_s: float,
    timeout_s: float,
) -> dict[str, Any]:
    """Poll GET /extraction-jobs/{id} until a terminal state or timeout."""
    deadline = time.monotonic() + timeout_s
    last_body: dict[str, Any] = {}
    while time.monotonic() < deadline:
        resp = client.get(f"{base_url}/extraction-jobs/{job_id}")
        resp.raise_for_status()
        body = resp.json()
        status = str(body.get("status") or "").lower()
        last_body = body
        if status in TERMINAL_STATUSES:
            return body
        time.sleep(poll_interval_s)
    last_body["_timed_out"] = True
    return last_body


def _count_tech_points_by_source(
    client: httpx.Client, base_url: str, task_id: str
) -> tuple[int, int, int, int]:
    """Count expert_tech_points for a task bucketed by source_type.

    We reuse the tasks detail endpoint when available; otherwise the caller
    can pass a pre-counted dict via the ``--stats-endpoint`` future hook.
    Returns ``(visual, audio, visual_plus_audio, total)``.
    """
    # There is no dedicated per-source count endpoint; use tasks/{id} which
    # exposes ``tech_points_by_source`` in Feature-014/015.
    resp = client.get(f"{base_url}/tasks/{task_id}")
    resp.raise_for_status()
    body = resp.json()
    counts = body.get("tech_points_by_source") or {}
    visual = int(counts.get("visual", 0))
    audio = int(counts.get("audio", 0))
    both = int(counts.get("visual+audio", 0))
    total = visual + audio + both
    return visual, audio, both, total


# ── Orchestrator ────────────────────────────────────────────────────────────


def run_one(
    client: httpx.Client,
    base_url: str,
    video: ReferenceVideo,
    *,
    poll_interval_s: float,
    timeout_s: float,
) -> VideoRunResult:
    try:
        job_id = _submit_job(client, base_url, video)
    except httpx.HTTPError as exc:
        return _fail_result(video, "http_error", f"submit failed: {exc}")
    except RuntimeError as exc:
        return _fail_result(video, "failed", str(exc))

    job_body = _poll_job(
        client, base_url, job_id,
        poll_interval_s=poll_interval_s,
        timeout_s=timeout_s,
    )

    if job_body.get("_timed_out"):
        return _fail_result(video, "timeout",
                            f"did not reach terminal state within {timeout_s}s")

    status = str(job_body.get("status") or "").lower()
    duration_s = _duration_seconds(job_body)

    if status != "success":
        err = str(job_body.get("error_message") or "")
        return VideoRunResult(
            name=video.name,
            cos_object_key=video.cos_object_key,
            status=status or "failed",
            duration_s=duration_s,
            visual_items=0,
            audio_items=0,
            visual_plus_audio_items=0,
            total_items=0,
            expected_min=video.expected_items_min,
            expected_max=video.expected_items_max,
            passed=False,
            error_message=err[:500],
            baseline_f002_seconds=video.baseline_f002_seconds,
        )

    task_id = str(job_body.get("analysis_task_id") or "")
    try:
        v, a, va, total = _count_tech_points_by_source(client, base_url, task_id)
    except httpx.HTTPError as exc:
        return _fail_result(video, "http_error", f"count failed: {exc}")

    passed = video.expected_items_min <= total <= video.expected_items_max
    return VideoRunResult(
        name=video.name,
        cos_object_key=video.cos_object_key,
        status=status,
        duration_s=duration_s,
        visual_items=v,
        audio_items=a,
        visual_plus_audio_items=va,
        total_items=total,
        expected_min=video.expected_items_min,
        expected_max=video.expected_items_max,
        passed=passed,
        error_message="" if passed else
            f"total={total} outside [{video.expected_items_min},{video.expected_items_max}]",
        baseline_f002_seconds=video.baseline_f002_seconds,
    )


def run_regression(
    videos: list[ReferenceVideo],
    *,
    base_url: str = DEFAULT_BASE_URL,
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    client: httpx.Client | None = None,
    measure_wallclock: bool = False,
) -> list[VideoRunResult]:
    """Drive the regression over every video and return structured results."""
    owns_client = client is None
    if client is None:
        client = httpx.Client(timeout=httpx.Timeout(60.0))

    results: list[VideoRunResult] = []
    try:
        for video in videos:
            logger.info("→ regression: %s (%s)", video.name, video.cos_object_key)
            result = run_one(
                client, base_url, video,
                poll_interval_s=poll_interval_s, timeout_s=timeout_s,
            )
            results.append(result)
            logger.info(
                "  status=%s total_items=%d expected=[%d,%d] passed=%s",
                result.status, result.total_items,
                result.expected_min, result.expected_max, result.passed,
            )
    finally:
        if owns_client:
            client.close()
    return results


# ── Report rendering ─────────────────────────────────────────────────────────


def render_verification_md(
    results: list[VideoRunResult],
    *,
    measure_wallclock: bool = False,
) -> str:
    """Build a Markdown report suitable for verification.md."""
    lines: list[str] = []
    lines.append("# Feature-015 Reference Video Regression Report")
    lines.append("")
    lines.append(f"- Total videos: **{len(results)}**")
    passed_ct = sum(1 for r in results if r.passed)
    lines.append(f"- Passed: **{passed_ct} / {len(results)}**")
    lines.append("")

    headers = [
        "Video", "Status", "Duration(s)", "Visual",
        "Audio", "Visual+Audio", "Total",
        "Expected [min, max]", "Passed?", "Error",
    ]
    if measure_wallclock:
        headers.insert(-1, "Baseline(s)")
        headers.insert(-1, "Ratio vs Baseline")

    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")

    for r in results:
        row = [
            r.name,
            r.status,
            f"{r.duration_s:.1f}" if r.duration_s is not None else "-",
            str(r.visual_items),
            str(r.audio_items),
            str(r.visual_plus_audio_items),
            str(r.total_items),
            f"[{r.expected_min}, {r.expected_max}]",
        ]
        if measure_wallclock:
            row.append(
                f"{r.baseline_f002_seconds:.1f}"
                if r.baseline_f002_seconds is not None else "-"
            )
            ratio = r.wallclock_ratio
            if ratio is None:
                row.append("-")
            else:
                verdict = "PASS" if ratio <= 0.9 else "FAIL"
                row.append(f"{ratio:.2f} ({verdict})")
        row.append("✓" if r.passed else "✗")
        row.append(r.error_message or "")
        lines.append("| " + " | ".join(row) + " |")

    lines.append("")
    lines.append(
        f"Generated by `specs/015-kb-pipeline-real-algorithms/scripts/"
        f"run_reference_regression.py` — measure_wallclock="
        f"{str(measure_wallclock).lower()}."
    )
    return "\n".join(lines) + "\n"


# ── Helpers ──────────────────────────────────────────────────────────────────


def _fail_result(video: ReferenceVideo, status: str, msg: str) -> VideoRunResult:
    return VideoRunResult(
        name=video.name,
        cos_object_key=video.cos_object_key,
        status=status,
        duration_s=None,
        visual_items=0,
        audio_items=0,
        visual_plus_audio_items=0,
        total_items=0,
        expected_min=video.expected_items_min,
        expected_max=video.expected_items_max,
        passed=False,
        error_message=msg[:500],
        baseline_f002_seconds=video.baseline_f002_seconds,
    )


def _duration_seconds(job_body: dict[str, Any]) -> float | None:
    """Parse ``started_at``/``completed_at`` from the ExtractionJob payload."""
    from datetime import datetime
    started = job_body.get("started_at")
    completed = job_body.get("completed_at")
    if not started or not completed:
        return None
    try:
        t0 = datetime.fromisoformat(started.replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(completed.replace("Z", "+00:00"))
        return (t1 - t0).total_seconds()
    except (ValueError, AttributeError):
        return None


# ── Random-sample mode ───────────────────────────────────────────────────────


def fetch_random_sample(
    client: httpx.Client, base_url: str, limit: int
) -> list[ReferenceVideo]:
    """Pick ``limit`` already-classified videos from /classifications."""
    resp = client.get(
        f"{base_url}/classifications",
        params={"kb_extracted": "false", "page": 1, "page_size": limit},
    )
    resp.raise_for_status()
    body = resp.json()
    rows = body.get("data") or body.get("items") or []
    videos: list[ReferenceVideo] = []
    for row in rows[:limit]:
        tech = row.get("tech_category") or "unclassified"
        if tech == "unclassified":
            continue
        videos.append(ReferenceVideo(
            name=str(row.get("filename") or row.get("cos_object_key"))[-60:],
            cos_object_key=str(row["cos_object_key"]),
            tech_category=str(tech),
            expected_items_min=SAMPLE_MIN_DEFAULT,
            expected_items_max=SAMPLE_MAX_DEFAULT,
            has_speech=False,  # unknown at sample time
        ))
    return videos


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Feature-015 reference-video regression runner"
    )
    parser.add_argument("--manifest", type=Path, default=None,
                        help="Path to reference_videos.json manifest.")
    parser.add_argument("--random-sample", type=int, default=0,
                        help="Skip manifest and pull N videos from /classifications.")
    parser.add_argument("--output", type=Path, required=True,
                        help="Destination for the Markdown verification report.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,
                        help="API base URL (default: %(default)s).")
    parser.add_argument("--poll-interval", type=float,
                        default=DEFAULT_POLL_INTERVAL_S)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--measure-wallclock", action="store_true",
                        help="Include duration + ratio-vs-baseline columns (US4 SC-002).")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if not args.manifest and args.random_sample <= 0:
        parser.error("either --manifest or --random-sample N is required")

    if args.manifest:
        videos = load_manifest(args.manifest)
    else:
        with httpx.Client(timeout=60.0) as c:
            videos = fetch_random_sample(c, args.base_url, args.random_sample)

    if not videos:
        print("No videos to regress against — aborting.", file=sys.stderr)
        return 1

    results = run_regression(
        videos,
        base_url=args.base_url,
        poll_interval_s=args.poll_interval,
        timeout_s=args.timeout,
        measure_wallclock=args.measure_wallclock,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        render_verification_md(results, measure_wallclock=args.measure_wallclock),
        encoding="utf-8",
    )
    logger.info("Report written to %s", args.output)

    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
