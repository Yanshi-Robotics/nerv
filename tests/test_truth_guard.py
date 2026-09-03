import json

import pytest

from nerv.nerve.body import Observation
from nerv.platform.observe import TruthLeak, assemble


class FakeBody:
    name = "fake"

    def perceive(self):
        return Observation(state={"x": 1}, images=[])


class FakeWorld:
    def __init__(self, offered, payloads):
        self._offered, self._payloads = offered, payloads

    def sensors(self):
        return list(self._offered)

    def sensor(self, name):
        return self._payloads[name]


def test_unadvertised_stream_is_refused():
    with pytest.raises(TruthLeak):
        assemble(FakeBody(), FakeWorld(["camera:top"], {}), ["status"])


def test_truth_json_is_refused_and_sensor_json_is_accepted():
    truth = (json.dumps({"pos": [1, 2, 3], "room": "kitchen"}).encode(), "application/json")
    with pytest.raises(TruthLeak):
        assemble(FakeBody(), FakeWorld(["range:room"], {"range:room": truth}), ["range:room"])
    reading = (json.dumps({"ranges": [1.0, 2.0]}).encode(), "application/json")
    obs = assemble(FakeBody(), FakeWorld(["range:room"], {"range:room": reading}), ["range:room"])
    assert obs.state["ambient"]["range:room"] == {"ranges": [1.0, 2.0]}
