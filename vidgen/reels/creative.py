"""Creative director, hook, script, bibles, and storyboard — structured, duration-safe."""
from __future__ import annotations

import re
from typing import List, Optional

from vidgen.reels.comedy import (
    comedy_action,
    comedy_location,
    comedy_script_lines,
    detect_situation,
)
from vidgen.reels.constants import CONTENT_MODES, GENERIC_OPENINGS, HOOK_APPROACHES
from vidgen.reels.duration import (
    DurationExceeded,
    assert_duration,
    assign_timeline,
    estimate_speech_seconds,
    plan_shot_durations,
    restructure_timeline,
    snap_veo_duration,
)
from vidgen.reels.identity import build_cast, build_fictional_or_named_product
from vidgen.reels.intent import (
    classify_intent,
    content_mode_for,
    needs_cta,
    needs_product,
    strategy_beats,
    talking_head_for,
)
from vidgen.reels.language import infer_language, is_garbled_caption
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
    ("saas", "app", "software", "platform", "agent", "vidgen", "factory"),
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
    (ContentMode.PRODUCT_REVEAL, ("reveal", "launch", "unbox", "unboxing")),
    (ContentMode.BEFORE_AFTER, ("before after", "before-and-after", "transform")),
    (ContentMode.EMOTIONAL, ("emotional", "memory", "mother", "gift")),
    (ContentMode.INTERVIEW, ("interview", "q&a", "q and a")),
    (ContentMode.STORYTELLING, ("storytelling", "narrative short")),
    (ContentMode.CINEMATIC_COMMERCIAL, ("cinematic commercial", "film look")),
    (ContentMode.LIFESTYLE_COMMERCIAL, ("lifestyle commercial", "catalogue", "lookbook")),
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
    return content_mode_for(classify_intent(req))


def _looks_generic(text: str) -> bool:
    low = (text or "").strip().lower()
    return any(low.startswith(g) or g in low[:80] for g in GENERIC_OPENINGS)


_NAME_SKIP = {
    "create", "make", "reel", "reels", "shorts", "short", "video", "ad", "ads",
    "advertisement", "commercial", "social", "media", "instagram", "tiktok",
    "bengali", "bangla", "english", "second", "seconds", "this", "that", "with",
    "from", "for", "the", "and", "realistic", "premium", "natural", "authentic",
    "everyday", "subtle", "believable", "strong", "first", "young", "bangladeshi",
    "actor", "environment", "look", "acting", "voice", "hook", "style", "audience",
    "professionals", "people", "about", "into", "your", "our", "product",
}


def _infer_name(req: ReelRequest) -> str:
    if req.product_name.strip():
        return req.product_name.strip()
    quoted = re.search(r"[\"“]([^\"”]{2,40})[\"”]", req.idea)
    if quoted:
        return quoted.group(1).strip()
    named = re.search(
        r"(?:called|named|brand[:\s]+|product[:\s]+)\s*([A-Z][A-Za-z\u0980-\u09FF' -]{1,40})",
        req.idea,
    )
    if named:
        return named.group(1).strip(" .")
    kind = _product_kind(req)
    kind_names = {
        "perfume": "the perfume",
        "skincare": "the serum",
        "food": "the food",
        "fashion": "the piece",
        "saas": "the app",
        "finance": "the wallet",
    }
    if kind in kind_names:
        return kind_names[kind]
    words = [w for w in re.findall(r"[A-Za-z\u0980-\u09FF][\w\u0980-\u09FF'-]+", req.idea) if len(w) > 2]
    for w in words:
        if w.lower() not in _NAME_SKIP:
            return w
    return "the product"


