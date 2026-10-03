"""Creative director, hook, script, bibles, and storyboard — structured, duration-safe."""
from __future__ import annotations

import re
from typing import List, Optional

from vidgen.reels.constants import CONTENT_MODES, GENERIC_OPENINGS, HOOK_APPROACHES
from vidgen.reels.duration import (
    DurationExceeded,
    assert_duration,
    assign_timeline,
    estimate_speech_seconds,
    normalize_language,
    plan_shot_durations,
    restructure_timeline,
)
from vidgen.reels.schemas import (
    BrandBible,
    CharacterSpec,
    ContentMode,
    CreativeBrief,
    HookConcept,
    HookStrategy,
    PerformanceDirection,
    ProductSpec,
    ReelJob,
    ReelRequest,
    ReelScript,
    ReelShot,
    ScriptLine,
    Storyboard,
)


_PRODUCT_HINTS = (
    ("perfume", "fragrance", "scent", "attar", "perfume", "সুগন্ধি", "পারফিউম"),
    ("skincare", "serum", "cream", "moisturizer", "skin", "স্কিন", "ক্রيم", "সিরাম"),
    ("food", "restaurant", "cafe", "biryani", "tea", "খাবার", "রেস্তোরাঁ"),
    ("fashion", "clothing", "dress", "sharee", "saree", "পোশাক", "শাড়ি"),
    ("saas", "app", "software", "platform"),
    ("finance", "bank", "loan", "wallet"),
)

_MODE_RULES = [
    (ContentMode.UGC, ("ugc", "selfie", "unboxing", "review", "real people", "phone")),
    (ContentMode.PRODUCT_DEMO, ("how to", "demo", "use", "apply", "steps")),
    (ContentMode.TESTIMONIAL, ("testimonial", "review", "customer")),
    (ContentMode.FOUNDER_STYLE, ("founder", "owner", "i built", "our story")),
    (ContentMode.COMEDY, ("funny", "comedy", "joke", "হাস্য")),
    (ContentMode.STREET_STYLE, ("street", "dhaka", "market", "রাস্তা", "ঢাকা")),
    (ContentMode.DIRECT_RESPONSE_AD, ("buy now", "order", "limited", "offer", "cta hard")),
    (ContentMode.PROBLEM_SOLUTION, ("problem", "tired of", "fix", "solution")),
    (ContentMode.PRODUCT_REVEAL, ("reveal", "launch", "new", "unbox")),
    (ContentMode.BEFORE_AFTER, ("before", "after", "transform")),
    (ContentMode.EMOTIONAL, ("emotional", "memory", "mother", "gift")),
    (ContentMode.INTERVIEW, ("interview", "ask", "q&a")),
    (ContentMode.STORYTELLING, ("story", "once", "narrative")),
    (ContentMode.CINEMATIC_COMMERCIAL, ("cinematic", "film", "cinematic commercial")),
    (ContentMode.LIFESTYLE_COMMERCIAL, ("lifestyle", "premium", "everyday")),
]


def _blob(req: ReelRequest) -> str:
    return " ".join([
        req.idea, req.style, req.audience, req.goal, req.cta,
        req.product_name, req.brand_name, req.content_mode,
    ]).lower()


def _product_kind(req: ReelRequest) -> str:
    blob = _blob(req)
    for group in _PRODUCT_HINTS:
        if any(k in blob for k in group):
            return group[0]
    if req.product_name:
        return "product"
    return "idea"


def choose_content_mode(req: ReelRequest) -> str:
    if req.content_mode:
        key = req.content_mode.strip().upper().replace(" ", "_").replace("→", "_").replace("-", "_")
        for mode in CONTENT_MODES:
            if key == mode or key in mode:
                return mode
        try:
            return ContentMode[key].value
        except Exception:
            pass
    blob = _blob(req)
    for mode, keys in _MODE_RULES:
        if any(k in blob for k in keys):
            return mode.value
    kind = _product_kind(req)
    if kind in {"perfume", "skincare", "fashion"}:
        return ContentMode.LIFESTYLE_COMMERCIAL.value
    if kind in {"food"}:
        return ContentMode.STREET_STYLE.value
    if kind in {"saas", "finance"}:
        return ContentMode.DIRECT_RESPONSE_AD.value
    return ContentMode.UGC.value


