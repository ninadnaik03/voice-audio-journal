from flask import Flask, request, jsonify
from flask_cors import CORS
import os

from transcriber import transcribe_audio
from summarizer import generate_summary
from history import save_session, get_all_sessions, init_db

app = Flask(__name__)

# Allow your Vercel frontend
CORS(app, resources={r"/api/*": {"origins": "*"}})

init_db()
os.makedirs("chunks", exist_ok=True)

TRANSCRIPT = ""
chunk_index = 0


@app.route("/api/health")
def health():
    return {"status": "ok"}


@app.route("/api/register", methods=["POST"])
def register():
    if "audio" not in request.files:
        return jsonify({"success": False, "message": "No audio file"}), 400

    file = request.files["audio"]
    path = "temp_register.webm"
    file.save(path)

    try:
        text = transcribe_audio(path)
        return jsonify({
            "success": True,
            "message": "Voice registered (basic mode)",
            "heard": text
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/login", methods=["POST"])
def login():
    if "audio" not in request.files:
        return jsonify({"success": False, "message": "No audio file"}), 400

    file = request.files["audio"]
    path = "temp_login.webm"
    file.save(path)

    try:
        text = transcribe_audio(path)

        if "open" in text.lower():
            return jsonify({
                "success": True,
                "message": "Access granted",
                "heard": text
            })
        else:
            return jsonify({
                "success": False,
                "message": f"Say passphrase 'open'. Heard: {text}"
            })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/record_chunk", methods=["POST"])
def record_chunk():
    global TRANSCRIPT, chunk_index

    if "audio" not in request.files:
        return jsonify({"success": False}), 400

    file = request.files["audio"]
    path = f"chunks/chunk_{chunk_index}.webm"
    file.save(path)

    try:
        text = transcribe_audio(path)
        if text.strip():
            TRANSCRIPT += " " + text

        chunk_index += 1

        return jsonify({
            "success": True,
            "text": text.strip(),
            "full_transcript": TRANSCRIPT.strip()
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/finish", methods=["POST"])
def finish():
    global TRANSCRIPT, chunk_index

    if not TRANSCRIPT.strip():
        return jsonify({"success": False, "message": "No content"}), 400

    try:
        summary = generate_summary(TRANSCRIPT)
        save_session(summary, TRANSCRIPT)

        result = {
            "success": True,
            "transcript": TRANSCRIPT,
            "summary": summary
        }

        TRANSCRIPT = ""
        chunk_index = 0

        return jsonify(result)

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/history")
def history():
    try:
        rows = get_all_sessions()
        entries = [
            {"timestamp": r[0], "transcript": r[1], "summary": r[2]}
            for r in rows
        ]
        return jsonify({"success": True, "entries": entries})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


if __name__ == "__main__":
    app.run()