def build_brief(req: ReelRequest) -> CreativeBrief:
    lang = infer_language(req.language, req.idea)
    ctype = classify_intent(req)
    mode = choose_content_mode(req)
    kind = _product_kind(req)
    want_product = needs_product(ctype, req)
    want_cta = needs_cta(ctype)
    product = _infer_name(req) if want_product else ""
    audience = req.audience.strip() or (
        "young Bangladeshi viewers" if lang.startswith("bengali") else "young urban viewers"
    )
    talking = talking_head_for(ctype)
    cinematic = ctype == "CINEMATIC"
    style = req.style.strip() or (
        "phone-native, realistic, motivated camera"
        if not cinematic else "controlled cinematic short, still human"
    )
    dialect = "conversational Bangladeshi Bangla (Dhaka)" if lang.startswith("bengali") else "natural conversational English"
    if lang == "bengali_english":
        dialect = "Banglish — conversational Bangla with natural English nouns"
    cta = ""
    if want_cta:
        cta = req.cta.strip() or (
            "পেজে গিয়ে দেখো" if lang == "bengali" else
            "পেজে গিয়ে দেখো / check the page" if lang == "bengali_english" else
            "Check the product page"
        )
    beats = strategy_beats(ctype, float(req.duration_seconds))
    why = {
        "COMEDY": ("a face mid-mistake in the first 0.7s", "escalation they recognize", "the punchline"),
        "SKIT": ("a face mid-mistake in the first 0.7s", "the situation getting worse", "the button"),
        "EDUCATIONAL": ("a surprising claim or object", "one clean explanation", "a line they can repeat"),
        "EXPLAINER": ("a surprising claim or object", "one clean explanation", "a line they can repeat"),
        "FACT": ("a surprising claim", "the reason", "the takeaway"),
        "STORY": ("a person already in trouble", "the turn", "the payoff"),
        "MEME": ("a recognizable setup", "the escalation", "the punch"),
    }.get(ctype, (
        "Face or a specific action in the first 0.7s — never an empty room",
        "One lived moment, then proof",
        "One short close, not a banner" if not want_cta else "One short CTA, spoken like a friend",
    ))
    objective = req.goal.strip() or {
        "COMEDY": "Make someone send this to a friend",
        "EDUCATIONAL": "Leave the viewer with one thing they did not know",
        "STORY": "Make the last second land",
        "ADVERTISEMENT": "Make the viewer interested enough to look up the product",
        "UGC": "Feel like a person, not a catalogue",
    }.get(ctype, "Earn the next second of attention")
    return CreativeBrief(
        objective=objective,
        target_audience=audience,
        product_positioning=(f"{product} as a real object, used correctly" if want_product else ""),
        emotional_objective="curiosity plus recognition, never hype",
        creative_concept=f"{ctype}: {req.idea.strip()[:160]}",
        platform=req.platform or "instagram_reels",
        language=lang,
        dialect=dialect,
        duration_seconds=float(req.duration_seconds),
        tone="comic and dry" if ctype in {"COMEDY", "SKIT", "MEME"} else (
            "clear and specific" if ctype in {"EDUCATIONAL", "EXPLAINER", "FACT"} else "natural"
        ),
        visual_style=style,
        narrative_structure=" → ".join(beats),
        cta=cta,
        acting_style="committed, unperformed, timing-aware",
        camera_style="motivated, mostly locked or slow handheld, 9:16 safe-area",
        sound_direction="dialogue first; music only if the category needs it",
        content_mode=mode,
        look_into_camera=talking,
        talking_head=talking,
        creative_type=ctype,
        strategy_beats=beats,
        needs_product=want_product,
        needs_cta=want_cta,
        fictional_product=want_product and not bool(req.product_name.strip()),
        forbidden_claims=[
            "invented testimonials", "invented statistics", "medical claims",
            "awards", "celebrity endorsement", "guarantees",
        ],
        allowed_claims=list(req.allowed_claims),
        cultural_notes=(
            "Bangladesh social-media speech; no Indian-film formal Bangla unless requested."
            if lang.startswith("bengali") else "Avoid corporate advertising English."
        ),
        why_watch=why[0],
        why_stay=why[1],
        why_act=why[2],
    )


