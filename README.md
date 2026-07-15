# Voice Audio Journal

A private, browser-based voice journal with voice-passphrase access, live
speech-to-text transcription, reflection summaries, and journal history.

## Features

- Register and verify the spoken passphrase `open`
- Record journal entries with live transcription
- Pause, resume, finish, or discard a recording
- Generate a local reflection and mood summary
- Browse saved journal history
- Keep journal content on the user's device using browser storage

## Deploy to Vercel

No environment variables, database, build command, or API keys are required.

1. Import this GitHub repository in Vercel.
2. Leave **Framework Preset** as `Other`.
3. Leave the build and output settings at their defaults.
4. Select **Deploy**.

The included `vercel.json` supplies deployment and microphone security headers.

## Run locally

Serve the repository instead of opening the HTML file directly:

```bash
node serve.mjs
```

Then open `http://localhost:8000` in Chrome or Edge.

## Browser support and privacy

Live transcription uses the browser Web Speech API and currently works best in
Chrome and Edge. Journal entries and registration state are stored in that
browser's local storage. Clearing site data removes them, and entries do not
automatically sync between devices.

The legacy Flask prototype remains under `backend/` for reference, but Vercel
serves the root `index.html` and does not run the Python backend.
