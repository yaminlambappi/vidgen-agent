"""Autonomous character cast and fictional product identity. Deterministic, no I/O."""
from __future__ import annotations

import hashlib
import re
from typing import List, Tuple

from vidgen.reels.language import has_bengali
from vidgen.reels.schemas import CharacterSpec, ProductSpec, ReelRequest


_BN_MALE = (("Rafi", "ছেলে", "candidate"), ("Arif", "পুরুষ", "lead"))
_BN_FEMALE = (("Nusrat", "মেয়ে", "lead"), ("Shirin", "নারী", "lead"))
_EN_MALE = (("Omar", "man", "lead"), ("Leo", "guy", "lead"))
_EN_FEMALE = (("Maya", "woman", "lead"), ("Asha", "girl", "lead"))

_FICTIONAL = {
    "perfume": (("Nodi", "Atelier"), ("Raat", "Noir"), ("Chaaya", "Mist"), ("Kagoj", "Bloom")),
    "skincare": (("Dhan", "Serum"), ("Shishir", "Gel"), ("Mukto", "Cream")),
    "food": (("Chula", "Kitchen"), ("Adda", "Bites"), ("Ghoroa", "Table")),
    "default": (("Tokai", "Studio"), ("Bhasha", "Co"), ("Path", "Goods")),
}


def _seed(text: str) -> int:
    return int(hashlib.sha256((text or "idea").encode()).hexdigest()[:8], 16)


def _bd(req: ReelRequest, language: str) -> bool:
    blob = f"{req.idea} {req.audience} {language}".lower()
    return has_bengali(req.idea) or any(k in blob for k in ("bengali", "bangla", "dhaka", "bangladeshi", "বাংলা"))


def _gender_from_idea(idea: str) -> str:
    low = (idea or "").lower()
    if any(k in low for k in ("ছেলে", "পুরুষ", "boyfriend", "guy", "man ", "interview")):
        return "man"
    if any(k in low for k in ("মেয়ে", "নারী", "girlfriend", "woman", "girl")):
        return "woman"
    return ""


def _count_from_idea(idea: str) -> int:
    low = (idea or "").lower()
    if any(k in low for k in ("interview", "ইন্টারভিউ", "two people", "2 people", "দুইজন", "boss", "teacher")):
        return 2
    return 1


def fictional_product_name(kind: str, idea: str) -> Tuple[str, str]:
    bank = _FICTIONAL.get(kind) or _FICTIONAL["default"]
    brand, line = bank[_seed(idea) % len(bank)]
    return brand, f"{brand} {line}"


def infer_named_product(req: ReelRequest) -> str:
    if req.product_name.strip():
        return req.product_name.strip()
    quoted = re.search(r"[\"“]([^\"”]{2,40})[\"”]", req.idea or "")
    if quoted:
        return quoted.group(1).strip()
    named = re.search(
        r"(?:called|named|নামে)\s+([A-Za-z\u0980-\u09FF][A-Za-z0-9\u0980-\u09FF' -]{0,40})",
        req.idea or "",
    )
    if named:
        return named.group(1).strip(" .")
    nike = re.search(r"\b(nike|adidas|apple|samsung|google)\b", (req.idea or ""), re.I)
    if nike:
        return nike.group(1)
    return ""


def build_cast(req: ReelRequest, language: str, creative_type: str) -> List[CharacterSpec]:
    bd = _bd(req, language)
    n = min(2, _count_from_idea(req.idea))
    gender = _gender_from_idea(req.idea)
    if creative_type in {"COMEDY", "SKIT"} and "interview" in (req.idea or "").lower():
        n = 2
        gender = gender or "man"
    specs: List[CharacterSpec] = []
    if n == 2 and ("interview" in (req.idea or "").lower() or "ইন্টারভিউ" in (req.idea or "")):
        specs.append(_person(
            "Rafi" if bd else "Ray", "man", "24-28", bd, language,
            role="candidate",
            wardrobe="slightly tight shirt, cheap tie, nervous crease",
            personality="overconfident, answers before listening",
            voice="bright, too eager",
        ))
        specs.append(_person(
            "Kabir" if bd else "Hassan", "man", "38-46", bd, language,
            role="interviewer",
            wardrobe="plain office shirt, no tie, tired eyes",
            personality="exhausted, professionally polite",
            voice="flat, dry",
        ))
        return specs
    lead_gender = gender or ("woman" if creative_type in {"UGC", "ADVERTISEMENT", "LIFESTYLE"} else "man")
    name = ("Nusrat" if bd else "Maya") if lead_gender == "woman" else ("Arif" if bd else "Omar")
    specs.append(_person(
        name, lead_gender, "24-34", bd, language,
        role="lead",
        wardrobe="weekday cotton, no costume",
        personality="self-possessed, a little private",
        voice="warm, unhurried Bangladeshi conversational" if language.startswith("bengali") else "warm, unhurried",
    ))
    if n == 2:
        other = "woman" if lead_gender == "man" else "man"
        specs.append(_person(
            ("Shirin" if bd else "Asha") if other == "woman" else ("Rafi" if bd else "Leo"),
            other, "26-36", bd, language,
            role="other",
            wardrobe="everyday office or street clothes",
            personality="reactive, timing-aware",
            voice="conversational",
        ))
    return specs


def _person(name, gender, age, bd, language, role, wardrobe, personality, voice) -> CharacterSpec:
    return CharacterSpec(
        name=name,
        age=age,
        appearance=f"{'Bangladeshi' if bd else 'South Asian'} {gender}, real face, visible skin texture, not beauty-filtered",
        hair="dark, naturally textured, slightly lived-in",
        face="asymmetrical, lived-in, no porcelain smoothness",
        skin="natural tone with real texture; no plastic sheen",
        wardrobe=wardrobe,
        accessories="one small everyday accessory, consistent across shots",
        body="ordinary posture, not model-rigid",
        personality=personality,
        mannerisms="listens with the face first, then the hands",
        emotional_baseline="present, unperformed",
        speaking_style=voice,
        role=role,
    )


def build_fictional_or_named_product(req: ReelRequest, kind: str, needed: bool) -> ProductSpec:
    if not needed:
        return ProductSpec(name="", shape="none", required=False)
    named = infer_named_product(req)
    fictional = not bool(named)
    if fictional:
        brand, full = fictional_product_name(kind or "default", req.idea)
    else:
        brand, full = (req.brand_name or named.split()[0], named)
    shapes = {
        "perfume": (
            "rectangular clear glass perfume ATOMIZER with a silver spray collar and nozzle, "
            "pale liquid inside, short cap — NEVER a wine bottle, NEVER an empty kitchen flask"
        ),
        "skincare": "compact pump bottle, matte label, no invented logo type",
        "food": "real plated or packaged item, consistent across shots",
        "fashion": "garment as worn, fabric weight visible",
    }
    return ProductSpec(
        name=full,
        brand_name=brand,
        fictional=fictional,
        required=True,
        shape=shapes.get(kind, "believable physical object, locked across shots"),
        proportions="believable commercial proportions",
        packaging="simple unbranded-safe packaging if unknown" if fictional else f"recognizable {full} packaging where legally depictable",
        colors="muted real-world materials",
        materials="physical, scuffed-real materials",
        labels=f"only the name {full}" if fictional else "do not invent extra copy",
        logo="do not invent a celebrity or award mark",
        distinctive_details="keep silhouette, cap, and label placement identical across shots",
        correct_usage={
            "perfume": "spray on wrist or neck, then a small beat",
            "skincare": "dot and press",
        }.get(kind, "handle like a real owner"),
        orientation="label-readable when the product is the subject",
    )
