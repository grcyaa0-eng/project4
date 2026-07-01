import os
import uuid
from datetime import datetime, timezone

from flask import Flask, request, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv

from audit_log import init_db, log_entry, get_log, get_entry_by_content_id, update_status
from signals import groq_signal, stylometric_signal


def compute_label(confidence):
    """Maps a combined confidence score to (attribution, label_text)."""
    if confidence > 0.65:
        attribution = "likely_ai"
        label = f"This content shows strong indicators of AI generation. Confidence: {confidence:.2f}."
    elif confidence < 0.35:
        attribution = "likely_human"
        label = f"This content shows strong indicators of human authorship. Confidence: {confidence:.2f}."
    else:
        attribution = "uncertain"
        label = f"We can't confidently determine whether this content is AI-generated or human-written. Confidence: {confidence:.2f}."
    return attribution, label

load_dotenv()

app = Flask(__name__)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

init_db()


@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute;100 per day")
def submit():
    data = request.get_json(force=True) or {}
    text = data.get("text", "")
    creator_id = data.get("creator_id", "unknown")

    if not text.strip():
        return jsonify({"error": "text field is required"}), 400

    content_id = str(uuid.uuid4())

    try:
        llm_score, llm_reason = groq_signal(text)
    except Exception as e:
        return jsonify({"error": f"signal 1 failed: {str(e)}"}), 500

    try:
        style_score, style_details = stylometric_signal(text)
    except Exception as e:
        return jsonify({"error": f"signal 2 failed: {str(e)}"}), 500

    # Combined confidence: weighted average per planning.md
    # (LLM signal weighted higher - more holistic; see planning.md rationale)
    confidence = round(0.6 * llm_score + 0.4 * style_score, 3)
    attribution, label = compute_label(confidence)

    entry = {
        "content_id": content_id,
        "creator_id": creator_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "attribution": attribution,
        "confidence": confidence,
        "llm_score": llm_score,
        "style_score": style_score,
        "label": label,
        "status": "classified",
    }
    log_entry(entry)

    return jsonify({
        "content_id": content_id,
        "attribution": attribution,
        "confidence": confidence,
        "label": label,
    })


@app.route("/appeal", methods=["POST"])
def appeal():
    data = request.get_json(force=True) or {}
    content_id = data.get("content_id", "")
    creator_reasoning = data.get("creator_reasoning", "")

    if not content_id or not creator_reasoning.strip():
        return jsonify({"error": "content_id and creator_reasoning are required"}), 400

    existing = get_entry_by_content_id(content_id)
    if not existing:
        return jsonify({"error": f"no submission found for content_id {content_id}"}), 404

    update_status(content_id, "under_review", creator_reasoning)

    return jsonify({
        "content_id": content_id,
        "status": "under_review",
        "message": "Appeal received. Your submission has been flagged for human review.",
    })


@app.route("/log", methods=["GET"])
def view_log():
    return jsonify({"entries": get_log()})


if __name__ == "__main__":
    app.run(debug=True, port=5000)