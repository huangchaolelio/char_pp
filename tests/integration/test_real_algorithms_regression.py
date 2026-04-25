"""Integration test — Feature 015 reference-video regression runner (T018).

Covers:
  * ``run_regression()`` drives submit → poll → count → pass/fail verdict for
    each manifest entry against a MockTransport-backed ``httpx.Client``.
  * ``render_verification_md()`` produces a Markdown table with the expected
    rows + columns; ``--measure-wallclock`` adds the Baseline / Ratio pair.
  * ``main()`` exit code maps to "all passed" → 0 vs "any failed" → 1.

No real server, no real DB: we stub the three endpoints the runner hits and
assert on the Markdown artifact + return codes.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import httpx
import pytest


pytestmark = pytest.mark.integration


# ── Load the runner module by path (it lives under specs/, not a package) ────

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "specs"
    / "015-kb-pipeline-real-algorithms"
    / "scripts"
    / "run_reference_regression.py"
)


def _load_module():
    import sys
    spec = importlib.util.spec_from_file_location(
        "f015_reference_regression", _SCRIPT_PATH
    )
    assert spec and spec.loader, "failed to locate regression script"
    module = importlib.util.module_from_spec(spec)
    # @dataclass inspects sys.modules[cls.__module__] — register before exec.
    sys.modules["f015_reference_regression"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def runner():
    return _load_module()


# ── Mock endpoint factory ────────────────────────────────────────────────────


def _build_mock_transport(
    *,
    counts_by_cos_key: dict[str, dict[str, int]],
    job_status: str = "success",
    job_duration_s: float = 42.0,
    job_error: str = "",
) -> httpx.MockTransport:
    """Wire up the 3 endpoints the regression runner talks to.

    Each submission produces a deterministic ``task_id`` / ``extraction_job_id``
    derived from the cos_object_key so the subsequent polls/stats lookups
    agree with it.
    """
    from datetime import datetime, timedelta, timezone

    def _ids_for(cos_key: str) -> tuple[str, str]:
        # Deterministic UUID-ish strings (length + hex only — enough for httpx routing).
        h = abs(hash(cos_key))
        task_id = f"{h:032x}"[:32]
        job_id = f"{h + 1:032x}"[:32]
        return task_id, job_id

    key_for_task: dict[str, str] = {}
    key_for_job: dict[str, str] = {}
    job_for_task: dict[str, str] = {}

    for cos_key in counts_by_cos_key:
        tid, jid = _ids_for(cos_key)
        key_for_task[tid] = cos_key
        key_for_job[jid] = cos_key
        job_for_task[tid] = jid

    started = datetime(2026, 4, 25, 10, 0, 0, tzinfo=timezone.utc)
    completed = started + timedelta(seconds=job_duration_s)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path.endswith("/tasks/kb-extraction"):
            body = json.loads(request.content.decode("utf-8"))
            cos_key = body["cos_object_key"]
            task_id, _ = _ids_for(cos_key)
            return httpx.Response(200, json={
                "task_type": "kb_extraction",
                "accepted": 1, "rejected": 0,
                "items": [{"task_id": task_id, "rejected": False}],
                "channel": {"in_use": 1, "capacity": 50},
                "submitted_at": started.isoformat(),
            })

        if request.method == "GET" and "/tasks/" in path and "kb-extraction" not in path:
            task_id = path.rsplit("/", 1)[-1]
            cos_key = key_for_task.get(task_id)
            if not cos_key:
                return httpx.Response(404, json={"detail": "task not found"})
            counts = counts_by_cos_key[cos_key]
            return httpx.Response(200, json={
                "task_id": task_id,
                "extraction_job_id": job_for_task[task_id],
                "tech_points_by_source": {
                    "visual": counts.get("visual", 0),
                    "audio": counts.get("audio", 0),
                    "visual+audio": counts.get("visual+audio", 0),
                },
            })

        if request.method == "GET" and "/extraction-jobs/" in path:
            job_id = path.rsplit("/", 1)[-1]
            cos_key = key_for_job.get(job_id)
            if not cos_key:
                return httpx.Response(404, json={"detail": "job not found"})
            # Look up task_id by matching the same cos_key so counts queries
            # later target the right task.
            task_id = next(t for t, k in key_for_task.items() if k == cos_key)
            return httpx.Response(200, json={
                "job_id": job_id,
                "analysis_task_id": task_id,
                "status": job_status,
                "started_at": started.isoformat(),
                "completed_at": completed.isoformat(),
                "error_message": job_error,
            })

        return httpx.Response(500, json={"detail": f"unexpected route: {path}"})

    return httpx.MockTransport(handler)


# ── Tests ────────────────────────────────────────────────────────────────────


def test_run_regression_happy_path_three_videos(runner, tmp_path):
    """All 3 mock videos land inside their expected range → every row passes."""
    videos = [
        runner.ReferenceVideo(
            name="fh1", cos_object_key="tests/f015/fh1.mp4",
            tech_category="forehand_topspin",
            expected_items_min=5, expected_items_max=20,
            has_speech=True, baseline_f002_seconds=60.0,
        ),
        runner.ReferenceVideo(
            name="bh1", cos_object_key="tests/f015/bh1.mp4",
            tech_category="backhand_push",
            expected_items_min=3, expected_items_max=15,
            has_speech=False,
        ),
        runner.ReferenceVideo(
            name="serve1", cos_object_key="tests/f015/serve1.mp4",
            tech_category="serve",
            expected_items_min=1, expected_items_max=10,
            has_speech=True,
        ),
    ]
    counts = {
        "tests/f015/fh1.mp4": {"visual": 8, "audio": 2, "visual+audio": 0},    # 10
        "tests/f015/bh1.mp4": {"visual": 6, "audio": 0, "visual+audio": 0},    # 6
        "tests/f015/serve1.mp4": {"visual": 2, "audio": 1, "visual+audio": 0}, # 3
    }
    transport = _build_mock_transport(counts_by_cos_key=counts, job_duration_s=42.0)

    with httpx.Client(transport=transport, base_url="http://test/api/v1") as client:
        results = runner.run_regression(
            videos,
            base_url="http://test/api/v1",
            poll_interval_s=0.0,          # don't sleep in tests
            timeout_s=10.0,
            client=client,
        )

    assert len(results) == 3
    assert all(r.passed for r in results), [r.error_message for r in results]
    # Counts correctly attributed by source.
    assert results[0].visual_items == 8
    assert results[0].audio_items == 2
    assert results[0].total_items == 10
    # Duration parsed from started_at/completed_at.
    assert results[0].duration_s == pytest.approx(42.0, rel=1e-3)


def test_run_regression_flags_out_of_range_video(runner):
    """A video with too many items → passed=False + informative error_message."""
    videos = [
        runner.ReferenceVideo(
            name="fh_overflow", cos_object_key="tests/f015/overflow.mp4",
            tech_category="forehand_topspin",
            expected_items_min=1, expected_items_max=5,
        ),
    ]
    counts = {
        "tests/f015/overflow.mp4": {"visual": 10, "audio": 0, "visual+audio": 0},
    }
    transport = _build_mock_transport(counts_by_cos_key=counts)

    with httpx.Client(transport=transport, base_url="http://test/api/v1") as client:
        results = runner.run_regression(
            videos, base_url="http://test/api/v1",
            poll_interval_s=0.0, timeout_s=10.0, client=client,
        )

    assert len(results) == 1
    assert results[0].passed is False
    assert "total=10" in results[0].error_message
    assert "[1,5]" in results[0].error_message.replace(" ", "")


def test_run_regression_handles_failed_extraction_job(runner):
    """Job status=failed → passed=False, error_message carries backend reason."""
    videos = [
        runner.ReferenceVideo(
            name="broken", cos_object_key="tests/f015/broken.mp4",
            tech_category="forehand_topspin",
            expected_items_min=1, expected_items_max=50,
        ),
    ]
    counts = {"tests/f015/broken.mp4": {"visual": 0, "audio": 0, "visual+audio": 0}}
    transport = _build_mock_transport(
        counts_by_cos_key=counts,
        job_status="failed",
        job_error="VIDEO_QUALITY_REJECTED: fps=12 vs 15",
    )

    with httpx.Client(transport=transport, base_url="http://test/api/v1") as client:
        results = runner.run_regression(
            videos, base_url="http://test/api/v1",
            poll_interval_s=0.0, timeout_s=10.0, client=client,
        )

    assert results[0].passed is False
    assert results[0].status == "failed"
    assert "VIDEO_QUALITY_REJECTED" in results[0].error_message


def test_render_verification_md_without_wallclock(runner):
    result = runner.VideoRunResult(
        name="fh1", cos_object_key="tests/f015/fh1.mp4",
        status="success", duration_s=42.0,
        visual_items=8, audio_items=2, visual_plus_audio_items=0, total_items=10,
        expected_min=5, expected_max=20, passed=True, error_message="",
    )
    md = runner.render_verification_md([result], measure_wallclock=False)
    assert "# Feature-015 Reference Video Regression Report" in md
    assert "Passed: **1 / 1**" in md
    assert "| fh1 |" in md
    assert "| 8 | 2 | 0 | 10 |" in md
    assert "Baseline(s)" not in md


def test_render_verification_md_with_wallclock_ratio(runner):
    passing = runner.VideoRunResult(
        name="fast", cos_object_key="k1",
        status="success", duration_s=40.0,                 # ratio 0.5 → PASS
        visual_items=5, audio_items=0, visual_plus_audio_items=0, total_items=5,
        expected_min=1, expected_max=10, passed=True,
        baseline_f002_seconds=80.0,
    )
    slow = runner.VideoRunResult(
        name="slow", cos_object_key="k2",
        status="success", duration_s=80.0,                 # ratio 1.33 → FAIL
        visual_items=5, audio_items=0, visual_plus_audio_items=0, total_items=5,
        expected_min=1, expected_max=10, passed=True,
        baseline_f002_seconds=60.0,
    )
    md = runner.render_verification_md([passing, slow], measure_wallclock=True)
    assert "Baseline(s)" in md
    assert "Ratio vs Baseline" in md
    assert "0.50 (PASS)" in md
    assert "1.33 (FAIL)" in md


def test_main_exit_code_0_when_all_pass(runner, tmp_path, monkeypatch):
    """End-to-end: CLI with all-green videos returns 0 and writes the report."""
    manifest = tmp_path / "ref.json"
    manifest.write_text(json.dumps({
        "videos": [{
            "name": "fh1",
            "cos_object_key": "tests/f015/fh1.mp4",
            "tech_category": "forehand_topspin",
            "expected_items_min": 1,
            "expected_items_max": 50,
            "has_speech": True,
        }],
    }))
    output = tmp_path / "verification.md"

    counts = {"tests/f015/fh1.mp4": {"visual": 5, "audio": 2, "visual+audio": 0}}
    transport = _build_mock_transport(counts_by_cos_key=counts)
    # Runner internally constructs its own Client via run_regression() when
    # main() calls it without a client; we patch httpx.Client so that
    # default-constructed client in main() picks up our transport.
    real_client = httpx.Client

    def _patched_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(runner.httpx, "Client", _patched_client, raising=True)

    exit_code = runner.main([
        "--manifest", str(manifest),
        "--output", str(output),
        "--base-url", "http://test/api/v1",
        "--poll-interval", "0.0",
        "--timeout", "5.0",
    ])
    assert exit_code == 0
    assert output.exists()
    text = output.read_text(encoding="utf-8")
    assert "Passed: **1 / 1**" in text


def test_main_exit_code_1_when_any_fails(runner, tmp_path, monkeypatch):
    manifest = tmp_path / "ref.json"
    manifest.write_text(json.dumps({
        "videos": [{
            "name": "broken",
            "cos_object_key": "tests/f015/broken.mp4",
            "tech_category": "forehand_topspin",
            "expected_items_min": 100,
            "expected_items_max": 200,
            "has_speech": False,
        }],
    }))
    output = tmp_path / "verification.md"

    counts = {"tests/f015/broken.mp4": {"visual": 1, "audio": 0, "visual+audio": 0}}
    transport = _build_mock_transport(counts_by_cos_key=counts)
    real_client = httpx.Client

    def _patched_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(runner.httpx, "Client", _patched_client, raising=True)

    exit_code = runner.main([
        "--manifest", str(manifest),
        "--output", str(output),
        "--base-url", "http://test/api/v1",
        "--poll-interval", "0.0",
        "--timeout", "5.0",
    ])
    assert exit_code == 1
    assert output.exists()
