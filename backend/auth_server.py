"""Local speaker-verification service for Sunset Journal.

This service is intentionally separate from the legacy Flask prototype. It
stores normalized ECAPA-TDNN speaker embeddings, never raw enrolment audio.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
import certifi
from fastapi import Cookie, FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from speechbrain.inference.speaker import SpeakerRecognition
from speechbrain.utils.fetching import LocalStrategy
from pymongo import ASCENDING, MongoClient
from pymongo.errors import DuplicateKeyError

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("AUTH_SQLITE_PATH", BASE_DIR / "data" / "auth.db"))
MODEL_DIR = Path(os.getenv("VOICE_MODEL_DIR", BASE_DIR / "model_cache" / "ecapa"))
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:8000")
MONGODB_URI = os.getenv("MONGODB_URI", "").strip()
MONGODB_DATABASE = os.getenv("MONGODB_DATABASE", "sunset_journal")
COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "sunset_session")
COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "lax").lower()
SESSION_HOURS = int(os.getenv("SESSION_TTL_HOURS", "24"))
CHALLENGE_SECONDS = int(os.getenv("LOGIN_CHALLENGE_TTL_SECONDS", "120"))
THRESHOLD = float(os.getenv("VOICE_VERIFICATION_THRESHOLD", "0.72"))
MAX_UPLOAD_BYTES = 4 * 1024 * 1024


def find_ffmpeg() -> str:
    configured = os.getenv("FFMPEG_PATH")
    candidates = [
        configured,
        shutil.which("ffmpeg"),
        str(Path.home() / "OneDrive" / "Pictures" / "ffmpeg" / "bin" / "ffmpeg.exe"),
        str(Path.home() / "Pictures" / "ffmpeg" / "bin" / "ffmpeg.exe"),
        r"C:\ffmpeg\bin\ffmpeg.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError(
        "FFmpeg was not found. Set FFMPEG_PATH in backend/.env to the full path of ffmpeg.exe."
    )

ENROLMENT_PROMPTS = (
    "The evening feels calm and peaceful today.",
    "This private journal belongs to my voice.",
    "Seven quiet stars appeared above the window.",
)
CHALLENGE_PROMPTS = (
    "A quiet evening begins with one honest thought.",
    "The warm sunset rests beyond the open window.",
    "My journal keeps the words I choose to share.",
    "Soft evening light settles across the room.",
)
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,30}$")
GENERIC_FAILURE = "No matching account found. Please try again."


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.isoformat()


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL,
                    normalized_username TEXT NOT NULL UNIQUE,
                    voice_embedding TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS enrolments (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    normalized_username TEXT NOT NULL,
                    embeddings TEXT NOT NULL DEFAULT '[]',
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS challenges (
                    id TEXT PRIMARY KEY,
                    normalized_username TEXT NOT NULL,
                    phrase TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used_at TEXT
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    expires_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                );
                """
            )

    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def user(self, normalized: str):
        with self.connect() as db:
            return db.execute(
                "SELECT * FROM users WHERE normalized_username = ?", (normalized,)
            ).fetchone()

    def create_user(self, username: str, normalized: str, embedding: list[float]):
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO users(username, normalized_username, voice_embedding, created_at) VALUES(?,?,?,?)",
                (username, normalized, json.dumps(embedding), iso(now())),
            )
            return cursor.lastrowid

    def create_session(self, user_id: int):
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with self.connect() as db:
            db.execute(
                "INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)",
                (token_hash, user_id, iso(now() + timedelta(hours=SESSION_HOURS))),
            )
        return token

    def session_user(self, token: str | None):
        if not token:
            return None
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with self.connect() as db:
            return db.execute(
                """SELECT users.id, users.username, users.normalized_username
                   FROM sessions JOIN users ON users.id=sessions.user_id
                   WHERE token_hash=? AND expires_at>?""",
                (token_hash, iso(now())),
            ).fetchone()

    def create_enrolment(self, enrolment_id, username, normalized):
        with self.connect() as db:
            db.execute(
                "INSERT INTO enrolments(id,username,normalized_username,expires_at) VALUES(?,?,?,?)",
                (enrolment_id, username, normalized, iso(now() + timedelta(minutes=15))),
            )

    def enrolment(self, enrolment_id):
        with self.connect() as db:
            return db.execute(
                "SELECT * FROM enrolments WHERE id=? AND expires_at>?", (enrolment_id, iso(now()))
            ).fetchone()

    def save_embeddings(self, enrolment_id, embeddings):
        with self.connect() as db:
            db.execute("UPDATE enrolments SET embeddings=? WHERE id=?", (json.dumps(embeddings), enrolment_id))

    def delete_enrolment(self, enrolment_id):
        with self.connect() as db:
            db.execute("DELETE FROM enrolments WHERE id=?", (enrolment_id,))

    def create_challenge(self, challenge_id, normalized, phrase):
        with self.connect() as db:
            db.execute(
                "INSERT INTO challenges(id,normalized_username,phrase,expires_at) VALUES(?,?,?,?)",
                (challenge_id, normalized, phrase, iso(now() + timedelta(seconds=CHALLENGE_SECONDS))),
            )

    def consume_challenge(self, challenge_id, normalized):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM challenges WHERE id=? AND normalized_username=? AND used_at IS NULL AND expires_at>?",
                (challenge_id, normalized, iso(now())),
            ).fetchone()
            if row:
                db.execute("UPDATE challenges SET used_at=? WHERE id=?", (iso(now()), challenge_id))
            return row

    def delete_session(self, token):
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))


