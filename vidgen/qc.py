"""Quality Control Agents — intent-aware, structured failure reasons."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from vidgen.agents import BaseAgent
from google.genai import types
from vidgen.models import Shot, CinematicBible, Character, ContentIntent, QCFailureReason
from vidgen.utils.references import resolve_reference_path


class QCMAgent(BaseAgent):
    """
    Intent-aware Quality Control Agent.

    A technically valid MP4 is NOT sufficient for acceptance.
    QC evaluates whether the shot fulfils its intended objective.

    Failure reasons are machine-readable (QCFailureReason enum) to enable
    targeted prompt correction on retry.
    """

    def check_character_consistency(self, reference_image_path: str, generated_image_path: str) -> Dict[str, Any]:
        if not reference_image_path or not generated_image_path:
            return {"consistent": True, "score": 1.0, "note": "No reference image provided."}
        prompt = [
            self._image_part(reference_image_path), self._image_part(generated_image_path),
            "Is this the same person/subject? Scrutinize facial structure, unique features, and overall appearance. "
            "Ignore minor lighting or expression changes. "
            'Respond with JSON: {"consistent": boolean, "score": float (0.0-1.0), "note": "justification"}.',
        ]
        cfg = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.0)
        response = self.client.models.generate_content(model=self.model, contents=prompt, config=cfg)
        try:
            result = json.loads(response.text)
            return {
                "consistent": bool(result.get("consistent", False)),
                "score": float(result.get("score", 0.0)),
                "note": result.get("note", "")
            }
        except (json.JSONDecodeError, ValueError):
            return {"consistent": False, "score": 0.0, "error": "Failed to parse model response"}

    def check_visual_artifacts(self, generated_image_path: str) -> Dict[str, Any]:
        prompt = [
            self._image_part(generated_image_path),
            "Analyze this frame for visual artifacts: jitter, unnatural morphing, object instability, "
            "mangled hands/faces, AI-related glitches, impossible geometry. "
            'Respond with JSON: {"artifact_free": boolean, "issues": ["list"]}.',
        ]
        cfg = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1)
        response = self.client.models.generate_content(model=self.model, contents=prompt, config=cfg)
        try:
            result = json.loads(response.text)
            return {
                "artifact_free": bool(result.get("artifact_free", False)),
                "issues": result.get("issues", [])
            }
        except (json.JSONDecodeError, ValueError):
            return {"artifact_free": False, "issues": ["Failed to parse model response"]}

    def check_shot_intent(self, generated_image_path: str, shot: Shot,
                          content_intent: Optional[ContentIntent] = None) -> Dict[str, Any]:
        """
        Intent-aware check: did the shot fulfil its objective?
        Evaluates subject presence, action, and story beat.
        """
        obj = shot.shot_objective
        if obj:
            objective_text = (
                f"Shot objective: {obj.what_must_audience_see}\n"
                f"Primary subject: {obj.primary_subject}\n"
                f"Subject action: {obj.subject_action}\n"
                f"Story beat: {obj.story_beat}\n"
                f"Must not lose: {', '.join(obj.must_not_lose)}"
            )
        else:
            objective_text = (
                f"Primary subject: {shot.subject}\n"
                f"Required action: {shot.action}"
            )

        intent_text = ""
        if content_intent:
            intent_text = (
                f"\nContent primary subject: {content_intent.primary_subject} "
                f"(type: {content_intent.primary_subject_type})"
                f"\nProhibited outcomes: {', '.join(content_intent.prohibited_outcomes[:3])}"
            )

        prompt_text = (
            f"You are a QC supervisor for a professional film production.\n\n"
            f"== SHOT REQUIREMENTS ==\n{objective_text}{intent_text}\n\n"
            "== EVALUATION ==\n"
            "1. Is the primary subject clearly present and recognizable in this frame?\n"
            "2. Is the required action occurring?\n"
            "3. Does the frame serve the described story beat?\n"
            "4. Is any prohibited outcome present?\n\n"
            "Respond with JSON: "
            '{"subject_present": boolean, "action_present": boolean, '
            '"story_beat_served": boolean, "intent_failure": string or null, '
            '"passes_intent": boolean}'
        )
        contents = [self._image_part(generated_image_path), prompt_text]
        cfg = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.0)
        response = self.client.models.generate_content(model=self.model, contents=contents, config=cfg)
        try:
            result = json.loads(response.text)
            return {
                "subject_present": bool(result.get("subject_present", True)),
                "action_present": bool(result.get("action_present", True)),
                "story_beat_served": bool(result.get("story_beat_served", True)),
                "intent_failure": result.get("intent_failure"),
                "passes_intent": bool(result.get("passes_intent", True)),
            }
        except (json.JSONDecodeError, ValueError):
            return {"subject_present": True, "action_present": True,
                    "story_beat_served": True, "intent_failure": None, "passes_intent": True}

    def check_cinematic_style(self, generated_image_path: str, shot: Shot, cinematic_bible: CinematicBible) -> Dict[str, Any]:
        prompt = (
            "Does this frame adhere to the cinematic identity?\n\n"
            f"Color Palette: {cinematic_bible.color_palette}\n"
            f"Lighting Style: {cinematic_bible.lighting}\n"
            f"Camera Language: {cinematic_bible.camera_language}\n"
            f"Texture: {cinematic_bible.texture}\n\n"
            f"Shot Type: {shot.shot_type}, Lighting: {shot.lighting}, Composition: {shot.composition}\n\n"
            'Respond with JSON: {"style_adherent": boolean, "critique": "short critique if non-adherent"}.'
        )
        contents = [self._image_part(generated_image_path), prompt]
        cfg = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.2)
        response = self.client.models.generate_content(model=self.model, contents=contents, config=cfg)
        try:
            result = json.loads(response.text)
            return {
                "style_adherent": bool(result.get("style_adherent", False)),
                "critique": result.get("critique", "")
            }
        except (json.JSONDecodeError, ValueError):
            return {"style_adherent": False, "critique": "Failed to parse model response"}

    def check_continuity(self, frame1_path: str, frame2_path: str, shot1: Shot, shot2: Shot, characters: List[Character]) -> Dict[str, Any]:
        visible_chars1 = {c.character_id: c for c in characters if c.character_id in shot1.character_ids}
        visible_chars2 = {c.character_id: c for c in characters if c.character_id in shot2.character_ids}
        continuity_prompt = (
            "You are a continuity supervisor. Analyse these two sequential frames for errors. "
            "Check: wardrobe, props, character positions, background details, lighting.\n\n"
            f"Shot 1 characters: { {cid: c.name for cid, c in visible_chars1.items()} }\n"
            f"Shot 2 characters: { {cid: c.name for cid, c in visible_chars2.items()} }\n\n"
            'Respond with JSON: {"continuity_ok": boolean, "errors": ["list of errors"]}.'
        )
        contents = [self._image_part(frame1_path), self._image_part(frame2_path), continuity_prompt]
        cfg = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1)
        response = self.client.models.generate_content(model=self.model, contents=contents, config=cfg)
        try:
            result = json.loads(response.text)
            return {
                "continuity_ok": bool(result.get("continuity_ok", False)),
                "errors": result.get("errors", [])
            }
        except (json.JSONDecodeError, ValueError):
            return {"continuity_ok": False, "errors": ["Failed to parse model response"]}

    def critique_shot(self, frame_path: str, shot: Shot, cinematic_bible: CinematicBible,
                      prev_frame_path: str = None, prev_shot: Shot = None,
                      characters: List[Character] = None, storage=None,
                      content_intent: Optional[ContentIntent] = None) -> Dict[str, Any]:
        """
        Comprehensive intent-aware critique.

        Failure reasons are machine-readable QCFailureReason codes.
        A technically valid but narratively irrelevant shot FAILS.
        Style notes are advisory only (never fail alone).
        """
        critique: Dict[str, Any] = {"passed": True, "feedback": [], "failure_reasons": []}

        # 1. Artifact check — technical gate first
        artifact_report = self.check_visual_artifacts(frame_path)
        if not artifact_report["artifact_free"]:
            critique["passed"] = False
            critique["failure_reasons"].append(QCFailureReason.VISUAL_ARTIFACTS.value)
            critique["feedback"].append(
                f"Visual artifacts: {', '.join(artifact_report['issues'])}")
            return critique  # early exit — no point checking intent if frame is corrupt

        # 2. Intent check — did the shot fulfil its objective?
        intent_report = self.check_shot_intent(frame_path, shot, content_intent)
        if not intent_report["passes_intent"]:
            critique["passed"] = False
            if not intent_report["subject_present"]:
                critique["failure_reasons"].append(QCFailureReason.SUBJECT_MISSING.value)
                critique["feedback"].append(
                    f"Subject not present: {shot.subject or (shot.shot_objective.primary_subject if shot.shot_objective else 'unknown')}")
            if not intent_report["action_present"]:
                critique["failure_reasons"].append(QCFailureReason.ACTION_MISSING.value)
                critique["feedback"].append(f"Required action not visible: {shot.action}")
            if not intent_report["story_beat_served"]:
                critique["failure_reasons"].append(QCFailureReason.INTENT_MISMATCH.value)
                critique["feedback"].append(
                    f"Intent mismatch: {intent_report.get('intent_failure', 'story beat not served')}")

        # 3. Cinematic style — advisory
        style_report = self.check_cinematic_style(frame_path, shot, cinematic_bible)
        if not style_report["style_adherent"]:
            critique["feedback"].append(f"Style note (advisory): {style_report['critique']}")

        # 4. Character consistency
        if shot.character_ids and characters:
            char = next((c for c in characters if c.character_id == shot.character_ids[0]), None)
            ref_path = (resolve_reference_path(char, storage) if char and storage
                        else (char.reference_image_path if char else ""))
            if char and ref_path:
                char_report = self.check_character_consistency(ref_path, frame_path)
                if not char_report["consistent"]:
                    critique["passed"] = False
                    critique["failure_reasons"].append(QCFailureReason.CHARACTER_IDENTITY_BREAK.value)
                    critique["feedback"].append(
                        f"Character identity break: {char.name}. "
                        f"Score: {char_report['score']:.2f}. {char_report.get('note', '')}")

        # 5. Continuity check
        if prev_frame_path and prev_shot and characters:
            cont_report = self.check_continuity(prev_frame_path, frame_path, prev_shot, shot, characters)
            if not cont_report["continuity_ok"]:
                critique["passed"] = False
                critique["failure_reasons"].append(QCFailureReason.CONTINUITY_BREAK.value)
                critique["feedback"].append(
                    f"Continuity break: {', '.join(cont_report['errors'])}")

        return critique

    def generate_feedback_prompt(self, shot: Shot, critique: Dict[str, Any]) -> str:
        """
        Generates targeted prompt correction from structured failure reasons.
        Uses QCFailureReason codes to build specific corrective instructions.
        """
        if critique.get("passed"):
            return ""

        failure_reasons = critique.get("failure_reasons", [])
        feedback_lines = ["== PRIORITY RE-SHOOT — QC FAILURE =="]
        feedback_lines.append("The previous take failed QC. Address ALL of the following:")

        for issue in critique.get("feedback", []):
            feedback_lines.append(f"- {issue}")

        # Add targeted correction instructions per failure reason
        if QCFailureReason.SUBJECT_MISSING.value in failure_reasons:
            obj = shot.shot_objective
            subject = obj.primary_subject if obj else shot.subject
            feedback_lines.append(
                f"CRITICAL: The primary subject '{subject}' MUST be clearly visible. "
                "Do not substitute a generic scene.")
        if QCFailureReason.ACTION_MISSING.value in failure_reasons:
            feedback_lines.append(
                f"CRITICAL: Show the action explicitly: {shot.action}. "
                "The action must be unambiguous and in progress.")
        if QCFailureReason.CHARACTER_IDENTITY_BREAK.value in failure_reasons:
            feedback_lines.append(
                "CRITICAL: Character appearance MUST exactly match the reference. "
                "Do not alter face, wardrobe, age, or body proportions.")
        if QCFailureReason.CONTINUITY_BREAK.value in failure_reasons:
            feedback_lines.append(
                "CRITICAL: Maintain all continuity from previous shot: "
                "wardrobe, location, props, lighting, character positions.")
        if QCFailureReason.VISUAL_ARTIFACTS.value in failure_reasons:
            feedback_lines.append(
                "CRITICAL: No digital artifacts, morphing, instability, or impossible anatomy. "
                "Photorealistic natural rendering only.")
        if QCFailureReason.INTENT_MISMATCH.value in failure_reasons:
            obj = shot.shot_objective
            if obj and obj.what_must_audience_see:
                feedback_lines.append(
                    f"CRITICAL: The audience must see: {obj.what_must_audience_see}")

        feedback_lines.append("== END RE-SHOOT NOTE ==")
        return "\n".join(feedback_lines)

    @staticmethod
    def _image_part(path: str):
        path_obj = Path(path)
        return types.Part.from_bytes(data=path_obj.read_bytes(), mime_type="image/png")

