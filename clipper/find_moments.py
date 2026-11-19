#!/usr/bin/env python3
"""Ищет моменты стрима, которые зрители реально хотят увидеть.

Сигналы (берутся с YouTube через yt-dlp, видео не скачивается):
  1. heatmap  — «Самые пересматриваемые» моменты (график над полосой прокрутки)
  2. chat     — всплески чата трансляции (сколько сообщений и о чём)
  3. comments — таймкоды, которые зрители пишут в комментариях

Результат: moments.md (таблица для человека) и moments.json (для скриптов).

  python find_moments.py "https://www.youtube.com/watch?v=ID" --top 25
  python find_moments.py ID --no-chat          # без чата (быстрее)
"""
import argparse
import collections
import glob
import json
import os
import re
import subprocess
import sys

STOP = set("""the a an and or to of in is it i you he she we they this that be for on at
with my me your so no yes lol lmao bro speed ishowspeed what why how its im dont like just
de la el en que y es un una lo los las por con para se no si ya mi tu""".split())
TS_RE = re.compile(r"(?<![\d:])(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?![\d:])")


def fmt(sec):
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def ytdlp(args):
    return subprocess.run([sys.executable, "-m", "yt_dlp", *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def peaks(series, n, min_gap):
    """series: [(t, score)] -> top-n локальных максимумов, не ближе min_gap друг к другу."""
    out = []
    for t, v in sorted(series, key=lambda x: -x[1]):
        if v <= 0 or any(abs(t - u) < min_gap for u, _ in out):
            continue
        out.append((t, v))
        if len(out) >= n:
            break
    return out


def heatmap_signal(info):
    hm = info.get("heatmap") or []
    return [((h["start_time"] + h["end_time"]) / 2, float(h["value"])) for h in hm]


def comment_signal(info, duration):
    bins = collections.defaultdict(float)
    texts = collections.defaultdict(list)
    for c in info.get("comments") or []:
        text = c.get("text") or ""
        for m in TS_RE.finditer(text):
            h, mi, s = m.groups()
            t = int(h or 0) * 3600 + int(mi) * 60 + int(s)
            if duration and t > duration:
                continue
            b = t // 30 * 30
            bins[b] += 1 + (c.get("like_count") or 0) / 10
            if len(texts[b]) < 3:
                texts[b].append(text.strip().replace("\n", " ")[:140])
    return [(b + 15, v) for b, v in bins.items()], texts


def chat_signal(path, bin_sec=10):
    counts = collections.Counter()
    words = collections.defaultdict(collections.Counter)
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                d = json.loads(line)
                act = d["replayChatItemAction"]
                t = int(act["videoOffsetTimeMsec"]) / 1000
                item = act["actions"][0]["addChatItemAction"]["item"]
                r = next(iter(item.values()))
                runs = r.get("message", {}).get("runs", [])
            except (KeyError, ValueError, IndexError, StopIteration, TypeError):
                continue
            b = int(t // bin_sec * bin_sec)
            counts[b] += 1
            text = " ".join(x.get("text", "") for x in runs).lower()
            for w in re.findall(r"[a-záéíóúñü0-9]{3,}", text):
                if w not in STOP:
                    words[b][w] += 1
    if not counts:
        return [], {}
    # всплеск = сообщений больше, чем в среднем за соседние 5 минут
    series = []
    last = max(counts)
    for b, c in counts.items():
        lo = max(0, min(b - 300, last - 600))
        hi = min(last + bin_sec, max(b + 300, 600))
        around = [counts.get(x, 0) for x in range(lo, hi, bin_sec)]
        base = sum(around) / len(around) or 1
        series.append((b + bin_sec / 2, c / base))
    top_words = {b: ", ".join(w for w, _ in words[b].most_common(6)) for b in words}
    return series, top_words


def nearest(d, t, width):
    keys = [k for k in d if abs(k - t) <= width]
    return d[min(keys, key=lambda k: abs(k - t))] if keys else ""


def main():
    ap = argparse.ArgumentParser(description="Поиск моментов, которые хотят увидеть зрители")
    ap.add_argument("source", help="ссылка или ID видео YouTube")
    ap.add_argument("--top", type=int, default=25, help="сколько кандидатов вывести")
    ap.add_argument("--no-chat", action="store_true", help="не качать чат трансляции")
    ap.add_argument("--max-comments", type=int, default=2000)
    ap.add_argument("--out", default="moments")
    ap.add_argument("--info-json", help="готовый info.json (для теста, без сети)")
    ap.add_argument("--chat-json", help="готовый live_chat.json (для теста, без сети)")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    if a.info_json:
        info = json.load(open(a.info_json, encoding="utf-8"))
    else:
        print("Загружаю данные видео и комментарии...")
        r = ytdlp(["-J", "--skip-download", "--get-comments", "--extractor-args",
                   f"youtube:max_comments={a.max_comments},all,0,0;comment_sort=top", a.source])
        if r.returncode != 0:
            sys.exit("yt-dlp: " + r.stderr.strip()[-500:])
        info = json.loads(r.stdout)
    duration = info.get("duration") or 0

    chat_path = a.chat_json
    if not chat_path and not a.no_chat:
        print("Загружаю чат трансляции (может занять несколько минут)...")
        ytdlp(["--skip-download", "--write-subs", "--sub-langs", "live_chat",
               "-o", os.path.join(a.out, "chat"), a.source])
        found = glob.glob(os.path.join(a.out, "chat*.live_chat.json"))
        chat_path = found[0] if found else None
        if not chat_path:
            print("Чат недоступен — продолжаю без него.")

    sig_heat = heatmap_signal(info)
    sig_comm, comm_texts = comment_signal(info, duration)
    sig_chat, chat_words = chat_signal(chat_path) if chat_path else ([], {})
    print(f"Сигналы: heatmap {len(sig_heat)}, комментарии с таймкодами {len(sig_comm)}, "
          f"чат {len(sig_chat)} интервалов")

    # нормируем каждый сигнал к 0..1 и складываем по 60-секундным окнам
    total = collections.defaultdict(float)
    why = collections.defaultdict(set)
    for name, sig, w in (("пересматривают", sig_heat, 1.0),
                         ("таймкоды в комментах", sig_comm, 1.2),
                         ("всплеск чата", sig_chat, 0.8)):
        if not sig:
            continue
        mx = max(v for _, v in sig) or 1
        for t, v in peaks(sig, a.top * 3, 45):
            b = int(t // 60 * 60)
            total[b] += w * v / mx
            why[b].add(name)
    best = peaks([(b + 30, v) for b, v in total.items()], a.top, 90)

    rows = []
    for t, score in sorted(best):
        b = int((t - 30) // 60 * 60)
        rows.append({
            "time": int(t), "time_str": fmt(t), "score": round(score, 2),
            "signals": sorted(why[b]),
            "chat_words": nearest(chat_words, t, 40),
            "comments": nearest(comm_texts, t, 60),
            "clip_start": fmt(max(0, t - 60)), "clip_end": fmt(t + 60),
        })
    json.dump(rows, open(os.path.join(a.out, "moments.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    with open(os.path.join(a.out, "moments.md"), "w", encoding="utf-8") as f:
        f.write(f"# Кандидаты: {info.get('title', '')}\n\n")
        f.write("| Время | Оценка | Почему | Чат пишет | Комментарии |\n|---|---|---|---|---|\n")
        for r in rows:
            com = " / ".join(r["comments"]) if r["comments"] else ""
            f.write(f"| {r['time_str']} | {r['score']} | {', '.join(r['signals'])} | "
                    f"{r['chat_words']} | {com.replace('|', '/')} |\n")
    print(f"Готово: {len(rows)} кандидатов -> {a.out}/moments.md")
    for r in rows:
        print(f"  {r['time_str']}  {r['score']:>5}  {', '.join(r['signals'])}  {r['chat_words']}")


if __name__ == "__main__":
    main()