def _looks_generic(text: str) -> bool:
    low = (text or "").strip().lower()
    return any(low.startswith(g) or g in low[:80] for g in GENERIC_OPENINGS)


def _infer_name(req: ReelRequest) -> str:
    if req.product_name.strip():
        return req.product_name.strip()
    # First quoted or capitalized token after "for this/the"
    m = re.search(r"(?:for this|for the|product[:\s]+)\s*([A-Za-z\u0980-\u09FF][\w\u0980-\u09FF' -]{1,40})", req.idea, re.I)
    if m:
        return m.group(1).strip(" .")
    words = [w for w in re.findall(r"[A-Za-z\u0980-\u09FF][\w\u0980-\u09FF'-]+", req.idea) if len(w) > 2]
    skip = {"create", "make", "reel", "shorts", "bengali", "english", "second", "seconds", "this", "that", "with", "from"}
    for w in words:
        if w.lower() not in skip:
            return w
    return "the product"


def build_brief(req: ReelRequest) -> CreativeBrief:
    lang = normalize_language(req.language)
    mode = choose_content_mode(req)
    kind = _product_kind(req)
    product = _infer_name(req)
    audience = req.audience.strip() or (
        "young Bangladeshi professionals" if lang.startswith("bengali") else "young urban professionals"
    )
    talking = mode in {ContentMode.UGC.value, ContentMode.FOUNDER_STYLE.value, ContentMode.INTERVIEW.value, ContentMode.TESTIMONIAL.value}
    cinematic = mode == ContentMode.CINEMATIC_COMMERCIAL.value
    style = req.style.strip() or (
        "premium but realistic, handheld-adjacent, natural light"
        if not cinematic else "controlled cinematic commercial, still realistic"
    )
    dialect = "conversational Bangladeshi Bangla (Dhaka)" if lang.startswith("bengali") else "natural conversational English"
    if lang == "bengali_english":
        dialect = "Banglish — conversational Bangla with natural English product names"
    cta = req.cta.strip() or (
        "পেজে গিয়ে দেখো" if lang == "bengali" else
        "পেজে গিয়ে দেখো / check the page" if lang == "bengali_english" else
        "Check the product page"
    )
    visual = {
        ContentMode.UGC.value: "phone-native, slightly imperfect, real room, real skin texture",
        ContentMode.STREET_STYLE.value: "Dhaka-adjacent daylight, real streets, no stock-travel look",
        ContentMode.PRODUCT_DEMO.value: "clean tabletop + real hands, product-true colors",
        ContentMode.LIFESTYLE_COMMERCIAL.value: "lived-in premium interior, naturalistic beauty, no plastic skin",
        ContentMode.DIRECT_RESPONSE_AD.value: "clear product, readable CTA, fast social pacing",
        ContentMode.CINEMATIC_COMMERCIAL.value: "composed but human, motivated camera, no fake flares",
    }.get(mode, "realistic, socially native, commercially clear")
    return CreativeBrief(
        objective=req.goal.strip() or "Make the viewer interested enough to visit the product page",
        target_audience=audience,
        product_positioning=f"{product} as a realistic everyday upgrade, not a fantasy luxury prop",
        emotional_objective="curiosity plus quiet confidence, never hype",
        creative_concept=f"{mode.replace('_', ' ').title()} for {product}: a real person, a real moment, the product used correctly",
        platform=req.platform or "instagram_reels",
        language=lang,
        dialect=dialect,
        duration_seconds=float(req.duration_seconds),
        tone="premium-but-real" if "premium" in style.lower() else "natural and trustworthy",
        visual_style=style or visual,
        narrative_structure="hook → lived moment → product truth → simple CTA",
        cta=cta,
        acting_style="restrained, private, no exaggerated AI expressions",
        camera_style="motivated, mostly locked or slow handheld, 9:16 safe-area",
        sound_direction="room tone, product foley, music ducked under voice",
        content_mode=mode,
        look_into_camera=talking,
        talking_head=talking,
        forbidden_claims=[
            "invented testimonials", "invented statistics", "medical claims",
            "awards", "celebrity endorsement", "guarantees",
        ],
        allowed_claims=list(req.allowed_claims),
        cultural_notes=(
            "Bangladesh social-media speech; no Indian-film formal Bangla unless requested."
            if lang.startswith("bengali") else "Avoid corporate advertising English."
        ),
    )


