"""Compare the intent classifier with a hand-made labelling of every distinct request text.

The labels were written by a person reading each non-templated request; every other text uses one
of the deletion templates (twelve stock opening sentences such as "Please erase the account linked
to this email address."). Requests that only verify an earlier deletion count as deletion audits,
as the gold samples treat them (e.g. case_25, case_28).

    python code/scripts/check_intent.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit.data import load_requests  # noqa: E402
from audit.intent import IntentClassifier  # noqa: E402

# Distinct texts a person judged NOT to authorize deletion, with the reason.
NOT_DELETION = {
    "Can you tell me what personal information you hold about me? This is an information request, not a request to erase it.": "access",
    "Could you send me a copy of the information held on my account? I still use it, so please don't delete or change anything.": "access",
    "I can't access my profile and need help resetting the login. Do not close the account.": "account support",
    "I need a copy of last year's invoices for my records. Please leave my profile and purchase history in place.": "access",
    "I'd like to download my account information in a portable format. Please keep the account as it is.": "export",
    "My surname is misspelled on the account. Can you correct it without removing any of my records?": "correction",
    "Please change the email address used for this account. I want to continue using the service.": "correction",
    "Please remove me from marketing lists only. I still want receipts and service messages for this account.": "marketing opt-out",
    "Please stop the promotional emails. I still need the account and my order history, so I am not asking for deletion.": "marketing opt-out",
    "The mobile number on my profile is out of date. Please replace it with the new one and leave the account active.": "correction",
}


def main() -> int:
    requests = load_requests() + load_requests("sample_deletion_requests.csv")
    classifier = IntentClassifier(requests)
    texts = sorted(set(r.request_text for r in requests))
    wrong = []
    for text in texts:
        expected = text not in NOT_DELETION
        if classifier.verdicts[text].authorizes_deletion != expected:
            wrong.append((expected, classifier.verdicts[text].request_type, text))
    for expected, kind, text in wrong:
        print(f"WRONG  expected authorizes_deletion={expected}, model said {kind}: {text[:120]}")
    case_level = sum(classifier.authorizes_deletion(r) == (r.request_text not in NOT_DELETION) for r in requests)
    print(f"\ntexts: {len(texts) - len(wrong)}/{len(texts)} correct   requests: {case_level}/{len(requests)} correct")
    print(f"non-deletion requests: {sum(r.request_text in NOT_DELETION for r in requests)} of {len(requests)}")
    return 1 if wrong else 0


if __name__ == "__main__":
    raise SystemExit(main())
