"""Detects highly sensitive identifiers in user input before it reaches the LLM.

Messages containing government IDs, payment card numbers or medical records are
rejected outright: they are never forwarded to Gemini, stored, or logged.
"""

import re
from dataclasses import dataclass

_SSN = re.compile(r"\b(?!000|666|9\d\d)\d{3}[- ](?!00)\d{2}[- ](?!0000)\d{4}\b")
_AADHAAR = re.compile(r"\b[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}\b")
_PAN = re.compile(r"\b[A-Z]{3}[PCHABGJLFT][A-Z]\d{4}[A-Z]\b", re.IGNORECASE)
_PASSPORT_IN = re.compile(r"\b[A-PR-WY][1-9]\d\s?\d{4}[1-9]\b")
_CARD_CANDIDATE = re.compile(r"\b(?:\d[ -]?){13,19}\b")

# Keyword-triggered patterns: these formats are too generic to match without context.
_PASSPORT_CONTEXT = re.compile(r"passport\s*(?:no\.?|number|#)?\s*[:\-]?\s*[A-Z0-9]{6,9}\b", re.IGNORECASE)
_LICENSE_CONTEXT = re.compile(
    r"(?:driver'?s?|driving)\s*licen[cs]e\s*(?:no\.?|number|#)?\s*[:\-]?\s*[A-Z0-9\- ]{6,20}\b",
    re.IGNORECASE,
)
_MEDICAL_CONTEXT = re.compile(
    r"\b(?:medical record|patient (?:id|record|number)|MRN|diagnosis report|lab report)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PiiFinding:
    kind: str


def _luhn_valid(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def detect_sensitive_data(text: str) -> list[PiiFinding]:
    findings: list[PiiFinding] = []

    if _SSN.search(text):
        findings.append(PiiFinding("Social Security Number"))
    if _AADHAAR.search(text):
        findings.append(PiiFinding("Aadhaar number"))
    if _PAN.search(text):
        findings.append(PiiFinding("PAN number"))
    if _PASSPORT_IN.search(text) or _PASSPORT_CONTEXT.search(text):
        findings.append(PiiFinding("passport number"))
    if _LICENSE_CONTEXT.search(text):
        findings.append(PiiFinding("driver's license number"))
    if _MEDICAL_CONTEXT.search(text):
        findings.append(PiiFinding("medical / patient record"))

    for match in _CARD_CANDIDATE.finditer(text):
        digits = re.sub(r"\D", "", match.group())
        if 13 <= len(digits) <= 19 and _luhn_valid(digits):
            findings.append(PiiFinding("payment card number"))
            break

    return findings


def rejection_message(findings: list[PiiFinding], hr_contact_email: str) -> str:
    kinds = ", ".join(sorted({f.kind for f in findings}))
    return (
        f"For your security, I can't process messages containing sensitive personal data "
        f"(detected: {kinds}). Please remove it and ask your question again. "
        f"If HR needs this information, share it only through the official HR portal "
        f"or by contacting {hr_contact_email} directly."
    )