def _hook_bank(lang: str, product: str, kind: str) -> dict:
    if lang.startswith("bengali"):
        banks = {
            "perfume": {
                "curiosity": (f"{product}টা একবার শুঁকলেই মাথায় থেকে যায়।", "Close on a wrist, then the bottle — no logo punch-in yet."),
                "problem": ("সারাদিন অফিস... গায়ে সেই একই ডিও।", "Tired office light, then a small personal reset."),
                "relatable_situation": ("বন্ধুরা জিজ্ঞেস করে, নতুন কিছু লাগছে তো?", "Casual mirror check, not a commercial stare."),
                "product_reveal": (f"এটা {product}। বাহিরের মতো লাগানোর দরকার নেই।", "Hands place the real bottle on a real table."),
                "direct_statement": (f"{product} জোর করে সুন্দর লাগায় না। নিজের মতো রাখে।", "Quiet close-up, no smile-to-camera."),
                "question": ("তোমার সিগনেচার স্মেল আছে?", "Someone pauses mid-leave, sniffs a sleeve."),
            },
            "skincare": {
                "curiosity": ("আয়নায় একবার দেখলেই বোঝা যায়, ত্বকটা শান্ত।", "Bathroom mirror, no beauty ring."),
                "problem": ("সারাদিন এসি... মুখ শুকিয়ে কাগজ।", "Office-tired skin, then a small reset."),
                "relatable_situation": ("বন্ধুরা বলে, স্কিনকারে কিছু করছ নাকি?", "Phone-height, real sink, real light."),
                "product_reveal": (f"এটা {product}। জোর করে গ্লো না।", "Pump, one dot, press."),
                "direct_statement": (f"{product} নাটক করে না। কাজ করে।", "Hands only, product true."),
                "question": ("ত্বকটা কি সারাদিন রাগ করে থাকে?", "A pause before leaving the house."),
            },
            "food": {
                "curiosity": (f"{product}— গন্ধটা রাস্তা থেকেই ধরা যায়।", "Steam, real plate, no food-porn glaze."),
                "problem": ("বাইরের খাবারে একই তেল, একই ক্লান্তি।", "A real table, late afternoon."),
                "relatable_situation": ("এক কাপ, তারপর আর কথা নেই।", "Hands around a warm cup."),
                "product_reveal": (f"এটা {product}। ঘরে যেমন, এখানেও।", "Real pour or plated bite."),
                "direct_statement": (f"{product} চিৎকার করে না। তুলে খাওয়া যায়।", "Close on food, then a face."),
                "question": ("আজকে কি সত্যি খেতে ইচ্ছে করছে?", "A look down at the plate."),
            },
            "saas": {
                "curiosity": (f"এই রিলটা {product}-এ এক লাইনে বানানো।", "Face already talking, phone in hand."),
                "problem": ("একটা রিলের জন্য দুই সপ্তাহ এজেন্সি।", "Tired at a laptop, then a look up."),
                "relatable_situation": ("আইডিয়া ছিল। এডিটর ছিল না।", "Talking to one friend, laptop open."),
                "product_reveal": (f"এটা {product}। আইডিয়া লিখো, রিল বেরোয়।", "Laptop or phone, no fake UI."),
                "direct_statement": (f"{product} অভিনয় করে না। আউটপুট দেয়।", "Still, honest framing."),
                "question": ("তোমার এডিটর কাল ফ্রি?", "A small pause."),
            },
        }
        default = {
            "curiosity": (f"{product}টা একবার দেখলেই মাথায় থেকে যায়।", "Face, then the thing in hand."),
            "problem": ("সারাদিন একই রুটিন। একটু বদল দরকার।", "Real room, late light."),
            "relatable_situation": ("কেউ জিজ্ঞেস করল, নতুন কিছু নাকি?", "Talking to one friend, not an ad."),
            "product_reveal": (f"এটা {product}। যেমন ব্যবহার করি, তেমন।", "Real hands."),
            "direct_statement": (f"{product} অভিনয় করে না।", "Still, honest framing."),
            "question": ("এটা কি তোমার দরকার?", "A small pause."),
        }
        return banks.get(kind, default)
    banks = {
        "perfume": {
            "curiosity": (f"{product} stays on you without announcing itself.", "Wrist, then bottle. No logo slam."),
            "problem": ("Long day. Same tired scent on your shirt.", "Office-tired, then a private reset."),
            "relatable_situation": ("Someone asked if I changed my scent.", "Mirror, not a hard sell."),
            "product_reveal": (f"This is {product}. Used the way you actually use it.", "Real hands, real packaging."),
            "direct_statement": (f"{product} doesn't perform luxury. It sits close.", "Still, honest framing."),
            "question": ("Do you have a scent people remember?", "A pause before leaving the room."),
        },
        "skincare": {
            "curiosity": ("My face stopped looking tired before I did.", "Bathroom mirror, no ring light."),
            "problem": ("AC all day. Skin like paper.", "Office-tired, then a press of serum."),
            "relatable_situation": ("Someone asked what I changed. It was this.", "Sink, real light."),
            "product_reveal": (f"This is {product}. Dot, press, leave.", "Hands, pump, no smear theatre."),
            "direct_statement": (f"{product} does not perform glow. It sits in.", "Still, honest framing."),
            "question": ("Does your skin stay angry until night?", "A pause at the door."),
        },
        "saas": {
            "curiosity": (f"This Reel was one sentence in {product}.", "Face already talking, laptop open."),
            "problem": ("Agencies take two weeks for one Reel.", "Tired at a desk, then a look up."),
            "relatable_situation": ("I had the idea. I did not have an editor.", "Talking to one friend."),
            "product_reveal": (f"This is {product}. Type the idea. Get the Reel.", "Laptop or phone, no fake UI."),
            "direct_statement": (f"{product} does not perform. It ships the video.", "Still, honest framing."),
            "question": ("Is your editor free tomorrow?", "A small pause."),
        },
        "food": {
            "curiosity": (f"You smell {product} before you see it.", "Steam, real plate."),
            "problem": ("Same oily takeout. Same afternoon crash.", "A real table."),
            "relatable_situation": ("I went quiet after the first sip.", "Hands on a cup."),
            "product_reveal": (f"This is {product}. The way it actually comes.", "Pour or plated bite."),
            "direct_statement": (f"{product} does not shout. You just eat.", "Food, then a face."),
            "question": ("When did you last want a second plate?", "A look down."),
        },
    }
    default = {
        "curiosity": (f"{product} makes more sense in a real room than in an ad.", "Face, then the object."),
        "problem": ("Same routine. Need one honest change.", "Real room, late light."),
        "relatable_situation": ("Someone asked if this was new.", "Talking to one friend."),
        "product_reveal": (f"This is {product}. Used like a person uses it.", "Real hands."),
        "direct_statement": (f"{product} does not perform. It works.", "Still, honest framing."),
        "question": ("Do you actually need this, or just another ad?", "A small pause."),
    }
    return banks.get(kind, default)


