# ClipFusion AI

Turns long videos (gaming streams, podcasts, interviews) into vertical Shorts: finds the best moments
automatically, keeps the speaker's face in frame, exports clean 1080x1920 MP4 files (no burned-in
subtitles), writes a title, description and hashtags for each clip, and uploads them to YouTube one by one,
in the order they appear in the original video, through the official API.

## Start it (Windows)

```powershell
powershell -ExecutionPolicy Bypass -File start.ps1     # then open http://localhost:3000/app
powershell -ExecutionPolicy Bypass -File stop.ps1
```

The API runs on **http://localhost:8010** (port 8000 is left to other programs). `start.ps1` checks that
whatever answers on port 8010 really is ClipFusion and stops with a message naming the other program
otherwise; `stop.ps1` only stops ClipFusion's own processes.

There is no login and no API key is needed for clipping. The app only answers on this computer. AI runs
locally and for free (faster-whisper, local highlight scoring); Claude is optional (`ANTHROPIC_API_KEY`).

| Option | What it does |
|---|---|
| `start.ps1 -Celery` | Uses Redis + Celery workers instead of the built-in worker |
| `start.ps1 -Dev` | Runs the website in development mode |
| `start.ps1 -ApiPort 8020` | Uses another API port (also set `BACKEND_PUBLIC_URL` and the Google redirect URI) |

`start.ps1` rebuilds the website when its code changed or when the API address changed. Logs are in
`backend\data\logs`.

## First-time setup on another PC

1. Install [Scoop](https://scoop.sh), then `scoop install ffmpeg postgresql redis uv nodejs`
2. Backend:
   ```powershell
   cd backend
   uv venv --python 3.12 .venv
   uv pip install --python .venv\Scripts\python.exe -r pyproject.toml --extra dev --extra mediapipe
   .venv\Scripts\python.exe scripts\download_assets.py     # face models
   ```
3. Website: `cd frontend`, `npm ci`
4. Run `start.ps1`. The first analysis downloads the speech model.

## How to use it

1. **Add a video**: upload the original file, paste a direct link (Dropbox, Google Drive, OneDrive, S3 ...),
   or paste a YouTube link. Confirm *"I have the creator's permission, a licence, or other rights to reuse
   and edit this video"* (owning the channel is not required).
   - A YouTube link fetches the title, channel, duration and thumbnail from the official YouTube Data API
     (with `YOUTUBE_API_KEY` or a connected Google account) and sets the credit line
     `Credit: <channel> — <url>`. YouTube's Terms do not allow downloading, so attach the creator-provided
     file (upload or direct link) to that video.
2. **Processing starts automatically** (Fast / Balanced / Best, clip lengths and count from Settings): audio,
   transcription, video analysis, highlights, face tracking + framing, rendering and metadata. The video
   page shows the real progress of every stage.
3. **Review the clips** in source-timestamp order (1, 2, 3 ...). Each clip shows its transcript, title,
   description and hashtags. If the framing would cut a face, the clip waits for a framing review.
   **Remove Clip** deletes only that clip's files.
4. **Upload to YouTube**: one clip, the next one, or all remaining clips. Only one upload runs at a time per
   channel, in sequence order; the run stops at the first failure and **Retry** resumes from that clip.
5. **After a verified upload** (YouTube returned the video id and confirmed it exists and was processed),
   that clip's MP4, proxy and thumbnail are deleted to free space; its title, description, hashtags,
   timestamps and YouTube link are kept. The **Storage** page shows what is stored and lets you delete an
   original you no longer need (or turn on *delete the original automatically* in Settings; off by default).

## Optional features that need credentials

Copy values from [.env.example](.env.example) into `.env` and restart. Details: [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md).

| Feature | Settings |
|---|---|
| YouTube link details without connecting an account | `YOUTUBE_API_KEY` (YouTube Data API v3) |
| YouTube uploads, retention data | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`; redirect URI `http://localhost:8010/api/v1/accounts/callback/google` (YouTube keeps uploads private until Google audits the project) |
| AI-written titles and AI ranking | `ANTHROPIC_API_KEY` (optional; local metadata is used otherwise) |
| Instagram publishing | `INSTAGRAM_APP_ID`, `INSTAGRAM_APP_SECRET` and a professional account |
| S3 / Cloudflare R2 storage | `STORAGE_BACKEND=s3` and the `S3_*` values |

If you connected Google before the port change, add the new redirect URI (port 8010) in the Google Cloud
console.

## Before putting it on the internet

No-login mode refuses to run with `ENVIRONMENT=production`. Set `AUTH_REQUIRED=true`, a 32+ character
`SECRET_KEY`, `TOKEN_ENCRYPTION_KEYS`, `COOKIE_SECURE=true` and HTTPS. The backend supports accounts, but the
website's login pages were removed, so they would have to be added back first. `docker-compose.yml` and
`backend/Dockerfile` are provided but have not been built or tested, and `frontend` has no Dockerfile yet.

## Tests

```powershell
cd backend;  .venv\Scripts\python.exe -m pytest tests -q
cd frontend; npm run typecheck; npm run lint; npm test
```

## Layout

`backend/app/modules` (one package per feature), `backend/app/workers` (background jobs), `frontend/src`
(website), `shared/fixtures` (cases that keep the live preview identical to the export),
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/API.md](docs/API.md), [docs/API_V2.md](docs/API_V2.md).
