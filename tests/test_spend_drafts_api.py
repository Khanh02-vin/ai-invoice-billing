"""Giao dịch tạm: lưu trước khi mở app ngân hàng, xác nhận sau khi chuyển."""

from __future__ import annotations


def _draft(client, headers, **overrides):
    body = {
        "amount": 120000,
        "merchant_name": "Quán Bụi",
        "bank_bin": "970422",
        "account": "0123456789012",
        "lat": 10.78,
        "lon": 106.7,
        **overrides,
    }
    return client.post("/spends/drafts", json=body, headers=headers)


def test_create_list_and_confirm_draft(client, make_user):
    _, headers = make_user("an")

    created = _draft(client, headers)
    assert created.status_code == 200, created.text
    draft = created.json()["draft"]
    assert draft["amount"] == 120000
    assert draft["lat"] == 10.78
    assert draft["lon"] == 106.7
    assert draft["merchant_name"] == "Quán Bụi"

    listed = client.get("/spends/drafts", headers=headers).json()["drafts"]
    assert [d["id"] for d in listed] == [draft["id"]]

    confirmed = client.post(
        f"/spends/drafts/{draft['id']}/confirm",
        json={"category": "restaurant"},
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    spend = confirmed.json()["spend"]
    assert spend["amount"] == 120000
    assert spend["place_name"] == "Quán Bụi"

    assert client.get("/spends/drafts", headers=headers).json()["drafts"] == []
    spends = client.get("/spends", headers=headers).json()["spends"]
    assert len(spends) == 1
    assert spends[0]["amount"] == 120000


def test_draft_isolated_per_user_and_delete(client, make_user):
    _, headers_a = make_user("an")
    _, headers_b = make_user("binh")
    draft_id = _draft(client, headers_a).json()["draft"]["id"]

    assert client.get("/spends/drafts", headers=headers_b).json()["drafts"] == []
    assert client.delete(f"/spends/drafts/{draft_id}", headers=headers_b).status_code == 404
    assert client.delete(f"/spends/drafts/{draft_id}", headers=headers_a).status_code == 200
    assert client.get("/spends/drafts", headers=headers_a).json()["drafts"] == []