def _category_hooks(brief: CreativeBrief, req: ReelRequest) -> dict:
    lang = brief.language
    ctype = brief.creative_type
    idea = (req.idea or "").strip()
    if ctype in {"COMEDY", "SKIT", "MEME"}:
        if lang.startswith("bengali"):
            return {
                "curiosity": ("স্যার, আমি একদম ready।", "Two faces already at a cheap desk."),
                "relatable_situation": ("ইন্টারভিউতে ঢুকলাম। মনে মনে full marks।", "Locked two-shot across a cheap office desk."),
                "direct_statement": ("প্রশ্ন শুনেও উত্তর দিয়ে ফেলি।", "Mouth already moving, interviewer staring."),
                "question": ("এই প্রশ্নটা কি সিরিয়াস?", "Blink, then double down."),
            }
        return {
            "curiosity": ("I am extremely prepared.", "Two faces already at a cheap desk."),
            "relatable_situation": ("Walked in like I had the job.", "Locked two-shot, cheap office desk."),
            "direct_statement": ("I answer before I hear the question.", "Mouth already moving."),
            "question": ("Was that a real question?", "Blink, then double down."),
        }
    if ctype in {"EDUCATIONAL", "EXPLAINER", "FACT"}:
        if lang.startswith("bengali"):
            return {
                "curiosity": ("কালো গহ্বর আলোকেও খেয়ে ফেলে।", "A dark circle, then a face saying it."),
                "question": ("আলো কেন বেরোতে পারে না?", "One object, one question."),
                "direct_statement": ("Escape velocity আলোর চেয়ে বেশি।", "Hands drawing a simple orbit."),
            }
        return {
            "curiosity": ("A black hole can eat light.", "Object, then a face."),
            "question": ("Why can't light leave?", "One object, one question."),
            "direct_statement": ("Escape velocity is faster than light.", "Hands, simple orbit."),
        }
    if ctype == "STORY":
        if lang.startswith("bengali"):
            return {
                "curiosity": ("সেই দিনটা আমি ভুলতে পারিনি।", "Face already mid-memory."),
                "relatable_situation": ("বাসায় ফিরে দরজাটা ধরে দাঁড়ালাম।", "Hand on a real door."),
            }
        return {
            "curiosity": ("I still remember the exact second.", "Face already mid-memory."),
            "relatable_situation": ("I stopped with my hand on the door.", "A real door."),
        }
    if not brief.needs_product:
        topic = idea[:48] or "this"
        if lang.startswith("bengali"):
            return {
                "curiosity": ("এটা একবার দেখলেই মাথায় থেকে যায়।", "Face or action already happening."),
                "relatable_situation": ("এমন হয়েছে তোমারও।", "A real room, late light."),
                "direct_statement": ("কথাটা ছোট, কিন্তু সত্যি।", "Still, honest framing."),
            }
        return {
            "curiosity": ("Watch this for one second.", "Face or action already happening."),
            "relatable_situation": ("You have been in this room.", "A real room."),
            "direct_statement": ("Short, and true.", "Still, honest framing."),
        }
    return _hook_bank(lang, _infer_name(req), _product_kind(req))


