#!/usr/bin/env python3
"""Нарезка стрима/видео на короткие вертикальные клипы (10-15 сек).

Работает без платных сервисов: yt-dlp скачивает нужный отрезок,
ffmpeg режет. Самые "громкие" моменты (крики, хайп, реакции)
находятся автоматически по громкости звука.

Примеры:
  python3 stream_clipper.py "https://www.youtube.com/live/qSyo5d-dVzA" --start 30:00 --end 32:00
  python3 stream_clipper.py video.mp4 --count 8 --min 10 --max 15
  python3 stream_clipper.py video.mp4 --at 1:02:10 --at 1:05:40   # вручную по таймкодам
  python3 stream_clipper.py "https://www.youtube.com/live/ID" --live   # режет эфир на ходу
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
    if src.startswith("concat:"):
        # куски эфира: декодируем с начала и режем точно, иначе видео ждёт
        # ключевой кадр и начинается позже звука
        seek = ["-i", src, "-ss", f"{start:.3f}"]
    else:
        seek = ["-ss", f"{start:.3f}", "-i", src]
    cmd = ["ffmpeg", "-v", "error", "-y", *seek,
           "-t", f"{length:.2f}", *vf, "-af", "aresample=async=1",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", dst]
    subprocess.run(cmd, check=True)


def stream_url(source):
    """Прямые ссылки на поток эфира: [общий] или [видео, звук].

    YouTube часто отдаёт эфир раздельными потоками видео и звука.
    Локальный файл (для теста) — путь, или "видео::звук".
    """
    if os.path.exists(source):
        return [source], True
    if "::" in source and all(os.path.exists(x) for x in source.split("::")):
        return source.split("::"), True
    r = subprocess.run([sys.executable, "-m", "yt_dlp", "-g", "-f",
                        "bv*[height<=1080]+ba/b[height<=1080]/b", source],
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        sys.exit("Не удалось получить поток: " + r.stderr.strip()[-500:])
    return r.stdout.strip().splitlines()[:2], False


def live_mode(a):
    """Смотрит эфир в реальном времени и режет клипы по ходу.

    Хранится только последняя минута-полторы в виде кусков по 2 сек,
    старые куски удаляются, поэтому весь стрим на диск не пишется.
    """
    import time
    length = (a.min + a.max) / 2
    pre, post = 0.6 * length, 0.4 * length
    buf = os.path.join(a.out, "_live_buffer")
    os.makedirs(buf, exist_ok=True)
    for f in os.listdir(buf):
        os.remove(os.path.join(buf, f))
    urls, is_file = stream_url(a.source)
    seg_list = os.path.join(buf, "list.csv")
    cmd = ["ffmpeg", "-v", "error", "-y"]
    for u in urls:
        if is_file:
            cmd.append("-re")  # тест: читать файл со скоростью эфира
        cmd += ["-i", u]
    audio_in = 1 if len(urls) > 1 else 0
    # -copyts: сохраняем исходные метки времени эфира. У видео и звука они общие,
    # поэтому даже если потоки подключились в разные моменты, звук не съедет.
    # Без -reset_timestamps куски идут непрерывно и склеиваются без рывков.
    cmd += ["-map", "0:v:0", "-map", f"{audio_in}:a:0", "-c", "copy", "-copyts",
            "-f", "segment", "-segment_time", "2",
            "-segment_format", "mpegts", "-segment_list", seg_list,
            "-segment_list_type", "csv", os.path.join(buf, "seg%06d.ts")]
    rec = subprocess.Popen(cmd)
    print("Смотрю эфир... Ctrl+C — остановить.")

    segs = []        # (start, end, path)
    timeline = []    # (time_sec, dB)
    read_lines = 0
    t_origin = None
    pending = None   # время пика, ждём хвост
    last_end = -1e9
    made = 0
    try:
        while True:
            alive = rec.poll() is None
            lines = open(seg_list).read().splitlines() if os.path.exists(seg_list) else []
            for line in lines[read_lines:]:
                name, s0, e0 = line.rsplit(",", 2)
                path = os.path.join(buf, name)
                s0, e0 = float(s0), float(e0)
                if e0 - s0 > 30:  # битая запись (абсолютное время эфира) — пропускаем
                    continue
                if t_origin is None:
                    t_origin = s0  # время от начала нарезки
                s0, e0 = s0 - t_origin, e0 - t_origin
                segs.append((s0, e0, path))
                for i, db in enumerate(loudness_per_second(path)):
                    timeline.append((s0 + i, db))
            read_lines = len(lines)
            now = segs[-1][1] if segs else 0

            # поиск пика: заметно громче фона последних 2 минут
            if pending is None and len(timeline) > 20:
                recent = sorted(db for t, db in timeline if t > now - 120)
                base = recent[len(recent) // 2]
                for t, db in timeline:
                    if t < now - 6 or t < last_end + a.cooldown:
                        continue
                    if db > base + a.sensitivity and db > -30:
                        pending = t
                        print(f"Пик {fmt(t)}: {db:.1f} дБ (фон {base:.1f})")
                        break

            # пик найден и хвост уже записан -> режем клип
            if pending is not None and now >= pending + post:
                start = max(segs[0][0], pending - pre)
                parts = [p for s0, e0, p in segs if e0 > start and s0 < start + length]
                first = next(s0 for s0, e0, p in segs if e0 > start)
                concat = "concat:" + "|".join(parts)
                made += 1
                dst = os.path.join(a.out, f"live_{made:02d}_{fmt(start).replace(':', '-')}.mp4")
                cut(concat, start - first, length, dst, not a.horizontal)
                print(f"[{made}] клип {fmt(start)}–{fmt(start + length)} -> {dst}", flush=True)
                last_end, pending = start + length, None
                if a.count_live and made >= a.count_live:
                    break

            # удаляем старые куски
            keep_from = now - max(a.buffer, length + 10)
            while segs and segs[0][1] < keep_from:
                os.remove(segs.pop(0)[2])
            timeline = [x for x in timeline if x[0] > now - 180]

            if not alive:
                break
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        if rec.poll() is None:
            rec.terminate()
        shutil.rmtree(buf, ignore_errors=True)
    print(f"Эфир: сохранено {made} клипов в {a.out}/")


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
    ap.add_argument("--live", action="store_true",
                    help="живой режим: резать идущий эфир по ходу, не скачивая целиком")
    ap.add_argument("--sensitivity", type=float, default=8,
                    help="живой режим: на сколько дБ громче фона = пик (меньше -> больше клипов)")
    ap.add_argument("--cooldown", type=float, default=20,
                    help="живой режим: мин. пауза между клипами, сек")
    ap.add_argument("--buffer", type=float, default=90,
                    help="живой режим: сколько секунд эфира держать в памяти")
    ap.add_argument("--count-live", type=int, default=0,
                    help="живой режим: остановиться после N клипов (0 = до конца эфира)")
    a = ap.parse_args()

    need("ffmpeg"), need("ffprobe")
    if a.min > a.max:
        sys.exit("--min не может быть больше --max")
    length = (a.min + a.max) / 2
    os.makedirs(a.out, exist_ok=True)
    if a.live:
        return live_mode(a)
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
