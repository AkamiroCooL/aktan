#!/usr/bin/env python3
"""Монтаж Shorts в формате "реакция/разбор": твой голос — главное, фрагмент — цитата.

Кадр 1080x1920:
  сверху  — заголовок (крючок, твоими словами)
  центр   — фрагмент стрима (как цитата)
  ниже    — твой комментарий текстом / твоя веб-камера (--facecam)
  снизу   — подпись источника (обязательно указывай автора)
Звук: твоя озвучка (--voice) поверх, оригинал приглушается.
Если озвучка длиннее фрагмента, последний кадр замирает, пока ты говоришь.

Пример:
  python montage.py clips/live_01_0-16.mp4 --title "Speed ВПЕРВЫЕ увидел полицию в GTA 6" \
      --comment "Обратите внимание на звёзды розыска — это новая механика" \
      --voice my_voice.m4a --source "IShowSpeed (YouTube)" --out shorts/01.mp4
"""
import argparse
import os
import shutil
import subprocess
import sys
import textwrap

FONTS = ["C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/arial.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/System/Library/Fonts/Supplemental/Arial Bold.ttf"]


def probe_duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", path], capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def esc(text):
    """Экранирование текста для drawtext."""
    return (text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\u2019")
            .replace("%", "\\%").replace(",", "\\,"))


def ffpath(p):
    return p.replace("\\", "/").replace(":", "\\:")


def text_block(font, text, y, size, width_chars, color="white", box=None):
    lines = textwrap.wrap(text, width_chars)[:4]
    out = []
    for i, line in enumerate(lines):
        f = (f"drawtext=fontfile='{ffpath(font)}':text='{esc(line)}':fontsize={size}:"
             f"fontcolor={color}:x=(w-text_w)/2:y={y + i * int(size * 1.25)}:"
             "borderw=3:bordercolor=black")
        if box:
            f += f":box=1:boxcolor={box}:boxborderw=14"
        out.append(f)
    return out


def main():
    ap = argparse.ArgumentParser(description="Монтаж Shorts в формате реакции/разбора")
    ap.add_argument("clip", help="фрагмент (вертикальный клип из stream_clipper или любой)")
    ap.add_argument("--title", required=True, help="заголовок-крючок сверху")
    ap.add_argument("--comment", default="", help="твой комментарий текстом под фрагментом")
    ap.add_argument("--beat", action="append", default=[],
                    help="текстовый разбор: фразы сменяют друг друга (можно несколько раз)")
    ap.add_argument("--beat-sec", type=float, default=3.5, help="сколько секунд показывать каждую фразу")
    ap.add_argument("--max-clip-share", type=float, default=0.35,
                    help="макс. доля чужого видео в ролике (по умолчанию 35%%)")
    ap.add_argument("--lang", default="en", choices=["en", "es", "ru"],
                    help="язык служебных подписей (Source/Fuente/Источник)")
    ap.add_argument("--voice", help="твоя озвучка (mp3/m4a/wav) — главное для монетизации")
    ap.add_argument("--facecam", help="видео с твоей веб-камеры (кладётся под фрагмент)")
    ap.add_argument("--source", required=True, help="автор оригинала, напр. 'IShowSpeed (YouTube)'")
    ap.add_argument("--brand", default="", help="название твоего канала (вотермарк)")
    ap.add_argument("--clip-volume", type=float, default=0.35,
                    help="громкость оригинала под озвучкой (0..1)")
    ap.add_argument("--font", help="путь к .ttf шрифту с кириллицей")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    for t in ("ffmpeg", "ffprobe"):
        if not shutil.which(t):
            sys.exit(f"Не найден {t}")
    font = a.font or next((f for f in FONTS if os.path.exists(f)), None)
    if not font:
        sys.exit("Не найден шрифт, укажи --font путь_к_шрифту.ttf")
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)

    clip_len = probe_duration(a.clip)
    voice_len = probe_duration(a.voice) if a.voice else 0
    beats_len = len(a.beat) * a.beat_sec
    own_len = max(voice_len, beats_len)
    if own_len:
        # чужого видео не больше max_clip_share от ролика
        clip_len = min(clip_len, own_len * a.max_clip_share)
    total = max(clip_len, own_len)
    hold = max(0.0, total - clip_len)

    inputs = ["-i", a.clip]
    if a.voice:
        inputs += ["-i", a.voice]
    if a.facecam:
        inputs += ["-i", a.facecam]
    cam_idx = 1 + (1 if a.voice else 0)

    # фрагмент: если клип вертикальный с размытым фоном — берём центральную полосу 16:9
    v = (f"[0:v]trim=0:{clip_len:.2f},setpts=PTS-STARTPTS,crop=iw:'if(gt(ih,iw),iw*9/16,ih)':0:'if(gt(ih,iw),(ih-iw*9/16)/2,0)',"
         "scale=1000:-2,setsar=1")
    if hold:
        v += f",tpad=stop_mode=clone:stop_duration={hold:.2f}"
    v += "[clipv]"
    parts = [v, f"color=c=0x0f0f14:s=1080x1920:d={total:.2f}[bg]",
             "[bg][clipv]overlay=(W-w)/2:520:shortest=1[base]"]
    last = "base"
    if a.facecam:
        parts.append(f"[{cam_idx}:v]scale=600:-2,setsar=1[cam]")
        parts.append(f"[{last}][cam]overlay=(W-w)/2:1150:shortest=0:eof_action=pass[withcam]")
        last = "withcam"

    draws = text_block(font, a.title.upper(), 170, 70, 22, "yellow")
    draws += [f"drawbox=x=40:y=505:w=1000:h=4:color=yellow:t=fill"]
    if a.comment and not a.facecam:
        draws += text_block(font, a.comment, 1180, 54, 30, "white", "black@0.55")
    for i, beat in enumerate(a.beat):
        t0, t1 = i * a.beat_sec, (i + 1) * a.beat_sec
        y = 1180 if not a.comment else 1420
        for f in text_block(font, beat, y, 56, 28, "white", "black@0.6"):
            draws.append(f + f":enable='between(t,{t0:.2f},{t1:.2f})'")
    label = {"en": "Source", "es": "Fuente", "ru": "Источник"}[a.lang]
    draws += text_block(font, f"{label}: {a.source}", 1790, 34, 60, "0xbbbbbb")
    if a.brand:
        draws += text_block(font, a.brand, 60, 40, 40, "white")
    parts.append(f"[{last}]" + ",".join(draws) + "[v]")

    if a.voice:
        parts.append(f"[0:a]atrim=0:{clip_len:.2f},volume={a.clip_volume},apad[ca]")
        parts.append("[1:a]volume=1.0,apad[va]")
        parts.append(f"[ca][va]amix=inputs=2:duration=longest:normalize=0,atrim=0:{total:.2f}[a]")
    else:
        parts.append(f"[0:a]atrim=0:{clip_len:.2f},apad,atrim=0:{total:.2f}[a]")

    cmd = ["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(parts),
           "-map", "[v]", "-map", "[a]", "-t", f"{total:.2f}", "-r", "30",
           "-c:v", "libx264", "-preset", "medium", "-crf", "20",
           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", a.out]
    subprocess.run(cmd, check=True)
    print(f"Готово: {a.out} ({total:.1f} c)")
    print(f"Чужое видео: {clip_len:.1f} c из {total:.1f} c ({clip_len / total:.0%})")
    if not a.voice and not a.facecam and not a.beat:
        print("ВНИМАНИЕ: без твоей озвучки/камеры это всё ещё просто нарезка — "
              "для монетизации добавь --voice.")


if __name__ == "__main__":
    main()