def build_hooks(brief: CreativeBrief, req: ReelRequest) -> HookStrategy:
    product = _infer_name(req) if brief.needs_product else (brief.creative_type or "reel")
    lang = brief.language
    lines = _category_hooks(brief, req)
    concepts: List[HookConcept] = []
    preferred = {
        "COMEDY": "relatable_situation",
        "SKIT": "relatable_situation",
        "MEME": "relatable_situation",
        "EDUCATIONAL": "curiosity",
        "EXPLAINER": "question",
        "FACT": "curiosity",
        "STORY": "curiosity",
        "UGC": "relatable_situation",
        "ADVERTISEMENT": "direct_statement",
    }.get(brief.creative_type, "curiosity")
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
    product = _infer_name(req) if brief.needs_product else ""
    lang = brief.language
    hook_line = (hook.chosen.line if hook.chosen else "").strip()
    kind = _product_kind(req)
    ctype = brief.creative_type
    if ctype in {"COMEDY", "SKIT", "MEME"}:
        return comedy_script_lines(req.idea, lang)
    if ctype in {"EDUCATIONAL", "EXPLAINER", "FACT"}:
        if lang.startswith("bengali"):
            return [
                hook_line or "কালো গহ্বর আলোকেও খেয়ে ফেলে।",
                "এত ভারী যে বেরোতে গেলে আলোর চেয়েও দ্রুত যেতে হয়।",
                "তাই কিছু বেরোয় না — আলোও না।",
            ]
        return [
            hook_line or "A black hole can eat light.",
            "Gravity is so strong escape speed is faster than light.",
            "So nothing leaves. Not even light.",
        ]
    if ctype == "STORY":
        if lang.startswith("bengali"):
            return [
                hook_line or "সেই দিনটা আমি ভুলতে পারিনি।",
                "দরজার বাইরে দাঁড়িয়ে একবার শ্বাস নিলাম।",
                "তারপর ঢুকলাম।",
            ]
        return [
            hook_line or "I still remember the exact second.",
            "I took one breath outside the door.",
            "Then I went in.",
        ]
    if not brief.needs_product:
        extra = "এমন হয়েছে তোমারও।" if lang.startswith("bengali") else "You have been here."
        close = "এইটুকুই।" if lang.startswith("bengali") else "That's the whole thing."
        return [hook_line or extra, extra, close]
    if lang == "bengali":
        mid = {
            "perfume": "জোরে ঘোষণা করার মতো কিছু না। কাছে এলেই বোঝা যায়।",
            "skincare": "জোর করে গ্লো না। একটু হলেই বোঝা যায়।",
            "food": "চিৎকার করে না। তুলে খেলেই বোঝা যায়।",
            "saas": "আইডিয়া লিখো। রিলটা বেরিয়ে আসে। এডিটরের অপেক্ষা না।",
        }.get(kind, "জোরে বলার মতো কিছু না। কাছে এলেই বোঝা যায়।")
        body = [hook_line, mid, f"{product} এমন, যেটা নিজের মতো ব্যবহার করা যায়।"]
        if kind == "saas":
            body = [
                hook_line or f"এই রিলটা {product}-এ এক লাইনে বানানো।",
                "এজেন্সি দুই সপ্তাহ নেয়। আমি একটা বাক্য লিখেছি।",
                brief.cta or "নিজের আইডিয়ায় চালাও।",
            ]
        if brief.needs_cta and brief.cta:
            body.append(brief.cta)
    elif lang == "bengali_english":
        mid = {
            "perfume": f"{product}টা loud না। কাছে এলেই catch হয়।",
            "skincare": f"{product}টা dramatic না। মুখে দিলেই বোঝা যায়।",
            "food": f"{product}টা extra না। এক বাইটেই ধরা যায়।",
            "saas": f"{product}এ idea লিখো। Reel বেরোয়। Editor wait না।",
        }.get(kind, f"{product}টা loud না। কাছে এলেই বোঝা যায়।")
        body = [hook_line, mid, "Office থেকে বেরোনোর আগে একটু — that's it."]
        if kind == "saas":
            body = [
                hook_line or f"This Reel was one line in {product}.",
                "Agency দুই সপ্তাহ নেয়। আমি একটা sentence লিখেছি।",
                brief.cta or "Run it on your own idea.",
            ]
        if brief.needs_cta and brief.cta:
            body.append(brief.cta)
    else:
        mid = {
            "perfume": "It doesn't announce itself. You notice it when someone leans in.",
            "skincare": "It doesn't perform glow. You notice it in the afternoon light.",
            "food": "It doesn't shout. You just want the next bite.",
            "saas": "Type the idea. The Reel comes out. No editor queue.",
        }.get(kind, "It doesn't perform. You notice it when you actually use it.")
        body = [hook_line, mid, f"{product} is for the version of you that already left the house."]
        if kind == "saas":
            body = [
                hook_line or f"This Reel was one sentence in {product}.",
                "Agencies take two weeks. I typed one line.",
                brief.cta or f"Run {product} on your own idea.",
            ]
        if brief.needs_cta and brief.cta:
            body.append(brief.cta)
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
    if brief.creative_type in {"COMEDY", "SKIT"} or _product_kind(req) == "saas":
        # Punchline / CTA is the last line. Never trim it to fit a budget.
        joke = estimate_speech_seconds(" ".join(raw), brief.language)
        return _finalize_script(brief, raw, target, max(speech_budget, joke + 0.4))
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
            speaker=_speaker_for(brief, text, i),
            text=text,
            start_seconds=round(cursor, 3),
            estimated_seconds=est,
            emotion="natural",
            on_camera=bool(brief.talking_head),
        ))
        cursor += est + 0.25
    full = " ".join(texts)
    speech = estimate_speech_seconds(full, brief.language)
    if speech > speech_budget and texts:
        texts = texts[: max(1, len(texts) - 1)]
        return _finalize_script(brief, texts, target, speech_budget)
    if _looks_generic(full) or is_garbled_caption(full, brief.language):
        raise DurationExceeded("script matched generic or garbled copy and was rejected")
    assert_duration(min(speech, target), "script speech estimate")
    return ReelScript(
        language=brief.language,
        hook_line=texts[0] if texts else "",
        body_lines=lines,
        cta_line=(texts[-1] if texts and brief.needs_cta else ""),
        full_text=full,
        estimated_speech_seconds=speech,
        target_duration=target,
    )


