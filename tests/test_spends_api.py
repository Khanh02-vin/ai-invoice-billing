"""API ghi chi tiêu: số người mặc định, mức chi/người, và quyền xoá."""
from __future__ import annotations

LAT, LON = 10.0, 106.0


def _place(client, headers) -> str:
    response = client.post(
        "/places",
        json={"name": "Cơm Tấm Bụi", "category": "restaurant", "lat": LAT, "lon": LON},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["place"]["id"]


def _spend(client, headers, place_id, *, amount=45_000, **extra):
    body = {"place_id": place_id, "amount": amount, **extra}
    return client.post("/spends", json=body, headers=headers)


def test_party_size_defaults_to_one(client, make_user):
    _, headers = make_user("an")
    place_id = _place(client, headers)

    response = _spend(client, headers, place_id, amount=45_000)

    assert response.status_code == 200, response.text
    spend = response.json()["spend"]
    assert spend["party_size"] == 1
    assert spend["spend_per_person"] == 45_000
    assert spend["spend_label"] == "mức chi ghi nhận"


def test_spend_per_person_is_amount_divided_by_party(client, make_user):
    """250k cho 5 người không phải 'giá 250k' — mức chi/người là 50k."""
    _, headers = make_user("an")
    place_id = _place(client, headers)

    response = _spend(client, headers, place_id, amount=250_000, party_size=5)

    assert response.status_code == 200, response.text
    assert response.json()["spend"]["spend_per_person"] == 50_000


def test_rejects_zero_and_negative_amount(client, make_user):
    _, headers = make_user("an")
    place_id = _place(client, headers)

    assert _spend(client, headers, place_id, amount=0).status_code == 422
    assert _spend(client, headers, place_id, amount=-1_000).status_code == 422


def test_rejects_zero_party_size(client, make_user):
    _, headers = make_user("an")
    place_id = _place(client, headers)

    assert _spend(client, headers, place_id, party_size=0).status_code == 422


def test_user_b_cannot_read_user_a_spends(client, make_user):
    _, headers_a = make_user("an")
    _, headers_b = make_user("binh")
    place_id = _place(client, headers_a)
    assert _spend(client, headers_a, place_id).status_code == 200

    response = client.get("/spends", headers=headers_b)

    assert response.status_code == 200
    assert response.json()["spends"] == []


def test_delete_is_owner_only(client, make_user):
    _, headers_a = make_user("an")
    _, headers_b = make_user("binh")
    place_id = _place(client, headers_a)
    spend_id = _spend(client, headers_a, place_id).json()["spend"]["id"]

    forbidden = client.delete(f"/spends/{spend_id}", headers=headers_b)
    assert forbidden.status_code == 403

    # Người tạo vẫn thấy và xoá được.
    assert client.delete(f"/spends/{spend_id}", headers=headers_a).status_code == 200
    assert client.get("/spends", headers=headers_a).json()["spends"] == []
