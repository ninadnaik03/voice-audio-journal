# Sunset Journal

A private voice journal with username-based speaker enrolment, voice
verification, live transcription, reflection summaries, and browser-local
journal history.

## Voice account flow

Registration records three prompted samples. The local authentication service
extracts a normalized 192-dimensional ECAPA-TDNN speaker embedding from each
sample, averages them, and stores only the resulting profile in SQLite.

Login creates a short-lived, single-use sentence challenge. Access requires both
a reasonable transcript match and a cosine similarity above the configured
speaker threshold. Sessions use a random HttpOnly cookie; only a hash of the
session token is stored.

Raw enrolment and login recordings are converted in a temporary directory and
deleted immediately after processing.

## Local requirements

- Python 3.11 or 3.12
- Node.js
- FFmpeg available on `PATH`
- Chrome or Edge

Create a Python environment and install the authentication dependencies:

```bash
cd backend
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements-auth.txt
copy .env.example .env
uvicorn auth_server:app --reload --port 8001
```

## Free production setup

The static website runs on Vercel. The voice-auth API is packaged for a
Hugging Face Docker Space in `backend/Dockerfile`, and account records can use
MongoDB Atlas M0.

Production secrets belong in the Space settings, never in Git:

```env
MONGODB_URI=mongodb+srv://...
MONGODB_DATABASE=sunset_journal
FRONTEND_ORIGIN=https://your-vercel-domain.vercel.app
SESSION_COOKIE_SECURE=true
SESSION_COOKIE_SAMESITE=none
```

After the Space URL is created, set this before the main application script:

```html
<script>window.SUNSET_AUTH_API = 'https://your-space.hf.space';</script>
```

Only usernames, normalized speaker embeddings, challenges, and sessions are
stored in Atlas. Raw authentication recordings are processed in temporary
files and deleted. Journal entries remain in the user's browser storage.

In a second terminal, run the frontend:

```bash
node serve.mjs
```

Open `http://localhost:8000`.

On Windows, the backend also automatically checks
`%USERPROFILE%\OneDrive\Pictures\ffmpeg\bin\ffmpeg.exe`. If FFmpeg is stored
elsewhere, set its full executable path in `backend/.env`:

```text
FFMPEG_PATH=C:\path\to\ffmpeg\bin\ffmpeg.exe
```

The speaker model downloads on first use and is cached under
`backend/model_cache/`. Local account data is stored in `backend/data/auth.db`.
Both locations are ignored by Git.

## Configuration

`VOICE_VERIFICATION_THRESHOLD` defaults to `0.72`. This is only a development
starting point and must be calibrated with representative genuine-user and
impostor recordings before production use.

For HTTPS production hosting, set `SESSION_COOKIE_SECURE=true` and restrict
`FRONTEND_ORIGIN` to the exact frontend origin.

## Privacy and security limitations

Journal transcripts and summaries still remain in the browser's local storage.
The server stores usernames, normalized speaker embeddings, challenges, and
hashed session tokens.

This development implementation uses a pretrained speaker-embedding model for
voice verification. It is not production-grade biometric security and does not
yet include dedicated anti-spoofing or liveness detection.

It can reduce casual cross-account access, but it cannot reliably stop replayed
recordings, cloned voices, synthetic speech, deepfakes, or a determined
impersonator. Voice can also vary with illness, microphones, and background
noise, so legitimate users may occasionally be rejected.

## Deployment

The current Vercel static deployment cannot run the PyTorch/SpeechBrain service.
Do not deploy this authentication phase until the Python service is hosted on a
suitable container or VM and the frontend API base URL is configured for it.
