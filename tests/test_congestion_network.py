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
