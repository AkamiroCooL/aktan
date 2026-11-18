#!/usr/bin/env python3
"""Нарезка стрима/видео на короткие вертикальные клипы (10-15 сек).

Работает без платных сервисов: yt-dlp скачивает нужный отрезок,
ffmpeg режет. Самые "громкие" моменты (крики, хайп, реакции)
находятся автоматически по громкости звука.

Примеры:
  python3 stream_clipper.py "https://www.youtube.com/live/qSyo5d-dVzA" --start 30:00 --end 32:00
  python3 stream_clipper.py video.mp4 --count 8 --min 10 --max 15
  python3 stream_clipper.py video.mp4 --at 1:02:10 --at 1:05:40   # вручную по таймкодам
"""
import argparse
import array
import math
import os
import shutil
import subprocess
import sys

SAMPLE_RATE = 8000  # для анализа громкости достаточно


def parse_time(t):
    """'1:02:03' / '62:03' / '3723' -> секунды."""
    if t is None:
        return None
    parts = [float(p) for p in str(t).split(":")]
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec


def fmt(sec):
    sec = int(sec)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def need(tool):
    if not shutil.which(tool):
        sys.exit(f"Не найден {tool}. Установи его (см. clipper/README.md).")


def download(url, start, end, workdir):
    """Скачивает видео (или только отрезок start..end) через yt-dlp."""
    out = os.path.join(workdir, "source.%(ext)s")
    cmd = [sys.executable, "-m", "yt_dlp", "-f",
           "bv*[height<=1080]+ba/b[height<=1080]/b",
           "--merge-output-format", "mp4", "-o", out, "--no-part"]
    if start is not None or end is not None:
        s = fmt(start or 0)
        e = fmt(end) if end is not None else "inf"
        cmd += ["--download-sections", f"*{s}-{e}", "--force-keyframes-at-cuts"]
    cmd.append(url)
    print("Скачиваю:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    for f in os.listdir(workdir):
        if f.startswith("source."):
            return os.path.join(workdir, f)
    sys.exit("Не удалось скачать видео.")


def duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", path],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def loudness_per_second(path):
    """Громкость (дБ) каждой секунды видео."""
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1",
                        "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"],
                       capture_output=True, check=True)
    samples = array.array("h")
    samples.frombytes(p.stdout[: len(p.stdout) // 2 * 2])
    out = []
    for i in range(0, len(samples), SAMPLE_RATE):
        chunk = samples[i:i + SAMPLE_RATE]
        if not chunk:
            break
        rms = math.sqrt(sum(x * x for x in chunk) / len(chunk))
        out.append(20 * math.log10(rms / 32768) if rms > 0 else -100.0)
    return out


def pick_moments(db, count, length, total):
    """Выбирает самые громкие непересекающиеся моменты.

    Клип ставится так, чтобы пик был примерно на 60% длины:
    сначала завязка, потом реакция.
    """
    n = len(db)
    # сглаживаем: средняя громкость окна 3 сек вокруг секунды
    smooth = [sum(db[max(0, i - 1):i + 2]) / len(db[max(0, i - 1):i + 2]) for i in range(n)]
    order = sorted(range(n), key=lambda i: smooth[i], reverse=True)
    chosen = []
    for peak in order:
        if len(chosen) >= count:
            break
        start = max(0.0, min(peak - 0.6 * length, total - length))
        end = start + length
        if any(start < e + 1 and end > s - 1 for s, e, _ in chosen):
            continue
        chosen.append((start, end, smooth[peak]))
    return sorted(chosen)


def cut(src, start, length, dst, vertical):
    vf = []
    if vertical:
        filt = ("[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920,boxblur=20:5[bg];"
                "[0:v]scale=1080:-2[fg];"
                "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v]")
        vf = ["-filter_complex", filt, "-map", "[v]", "-map", "0:a?"]
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.2f}", "-i", src,
           "-t", f"{length:.2f}", *vf,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", dst]
    subprocess.run(cmd, check=True)


def main():
    ap = argparse.ArgumentParser(description="Нарезка стрима на короткие клипы")
    ap.add_argument("source", help="ссылка YouTube/Twitch или путь к файлу")
    ap.add_argument("--start", help="начало отрезка стрима (напр. 30:00)")
    ap.add_argument("--end", help="конец отрезка стрима (напр. 32:00)")
    ap.add_argument("--count", type=int, default=6, help="сколько клипов (по умолч. 6)")
    ap.add_argument("--min", type=float, default=10, help="мин. длина клипа, сек")
    ap.add_argument("--max", type=float, default=15, help="макс. длина клипа, сек")
    ap.add_argument("--at", action="append", default=[],
                    help="вручную: таймкод начала клипа (можно несколько раз)")
    ap.add_argument("--horizontal", action="store_true",
                    help="оставить 16:9 (по умолчанию 9:16 для Shorts/TikTok/Reels)")
    ap.add_argument("--out", default="clips", help="папка для клипов")
    a = ap.parse_args()

    need("ffmpeg"), need("ffprobe")
    if a.min > a.max:
        sys.exit("--min не может быть больше --max")
    length = (a.min + a.max) / 2
    os.makedirs(a.out, exist_ok=True)
    start, end = parse_time(a.start), parse_time(a.end)

    if os.path.exists(a.source):
        src, offset = a.source, 0.0
        if start is not None or end is not None:
            # локальный файл: анализируем только нужный кусок
            tmp = os.path.join(a.out, "_section.mp4")
            cmd = ["ffmpeg", "-v", "error", "-y", "-ss", str(start or 0), "-i", a.source]
            if end is not None:
                cmd += ["-t", str(end - (start or 0))]
            subprocess.run(cmd + ["-c", "copy", tmp], check=True)
            src, offset = tmp, start or 0.0
    else:
        src, offset = download(a.source, start, end, a.out), start or 0.0

    total = duration(src)
    if a.at:
        moments = [(parse_time(t) - offset, parse_time(t) - offset + length, None) for t in a.at]
    else:
        print("Ищу самые громкие моменты...")
        moments = pick_moments(loudness_per_second(src), a.count, min(length, total), total)

    for i, (s, e, score) in enumerate(moments, 1):
        dst = os.path.join(a.out, f"clip_{i:02d}_{fmt(s + offset).replace(':', '-')}.mp4")
        cut(src, s, e - s, dst, not a.horizontal)
        extra = f", громкость {score:.1f} дБ" if score is not None else ""
        print(f"[{i}] {fmt(s + offset)}–{fmt(e + offset)} ({e - s:.0f} c{extra}) -> {dst}")
    print(f"Готово: {len(moments)} клипов в папке {a.out}/")


if __name__ == "__main__":
    main()
