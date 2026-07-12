#!/usr/bin/env python3
"""Offline track analysis for hand-authored Beat Slicer levels.

Requires ffmpeg/ffprobe and numpy. Produces a stable JSON report with the tempo
grid, bar energy, onset peaks, section suggestions, and rare attack candidates.
It does not generate gameplay randomly: the report is input for a human/agent
that authors the actual chart in beat_slicer.html.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np


def run(cmd: list[str]) -> bytes:
    try:
        return subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
    except FileNotFoundError as exc:
        raise SystemExit(f"missing executable: {cmd[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.stderr.decode("utf-8", "replace")) from exc


def probe(path: Path) -> dict:
    raw = run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration,size:format_tags=title,artist", "-of", "json", str(path),
    ])
    data = json.loads(raw)
    fmt = data.get("format", {})
    tags = fmt.get("tags", {})
    return {
        "duration": float(fmt.get("duration", 0)),
        "size": int(fmt.get("size", path.stat().st_size)),
        "title": tags.get("title", path.stem),
        "artist": tags.get("artist", ""),
    }


def decode(path: Path, sample_rate: int) -> np.ndarray:
    raw = run([
        "ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1",
        "-ar", str(sample_rate), "-f", "f32le", "pipe:1",
    ])
    audio = np.frombuffer(raw, dtype="<f4").astype(np.float64)
    if audio.size < sample_rate:
        raise SystemExit("track is shorter than one second")
    peak = float(np.max(np.abs(audio))) or 1.0
    return audio / peak


def frame_energies(audio: np.ndarray, sr: int, hop: int, win_size: int) -> tuple[np.ndarray, ...]:
    count = 1 + max(0, (audio.size - win_size) // hop)
    total = np.empty(count, dtype=np.float64)
    bass = np.empty(count, dtype=np.float64)
    high = np.empty(count, dtype=np.float64)
    window = np.hanning(win_size)
    hz = np.fft.rfftfreq(win_size, 1 / sr)
    bass_mask = (hz >= 30) & (hz <= 220)
    high_mask = (hz >= 2500) & (hz <= min(10000, sr / 2))
    offsets = np.arange(win_size)
    batch = 1024
    for start in range(0, count, batch):
        end = min(count, start + batch)
        bases = (np.arange(start, end) * hop)[:, None]
        frames = audio[bases + offsets] * window
        power = np.abs(np.fft.rfft(frames, axis=1)) ** 2
        total[start:end] = np.mean(frames * frames, axis=1)
        bass[start:end] = np.sum(power[:, bass_mask], axis=1) / win_size**2
        high[start:end] = np.sum(power[:, high_mask], axis=1) / win_size**2
    return total, bass, high


def log_band(values: np.ndarray) -> np.ndarray:
    scale = float(np.percentile(values, 99)) + 1e-12
    return np.log1p(1000 * values / scale)


def onset_envelope(total: np.ndarray, bass: np.ndarray, high: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    lt, lb, lh = log_band(total), log_band(bass), log_band(high)
    diff = lambda x: np.maximum(0, np.diff(x, prepend=x[0]))
    raw = diff(lt) + 2.0 * diff(lb) + diff(lh)
    local = np.convolve(raw, np.ones(33) / 33, mode="same")
    onset = np.maximum(0, raw - local)
    bass_onset = diff(lb)
    return onset, bass_onset


def tempo(onset: np.ndarray, hop_seconds: float, bpm_min: float, bpm_max: float) -> tuple[float, float, np.ndarray]:
    min_lag = max(2, round(60 / bpm_max / hop_seconds))
    max_lag = min(len(onset) // 3, round(60 / bpm_min / hop_seconds))
    scores = np.zeros(max_lag + 1)
    for lag in range(min_lag, max_lag + 1):
        corr = float(np.dot(onset[:-lag], onset[lag:]))
        bpm = 60 / (lag * hop_seconds)
        prior = math.exp(-0.5 * (math.log2(bpm / 110) / 0.85) ** 2)
        scores[lag] = corr * prior
    best = int(np.argmax(scores))
    refined = float(best)
    if min_lag < best < max_lag:
        y0, y1, y2 = scores[best - 1 : best + 2]
        den = y0 - 2 * y1 + y2
        delta = (y0 - y2) / (2 * den) if abs(den) > 1e-12 else 0
        if abs(delta) < 1:
            refined += float(delta)
    bpm = 60 / (refined * hop_seconds)
    confidence = float(scores[best] / (np.median(scores[min_lag : max_lag + 1]) + 1e-9))
    return bpm, confidence, scores


def grid_phase(onset: np.ndarray, bass_onset: np.ndarray, lag: float, hop_seconds: float) -> tuple[float, int]:
    period = max(2, round(lag))
    phase_scores = np.array([np.sum(onset[o::period]) for o in range(period)])
    offset_frame = int(np.argmax(phase_scores))
    beat_frames = np.arange(offset_frame, len(onset), lag)
    downbeat_scores = []
    for phase in range(4):
        score = 0.0
        for frame in beat_frames[phase::4]:
            i = int(round(frame))
            lo, hi = max(0, i - 1), min(len(bass_onset), i + 2)
            score += float(np.max(bass_onset[lo:hi]))
        downbeat_scores.append(score)
    downbeat_phase = int(np.argmax(downbeat_scores))
    bar0 = offset_frame * hop_seconds + downbeat_phase * lag * hop_seconds
    bar_length = 4 * lag * hop_seconds
    while bar0 - bar_length >= 0:
        bar0 -= bar_length
    return bar0, downbeat_phase


def db(value: float) -> float:
    return 10 * math.log10(max(value, 1e-12))


def bar_profile(
    total: np.ndarray, bass: np.ndarray, onset: np.ndarray, bar0: float,
    interval: float, hop_seconds: float, duration: float,
) -> list[dict]:
    result = []
    bar_len = 4 * interval
    bar = 0
    t = bar0
    while t < duration - 0.25:
        lo = max(0, round(t / hop_seconds))
        hi = min(len(total), round((t + bar_len) / hop_seconds))
        if hi <= lo:
            break
        result.append({
            "bar": bar,
            "t": round(t, 4),
            "db": round(db(float(np.mean(total[lo:hi]))), 1),
            "bassDb": round(db(float(np.mean(bass[lo:hi]))), 1),
            "onset": round(float(np.mean(onset[lo:hi])), 4),
            "peakDb": round(db(float(np.max(total[lo:hi]))), 1),
        })
        bar += 1
        t += bar_len
    return result


def strongest_peaks(onset: np.ndarray, hop_seconds: float, count: int, center_seconds: float = 0) -> list[dict]:
    threshold = float(np.percentile(onset, 80))
    candidates = [i for i in range(2, len(onset) - 2)
                  if onset[i] >= threshold and onset[i] > onset[i - 1] and onset[i] >= onset[i + 1]]
    selected: list[int] = []
    min_frames = max(1, round(0.12 / hop_seconds))
    for i in sorted(candidates, key=lambda j: onset[j], reverse=True):
        if all(abs(i - j) >= min_frames for j in selected):
            selected.append(i)
        if len(selected) >= count:
            break
    return [{"t": round(i * hop_seconds + center_seconds, 4), "v": round(float(onset[i]), 4)} for i in selected]


def refine_grid_from_peaks(
    peaks: list[dict], bass_onset: np.ndarray, initial_interval: float,
    hop_seconds: float, center_seconds: float, duration: float,
) -> tuple[float, float, int]:
    """Fit a half-beat lattice to strong attacks, then vote for the 4/4 downbeat."""
    chosen = peaks[: min(50, len(peaks))]
    if len(chosen) < 8:
        return initial_interval, 0.0, 0
    sub = initial_interval / 2
    times = np.array([p["t"] for p in chosen])
    weights = np.array([p["v"] for p in chosen]) ** 2
    angles = (times % sub) / sub * 2 * np.pi
    circular = np.sum(weights * np.exp(1j * angles))
    phase = (np.angle(circular) % (2 * np.pi)) / (2 * np.pi) * sub
    slots = np.rint((times - phase) / sub)
    design = np.column_stack([np.ones(len(slots)), slots])
    intercept, slope = np.linalg.lstsq(design, times, rcond=None)[0]
    residual = np.abs(design @ np.array([intercept, slope]) - times)
    good = residual < 0.06
    if np.count_nonzero(good) >= 8:
        intercept, slope = np.linalg.lstsq(design[good], times[good], rcond=None)[0]
    interval = float(slope * 2)
    base = float(intercept % slope)
    bar_length = interval * 4
    scores = []
    for phase8 in range(8):
        first = base + phase8 * slope
        score = 0.0
        for t in np.arange(first, duration, bar_length):
            frame = int(round((t - center_seconds) / hop_seconds))
            lo, hi = max(0, frame - 1), min(len(bass_onset), frame + 2)
            if hi > lo:
                score += float(np.max(bass_onset[lo:hi]))
        scores.append(score)
    downbeat_half_phase = int(np.argmax(scores))
    bar0 = base + downbeat_half_phase * slope
    while bar0 >= bar_length:
        bar0 -= bar_length
    return interval, bar0, downbeat_half_phase


def classify_sections(bars: list[dict]) -> list[dict]:
    if not bars:
        return []
    energy = np.array([b["db"] for b in bars])
    onset = np.array([b["onset"] for b in bars])
    quiet = float(np.max(energy) - 28)
    e60, o60 = np.percentile(energy, 60), np.percentile(onset, 60)
    labels = []
    for e, o in zip(energy, onset):
        labels.append("silence" if e < quiet else "drop" if e >= e60 and o >= o60 else "active" if e >= e60 else "low")
    groups = []
    start = 0
    for i in range(1, len(labels) + 1):
        if i == len(labels) or labels[i] != labels[start]:
            groups.append({"fromBar": start, "toBar": i - 1, "kind": labels[start]})
            start = i
    return groups


def attack_candidates(bars: list[dict], limit: int = 6) -> list[dict]:
    if not bars:
        return []
    e = np.array([b["db"] for b in bars]); bass = np.array([b["bassDb"] for b in bars]); onset = np.array([b["onset"] for b in bars])
    norm = lambda x: (x - np.median(x)) / (np.std(x) + 1e-9)
    rise = np.diff(e, prepend=e[0])
    score = norm(e) + .7 * norm(bass) + .8 * norm(onset) + 1.2 * np.maximum(0, norm(rise))
    chosen = []
    for i in np.argsort(score)[::-1]:
        if all(abs(int(i) - j) >= 6 for j in chosen):
            chosen.append(int(i))
        if len(chosen) >= limit:
            break
    return [{"bar": i, "t": bars[i]["t"], "score": round(float(score[i]), 3)} for i in sorted(chosen)]


def emit_js(path: Path, report: dict) -> None:
    compact = {
        "title": report["track"]["title"], "artist": report["track"]["artist"],
        "duration": report["track"]["duration"], "bpm": report["grid"]["bpm"],
        "iv": report["grid"]["interval"], "off": report["grid"]["bar0"],
        "attackCandidates": report["attackCandidates"],
    }
    text = "// Generated by tools/analyze_track.py; author the chart, do not randomize it.\n"
    text += "const TRACK_ANALYSIS=" + json.dumps(compact, ensure_ascii=False, separators=(",", ":")) + ";\n"
    text += "const T=(bar,q)=>TRACK_ANALYSIS.off+(bar*4+q)*TRACK_ANALYSIS.iv;\n"
    path.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("track", type=Path)
    parser.add_argument("--out", type=Path, default=Path("track-analysis.json"))
    parser.add_argument("--emit-js", type=Path)
    parser.add_argument("--sample-rate", type=int, default=22050)
    parser.add_argument("--hop", type=int, default=256)
    parser.add_argument("--window", type=int, default=1024)
    parser.add_argument("--bpm-min", type=float, default=60)
    parser.add_argument("--bpm-max", type=float, default=200)
    parser.add_argument("--peaks", type=int, default=80)
    args = parser.parse_args()
    if not args.track.is_file():
        raise SystemExit(f"track not found: {args.track}")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise SystemExit("ffmpeg and ffprobe must be installed")

    meta = probe(args.track)
    audio = decode(args.track, args.sample_rate)
    total, bass, high = frame_energies(audio, args.sample_rate, args.hop, args.window)
    onset, bass_onset = onset_envelope(total, bass, high)
    hop_seconds = args.hop / args.sample_rate
    bpm, confidence, _ = tempo(onset, hop_seconds, args.bpm_min, args.bpm_max)
    interval = 60 / bpm
    center_seconds = args.window / (2 * args.sample_rate)
    peaks = strongest_peaks(onset, hop_seconds, args.peaks, center_seconds)
    interval, bar0, downbeat_phase = refine_grid_from_peaks(
        peaks, bass_onset, interval, hop_seconds, center_seconds, meta["duration"]
    )
    bpm = 60 / interval
    bars = bar_profile(total, bass, onset, bar0, interval, hop_seconds, meta["duration"])
    report = {
        "schema": "beat-slicer-track-analysis/v1",
        "track": {**meta, "file": args.track.name},
        "grid": {
            "bpm": round(bpm, 4), "interval": round(interval, 7),
            "bar0": round(bar0, 4), "downbeatPhase": downbeat_phase,
            "confidence": round(confidence, 3), "beatsPerBar": 4,
        },
        "bars": bars,
        "sections": classify_sections(bars),
        "topPeaks": peaks,
        "attackCandidates": attack_candidates(bars),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.emit_js:
        args.emit_js.parent.mkdir(parents=True, exist_ok=True)
        emit_js(args.emit_js, report)
    print(f"{meta['artist']} — {meta['title']}".strip(" —"))
    print(f"duration={meta['duration']:.3f}s bpm={bpm:.3f} interval={interval:.6f}s bar0={bar0:.4f}s confidence={confidence:.2f}")
    print(f"bars={len(bars)} peaks={len(report['topPeaks'])} attacks={[x['bar'] for x in report['attackCandidates']]}")
    print(f"wrote {args.out}" + (f" and {args.emit_js}" if args.emit_js else ""))


if __name__ == "__main__":
    main()
