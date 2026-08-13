# Production Readiness Audit: `vidgen-agent`

## 1. SDK & Model Inspection
*   **google-genai SDK**: `2.8.0` (Verified)
*   **Veo Model**: `veo-3.1-generate-001` (Configured, not verified via API call)

## 2. Capability Audit
*   **Image/Reference Inputs (Veo)**: **MISSING**. The `VeoVideoGenerator` in `vidgen/providers/video.py` is currently configured for text-to-video only (`GenerateVideosConfig` does not include input image/reference structures).
*   **Canonical Visual References**: **VERIFIED** (partially). The `Character` and `Location` models have a `canonical_visual_assets` field, but this is currently unused by the `PromptCompiler` or `VeoVideoGenerator`.
*   **ShotGenerationPackage References**: **MISSING**. The `PromptCompiler` is not currently passing `canonical_visual_assets` into the prompt.
*   **Continuity Survival**: **UNVERIFIED**. Character and prop IDs exist in the models and are referenced in `PromptCompiler` (via `shot.character_states` and `shot.prop_states`), but there is no mechanism to ensure consistent visual appearance across different Veo generation calls.
*   **Failed Shot Regeneration**: **MISSING**. The Orchestrator allows skipping shots that have `prompt_version` set, but it does not support "smart" regeneration that would re-examine continuity with surrounding shots if a failure occurs.

## 3. Safety & Production Configuration
*   **Production Safety Guard**: **VERIFIED**.
    *   `settings.is_production` check in `main.py` and `config.py` correctly uses `FILM_MODE == "production"` AND `ALLOW_REAL_GENERATION == True`.
    *   `MockVideoGenerator` is used in simulation mode.

## 4. Risks
1.  **Character Inconsistency**: Without passing image-based character references (`canonical_visual_assets`) to Veo, characters will likely lose identity between shots.
2.  **Lack of Visual Grounding**: The current prompt only describes characters textually; it does not ground them in visual references.

## 5. Required Changes for Persistent Character Identity
1.  **Implement Visual References**: Update `PromptCompiler` to extract URIs from `canonical_visual_assets` and include them in the prompt structure (or a corresponding metadata field if the Veo SDK supports it).
2.  **SDK Update for Reference/Image Inputs**: Investigate and implement support for `image_input` or similar in `VeoVideoGenerator` if the SDK/Model allows.
3.  **Continuity-Aware Regeneration**: Enhance the Orchestrator to allow regenerating a specific shot while optionally taking into account the artifacts of the preceding and following shots.

## 6. Exact Next Implementation Steps
1.  **Enhance PromptCompiler**: Pass `canonical_visual_assets` to the compiled prompt.
2.  **Refactor VeoVideoGenerator**: Update `generate_shot` to handle image inputs if possible via the `google-genai` SDK.
3.  **Implement Continuity-Aware Regeneration**: Modify Orchestrator's regeneration logic.