def build_hooks(brief: CreativeBrief, req: ReelRequest) -> HookStrategy:
    product = _infer_name(req)
    lang = brief.language
    kind = _product_kind(req)
    if lang.startswith("bengali"):
        lines = {
            "curiosity": (f"{product}টা একবার শুঁকলেই মাথায় থেকে যায়।", "Close on a wrist, then the bottle — no logo punch-in yet."),
            "problem": ("সারাদিন অফিস... গায়ে সেই একই ডিও।", "Tired office light, then a small personal reset."),
            "relatable_situation": ("বন্ধুরা জিজ্ঞেস করে, নতুন কিছু লাগছে তো?", "Casual mirror check, not a commercial stare."),
            "product_reveal": (f"এটা {product}। বাহিরের মতো লাগানোর দরকার নেই।", "Hands place the real bottle on a real table."),
            "direct_statement": (f"{product} জোর করে সুন্দর লাগায় না। নিজের মতো রাখে।", "Quiet close-up, no smile-to-camera."),
            "question": ("তোমার সিগনেচার স্মেল আছে?", "Someone pauses mid-leave, sniffs a sleeve."),
        }
    else:
        lines = {
            "curiosity": (f"This {kind} stays on you without announcing itself.", "Wrist, then bottle. No logo slam."),
            "problem": ("Long day. Same tired scent on your shirt.", "Office-tired, then a private reset."),
            "relatable_situation": ("Someone asked if I got a new perfume.", "Mirror, not a hard sell."),
            "product_reveal": (f"This is {product}. Used the way you actually use it.", "Real hands, real packaging."),
            "direct_statement": (f"{product} doesn't perform luxury. It sits close.", "Still, honest framing."),
            "question": ("Do you have a scent people remember?", "A pause before leaving the room."),
        }
    concepts: List[HookConcept] = []
    preferred = {
        ContentMode.UGC.value: "relatable_situation",
        ContentMode.PRODUCT_REVEAL.value: "product_reveal",
        ContentMode.PROBLEM_SOLUTION.value: "problem",
        ContentMode.DIRECT_RESPONSE_AD.value: "direct_statement",
        ContentMode.EMOTIONAL.value: "emotional_moment",
    }.get(brief.content_mode, "curiosity")
    for i, approach in enumerate(HOOK_APPROACHES):
        if approach not in lines and approach not in {"surprise", "visual_interruption", "emotional_moment", "transformation", "pattern_interrupt"}:
            continue
        if approach in lines:
            line, visual = lines[approach]
        else:
            line = lines["curiosity"][0]
            visual = "Pattern interrupt: product enters frame from a real pocket or bag."
        score = 0.72 + (0.2 if approach == preferred else 0.0) - i * 0.01
        concepts.append(HookConcept(
            approach=approach, concept=f"{approach} hook for {product}",
            visual=visual, line=line, score=round(score, 3),
            rationale=f"Fits {brief.content_mode} and {brief.target_audience}",
        ))
    concepts.sort(key=lambda c: c.score, reverse=True)
    chosen = concepts[0]
    return HookStrategy(concepts=concepts[:8], chosen=chosen)


def _script_lines(brief: CreativeBrief, req: ReelRequest, hook: HookStrategy) -> List[str]:
    product = _infer_name(req)
    lang = brief.language
    hook_line = (hook.chosen.line if hook.chosen else "").strip()
    if lang == "bengali":
        body = [
            hook_line,
            "জোরে ঘোষণা করার মতো কিছু না। কাছে এলেই বোঝা যায়।",
            f"{product} এমন, যেটা নিজের মতো লাগানো যায়।",
            brief.cta,
        ]
    elif lang == "bengali_english":
        body = [
            hook_line,
            f"{product}টা loud না। কাছে এলেই catch হয়।",
            "Office থেকে বেরোনোর আগে একটু — that's it.",
            brief.cta,
        ]
    else:
        body = [
            hook_line,
            "It doesn't announce itself. You notice it when someone leans in.",
            f"{product} is for the version of you that already left the house.",
            brief.cta,
        ]
    # Strip generic AI openings
    cleaned = []
    for line in body:
        if _looks_generic(line):
            continue
        if line and line not in cleaned:
            cleaned.append(line)
    if not cleaned:
        cleaned = [f"{product}.", brief.cta]
    return cleaned