def _speaker_for(brief: CreativeBrief, text: str, index: int) -> str:
    t = (text or "").lower()
    label = t.split(":", 1)[0].strip() if ":" in t else ""
    mapping = {
        "ইন্টারভিউয়ার": "interviewer", "interviewer": "interviewer",
        "ছেলে": "candidate", "candidate": "candidate",
        "বস": "boss", "boss": "boss",
        "জুনিয়র": "junior", "junior": "junior",
        "মা": "mother", "mom": "mother", "mother": "mother",
        "ড্রাইভার": "driver", "driver": "driver",
        "যাত্রী": "passenger", "passenger": "passenger",
        "বন্ধু": "friend", "friend": "friend",
        "student": "student",
    }
    if label in mapping:
        return mapping[label]
    if "ইন্টারভিউয়ার" in text:
        return "interviewer"
    return "talent" if brief.talking_head else "narrator"


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
        # Perfume / social default: a real Bangladeshi woman if culture is implied.
        gender = "woman"
        bd = bd or brief.language.startswith("bengali")
        name = "Nusrat" if bd else "Maya"
        wardrobe = "weekday cotton shirt, small gold stud, no costume styling"
        hair = "dark, naturally textured, slightly lived-in"
        voice = "warm, unhurried Bangladeshi conversational" if brief.language.startswith("bengali") else "warm, unhurried"
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
    spec = build_fictional_or_named_product(req, _product_kind(req), brief.needs_product)
    user_images = [a.uri or a.local_path for a in req.assets if a.kind in {"product_image", "logo"} and (a.uri or a.local_path)]
    if user_images:
        spec.reference_uris = user_images
        spec.user_image_authoritative = True
        spec.fictional = False
    return spec


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


def _shot_purposes(n: int, brief: CreativeBrief) -> List[str]:
    beats = list(brief.strategy_beats) or ["hook", "moment", "close"]
    if n <= 1:
        return [beats[0] if beats else "hook"]
    if n >= len(beats):
        return beats[:n] if len(beats) >= n else beats + ["close"] * (n - len(beats))
    # Compress: first, middle..., last
    if n == 2:
        return [beats[0], beats[-1]]
    return [beats[0], *beats[1:-1][: n - 2], beats[-1]]


