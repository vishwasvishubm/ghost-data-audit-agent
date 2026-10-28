"""Check screenshot observations against a hand-made reading of each screenshot.

The expected facts below were transcribed by a person looking at the images, independently of
the model. The check compares the set of resources found per screenshot and each listed field.

    python code/scripts/check_vision.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit.data import load_evidence_manifest  # noqa: E402
from audit.evidence import images  # noqa: E402

# file_id -> {resource_id: {field: expected value}}; fields: user_ref, email, phone, name, lookup_key, or attrs keys.
EXPECTED: dict[str, dict[str, dict]] = {
    "evidence_04": {  # account admin console: Haruka Sato
        "account_5762": {"user_ref": "user_11", "email": "haruka.s1838@zoho.com", "phone": "+81 90 7610 2507", "name": "Haruka Sato"},
        "support_9538": {"user_ref": "user_11", "lookup_key": "haruka.s1838@zoho.com", "email": None},
    },
    "evidence_09": {  # search inspector: real hit plus a near-identical decoy
        "search_428": {"user_ref": "user_13", "email": "g.silva76@gmail.com", "name": "Gabriel Silva"},
        "search_1874": {"user_ref": None, "email": "g.silva76.jr@gmail.com"},
    },
    "evidence_14": {  # connector console: two customer-selecting flows, two system flows without ids
        "data_flow_1121": {"lookup_key": "jain.aditi@gmail.com", "email": None, "enabled": True},
        "data_flow_9846": {"lookup_key": "lucia-torres@gmail.com", "email": None, "enabled": True},
    },
    "evidence_19": {"analytics_7438": {"user_ref": "user_20", "email": "darwish.omar@icloud.com", "name": "Omar Darwish"}},
    "evidence_23": {  # export console: one customer export among staff exports
        "export_1260": {"email": "ingrid.dahl@outlook.com", "download_available": True},
        "export_1261": {"email": "marcus.lee@northstar.shop"},
        "export_1259": {"email": "sara.kim@northstar.shop"},
        "export_1258": {"email": "daniel.park@northstar.shop", "download_available": True},
    },
    "evidence_28": {"support_1034": {"email": "kagyeman8947@fastmail.com", "name": "Kofi Agyeman"}},
    "evidence_32": {  # backup console: account column selects the archive's subject
        "backup_9291": {"lookup_key": "bluepeakco@outlook.com"},
        "backup_9288": {"lookup_key": "miller.logistics@company.com"},
        "backup_9287": {"lookup_key": "areed1433@gmail.com"},
        "backup_9286": {"lookup_key": "summit-retail@company.com"},
    },
    "evidence_37": {f"audit_record_00{i}": {"user_ref": "user_03"} for i in range(1, 7)},
    # Reference registry page about a bare user_17 (resolved to tombstone_17 by the data catalog later).
    "evidence_42": {"user_17": {"user_ref": "user_17", "email": None}},
    # Object storage listing: files appear by name only (resolved to object ids by the catalog later).
    "evidence_46": {
        name: {"file_name": name}
        for name in (
            "delivery_confirmation_19.pdf", "delivery_confirmation_20.pdf", "return_label_4821.pdf",
            "return_label_4876.pdf", "invoice_NS-10493.pdf", "parcel_damage_7713.jpg",
            "customer_export_2026-02-10.zip", "customer_export_2026-02-12.zip",
        )
    },
    "evidence_60": {"marketing_001": {"email": "sj.park@naver.com", "phone": "+82 10-5837-9451", "name": "Soo-jin Park"}},
}


def value(obs, field: str):
    return getattr(obs, field) if hasattr(obs, field) else obs.attrs.get(field)


def main() -> int:
    manifest = {ev.file_id: ev for ev in load_evidence_manifest()}
    resource_hits = resource_total = field_hits = field_total = 0
    for file_id, expected in EXPECTED.items():
        found = {o.resource_id: o for o in images.parse(manifest[file_id])}
        missing, extra = sorted(set(expected) - set(found)), sorted(set(found) - set(expected))
        resource_total += len(set(expected) | set(found))
        resource_hits += len(set(expected) & set(found))
        problems = [f"missing {missing}"] * bool(missing) + [f"extra {extra}"] * bool(extra)
        for rid, fields in expected.items():
            for field, want in fields.items():
                if rid not in found:
                    continue
                field_total += 1
                got = value(found[rid], field)
                if got == want:
                    field_hits += 1
                else:
                    problems.append(f"{rid}.{field}: expected {want!r}, got {got!r}")
        print(f"{'ok  ' if not problems else 'DIFF'}  {file_id}  {manifest[file_id].path.name}")
        for p in problems:
            print(f"        {p}")
    print(f"\nresources: {resource_hits}/{resource_total} matched   fields: {field_hits}/{field_total} correct")
    return 0 if resource_hits == resource_total and field_hits == field_total else 1


if __name__ == "__main__":
    raise SystemExit(main())
