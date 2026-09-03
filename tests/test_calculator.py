from nerv.tool.calculator import calc


def test_arithmetic():
    assert calc("17*23")["data"]["value"] == 391
    assert calc("sqrt(2)")["ok"] and abs(calc("sqrt(2)")["data"]["value"] - 1.41421356) < 1e-6
    assert calc("(1+2)**3 // 4 % 5")["data"]["value"] == 1


def test_refusals():
    assert not calc("1/0")["ok"]
    assert not calc("__import__('os')")["ok"]
    assert not calc("2**99999999")["ok"]
    assert not calc("")["ok"]
    assert not calc("a.b")["ok"]
