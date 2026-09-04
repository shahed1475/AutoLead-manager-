import io

import pytest
from openpyxl import Workbook

from backend.automation.file_import import parse_upload


def _xlsx(sheets: dict) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _csv(text: str) -> bytes:
    return text.encode("utf-8")


# ── combined layout ──────────────────────────────────────────────────────

def test_combined_csv():
    data = _csv("City/Town,State,Niche\nNew York,New York,Hair Salon\nMiami,Florida,Beauty Salon\n")
    p = parse_upload("leads.csv", data)
    assert p.layout == "combined"
    assert set(p.niches) == {"Hair Salon", "Beauty Salon"}
    assert {loc["city"] for loc in p.locations} == {"New York", "Miami"}
    assert p.combinations == len(p.niches) * len(p.locations)


def test_combined_xlsx_with_aliased_headers():
    data = _xlsx({"Sheet1": [["town", "region", "industry"],
                             ["Chicago", "IL", "Dental Clinic"],
                             ["Houston", "TX", "Restaurant"]]})
    p = parse_upload("data.xlsx", data)
    assert p.layout == "combined"
    assert len(p.locations) == 2 and len(p.niches) == 2


# ── separate sheets ──────────────────────────────────────────────────────

def test_separate_sheets_in_one_xlsx():
    data = _xlsx({
        "Locations": [["City/Town", "State"], ["New York", "NY"], ["LA", "CA"], ["Chicago", "IL"]],
        "Niches":    [["Niche"], ["Hair Salon"], ["Barber Shop"]],
    })
    p = parse_upload("book.xlsx", data)
    assert p.layout == "separate"
    assert len(p.locations) == 3 and len(p.niches) == 2
    assert p.combinations == 6


def test_separate_two_files():
    locs = _csv("City,State\nNew York,NY\nMiami,FL\n")
    niches = _csv("Niche\nHair Salon\n")
    p = parse_upload("locations.csv", locs, "niches.csv", niches)
    assert p.layout == "separate"
    assert len(p.locations) == 2 and p.niches == ["Hair Salon"]


# ── validation + hygiene ─────────────────────────────────────────────────

def test_missing_niche_column_raises():
    with pytest.raises(ValueError, match="niche"):
        parse_upload("x.csv", _csv("City,State\nNY,NY\n"))


def test_unsupported_extension_raises():
    with pytest.raises(ValueError):
        parse_upload("old.xls", b"\x00\x01")


def test_whitespace_and_blank_rows_cleaned():
    data = _csv("City,State,Niche\n  New York  , NY ,  Hair Salon \n\n,,\nMiami,FL,Hair Salon\n")
    p = parse_upload("x.csv", data)
    assert {loc["city"] for loc in p.locations} == {"New York", "Miami"}
    assert p.niches == ["Hair Salon"]


def test_duplicate_collapse_warns():
    data = _csv("City,State,Niche\nNY,NY,Hair Salon\nNY,NY,Hair Salon\n")
    p = parse_upload("x.csv", data)
    assert len(p.locations) == 1 and len(p.niches) == 1
    assert any("duplicate" in w.lower() for w in p.warnings)


# ── location list must never become the niche list ───────────────────────

def test_type_column_of_place_types_is_not_used_as_niche():
    # A locations list with a City/Town classification column (header "Type")
    # must NOT be parsed as niches=["City","Town"].
    data = _csv("City,Type,State\nAbbeville,City,Alabama\nAkron,Town,Alabama\n")
    with pytest.raises(ValueError):
        parse_upload("places.csv", data)


def test_niche_column_filled_with_place_types_is_rejected():
    # Even when the header is a real niche alias, all-place-type values are a
    # location classification, not business categories.
    data = _csv("City,Category,State\nBurlington,City,Vermont\nMontpelier,Town,Vermont\n")
    with pytest.raises(ValueError, match="place type"):
        parse_upload("places.csv", data)


def test_legit_combined_still_parses_with_category_header():
    data = _csv("City,Category,State\nBurlington,Dental Clinic,Vermont\nMontpelier,Med Spa,Vermont\n")
    p = parse_upload("leads.csv", data)
    assert set(p.niches) == {"Dental Clinic", "Med Spa"}
    assert {l["city"] for l in p.locations} == {"Burlington", "Montpelier"}


def test_first_name_header_not_matched_as_state_via_substring():
    # "st" must not match inside "First Name" — a bare-substring header match
    # would wrongly treat the name column as the state column.
    data = _csv("City,First Name,Niche\nNYC,Alice,Hair Salon\n")
    p = parse_upload("x.csv", data)
    assert p.locations[0]["state"] is None
    assert p.niches == ["Hair Salon"]
