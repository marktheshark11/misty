"""End-to-end check of server.py against fake_misty.py. Run: python test_server.py"""
import os
import socket
import subprocess
import sys
import tempfile
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_up(url):
    for _ in range(50):
        try:
            return requests.get(url, timeout=1)
        except requests.RequestException:
            time.sleep(0.1)
    raise RuntimeError(f"{url} did not start")


tmp = tempfile.TemporaryDirectory()
script = os.path.join(tmp.name, "ticker.py")
with open(script, "w") as f:
    f.write("import time\nn = 0\nwhile True:\n    print('tick', n, flush=True)\n    n += 1\n    time.sleep(0.1)\n")

fp, sp = free_port(), free_port()
env = dict(os.environ, MISTY_IP=f"127.0.0.1:{fp}", MISTY_UI_DATA_DIR=tmp.name, MISTY_UI_TEST_BEHAVIOUR=script)
procs = [subprocess.Popen([sys.executable, os.path.join(HERE, "fake_misty.py"), "--port", str(fp)], stdout=subprocess.DEVNULL),
         subprocess.Popen([sys.executable, os.path.join(HERE, "server.py"), "--port", str(sp)], env=env, cwd=HERE,
                          stdout=subprocess.DEVNULL)]
base, fake = f"http://127.0.0.1:{sp}", f"http://127.0.0.1:{fp}"
try:
    wait_up(base + "/api/state")
    wait_up(fake + "/api/battery")

    # proxy
    r = requests.post(base + "/misty/led?x=1", json={"red": 1})
    assert r.status_code == 200 and r.json()["result"] is True
    seen = requests.get(fake + "/api/requests").json()
    assert {"method": "POST", "path": "/api/led?x=1", "body": '{"red": 1}'} in seen, seen
    assert requests.get(base + "/misty/battery").json()["result"]["chargePercent"] == 0.76

    # knobs
    k = requests.get(base + "/api/knobs").json()["knobs"]
    stop = next(x for x in k if x["name"] == "STOP_DISTANCE")
    assert stop["default"] == 2.0 and stop["value"] == 2.0 and stop["help"], stop
    assert next(x for x in k if x["name"] == "PITCH_LIMITS")["default"] == [-40, 25]
    r = requests.put(base + "/api/knobs", json={"values": {"STOP_DISTANCE": 1.5, "PITCH_LIMITS": [-30, 20]}})
    assert r.status_code == 200
    k = {x["name"]: x for x in r.json()["knobs"]}
    assert k["STOP_DISTANCE"]["value"] == 1.5 and k["STOP_DISTANCE"]["default"] == 2.0
    assert k["PITCH_LIMITS"]["value"] == [-30, 20]
    assert requests.put(base + "/api/knobs", json={"values": {"NOPE": 1}}).status_code == 400
    assert requests.put(base + "/api/knobs", json={"values": {"STOP_DISTANCE": "2"}}).status_code == 400
    assert requests.put(base + "/api/knobs", json={"values": {"STOP_DISTANCE": True}}).status_code == 400
    assert requests.put(base + "/api/knobs", json={"values": {"PITCH_LIMITS": [1]}}).status_code == 400
    r = requests.put(base + "/api/knobs", json={"values": {}})
    assert next(x for x in r.json()["knobs"] if x["name"] == "STOP_DISTANCE")["value"] == 2.0

    # security
    r = requests.post(base + "/api/behaviour/stop", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403 and "error" in r.json()
    assert requests.get(base + "/misty/battery", headers={"Host": "evil.example"}).status_code == 403
    assert requests.get(base + "/ui/../server.py").status_code in (403, 404)

    # config
    assert requests.get(base + "/api/config").json() == {}
    assert requests.put(base + "/api/config", json={"a": [1, 2]}).json() == {"ok": True}
    assert requests.get(base + "/api/config").json() == {"a": [1, 2]}
    assert requests.put(base + "/api/config", json=[1]).status_code == 400

    # behaviours
    assert requests.post(base + "/api/behaviour/start", json={"name": "rm -rf"}).status_code == 400
    assert requests.post(base + "/api/behaviour/start", json={"name": "test"}).json() == {"ok": True}
    assert requests.post(base + "/api/behaviour/start", json={"name": "test"}).status_code == 409
    for _ in range(50):
        s = requests.get(base + "/api/state").json()
        if len(s["log"]) >= 2:
            break
        time.sleep(0.1)
    assert s["behaviour"] == "test" and s["log"][0] == "tick 0", s
    assert requests.post(base + "/api/behaviour/stop").json() == {"ok": True}
    s = requests.get(base + "/api/state").json()
    assert s["behaviour"] is None and s["next"] >= 2
    paths = [x["path"] for x in requests.get(fake + "/api/requests").json()]
    assert "/api/drive/stop" in paths and "/api/head" in paths and "/api/faces/detection/stop" in paths, paths
    # stop with nothing running still succeeds
    assert requests.post(base + "/api/behaviour/stop").json() == {"ok": True}
    print("ok")
finally:
    for p in procs:
        p.terminate()
    for p in procs:
        p.wait()
    tmp.cleanup()
