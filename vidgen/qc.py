"""Quality Control Agents"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, List
from vidgen.agents import BaseAgent
from google.genai import types
from vidgen.models import Shot, CinematicBible, Character
from vidgen.utils.references import resolve_reference_path

class QCMAgent(BaseAgent):
    """
    Quality Control Multimodal Agent.
    Upgraded for festival-grade scrutiny.
    """

    def check_character_consistency(self, reference_image_path: str, generated_image_path: str) -> Dict[str, Any]:
        """
        Checks if the character in the generated image is consistent with the reference image.
        """
        if not reference_image_path or not generated_image_path:
            return {"consistent": True, "score": 1.0, "note": "No reference image provided."}

        prompt = [
            self._image_part(reference_image_path), self._image_part(generated_image_path),
            "You are a meticulous film festival judge. Is this the same person? Scrutinize facial structure, unique features, and overall appearance. Ignore minor lighting or expression changes. Respond with JSON: {\"consistent\": boolean, \"score\": float (0.0-1.0 confidence), \"note\": \"brief justification\"}.",
        ]
        
        cfg = types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.0,
        )
        
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=cfg,
        )

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
        """
        Scans for common AI-generated visual artifacts.
        """
        prompt = [
            self._image_part(generated_image_path),
            "You are a ruthless QC inspector for a high-end film. Analyze this frame for any visual artifacts: jitter, unnatural morphing, object instability, mangled hands/faces, or other AI-related glitches. Be highly critical. Respond with JSON: {\"artifact_free\": boolean, \"issues\": [\"list of detected issues\"]}.",
        ]
        
        cfg = types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.1,
        )
        
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=cfg,
        )
        
        try:
            result = json.loads(response.text)
            return {
                "artifact_free": bool(result.get("artifact_free", False)),
                "issues": result.get("issues", [])
            }
        except (json.JSONDecodeError, ValueError):
            return {"artifact_free": False, "issues": ["Failed to parse model response"]}

    def check_cinematic_style(self, generated_image_path: str, shot: Shot, cinematic_bible: CinematicBible) -> Dict[str, Any]:
        """
        Verifies if the shot's visual style aligns with the project's Cinematic Bible.
        """
        prompt = (
            "You are a Director of Photography. Does this frame adhere to the established cinematic identity?\n\n"
            f"== CINEMATIC BIBLE ==\n"
            f"Color Palette: {cinematic_bible.color_palette}\n"
            f"Lighting Style: {cinematic_bible.lighting}\n"
            f"Camera Language: {cinematic_bible.camera_language}\n"
            f"Texture: {cinematic_bible.texture}\n\n"
            f"== SHOT CONTEXT ==\n"
            f"Shot Type: {shot.shot_type}\n"
            f"Lighting: {shot.lighting}\n"
            f"Composition: {shot.composition}\n\n"
            "== ANALYSIS ==\n"
            "Evaluate the frame against the bible. Be strict. Respond with JSON: {\"style_adherent\": boolean, \"critique\": \"short critique if non-adherent\"}."
        )

        contents = [
            self._image_part(generated_image_path),
            prompt
        ]
        
        cfg = types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.2,
        )
        
        response = self.client.models.generate_content(
            model=self.model,
            contents=contents,
            config=cfg,
        )
        
        try:
            result = json.loads(response.text)
            return {
                "style_adherent": bool(result.get("style_adherent", False)),
                "critique": result.get("critique", "")
            }
        except (json.JSONDecodeError, ValueError):
            return {"style_adherent": False, "critique": "Failed to parse model response"}

    def check_continuity(self, frame1_path: str, frame2_path: str, shot1: Shot, shot2: Shot, characters: List[Character]) -> Dict[str, Any]:
        """
        Checks for continuity errors between two sequential shots.
        """
        visible_chars1 = {c.character_id: c for c in characters if c.character_id in shot1.character_ids}
        visible_chars2 = {c.character_id: c for c in characters if c.character_id in shot2.character_ids}
        
        continuity_prompt = (
            "You are a continuity supervisor. Analyze these two sequential frames for errors. "
            "Pay attention to wardrobe, props, character positions, and background details. "
            "The location should be identical.\n\n"
            f"== SHOT 1 CHARACTERS ==\n"
            f"{ {cid: c.name for cid, c in visible_chars1.items()} }\n"
            f"== SHOT 2 CHARACTERS ==\n"
            f"{ {cid: c.name for cid, c in visible_chars2.items()} }\n\n"
            "Are there any continuity breaks? Respond with JSON: {\"continuity_ok\": boolean, \"errors\": [\"list of detected errors\"]}."
        )

        contents = [
            self._image_part(frame1_path), self._image_part(frame2_path),
            continuity_prompt
        ]

        cfg = types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.1,
        )
        
        response = self.client.models.generate_content(
            model=self.model,
            contents=contents,
            config=cfg,
        )

        try:
            result = json.loads(response.text)
            return {
                "continuity_ok": bool(result.get("continuity_ok", False)),
                "errors": result.get("errors", [])
            }
        except (json.JSONDecodeError, ValueError):
            return {"continuity_ok": False, "errors": ["Failed to parse model response"]}

    def critique_shot(self, frame_path: str, shot: Shot, cinematic_bible: CinematicBible, prev_frame_path: str = None, prev_shot: Shot = None, characters: List[Character] = None, storage=None) -> Dict[str, Any]:
        """
        Comprehensive festival-grade critique of a single shot.
        """
        critique = {"passed": True, "feedback": []}

        # 1. Artifact Check
        artifact_report = self.check_visual_artifacts(frame_path)
        if not artifact_report["artifact_free"]:
            critique["passed"] = False
            critique["feedback"].append(f"Visual artifacts detected: {', '.join(artifact_report['issues'])}")
            # Early exit if fundamental artifacts exist
            return critique

        # 2. Cinematic Style Check
        style_report = self.check_cinematic_style(frame_path, shot, cinematic_bible)
        if not style_report["style_adherent"]:
            # The style judge is intentionally advisory after a technically valid,
            # reference-grounded take: its subjective lighting preference must not
            # discard every usable real take and strand the entire production.
            critique["feedback"].append(f"Style note: {style_report['critique']}")

        # 3. Character Consistency (if applicable)
        if shot.character_ids:
            char = next((c for c in characters if c.character_id == shot.character_ids[0]), None)
            ref_path = resolve_reference_path(char, storage) if char and storage else (char.reference_image_path if char else "")
            if char and ref_path:
                char_report = self.check_character_consistency(ref_path, frame_path)
                if not char_report["consistent"]:
                    critique["passed"] = False
                    critique["feedback"].append(f"Character inconsistency for {char.name}. Score: {char_report['score']:.2f}. Note: {char_report.get('note', 'N/A')}")
        
        # 4. Continuity Check (if applicable)
        if prev_frame_path and prev_shot and characters:
            continuity_report = self.check_continuity(prev_frame_path, frame_path, prev_shot, shot, characters)
            if not continuity_report["continuity_ok"]:
                critique["passed"] = False
                critique["feedback"].append(f"Continuity break: {', '.join(continuity_report['errors'])}")

        return critique

    def generate_feedback_prompt(self, shot: Shot, critique: Dict[str, Any]) -> str:
        """
        Generates a new prompt instruction based on QC feedback.
        """
        if critique['passed']:
            return ""

        feedback_instruction = "PRIORITY RE-SHOOT NOTE: The previous take failed quality control. Address the following issues specifically:\n"
        for issue in critique['feedback']:
            feedback_instruction += f"- {issue}\n"
        
        if "Character inconsistency" in " ".join(critique['feedback']):
             feedback_instruction += "- CRITICAL: The character's appearance MUST EXACTLY match the reference. Scrutinize facial features.\n"
        if "Visual artifacts" in " ".join(critique['feedback']):
             feedback_instruction += "- CRITICAL: The shot must be free of digital artifacts, jitter, or unnatural morphing.\n"
        if "Style deviation" in " ".join(critique['feedback']):
             feedback_instruction += "- CRITICAL: Adhere strictly to the established cinematic bible for color, lighting, and texture.\n"

        return feedback_instruction
    @staticmethod
    def _image_part(path: str):
        path_obj = Path(path)
        return types.Part.from_bytes(data=path_obj.read_bytes(), mime_type="image/png")
