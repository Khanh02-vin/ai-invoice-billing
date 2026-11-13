"""API địa điểm: gộp theo tên + khoảng cách, và gợi ý OSM không có giá."""
from __future__ import annotations

LAT, LON = 10.0, 106.0


def _post_place(client, headers, *, name="Cà phê Góc", lat=LAT, lon=LON, **extra):
    body = {"name": name, "category": "cafe", "lat": lat, "lon": lon, **extra}
    response = client.post("/places", json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_same_name_within_50m_is_one_place(client, make_user):
    """Cùng tên, cách nhau ~40 m → cùng một quán."""
    _, headers = make_user("an")
    first = _post_place(client, headers)
    second = _post_place(client, headers, lat=LAT + 0.00036)

    assert first["created"] is True
    assert second["created"] is False
    assert second["place"]["id"] == first["place"]["id"]


def test_same_name_500m_apart_is_two_places(client, make_user):
    """~500 m thì phải là hai quán khác nhau (bản cũ gộp theo round(lat, 3))."""
    _, headers = make_user("an")
    first = _post_place(client, headers)
    second = _post_place(client, headers, lat=LAT + 0.0045)

    assert second["created"] is True
    assert second["place"]["id"] != first["place"]["id"]


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_nearby_osm_results_are_unpriced(client, make_user, monkeypatch):
    """OSM không có giá: kết quả phải là ``priced: false`` và bị lọc theo bán kính."""
    _, headers = make_user("an")
    payload = [
        {"name": "Quán Bụi", "type": "restaurant", "lat": str(LAT + 0.001), "lon": str(LON)},
        {"name": "Xa quá", "type": "cafe", "lat": str(LAT + 1.0), "lon": str(LON)},
    ]
    monkeypatch.setattr("src.places_osm.httpx.get", lambda *a, **k: _FakeResponse(payload))

    response = client.get(
        "/places/nearby",
        params={"lat": LAT, "lon": LON, "radius_m": 2000},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "osm"
    assert [place["name"] for place in body["places"]] == ["Quán Bụi"]
    assert all(place["priced"] is False for place in body["places"])