def build_storyboard(job: ReelJob) -> Storyboard:
    assert job.brief and job.script
    brief = job.brief
    script = job.script
    product = job.product_bible or ProductSpec(name="", shape="none")
    chars = job.character_bible or [build_character(brief, job.request)]
    char = chars[0]
    target = min(float(brief.duration_seconds), 30.0)
    preferred = 3 if target >= 15 else 2
    if brief.creative_type in {"EDUCATIONAL", "EXPLAINER", "FACT"}:
        preferred = 2 if target <= 20 else 3
    if brief.creative_type in {"COMEDY", "SKIT"}:
        # One Veo take = one room, two faces. A second generation is how the actor changed.
        preferred = 1
        durations = [float(8 if target >= 8 else snap_veo_duration(target))]
    elif _product_kind(job.request) == "saas" and brief.talking_head:
        preferred = 1
        durations = [float(8 if target >= 8 else snap_veo_duration(target))]
    else:
        durations = plan_shot_durations(target, preferred_shots=preferred)
        durations = [float(d) for d in restructure_timeline(durations, cap=target)]
    if brief.creative_type in {"EDUCATIONAL", "EXPLAINER", "FACT"}:
        durations = durations[:preferred]
    spans = assign_timeline(durations)
    purposes = _shot_purposes(len(spans), brief)
    lines = script.body_lines
    situation = detect_situation(job.request.idea if job.request else "")
    location = {
        "COMEDY": comedy_location(situation),
        "SKIT": comedy_location(situation),
        "EDUCATIONAL": "plain room, one practical lamp, nothing decorative",
        "EXPLAINER": "plain room, one practical lamp, nothing decorative",
        "FACT": "plain room, one practical lamp, nothing decorative",
        "STORY": "lived-in apartment doorway, late light",
        "LIFESTYLE": "Dhaka-adjacent street / tea stall light, late afternoon",
        "UGC": "real apartment, phone-height, window light",
        "PRODUCT_DEMO": "kitchen or vanity table, practical lamp",
    }.get(brief.creative_type, "lived-in apartment, late-day window light")
    shots: List[ReelShot] = []
    for i, ((dur, start, end), purpose) in enumerate(zip(spans, purposes)):
        if brief.creative_type in {"COMEDY", "SKIT"} and len(spans) == 1:
            line = " ".join(l.text for l in lines if l.text)
        else:
            line = lines[min(i, len(lines) - 1)].text if lines else ""
        talking = bool(brief.talking_head) and purpose not in {"explanation", "compose", "graphic"}
        comedy = brief.creative_type in {"COMEDY", "SKIT"}
        gaze = "at the other person across the desk" if comedy else (
            "just past the lens, as if talking to one friend" if talking else "on the other person or the object"
        )
        who = [c.character_id for c in chars]
        if purpose in {"punchline", "escalation", "setup"} and len(chars) > 1:
            who = [c.character_id for c in chars]
        compose = purpose in {"explanation", "close"} and brief.creative_type in {"EDUCATIONAL", "EXPLAINER", "FACT"} and i == len(spans) - 1
        shots.append(ReelShot(
            shot_id=f"R{i+1:02d}",
            duration=dur,
            start_time=round(start, 3),
            end_time=round(end, 3),
            purpose=purpose,
            characters=who,
            character_state=char.emotional_baseline,
            location=location,
            environment=f"{location}. Real clutter at edges. No studio sweep.",
            wardrobe=(
                f"{chars[0].wardrobe} | {chars[1].wardrobe}"
                if comedy and len(chars) > 1 else char.wardrobe
            ),
            product_state=product.correct_usage if (brief.needs_product and "product" in purpose) else "",
            action=_action_for(purpose, product, char, brief.creative_type, chars, situation),
            dialogue=line,
            camera=(
                "locked third-person medium TWO-SHOT, both faces visible, camera on a desk or shelf — NEVER a selfie, NEVER first-person, NEVER a phone in a hand"
                if comedy else
                ("phone-height close-up on face" if str(purpose).startswith("hook") else "medium close-up")
            ),
            framing=(
                "9:16, both people in frame from the first frame. Desk between them. No empty room, no selfie arm."
                if comedy else
                "9:16, FIRST FRAME has a face, a conflict, or a specific object. Never an empty table."
            ),
            generation_strategy="compose" if compose else "veo",
            lens_look="28-35mm equivalent, natural contrast, no anamorphic flare",
            camera_motion="locked — no orbit, no handheld selfie sway" if comedy else "locked or 10cm motivated drift — never random orbit",
            lighting="overhead fluorescent, slightly ugly, no beauty dish" if comedy else "available window + practical lamp, no beauty dish",
            visual_style=brief.visual_style,
            sound=_sound_for(purpose, product.name),
            music="low bed, ducked",
            transition="hard_cut" if i else "none",
            continuity_requirements=[
                "same wardrobe", "same hair", "same two people",
                "same time of day", "same office desk",
            ] if comedy else [
                "same wardrobe", "same hair", "same product bottle",
                "same time of day", "same location geography",
            ],
            negative_constraints=[
                "plastic skin", "malformed hands", "invented logo",
                "changed clothes", "floating product", "fake lens flare",
                "looking at camera" if not talking else "exaggerated presenter smile",
            ] + (["selfie", "first-person", "phone in a hand", "one person only", "empty office"] if comedy else []),
            performance=PerformanceDirection(
                objective="play the wrong-answer joke, not sell a product" if comedy else "own the product privately, not sell it",
                emotional_state=char.emotional_baseline,
                subtext="this is already part of their day",
                body_language="unforced shoulders, real weight in the hands",
                gaze=gaze,
                gesture="hands on the desk, no phone, no product" if comedy else "small, motivated product handling",
                facial_reaction="micro, not a commercial grin",
                timing="let a breath exist before the line",
                pause="0.3s after the hook line",
                delivery_intensity="restrained",
            ),
            talking_head=talking,
            native_audio=talking and not compose,
            use_first_last_frame=(i > 0),
        ))
    total = sum(s.duration for s in shots)
    assert_duration(total, "storyboard")
    board = Storyboard(shots=shots, total_duration=round(total, 3), target_duration=target)
    job.storyboard = board
    from vidgen.reels.prompts import compile_shot_prompt
    prev = None
    for shot in shots:
        compile_shot_prompt(shot, job, prev)
        prev = shot
    return board