def build_script(brief: CreativeBrief, req: ReelRequest, hook: HookStrategy) -> ReelScript:
    raw = _script_lines(brief, req, hook)
    target = min(float(brief.duration_seconds), 30.0)
    # Speech should occupy ~55-70% of runtime; leave room for picture
    speech_budget = max(4.0, min(target * 0.68, target - 2.0))
    kept_text: List[str] = []
    cursor = 0.15
    for text in raw:
        est = estimate_speech_seconds(text, brief.language)
        if cursor + est > speech_budget and kept_text:
            break
        kept_text.append(text)
        cursor += est + 0.28
    while estimate_speech_seconds(" ".join(kept_text), brief.language) > speech_budget and len(kept_text) > 1:
        kept_text.pop()
    return _finalize_script(brief, kept_text, target, speech_budget)


def _finalize_script(brief: CreativeBrief, texts: List[str], target: float, speech_budget: float) -> ReelScript:
    lines: List[ScriptLine] = []
    cursor = 0.15
    for i, text in enumerate(texts):
        est = estimate_speech_seconds(text, brief.language)
        lines.append(ScriptLine(
            speaker="talent" if brief.talking_head and i == 0 else "narrator",
            text=text,
            start_seconds=round(cursor, 3),
            estimated_seconds=est,
            emotion="natural",
            on_camera=brief.talking_head and i == 0,
        ))
        cursor += est + 0.25
    full = " ".join(texts)
    speech = estimate_speech_seconds(full, brief.language)
    if speech > speech_budget and texts:
        texts = texts[: max(1, len(texts) - 1)]
        return _finalize_script(brief, texts, target, speech_budget)
    if _looks_generic(full):
        raise DurationExceeded("script matched generic AI opening and was rejected")
    assert_duration(min(speech, target), "script speech estimate")
    return ReelScript(
        language=brief.language,
        hook_line=texts[0] if texts else "",
        body_lines=lines,
        cta_line=texts[-1] if texts else brief.cta,
        full_text=full,
        estimated_speech_seconds=speech,
        target_duration=target,
    )


def build_character(brief: CreativeBrief, req: ReelRequest) -> CharacterSpec:
    audience = (brief.target_audience or "").lower()
    young = any(k in audience for k in ("young", "তরুণ", "university", "25", "30"))
    women = any(k in audience for k in ("women", "woman", "নারী", "মেয়ে"))
    men = any(k in audience for k in ("men", "man", "পুরুষ", "ছেলে"))
    bd = any(k in audience for k in ("bangladesh", "bangladeshi", "dhaka", "বাংলা"))
    if women and not men:
        gender = "woman"
        name = "Nusrat" if bd else "Maya"
        wardrobe = "simple cotton shirt, small gold earring, no costume styling"
        hair = "dark, naturally textured, slightly lived-in; not salon-perfect"
        voice = "warm, unhurried Bangladeshi conversational" if brief.language.startswith("bengali") else "warm, unhurried"
    elif men and not women:
        gender = "man"
        name = "Arif" if bd else "Omar"
        wardrobe = "light oxford shirt, sleeves once-rolled, everyday watch"
        hair = "short, slightly imperfect; not advertising-slick"
        voice = "low, casual Dhaka register" if brief.language.startswith("bengali") else "low, casual"
    else:
        gender = "young professional"
        name = "Riya" if bd else "Alex"
        wardrobe = "real weekday clothes, no logo wall"
        hair = "natural; visible texture"
        voice = "conversational"
    age = "24-32" if young else "28-38"
    return CharacterSpec(
        name=name,
        age=age,
        appearance=f"South Asian {gender}, real face, visible skin texture, not beauty-filtered",
        hair=hair,
        face="asymmetrical, lived-in, no porcelain smoothness",
        skin="natural tone with real texture; no plastic sheen",
        wardrobe=wardrobe,
        accessories="one small everyday accessory, consistent across shots",
        body="ordinary professional posture, not model-rigid",
        personality="self-possessed, a little private, not performing for the ad",
        mannerisms="touches the product the way a real owner would; looks away to think",
        emotional_baseline="quiet ease",
        speaking_style=voice,
    )


