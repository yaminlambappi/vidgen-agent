# School of Sufi

One reflection in. One 30-second munajat out.

`POST /generate` accepts a single field, `thought`. The engine turns it into one prayer in one visual world: six Bangla beats, five seconds each. A plan that is not one film never reaches Veo or Cloud Text-to-Speech. After the pictures exist, representative frames are checked. After the mux, the master is checked. A failure retries once where a retry can help, then stops. Nothing is uploaded until that master passes.

Veo 3.1 can only generate 4, 6, or 8 second clips. Each shot is generated at 6 seconds and trimmed from the first frame to 5. The six fitted shots are stored at `sufi/{job_id}/shots/shot_N.mp4`. The master is `sufi/{job_id}/final_short.mp4`. Veo's raw takes stay under `sufi/{job_id}/shot_N/`.

The default voice is `bn-IN-Wavenet-D` at speaking rate `0.72` and pitch `-6`. Those are settings (`TTS_VOICE`, `TTS_LANGUAGE`, `TTS_SPEAKING_RATE`, `TTS_PITCH`, `TTS_MAWLA_BREAK_MS`). In production the heard audio is transcribed, and a reading of মাওলা as মৌলা is rejected. The voice id is not treated as proof of how the line feels.

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

Without `FILM_MODE=production` and `ALLOW_REAL_GENERATION=true`, the picture is a local moving plate and the voice is a quiet tone, so the path can be checked without spending credits. Production calls Gemini, Veo, Cloud Text-to-Speech, and a second look at the frames and the transcript.

## Publish

Set these to post after a successful file:

- `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN`
- `YOUTUBE_PRIVACY` — `unlisted` unless you set `public` or `private`
- `SOCIAL_WEBHOOK_URL` — receives the mp4 as multipart form data

A target that is not configured is skipped. A target that is configured and fails leaves the mp4 on disk and returns an error.

## Cost ceilings

`MAX_VEO_CALLS` (12), `MAX_TTS_CALLS` (8), and `MAX_GEMINI_CALLS` (6). The job stops before a call that would pass the ceiling. A 30-second short spends 6 Veo calls, and one more for each shot that has to be filmed again.
