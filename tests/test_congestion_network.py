from lab.congestion import network as n


def test_parse_maxspeed():
    assert n.parse_maxspeed("30 mph") == 30 * 1.609344
    assert n.parse_maxspeed("50") == 50
    assert n.parse_maxspeed("48 km/h") == 48
    assert n.parse_maxspeed("national") is None and n.parse_maxspeed(None) is None


def test_directions():
    assert n._directions({"oneway": "yes"}) == (True, False)
    assert n._directions({"oneway": "-1"}) == (False, True)
    assert n._directions({"junction": "roundabout"}) == (True, False)
    assert n._directions({"highway": "motorway"}) == (True, False)
    assert n._directions({"highway": "primary"}) == (True, True)


def test_day_types():
    import datetime as dt
    from lab.congestion import webtris as w
    cal = {"terms": [["2025-09-02", "2025-10-24"]], "bank_holidays": ["2025-09-10"]}
    d = w.day_types(cal, dt.date(2025, 9, 1), dt.date(2025, 9, 14))
    assert d[dt.date(2025, 9, 9)] == "neutral"          # Tuesday in term
    assert d[dt.date(2025, 9, 10)] == "weekday_other"   # bank holiday
    assert d[dt.date(2025, 9, 12)] == "weekday_other"   # Friday
    assert d[dt.date(2025, 9, 13)] == "weekend"
    assert d[dt.date(2025, 9, 2)] == "neutral"


def test_site_table_keeps_mainline_only(tmp_path):
    import json
    from lab.congestion import webtris as w
    sites = [
        {"Id": "1", "Name": "M5/8291A", "Description": "MIDAS site at M5/8291A priority 1 on link 1; GPS Ref: 1;2; Northbound", "Longitude": -2.6, "Latitude": 51.5, "Status": "Active"},
        {"Id": "2", "Name": "5261/1", "Description": "TMU Site 5261/1 on M5 J21 northbound exit; GPS Ref: 1;2; Northbound", "Longitude": -2.6, "Latitude": 51.5, "Status": "Active"},
        {"Id": "3", "Name": "5284/2", "Description": "TMU Site 5284/2 on M4 eastbound between J22 and J21; GPS Ref: 1;2; Eastbound", "Longitude": -2.6, "Latitude": 51.5, "Status": "Active"},
        {"Id": "4", "Name": "x", "Description": "TMU Site x on M4; Carriageway Connector", "Longitude": -2.6, "Latitude": 51.5, "Status": "Active"},
    ]
    p = tmp_path / "s.json"
    p.write_text(json.dumps(sites))
    t = w.site_table(p, (-3, 51, -2, 52))
    assert [(s["site_id"], s["road"], s["kind"]) for s in t] == [("1", "M5", "midas"), ("3", "M4", "tmu")]