class MongoStore:
    def __init__(self, uri: str):
        self.db = MongoClient(
            uri,
            tls=True,
            tlsCAFile=certifi.where(),
            serverSelectionTimeoutMS=10000,
        )[MONGODB_DATABASE]
        self._indexes_ready = False

    def ensure_indexes(self):
        if self._indexes_ready:
            return
        self.db.users.create_index("normalized_username", unique=True)
        for name in ("enrolments", "challenges", "sessions"):
            self.db[name].create_index("expires_at", expireAfterSeconds=0)
        self._indexes_ready = True

    @staticmethod
    def clean(document):
        if document and "_id" in document:
            document["id"] = str(document.pop("_id"))
        return document

    def user(self, normalized):
        self.ensure_indexes()
        return self.clean(self.db.users.find_one({"normalized_username": normalized}))

    def create_user(self, username, normalized, embedding):
        result = self.db.users.insert_one({
            "username": username, "normalized_username": normalized,
            "voice_embedding": json.dumps(embedding), "created_at": now(),
        })
        return str(result.inserted_id)

    def create_session(self, user_id):
        token = secrets.token_urlsafe(32)
        self.db.sessions.insert_one({
            "token_hash": hashlib.sha256(token.encode()).hexdigest(),
            "user_id": str(user_id), "expires_at": now() + timedelta(hours=SESSION_HOURS),
        })
        return token

    def session_user(self, token):
        if not token:
            return None
        self.ensure_indexes()
        session = self.db.sessions.find_one({
            "token_hash": hashlib.sha256(token.encode()).hexdigest(), "expires_at": {"$gt": now()}
        })
        if not session:
            return None
        from bson import ObjectId
        try:
            return self.clean(self.db.users.find_one({"_id": ObjectId(session["user_id"])}))
        except Exception:
            return None

    def create_enrolment(self, enrolment_id, username, normalized):
        self.db.enrolments.insert_one({
            "_id": enrolment_id, "username": username, "normalized_username": normalized,
            "embeddings": "[]", "expires_at": now() + timedelta(minutes=15),
        })

    def enrolment(self, enrolment_id):
        return self.clean(self.db.enrolments.find_one({"_id": enrolment_id, "expires_at": {"$gt": now()}}))

    def save_embeddings(self, enrolment_id, embeddings):
        self.db.enrolments.update_one({"_id": enrolment_id}, {"$set": {"embeddings": json.dumps(embeddings)}})

    def delete_enrolment(self, enrolment_id):
        self.db.enrolments.delete_one({"_id": enrolment_id})

    def create_challenge(self, challenge_id, normalized, phrase):
        self.ensure_indexes()
        self.db.challenges.insert_one({
            "_id": challenge_id, "normalized_username": normalized, "phrase": phrase,
            "expires_at": now() + timedelta(seconds=CHALLENGE_SECONDS), "used_at": None,
        })

    def consume_challenge(self, challenge_id, normalized):
        return self.clean(self.db.challenges.find_one_and_update(
            {"_id": challenge_id, "normalized_username": normalized, "used_at": None,
             "expires_at": {"$gt": now()}},
            {"$set": {"used_at": now()}},
        ))

    def delete_session(self, token):
        self.db.sessions.delete_one({"token_hash": hashlib.sha256(token.encode()).hexdigest()})


