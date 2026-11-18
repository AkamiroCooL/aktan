# Нарезка стримов на клипы (10–15 сек)

Бесплатная альтернатива vidIQ: скачивает стрим (или только нужный отрезок),
сама находит самые громкие моменты (крики, хайп, реакции) и режет их
в вертикальные клипы 1080×1920 для Shorts / TikTok / Reels.

## Установка (один раз)

1. Python 3: https://www.python.org/downloads/
2. ffmpeg: https://ffmpeg.org/download.html
   (Windows: `winget install ffmpeg`, macOS: `brew install ffmpeg`)
3. `pip install -U yt-dlp`

## Использование

```bash
# пробный прогон: 2 минуты стрима -> клипы по 10-15 сек
python3 clipper/stream_clipper.py "https://www.youtube.com/live/qSyo5d-dVzA" --start 30:00 --end 32:00 --count 4

# весь файл, 8 лучших моментов
python3 clipper/stream_clipper.py stream.mp4 --count 8

# вручную по таймкодам
python3 clipper/stream_clipper.py stream.mp4 --at 1:02:10 --at 1:05:40
```

Параметры: `--min` / `--max` (длина клипа, по умолчанию 10–15 сек),
`--count` (сколько клипов), `--horizontal` (оставить 16:9), `--out` (папка).

Клипы появятся в папке `clips/`.

> Идущий в эфире стрим скачивается только после окончания трансляции,
> когда YouTube обработает запись.

> Нарезки чужих стримов могут получить страйк за авторские права —
> добавляй свою реакцию/комментарий.
