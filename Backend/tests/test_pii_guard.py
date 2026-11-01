import pytest

from app.services.pii_guard import detect_sensitive_data

# Synthetic, format-valid test values only (not real identifiers).
SENSITIVE = [
    ("My SSN is 123-45-6789", "Social Security Number"),
    ("aadhaar 2345 6789 0123", "Aadhaar number"),
    ("PAN: ABCPE1234F", "PAN number"),
    ("passport number Z1234567", "passport number"),
    ("my driving licence no MH12 20110012345", "driver's license number"),
    ("card 4111 1111 1111 1111 was charged", "payment card number"),
    ("attaching my medical record for the claim", "medical / patient record"),
]

SAFE = [
    "How many days of annual leave do I get?",
    "What is the notice period after 6 months of probation?",
    "Is the learning budget USD 1,000 per year?",
    "Can I work remotely 3 days per week?",
    "Call me on ext 4455 about employee ID 100234",
]


@pytest.mark.parametrize(("text", "kind"), SENSITIVE)
def test_detects_sensitive_data(text: str, kind: str) -> None:
    assert kind in {f.kind for f in detect_sensitive_data(text)}


@pytest.mark.parametrize("text", SAFE)
def test_allows_normal_questions(text: str) -> None:
    assert detect_sensitive_data(text) == []


def test_non_luhn_digit_run_is_not_a_card() -> None:
    assert "payment card number" not in {f.kind for f in detect_sensitive_data("ref 4111 1111 1111 1112")}
