# School of Sufi

One reflection in. One vertical short out.

`POST /generate` accepts a single field, `thought`. The engine writes a 30-second short for a brief reflection and a 60-second short for a longer one, then uploads it when YouTube or a social webhook is configured.

Veo 3.1 can only generate 4, 6, or 8 second clips. Each picture is generated at 8 seconds and held to an exact slot: 15+15 for a 30-second short, or 30+30 for a 60-second short. The slots always add up to the master length.

## Run

```bash
python cli.py --thought "Sincerity is the hidden root of remembrance." --no-publish
```

`--no-publish` renders the file and skips YouTube. Omit it when the upload credentials below are set.

```bash
curl -X POST http://localhost:8000/generate \
  -H 'content-type: application/json' \
  -d '{"thought":"Sincerity is the hidden root of remembrance."}'
```

Without `FILM_MODE=production` and `ALLOW_REAL_GENERATION=true`, the picture is a local plate and the voice is a quiet tone, so the path can be checked without spending credits. Production calls Gemini, Veo, and Cloud Text-to-Speech.

## Publish

Set these to post after a successful file:

- `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN`
- `YOUTUBE_PRIVACY` — `unlisted` unless you set `public` or `private`
- `SOCIAL_WEBHOOK_URL` — receives the mp4 as multipart form data

A target that is not configured is skipped. A target that is configured and fails leaves the mp4 on disk and returns an error.

## Cost ceilings

`MAX_VEO_CALLS` (3), `MAX_TTS_CALLS` (2), and `MAX_GEMINI_CALLS` (2). The job stops before a call that would pass the ceiling.
