from backend.automation.queue_builder import build_queue


def test_niche_outer_location_inner_ordering():
    locs = [{"city": "New York", "state": "NY"}, {"city": "LA", "state": "CA"}]
    niches = ["Hair Salon", "Barber Shop"]
    q = build_queue(locs, niches)
    assert [(i["niche"], i["city"]) for i in q] == [
        ("Hair Salon", "New York"), ("Hair Salon", "LA"),
        ("Barber Shop", "New York"), ("Barber Shop", "LA"),
    ]
    assert [i["position"] for i in q] == [0, 1, 2, 3]


def test_count_is_product():
    q = build_queue([{"city": f"c{i}", "state": None} for i in range(4)], ["a", "b", "c"])
    assert len(q) == 12


def test_empty_inputs_give_empty_queue():
    assert build_queue([], ["a"]) == []
    assert build_queue([{"city": "x", "state": None}], []) == []