def _pronouns(char: CharacterSpec) -> tuple[str, str]:
    he = "man" in (char.appearance or "").lower()
    return ("He", "his") if he else ("She", "her")


def _action_for(
    purpose: str,
    product: ProductSpec,
    char: CharacterSpec,
    ctype: str = "",
    cast: Optional[List[CharacterSpec]] = None,
    situation: str = "",
) -> str:
    she, her = _pronouns(char)
    other = (cast[1].name if cast and len(cast) > 1 else "the other person")
    if ctype in {"COMEDY", "SKIT", "MEME"}:
        return comedy_action(situation or "office", char.name, other)
    if ctype in {"EDUCATIONAL", "EXPLAINER", "FACT"}:
        if str(purpose).startswith("hook") or purpose == "question":
            return f"FIRST FRAME is {char.name}'s face saying the claim, or a single clear object. No landscape."
        return f"{char.name} explains with one simple hand gesture. No stock cosmos footage."
    if ctype == "STORY":
        return f"FIRST FRAME is {char.name} already in the moment. {she} does not walk into an empty room."
    if not product.required:
        return f"FIRST FRAME is {char.name}'s face or a specific action. No establishing emptiness."
    atomizer = "atomizer" if "atomizer" in (product.shape or "").lower() or "perfume" in (product.name or "").lower() else "product"
    if purpose in {"hook", "hook_and_product"}:
        return (
            f"FIRST FRAME is {char.name}'s face, already in close-up. "
            f"{she} is mid-thought, then lifts {her} {product.name} {atomizer}. "
            f"{she} does not turn away from us. No walk-in from behind."
        )
    if purpose in {"product", "proof", "product_truth", "product_and_cta", "product_cta"}:
        return f"{char.name}'s hands hold the same {product.name}; label toward camera."
    if purpose == "cta":
        return f"{char.name} glances down, then back; {product.name} stays in frame."
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
    job.character_bible = build_cast(req, job.brief.language, job.brief.creative_type)
    job.product_bible = build_product(job.brief, req)
    job.brand_bible = build_brand(job.brief, req)
    job.storyboard = build_storyboard(job)
    from vidgen.reels.assets import apply_compose_strategy, plan_assets
    apply_compose_strategy(job)
    plan_assets(job)
    from vidgen.reels.craft import assert_watchable
    from vidgen.reels.watchability import score_watchability
    score_watchability(job)
    assert_watchable(job)
    return job