def build_product(brief: CreativeBrief, req: ReelRequest) -> ProductSpec:
    name = _infer_name(req)
    kind = _product_kind(req)
    user_images = [a.uri or a.local_path for a in req.assets if a.kind in {"product_image", "logo"} and (a.uri or a.local_path)]
    authoritative = bool(user_images)
    shapes = {
        "perfume": "vertical glass bottle, cap consistent with the reference, no invented logo type",
        "skincare": "compact pump or jar, label exactly as supplied",
        "food": "real plated food or packaged item as supplied",
        "fashion": "garment as worn, fabric weight visible",
    }
    return ProductSpec(
        name=name,
        shape=shapes.get(kind, "use supplied reference; do not redesign"),
        proportions="match reference exactly" if authoritative else "believable commercial proportions",
        packaging="do not invent packaging, labels, or typography" if authoritative else "simple unbranded-safe packaging if unknown",
        colors="colors locked to reference" if authoritative else "muted real-world materials",
        materials="glass/metal/paper as in reference" if authoritative else "physical, scuffed-real materials",
        labels="reproduce only visible supplied text; never hallucinate copy",
        logo="do not invent a logo",
        typography="only text present on supplied assets",
        distinctive_details="keep cap, bottle shoulder, and label placement identical across shots",
        correct_usage={
            "perfume": "spray on wrist or neck, then a small beat — no mist-cloud glamour shot unless briefed",
            "skincare": "dot and press, not theatrical smearing",
        }.get(kind, "handle like a real owner"),
        orientation="label-readable when the product is the subject",
        reference_uris=user_images,
        user_image_authoritative=authoritative,
    )


def build_brand(brief: CreativeBrief, req: ReelRequest) -> BrandBible:
    return BrandBible(
        brand_name=req.brand_name or _infer_name(req),
        brand_voice="human, specific, never corporate",
        brand_tone=brief.tone,
        colors=[],
        typography="system-safe mobile captions, not kinetic type",
        logo_uri=next((a.uri for a in req.assets if a.kind == "logo" and a.uri), ""),
        product_rules="Never redesign the product. Never invent claims, awards, or reviews.",
        language=brief.language,
        cta_style="one short CTA, spoken once, captioned once",
        visual_style=brief.visual_style,
        forbidden_claims=list(brief.forbidden_claims),
    )


def _shot_purposes(n: int, mode: str) -> List[str]:
    if n == 1:
        return ["hook_and_product"]
    if n == 2:
        return ["hook", "product_and_cta"]
    if n == 3:
        return ["hook", "lived_moment", "product_cta"]
    return ["hook", "lived_moment", "product_truth", "cta"][:n]