store = MongoStore(MONGODB_URI) if MONGODB_URI else Store(DB_PATH)
_model: SpeakerRecognition | None = None


def model() -> SpeakerRecognition:
    global _model
    if _model is None:
        _model = SpeakerRecognition.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            savedir=str(MODEL_DIR),
            run_opts={"device": "cpu"},
            local_strategy=LocalStrategy.COPY,
        )
    return _model


def normalized_username(value: str) -> tuple[str, str]:
    display = value.strip()
    if not USERNAME_RE.fullmatch(display):
        raise HTTPException(
            422,
            "Use 3–30 characters containing letters, numbers, underscores, hyphens, or periods.",
        )
    return display, display.lower()


async def embedding_from_upload(upload: UploadFile) -> np.ndarray:
    raw = await upload.read(MAX_UPLOAD_BYTES + 1)
    if not raw or len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "Recording is missing or too large.")
    suffix = Path(upload.filename or "sample.webm").suffix or ".webm"
    return await asyncio.to_thread(embedding_from_bytes, raw, suffix)


def embedding_from_bytes(raw: bytes, suffix: str) -> np.ndarray:
    with tempfile.TemporaryDirectory(prefix="sunset-auth-") as temp:
        source = Path(temp) / f"source{suffix}"
        wav = Path(temp) / "audio.wav"
        source.write_bytes(raw)
        try:
            subprocess.run(
                [
                    find_ffmpeg(), "-v", "error", "-y", "-i", str(source),
                    "-ac", "1", "-ar", "16000", "-t", "12", str(wav),
                ],
                check=True,
                capture_output=True,
                timeout=20,
            )
        except (FileNotFoundError, subprocess.SubprocessError) as exc:
            raise HTTPException(400, "Audio could not be processed. Ensure FFmpeg is installed.") from exc
        samples, sample_rate = sf.read(str(wav), dtype="float32", always_2d=False)
        if samples.ndim > 1:
            samples = np.mean(samples, axis=1)
        signal = torch.from_numpy(np.asarray(samples, dtype=np.float32)).unsqueeze(0)
        duration = signal.shape[1] / sample_rate
        rms = float(torch.sqrt(torch.mean(signal**2)))
        if duration < 1.5 or duration > 12 or rms < 0.008:
            raise HTTPException(400, "Recording is too short, too long, or too quiet.")
        with torch.no_grad():
            vector = model().encode_batch(signal).squeeze().cpu().numpy()
    vector = vector.astype(np.float32)
    return vector / max(float(np.linalg.norm(vector)), 1e-12)


def set_cookie(response: Response, token: str):
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        max_age=SESSION_HOURS * 3600,
        path="/",
    )


class UsernameBody(BaseModel):
    username: str


