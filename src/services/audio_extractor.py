"""AudioExtractor — extracts WAV audio from video files using ffmpeg.

Responsibilities:
- Extract 16kHz mono WAV from any video via ffmpeg subprocess
- Estimate Signal-to-Noise Ratio (SNR) for audio quality gating
- Raise AudioExtractionError on failure (task falls back to visual-only mode)
"""

from __future__ import annotations

import logging
import math
import subprocess
import tempfile
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


class AudioExtractionError(Exception):
    """Raised when ffmpeg cannot extract audio from the video."""


class AudioExtractor:
    """Extracts WAV audio tracks from video files and estimates audio quality."""

    def __init__(self, snr_threshold_db: float = 10.0) -> None:
        self.snr_threshold_db = snr_threshold_db

    def extract_wav(self, video_path: str | Path, output_path: str | Path) -> Path:
        """Extract 16kHz mono WAV from video_path and save to output_path.

        Args:
            video_path: Path to the source video file.
            output_path: Destination path for the extracted WAV file.

        Returns:
            Path to the created WAV file.

        Raises:
            AudioExtractionError: If ffmpeg fails or the video has no audio stream.
        """
        video_path = Path(video_path)
        output_path = Path(output_path)

        cmd = [
            "ffmpeg",
            "-y",                  # overwrite output
            "-i", str(video_path),
            "-vn",                 # no video
            "-ar", "16000",        # 16kHz sample rate (Whisper requirement)
            "-ac", "1",            # mono
            "-f", "wav",
            str(output_path),
        ]
        logger.info("Extracting audio: %s → %s", video_path.name, output_path.name)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,  # 5-minute timeout
            )
        except subprocess.TimeoutExpired as exc:
            raise AudioExtractionError(f"ffmpeg timed out extracting audio: {video_path}") from exc
        except FileNotFoundError as exc:
            raise AudioExtractionError("ffmpeg not found in PATH") from exc

        if result.returncode != 0:
            stderr = result.stderr[-500:]  # last 500 chars of stderr
            # Check for "no audio stream" in stderr
            if "no such" in result.stderr.lower() or "invalid data" in result.stderr.lower():
                raise AudioExtractionError(f"No audio stream found in video: {video_path}")
            raise AudioExtractionError(f"ffmpeg failed (rc={result.returncode}): {stderr}")

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise AudioExtractionError(f"ffmpeg produced empty output for: {video_path}")

        logger.info("Audio extracted: %s (%.1f KB)", output_path.name, output_path.stat().st_size / 1024)
        return output_path

    def estimate_snr(self, wav_path: str | Path) -> float:
        """Estimate Signal-to-Noise Ratio of a WAV file in dB.

        Uses a simple energy-based estimation: computes per-frame RMS energy,
        estimates noise floor from the quietest 10% of frames, and computes
        SNR as 20*log10(signal_rms / noise_rms).

        Args:
            wav_path: Path to a 16kHz mono WAV file.

        Returns:
            Estimated SNR in dB. Returns 0.0 if the file cannot be read.
        """
        try:
            import wave
            wav_path = Path(wav_path)
            with wave.open(str(wav_path), "rb") as wf:
                n_frames = wf.getnframes()
                raw = wf.readframes(n_frames)
                samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)

            if len(samples) == 0:
                return 0.0

            # Frame-based RMS energy (50ms frames at 16kHz = 800 samples)
            frame_size = 800
            n_frames_est = len(samples) // frame_size
            if n_frames_est == 0:
                return 0.0

            frames = samples[: n_frames_est * frame_size].reshape(n_frames_est, frame_size)
            rms_per_frame = np.sqrt(np.mean(frames ** 2, axis=1))

            # Noise floor: 10th percentile of frame energies
            noise_rms = np.percentile(rms_per_frame, 10) + 1e-10
            signal_rms = np.mean(rms_per_frame) + 1e-10

            snr_db = float(20.0 * math.log10(signal_rms / noise_rms))
            logger.debug("SNR estimate for %s: %.1f dB", wav_path.name, snr_db)
            return snr_db

        except Exception as exc:
            logger.warning("SNR estimation failed for %s: %s", wav_path, exc)
            return 0.0

    def is_quality_sufficient(self, wav_path: str | Path) -> tuple[bool, float]:
        """Check if audio quality meets the SNR threshold.

        Returns:
            (is_sufficient, snr_db) tuple.
        """
        snr = self.estimate_snr(wav_path)
        return snr >= self.snr_threshold_db, snr
