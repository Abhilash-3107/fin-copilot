"""Uploading a spreadsheet through the API: what the page is told.

A file the app cannot place yet gets a 422 with a structured body (what the form
needs) and stores nothing; the same file sent again with the person's answer
lands. Sorting starts by itself, like a PDF's.
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).parent / "fixtures"
MAPPING = {"date": 0, "description": 1, "amount": 2, "balance": 3}


@pytest.fixture
def api(tmp_path, monkeypatch):
    from finance_copilot.api.deps import get_db as api_get_db
    from finance_copilot.config import settings
    from finance_copilot.db import connection as dbc
    from finance_copilot.main import app

    db_path = str(tmp_path / "ledger.db")
    monkeypatch.setattr(settings, "db_path", db_path)
    monkeypatch.setattr(settings, "llm_provider", "none")
    conn = dbc.get_migrated_db(db_path)

    def override():
        c = dbc.get_connection(db_path)
        try:
            yield c
        finally:
            c.close()

    app.dependency_overrides[api_get_db] = override
    with patch("finance_copilot.api.routes.annotations.embed_transaction", return_value=False), \
            patch("finance_copilot.api.routes.annotations._embed_in_background"):
        yield TestClient(app), conn
    app.dependency_overrides.pop(api_get_db, None)
    conn.close()


def upload(client, name, data=None, **fields):
    """Day first where a file of early-month days leaves it open (the fixtures' days stop at
    the 8th or the 12th); a test of that question sends its own date_order or `ask=True`."""
    body = data if data is not None else (FIXTURES / name).read_bytes()
    if fields.pop("ask", False) is False:
        fields.setdefault("date_order", "dmy")
    return client.post("/api/statements/upload", files={"file": (name, body, "application/octet-stream")}, data=fields)


def counts(conn):
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("statements", "transactions", "accounts", "import_layouts")}


class TestWhatIsAccepted:
    def test_a_file_of_no_known_kind_is_refused_by_its_content_not_its_name(self, api):
        client, conn = api
        got = upload(client, "statement.csv", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        assert got.status_code == 422
        assert got.json()["detail"] == "statement.csv isn't a PDF, CSV, XLSX or XLS file. Only those can be added."
        assert counts(conn)["statements"] == 0

    def test_a_pdf_still_lands(self, api):
        client, _ = api
        got = upload(client, "sample_statement.pdf")
        assert got.status_code == 200 and got.json()["rows_added"] == 8

    def test_a_csv_named_pdf_is_read_as_a_csv(self, api):
        client, conn = api
        got = upload(client, "export.pdf", (FIXTURES / "sample_statement.csv").read_bytes(), new_account_name="Main")
        assert got.status_code == 200 and got.json()["rows_added"] == 8

    def test_an_empty_file(self, api):
        client, _ = api
        got = upload(client, "empty.csv", b"")
        assert got.status_code == 422 and "empty" in got.json()["detail"]


class TestTheQuestion:
    def test_an_unreadable_layout_comes_back_as_a_422_with_what_the_form_needs(self, api):
        client, conn = api
        upload(client, "sample_statement.pdf")
        got = upload(client, "sample_export_oct.csv")
        assert got.status_code == 422
        body = got.json()
        assert body["needs"] == ["mapping", "account"]
        assert body["columns"] == ["When", "What", "Moved", "Left"]
        assert body["guess"] == MAPPING
        assert len(body["preview"]) == 5
        assert body["parsed"]["rows"][0] == {
            "date": "2026-10-02", "description": "UPI/SWIGGY/ORDER/610112233/UPI",
            "amount": 540.0, "direction": "debit", "balance": 51460.0,
        }
        assert body["accounts"] == [{"id": body["accounts"][0]["id"], "account_label": "Bank ··0000"}]
        assert counts(conn) == {"statements": 1, "transactions": 8, "accounts": 1, "import_layouts": 0}

    def test_no_account_number_in_the_body(self, api):
        client, _ = api
        upload(client, "sample_statement.pdf")
        text = upload(client, "sample_export_oct.csv").text
        assert "XXXX0000" not in text

    def test_the_answer_lands_it_and_the_next_file_asks_nothing(self, api):
        client, conn = api
        first = upload(client, "sample_export_oct.csv", mapping=json.dumps(MAPPING), new_account_name="Savings 2")
        assert first.status_code == 200
        shown = first.json()
        assert shown["account_is_new"] and shown["account_label"] == "Savings 2"
        assert shown["rows_added"] == 6 and shown["sorting_job_id"]
        second = upload(client, "sample_export_nov.csv")
        assert second.status_code == 200
        assert second.json()["account_id"] == shown["account_id"] and not second.json()["account_is_new"]

    def test_a_wrong_choice_comes_back_as_a_plain_message_on_a_422(self, api):
        client, conn = api
        got = upload(
            client, "sample_export_oct.csv",
            mapping=json.dumps({"date": 0, "description": 1, "debit": 3, "balance": 2}), new_account_name="X",
        )
        assert got.status_code == 422
        assert "running balance" in got.json()["detail"] and "needs" not in got.json()
        assert counts(conn)["statements"] == 0

    def test_choices_that_are_not_json_are_refused_in_words(self, api):
        client, _ = api
        got = upload(client, "sample_export_oct.csv", mapping="not json", new_account_name="X")
        assert got.status_code == 422 and "column choices" in got.json()["detail"]

    def test_an_existing_account_by_id(self, api):
        client, _ = api
        pdf = upload(client, "sample_statement.pdf").json()
        got = upload(
            client, "sample_export_oct.csv", mapping=json.dumps(MAPPING),
            account_id=pdf["account_id"], confirm_overlap="true",
        )
        assert got.status_code == 200 and got.json()["account_id"] == pdf["account_id"]


class TestOutcomes:
    def test_a_copy_of_a_month_printed_another_way_is_held_then_added_when_confirmed(self, api):
        client, conn = api
        upload(client, "sample_statement.pdf")
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\r\n")
        for row in csv.reader(io.StringIO((FIXTURES / "sample_statement.csv").read_text())):
            if len(row) >= 5 and row[0][:2].isdigit():
                row = [row[0], row[1][:10], *row[2:4]]  # a cut narration, and no balance
            elif row[:1] == ["Date"]:
                row = row[:4]
            writer.writerow(row)
        stripped = out.getvalue().encode()
        held = upload(client, "copy.csv", stripped)
        assert held.status_code == 422
        body = held.json()
        assert body["needs"] == ["overlap"]
        assert body["overlap"]["rows_in_overlap"] == 8 and body["overlap"]["unmatched"] == 8
        assert "twice" in body["detail"]
        assert counts(conn)["statements"] == 1
        added = upload(client, "copy.csv", stripped, confirm_overlap="true")
        assert added.status_code == 200 and added.json()["rows_added"] == 8

    def test_a_file_with_nothing_new_is_a_409(self, api):
        client, _ = api
        upload(client, "sample_statement.pdf")
        got = upload(client, "sample_statement.csv")
        assert got.status_code == 409 and got.json()["detail"].startswith("Nothing new")

    def test_the_same_file_twice_is_a_409(self, api):
        client, _ = api
        upload(client, "sample_statement.csv", new_account_name="Main")
        assert upload(client, "sample_statement.csv").status_code == 409

    def test_the_response_says_there_was_no_balance_to_check(self, api):
        client, _ = api
        data = b"Date,Narration,Amount\r\n01/10/2026,UPI/A/1,-10.00\r\n02/10/2026,UPI/B/2,5.00\r\n"
        got = upload(client, "no_balance.csv", data, new_account_name="Main").json()
        assert got["balance_check"]["checked"] == 0 and got["rows_added"] == 2

    def test_an_xlsx_and_an_xls(self, api):
        client, _ = api
        assert upload(client, "a.xlsx", (FIXTURES / "sample_statement.xlsx").read_bytes(), new_account_name="Main").status_code == 200
        client2_bytes = (FIXTURES / "sample_statement.xls").read_bytes()
        got = upload(client, "a.xls", client2_bytes)
        assert got.status_code == 409  # the same payments, already in Main


class TestPreview:
    def test_the_first_rows_as_they_would_be_read(self, api):
        client, conn = api
        got = client.post(
            "/api/statements/upload/preview",
            files={"file": ("x.csv", (FIXTURES / "sample_export_oct.csv").read_bytes(), "text/csv")},
            data={"mapping": json.dumps(MAPPING)},
        )
        assert got.status_code == 200
        assert got.json()["error"] is None and len(got.json()["rows"]) == 5
        assert counts(conn)["statements"] == 0

    def test_a_wrong_choice_is_an_error_in_the_body_not_a_failure(self, api):
        client, _ = api
        got = client.post(
            "/api/statements/upload/preview",
            files={"file": ("x.csv", (FIXTURES / "sample_export_oct.csv").read_bytes(), "text/csv")},
            data={"mapping": json.dumps({"date": 1, "description": 1, "amount": 2})},
        )
        assert got.status_code == 200 and got.json()["rows"] == [] and got.json()["error"]

    def test_a_file_that_is_not_a_table_is_a_422(self, api):
        client, _ = api
        got = client.post(
            "/api/statements/upload/preview",
            files={"file": ("x.png", b"\x89PNG\x00\x00", "image/png")}, data={"mapping": "{}"},
        )
        assert got.status_code == 422


class TestMovingAReversalAtAFileBoundary:
    """Move runs the same matcher as ingest: the reversal after a file's end is a
    payment of its own, not the earlier +100 on the same balance."""

    HEAD = "Example Bank\nAccount Number,XXXX4321\n\nDate,Narration,Withdrawal Amt.,Deposit Amt.,Balance\n"
    ONE = HEAD + "\n".join([
        "10/10/2026,UPI/GROCER/1,500.00,,9500.00",
        "12/10/2026,UPI/CAFE/2,400.00,,9100.00",
        "14/10/2026,UPI/RAVI/3/SPLIT,,100.00,9200.00",
    ])
    TWO = HEAD + "\n".join([
        "15/10/2026,UPI/SHOP/4/FAILED,100.00,,9100.00",
        "16/10/2026,UPI/SHOP/4/REVERSAL,,100.00,9200.00",
        "20/10/2026,UPI/RENT/5,5000.00,,4200.00",
    ])

    def test_the_reversal_stays_and_the_ledger_reconciles(self, api):
        client, conn = api
        one = upload(client, "one.csv", self.ONE.encode(), new_account_name="Main").json()
        two = upload(client, "two.csv", self.TWO.encode(), new_account_name="Other").json()
        assert one["account_id"] != two["account_id"]
        moved = client.patch(f"/api/statements/{two['id']}", json={"account_id": one["account_id"]})
        assert moved.status_code == 200, moved.text
        body = moved.json()
        assert (body["rows_already_there"], body["statement_deleted"]) == (0, False)
        rows = conn.execute("SELECT debit_credit, amount FROM transactions").fetchall()
        assert len(rows) == 6
        balance = 10000.0
        for r in rows:
            balance += r["amount"] if r["debit_credit"] == "credit" else -r["amount"]
        assert round(balance, 2) == 4200.0


class TestTheDateQuestionOverTheApi:
    EARLY = (
        b"Example Bank\nAccount Number,XXXX0000\n\nDate,Narration,Withdrawal Amt.,Deposit Amt.,Balance\n"
        b"01/10/2026,UPI/A/1,100.00,,24900.00\n02/10/2026,UPI/B/2,,50.00,24950.00\n03/10/2026,UPI/C/3,10.00,,24940.00\n"
    )

    def test_an_ambiguous_file_is_a_422_that_says_so_and_carries_both_readings(self, api):
        client, conn = api
        pdf = upload(client, "sample_statement.pdf").json()
        got = upload(client, "early.csv", self.EARLY, ask=True)
        assert got.status_code == 422
        body = got.json()
        assert body["needs"] == ["dates"]
        assert body["account_id"] == pdf["account_id"] and body["date_order"] == "dmy"
        assert set(body["parsed"]["date_choices"]) == {"dmy", "mdy"}
        assert counts(conn)["statements"] == 1

    def test_the_answer_lands_it(self, api):
        client, _ = api
        upload(client, "sample_statement.pdf")
        got = upload(client, "early.csv", self.EARLY, ask=True, date_order="mdy", confirm_overlap="true")
        assert got.status_code == 200, got.text

    def test_the_preview_follows_the_chosen_order(self, api):
        client, _ = api
        mapping = json.dumps({"date": 0, "description": 1, "debit": 2, "credit": 3, "balance": 4})

        def preview(**fields):
            return client.post(
                "/api/statements/upload/preview",
                files={"file": ("early.csv", self.EARLY, "text/csv")}, data={"mapping": mapping, **fields},
            ).json()

        assert preview()["rows"][0]["date"] == "2026-10-01"
        assert set(preview()["date_choices"]) == {"dmy", "mdy"}
        assert preview(date_order="mdy")["rows"][0]["date"] == "2026-01-10"


class TestHostileFiles:
    def test_a_zip_this_python_cannot_open_is_a_422_in_words_not_a_500(self, api):
        client, conn = api
        data = bytearray((FIXTURES / "sample_statement.xlsx").read_bytes())
        for signature, at in ((b"PK\x03\x04", 4), (b"PK\x01\x02", 6)):
            i = data.find(signature)
            while i != -1:
                data[i + at] = 128
                i = data.find(signature, i + 4)
        got = upload(client, "broken.xlsx", bytes(data))
        assert got.status_code == 422 and "workbook couldn't be opened" in got.json()["detail"]
        assert counts(conn)["statements"] == 0

    def test_the_preview_refuses_the_same_files_the_upload_does(self, api):
        client, _ = api
        too_wide = (b"Date,Narration,Amount," + b",".join([b"x"] * 70) + b"\n" + b"01/10/2026,a,1.00," + b",".join([b"1"] * 70) + b"\n")
        got = client.post(
            "/api/statements/upload/preview",
            files={"file": ("wide.csv", too_wide, "text/csv")}, data={"mapping": json.dumps({"date": 0, "description": 1, "amount": 2})},
        )
        assert got.status_code == 422 and "more than 64 columns" in got.json()["detail"]

    def test_an_xls_does_not_write_to_standard_output(self, capsys):
        from finance_copilot.parsers import spreadsheet as sheet

        sheet.read_table((FIXTURES / "sample_statement.xls").read_bytes())
        assert capsys.readouterr().out == ""


class TestWhatTheFormIsTold:
    def test_the_raw_preview_keeps_the_last_four_of_a_number_outside_the_description_column(self, api):
        client, _ = api
        text = (
            b"Date,Narration,Moved,Left,Ref\n"
            b"2026-10-01,NEFT/50100123456789/RENT,-100.00,900.00,50100123456789\n"
            b"2026-10-02,UPI/4001122/X,50.00,950.00,4001122\n"
        )
        body = upload(client, "odd.csv", text).json()
        assert body["guess"]["description"] == 1
        cells = [c for row in body["preview"] for c in row]
        # The description is shown whole, like the read preview below it.
        assert "NEFT/50100123456789/RENT" in cells
        assert body["preview"][0][4] == "XXXXXXXXXX6789"  # any other column keeps four digits
        assert "4001122" in " ".join(cells)  # seven digits: not a number to hide
        assert "900.00" in cells and "-100.00" in cells  # figures stay
        assert "50100123456789" not in json.dumps(body["preview"][0][4])

    def test_a_held_copy_says_all_of_its_payments_and_lists_the_accounts(self, api):
        client, _ = api
        pdf = upload(client, "sample_statement.pdf").json()
        stripped = (FIXTURES / "sample_statement.csv").read_text().replace("XXXX0000", "")
        # No balance column and shortened narration: nothing matches, over the same days.
        lines = ["Date,Narration,Withdrawal Amt.,Deposit Amt."]
        import csv as _csv
        import io as _io
        for row in _csv.reader(_io.StringIO(stripped)):
            if row and row[0][:2].isdigit() and "/" in row[0]:
                lines.append(",".join([row[0], row[1][:8], row[2].replace(",", ""), row[3].replace(",", "")]))
        body = upload(client, "copy.csv", "\n".join(lines).encode()).json()
        if body["needs"] == ["account"]:  # no number in this copy: say which account first
            body = upload(client, "copy.csv", "\n".join(lines).encode(), account_id=pdf["account_id"]).json()
        assert body["needs"] == ["overlap"]
        assert "all 8 of its payments on those days match nothing there" in body["detail"]
        assert body["account_id"] == pdf["account_id"]
        assert [a["id"] for a in body["accounts"]] == [pdf["account_id"]]


class TestMovingCopiesAtTheEdgesOfAFile:
    """Move uses the moved statement's printed period as the days it covers, as an
    upload does: its copy of the stored payment of the 10th, printed on the 11th, is
    that payment when the statement prints a period that includes the 10th."""

    ONE = TestMovingAReversalAtAFileBoundary.ONE
    COPIES = [
        "11/10/2026,UPI/GROCER/1 V,500.00,,9500.00",
        "13/10/2026,UPI/CAFE/2 V,400.00,,9100.00",
        "15/10/2026,UPI/RAVI/3/SPLIT V,,100.00,9200.00",
        "16/10/2026,UPI/SHOP/4/FAILED,100.00,,9100.00",
        "17/10/2026,UPI/SHOP/4/REVERSAL,,100.00,9200.00",
    ]

    def _already_there(self, api, period):
        client, _ = api
        head = "Example Bank\nAccount Number,XXXX9999\n" + period + "\nDate,Narration,Withdrawal Amt.,Deposit Amt.,Balance\n"
        one = upload(client, "one.csv", self.ONE.encode(), new_account_name="Main").json()
        two = upload(client, "two.csv", (head + "\n".join(self.COPIES)).encode(), new_account_name="Other").json()
        moved = client.patch(f"/api/statements/{two['id']}", json={"account_id": one["account_id"]})
        assert moved.status_code == 200, moved.text
        return moved.json()["rows_already_there"]

    def test_a_printed_period_that_includes_the_stored_day_makes_the_copy_a_duplicate(self, api):
        assert self._already_there(api, "Period: 01/10/2026 to 31/10/2026") == 3

    def test_without_a_printed_period_the_copy_before_the_first_payment_is_kept(self, api):
        assert self._already_there(api, "") == 2