def build_storyboard(job: ReelJob) -> Storyboard:
    assert job.brief and job.script and job.product_bible
    brief = job.brief
    script = job.script
    product = job.product_bible
    char = job.character_bible[0] if job.character_bible else build_character(brief, job.request)
    target = min(float(brief.duration_seconds), 30.0)
    durations = plan_shot_durations(target, preferred_shots=3 if target >= 15 else 2)
    durations = [float(d) for d in restructure_timeline(durations, cap=target)]
    spans = assign_timeline(durations)
    purposes = _shot_purposes(len(spans), brief.content_mode)
    lines = script.body_lines
    location = {
        ContentMode.STREET_STYLE.value: "Dhaka-adjacent street / tea stall light, late afternoon",
        ContentMode.UGC.value: "real apartment, phone-height, window light",
        ContentMode.PRODUCT_DEMO.value: "kitchen or vanity table, practical lamp",
    }.get(brief.content_mode, "lived-in apartment, late-day window light, Dhaka-neutral interior")
    shots: List[ReelShot] = []
    for i, ((dur, start, end), purpose) in enumerate(zip(spans, purposes)):
        line = lines[min(i, len(lines) - 1)].text if lines else ""
        talking = brief.talking_head and purpose in {"hook", "hook_and_product"}
        gaze = "into lens, casual" if talking else "off-lens, toward product or window"
        shots.append(ReelShot(
            shot_id=f"R{i+1:02d}",
            duration=dur,
            start_time=round(start, 3),
            end_time=round(end, 3),
            purpose=purpose,
            characters=[char.character_id],
            character_state=char.emotional_baseline,
            location=location,
            environment=f"{location}. Real clutter at edges. No studio sweep.",
            wardrobe=char.wardrobe,
            product_state=product.correct_usage if "product" in purpose else "present but not forced",
            action=_action_for(purpose, product, char),
            dialogue=line if (talking or purpose != "lived_moment") else "",
            camera="phone-height medium" if talking else ("tight insert" if "product" in purpose else "medium close-up"),
            framing="9:16, eyes/product in central safe area, headroom for captions at bottom",
            lens_look="28-35mm equivalent, natural contrast, no anamorphic flare",
            camera_motion="locked or 10cm motivated drift — never random orbit",
            lighting="available window + practical lamp, no beauty dish",
            visual_style=brief.visual_style,
            sound=_sound_for(purpose, product.name),
            music="low bed, ducked",
            transition="hard_cut" if i else "none",
            continuity_requirements=[
                "same wardrobe", "same hair", "same product bottle",
                "same time of day", "same location geography",
            ],
            negative_constraints=[
                "plastic skin", "malformed hands", "invented logo",
                "changed clothes", "floating product", "fake lens flare",
                "looking at camera" if not talking else "exaggerated presenter smile",
            ],
            performance=PerformanceDirection(
                objective="own the product privately, not sell it",
                emotional_state=char.emotional_baseline,
                subtext="this is already part of their day",
                body_language="unforced shoulders, real weight in the hands",
                gaze=gaze,
                gesture="small, motivated product handling",
                facial_reaction="micro, not a commercial grin",
                timing="let a breath exist before the line",
                pause="0.3s after the hook line",
                delivery_intensity="restrained",
            ),
            talking_head=talking,
            native_audio=talking,
            use_first_last_frame=(i > 0),
        ))
    total = sum(s.duration for s in shots)
    assert_duration(total, "storyboard")
    if total > target + 1e-9 and total <= 30.0:
        # Prefer under target when possible; still legal if <= 30
        pass
    return Storyboard(shots=shots, total_duration=round(total, 3), target_duration=target)


def _action_for(purpose: str, product: ProductSpec, char: CharacterSpec) -> str:
    if purpose in {"hook", "hook_and_product"}:
        return f"{char.name} enters frame mid-action, notices {product.name}, does not pose."
    if purpose == "lived_moment":
        return f"{char.name} uses {product.name} the ordinary way: {product.correct_usage}"
    if purpose in {"product_truth", "product_and_cta", "product_cta"}:
        return f"Hands set {product.name} down; label orientation matches the bible. Small hold."
    if purpose == "cta":
        return f"{char.name} walks out of frame; {product.name} remains, still, correctly oriented."
    return f"{char.name} handles {product.name} naturally."


def _sound_for(purpose: str, product: str) -> str:
    if "product" in purpose:
        return f"soft glass/plastic contact of {product} on wood; room tone"
    if purpose == "hook":
        return "room tone, distant city, no whoosh"
    return "room tone, faint cloth rustle"


def plan_production(job: ReelJob) -> ReelJob:
    """Run the full creative plan. No expensive calls."""
    req = job.request
    job.brief = build_brief(req)
    job.hook = build_hooks(job.brief, req)
    job.script = build_script(job.brief, req, job.hook)
    job.character_bible = [build_character(job.brief, req)]
    job.product_bible = build_product(job.brief, req)
    job.brand_bible = build_brand(job.brief, req)
    job.storyboard = build_storyboard(job)
    return job