app = FastAPI(title="Sunset Journal Voice Authentication")
app.add_middleware(
    CORSMiddleware,
    allow_origins=list({
        *[origin.strip() for origin in FRONTEND_ORIGIN.split(",") if origin.strip()],
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    }),
    allow_credentials=True,
    allow_origin_regex=r"https://[a-zA-Z0-9-]+\.vercel\.app",
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/api/health")
def health():
    return {"ok": True, "modelLoaded": _model is not None}


@app.post("/api/auth/register/start")
def register_start(body: UsernameBody):
    display, normalized = normalized_username(body.username)
    if store.user(normalized):
        raise HTTPException(409, "That username is unavailable.")
    enrolment_id = secrets.token_urlsafe(24)
    store.create_enrolment(enrolment_id, display, normalized)
    return {"enrolmentId": enrolment_id, "prompts": ENROLMENT_PROMPTS}


@app.post("/api/auth/register/sample")
async def register_sample(
    enrolment_id: str = Form(...),
    sample_index: int = Form(...),
    audio: UploadFile = File(...),
):
    if sample_index not in range(3):
        raise HTTPException(400, "Invalid sample.")
    row = store.enrolment(enrolment_id)
    if not row:
        raise HTTPException(400, "Enrolment expired. Please start again.")
    vector = await embedding_from_upload(audio)
    embeddings = json.loads(row["embeddings"])
    while len(embeddings) <= sample_index:
        embeddings.append(None)
    embeddings[sample_index] = vector.tolist()
    store.save_embeddings(enrolment_id, embeddings)
    return {"accepted": True, "sampleIndex": sample_index}


@app.post("/api/auth/register/complete")
def register_complete(body: dict, response: Response):
    enrolment_id = str(body.get("enrolmentId", ""))
    row = store.enrolment(enrolment_id)
    if not row:
        raise HTTPException(400, "Enrolment expired. Please start again.")
    vectors = json.loads(row["embeddings"])
    if len(vectors) != 3 or any(item is None for item in vectors):
        raise HTTPException(400, "Complete all three voice samples.")
    combined = np.mean(np.asarray(vectors, dtype=np.float32), axis=0)
    combined /= max(float(np.linalg.norm(combined)), 1e-12)
    try:
        user_id = store.create_user(row["username"], row["normalized_username"], combined.tolist())
    except (sqlite3.IntegrityError, DuplicateKeyError) as exc:
        raise HTTPException(409, "That username is unavailable.") from exc
    store.delete_enrolment(enrolment_id)
    token = store.create_session(user_id)
    set_cookie(response, token)
    return {"authenticated": True, "username": row["username"]}


@app.post("/api/auth/login/challenge")
def login_challenge(body: UsernameBody):
    _, normalized = normalized_username(body.username)
    challenge_id = secrets.token_urlsafe(24)
    phrase = secrets.choice(CHALLENGE_PROMPTS)
    store.create_challenge(challenge_id, normalized, phrase)
    return {"challengeId": challenge_id, "phrase": phrase, "expiresIn": CHALLENGE_SECONDS}


@app.post("/api/auth/login/verify")
async def login_verify(
    response: Response,
    username: str = Form(...),
    challenge_id: str = Form(...),
    transcript: str = Form(...),
    audio: UploadFile = File(...),
):
    try:
        _, normalized = normalized_username(username)
        challenge = store.consume_challenge(challenge_id, normalized)
        user = store.user(normalized)
        if not challenge or not user:
            raise ValueError
        expected = re.sub(r"[^a-z0-9 ]", "", challenge["phrase"].lower())
        heard = re.sub(r"[^a-z0-9 ]", "", transcript.lower())
        if SequenceMatcher(None, expected, heard).ratio() < 0.72:
            raise ValueError
        probe = await embedding_from_upload(audio)
        enrolled = np.asarray(json.loads(user["voice_embedding"]), dtype=np.float32)
        similarity = float(np.dot(probe, enrolled))
        if similarity < THRESHOLD:
            raise ValueError
    except Exception as exc:
        if isinstance(exc, HTTPException) and exc.status_code >= 500:
            raise
        raise HTTPException(401, GENERIC_FAILURE) from None
    token = store.create_session(user["id"])
    set_cookie(response, token)
    return {"authenticated": True, "username": user["username"]}


@app.get("/api/auth/session")
def auth_session(sunset_session: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    user = store.session_user(sunset_session)
    if not user:
        raise HTTPException(401, "Not authenticated.")
    return {"authenticated": True, "username": user["username"]}


@app.post("/api/auth/logout")
def logout(response: Response, sunset_session: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    if sunset_session:
        store.delete_session(sunset_session)
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"authenticated": False}
