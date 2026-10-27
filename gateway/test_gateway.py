"""gateway 브리지 로직 단위 테스트 (HTTP 왕복 없이 라우팅/환산만).

실행:  python -m unittest   (gateway/ 디렉터리에서; 외부 의존성 불필요 —
gateway.py 는 aiohttp 를 실행 시점에만 import 한다)
"""

import asyncio
import copy
import tempfile
import unittest
from pathlib import Path

import gateway
import shelfcoap
from shelfcoap import Message

HWID = "1e0582a6f932714e"
ADDR = ("fd11:22::1", 49152, 0, 0)


class BatteryPercentTest(unittest.TestCase):
    def test_measurement_failure_is_none(self):
        self.assertIsNone(gateway.battery_percent(None))
        self.assertIsNone(gateway.battery_percent(-1))

    def test_usb_power_is_none(self):
        # 4.4 V 초과 = USB 전원 (Q5 가 배터리 분리) — 펌웨어와 같은 기준
        self.assertIsNone(gateway.battery_percent(4855))
        self.assertIsNone(gateway.battery_percent(4401))

    def test_linear_estimate(self):
        self.assertEqual(gateway.battery_percent(4200), 100)
        self.assertEqual(gateway.battery_percent(3750), 50)
        self.assertEqual(gateway.battery_percent(3300), 0)

    def test_clamped(self):
        self.assertEqual(gateway.battery_percent(3000), 0)
        self.assertEqual(gateway.battery_percent(4300), 100)


class ParseReportTest(unittest.TestCase):
    def test_full_report(self):
        payload = (b'{"id":"' + HWID.encode() +
                   b'","seq":12,"raw":1140384,"mg":11403840,"mv":4855,'
                   b'"md":"a"}')
        self.assertEqual(gateway.parse_report(payload), {
            "id": HWID, "seq": 12, "raw": 1140384,
            "mg": 11403840, "mv": 4855, "mode": "active",
        })

    def test_optional_fields_missing(self):
        report = gateway.parse_report(b'{"id":"abc","mg":-1500}')
        self.assertEqual(report["mg"], -1500)
        self.assertIsNone(report["seq"])
        self.assertIsNone(report["mv"])
        self.assertIsNone(report["mode"])   # 구펌웨어: md 없음

    def test_mode_field(self):
        self.assertEqual(
            gateway.parse_report(b'{"id":"x","mg":1,"md":"i"}')["mode"],
            "idle")
        # 모르는 md 값은 None (미래 확장에 관대하게)
        self.assertIsNone(
            gateway.parse_report(b'{"id":"x","mg":1,"md":"z"}')["mode"])
        self.assertIsNone(
            gateway.parse_report(b'{"id":"x","mg":1,"md":3}')["mode"])

    def test_rejects_bad_payloads(self):
        for bad in (b"", b"not json", b"[1,2]", b'{"mg":1}',
                    b'{"id":"","mg":1}', b'{"id":"abc"}',
                    b'{"id":"abc","mg":"heavy"}', b'{"id":"abc","mg":true}'):
            with self.assertRaises(ValueError, msg=repr(bad)):
                gateway.parse_report(bad)


def make_gateway(**node_map):
    cfg = copy.deepcopy(gateway.DEFAULT_CONFIG)
    cfg["nodes"] = node_map
    gw = gateway.Gateway(cfg)
    # HTTP 를 건드리는 백그라운드 작업은 이름만 기록하고 버린다.
    gw.spawned = []

    def fake_spawn(coro):
        gw.spawned.append(coro.cr_code.co_name)
        coro.close()

    gw._spawn = fake_spawn
    return gw


def led_poll(hwid=HWID):
    return Message(type=shelfcoap.CON, code=shelfcoap.GET, mid=1, token=b"t",
                   options=[(shelfcoap.OPTION_URI_PATH, b"shelf"),
                            (shelfcoap.OPTION_URI_PATH, b"led"),
                            (shelfcoap.OPTION_URI_QUERY,
                             f"id={hwid}".encode())])


def weight_post(payload):
    return Message(type=shelfcoap.NON, code=shelfcoap.POST, mid=2, token=b"t",
                   options=[(shelfcoap.OPTION_URI_PATH, b"shelf"),
                            (shelfcoap.OPTION_URI_PATH, b"weight")],
                   payload=payload)


class CoapHandlerTest(unittest.TestCase):
    def handle(self, gw, msg):
        return asyncio.run(gw.coap_handler(msg, ADDR))

    def test_led_poll_registers_node_and_answers_from_cache(self):
        gw = make_gateway(**{HWID: "SHELF-A1"})
        code, payload = self.handle(gw, led_poll())
        self.assertEqual((code, payload), (shelfcoap.CONTENT, b"0"))
        node = gw.nodes[HWID]
        self.assertEqual(node.device_id, "SHELF-A1")   # 매핑 적용
        self.assertEqual(node.polls, 1)
        self.assertEqual(gw.spawned, ["_refresh_led"])  # 첫 LED 값 즉시 조회

        node.led_on = True                              # 캐시가 갱신되면
        _, payload = self.handle(gw, led_poll())
        self.assertEqual(payload, b"1")                 # 다음 폴부터 반영

    def test_unmapped_node_uses_hwid_as_device_id(self):
        gw = make_gateway()
        self.handle(gw, led_poll())
        self.assertEqual(gw.nodes[HWID].device_id, HWID)

    def test_weight_report_converts_and_posts(self):
        gw = make_gateway()
        payload = (b'{"id":"' + HWID.encode() +
                   b'","seq":3,"raw":842317,"mg":8423499,"mv":3750}')
        code, _ = self.handle(gw, weight_post(payload))
        self.assertEqual(code, shelfcoap.CHANGED)
        node = gw.nodes[HWID]
        self.assertEqual(node.grams, 8423)    # round(8423499 / 1000)
        self.assertEqual(node.battery, 50)    # 3750 mV
        self.assertEqual(node.reports, 1)
        self.assertIn("_post_weight", gw.spawned)

    def test_usb_powered_report_keeps_last_battery(self):
        gw = make_gateway()
        self.handle(gw, weight_post(
            b'{"id":"' + HWID.encode() + b'","mg":1000,"mv":3750}'))
        self.handle(gw, weight_post(
            b'{"id":"' + HWID.encode() + b'","mg":2000,"mv":4855}'))
        self.assertEqual(gw.nodes[HWID].battery, 50)  # USB 값으로 덮지 않음

    def test_bad_report_is_rejected(self):
        gw = make_gateway()
        code, _ = self.handle(gw, weight_post(b"not json"))
        self.assertEqual(code, shelfcoap.BAD_REQUEST)
        self.assertEqual(gw.nodes, {})

    def test_led_poll_without_id_is_rejected(self):
        gw = make_gateway()
        msg = led_poll()
        msg.options = [o for o in msg.options
                       if o[0] != shelfcoap.OPTION_URI_QUERY]
        code, _ = self.handle(gw, msg)
        self.assertEqual(code, shelfcoap.BAD_REQUEST)

    def test_wrong_method(self):
        gw = make_gateway()
        msg = led_poll()
        msg.code = shelfcoap.PUT
        code, _ = self.handle(gw, msg)
        self.assertEqual(code, shelfcoap.METHOD_NOT_ALLOWED)

    def test_unknown_path(self):
        gw = make_gateway()
        msg = led_poll()
        msg.options[1] = (shelfcoap.OPTION_URI_PATH, b"nope")
        code, _ = self.handle(gw, msg)
        self.assertEqual(code, shelfcoap.NOT_FOUND)


class ModeBridgeTest(unittest.TestCase):
    """전력 모드 브리지: GET shelf/mode 응답, md 불일치 교정 푸시, 일괄 전환."""

    def handle(self, gw, msg):
        return asyncio.run(gw.coap_handler(msg, ADDR))

    def mode_get(self, hwid=HWID):
        return Message(type=shelfcoap.CON, code=shelfcoap.GET, mid=5, token=b"t",
                       options=[(shelfcoap.OPTION_URI_PATH, b"shelf"),
                                (shelfcoap.OPTION_URI_PATH, b"mode"),
                                (shelfcoap.OPTION_URI_QUERY,
                                 f"id={hwid}".encode())])

    def test_mode_get_answers_current_mode(self):
        gw = make_gateway()
        code, payload = self.handle(gw, self.mode_get())
        self.assertEqual((code, payload), (shelfcoap.CONTENT, b"active"))
        self.assertIn(HWID, gw.nodes)          # 부팅 질의로도 노드 등록

        gw.mode = "idle"
        _, payload = self.handle(gw, self.mode_get())
        self.assertEqual(payload, b"idle")

    def test_report_md_mismatch_triggers_push(self):
        gw = make_gateway()   # gw.mode == "active"
        self.handle(gw, weight_post(
            b'{"id":"' + HWID.encode() + b'","mg":1000,"md":"i"}'))
        self.assertEqual(gw.nodes[HWID].mode, "idle")
        self.assertIn("_push_mode", gw.spawned)

    def test_report_md_match_does_not_push(self):
        gw = make_gateway()
        self.handle(gw, weight_post(
            b'{"id":"' + HWID.encode() + b'","mg":1000,"md":"a"}'))
        self.assertNotIn("_push_mode", gw.spawned)

    def test_old_firmware_without_md_is_left_alone(self):
        gw = make_gateway()
        self.handle(gw, weight_post(
            b'{"id":"' + HWID.encode() + b'","mg":1000}'))
        self.assertIsNone(gw.nodes[HWID].mode)
        self.assertNotIn("_push_mode", gw.spawned)

    def test_mismatch_push_is_rate_limited(self):
        gw = make_gateway()
        report = b'{"id":"' + HWID.encode() + b'","mg":1000,"md":"i"}'
        self.handle(gw, weight_post(report))
        self.assertEqual(gw.spawned.count("_push_mode"), 1)

        # 직후의 두 번째 보고는 재시도 간격에 걸려 푸시하지 않는다.
        self.handle(gw, weight_post(report))
        self.assertEqual(gw.spawned.count("_push_mode"), 1)

        # 재시도 간격이 지난 것으로 만들면 다시 푸시한다.
        gw.nodes[HWID].mode_push_at = 0.0
        self.handle(gw, weight_post(report))
        self.assertEqual(gw.spawned.count("_push_mode"), 2)

    def test_set_mode_pushes_to_alive_nodes(self):
        gw = make_gateway()
        self.handle(gw, led_poll())            # 노드 등록 (addr 있음)
        gw.spawned.clear()

        gw._set_mode("idle")
        self.assertEqual(gw.mode, "idle")
        self.assertEqual(gw.spawned, ["_push_mode"])

        gw.spawned.clear()
        gw._set_mode("idle")                   # 같은 모드는 아무것도 안 함
        self.assertEqual(gw.spawned, [])

    def test_ws_shelf_power_event_switches_mode(self):
        gw = make_gateway()
        self.handle(gw, led_poll())            # 노드 등록 (addr 있음)
        gw.spawned.clear()

        gw._handle_ws_event({"type": "shelf_power", "mode": "idle", "users": 0})
        self.assertEqual(gw.mode, "idle")
        self.assertEqual(gw.spawned, ["_push_mode"])

        gw.spawned.clear()
        gw._handle_ws_event({"type": "shelf_power", "mode": "nonsense"})
        self.assertEqual(gw.mode, "idle")      # 모르는 값은 무시
        self.assertEqual(gw.spawned, [])

    def test_ws_led_update_pushes_to_matching_node(self):
        gw = make_gateway(**{HWID: "SHELF-A1"})
        self.handle(gw, led_poll())
        node = gw.nodes[HWID]
        node.led_known = True                  # 캐시가 있어야 '변화'가 성립
        node.led_on = False
        gw.spawned.clear()

        gw._handle_ws_event({"type": "led_update", "device_id": "SHELF-A1",
                             "data": {"on": True, "time": "12:00:00"}})
        self.assertTrue(node.led_on)
        self.assertEqual(gw.spawned, ["_push_led"])

        gw.spawned.clear()
        gw._handle_ws_event({"type": "led_update", "device_id": "다른선반",
                             "data": {"on": False}})
        self.assertTrue(node.led_on)           # 다른 선반 이벤트는 무시
        self.assertEqual(gw.spawned, [])

        # 브라우저용 이벤트는 조용히 무시된다.
        gw._handle_ws_event({"type": "weight_update", "device_id": "SHELF-A1"})

    def test_led_change_pushes_only_in_active(self):
        # _refresh_led 의 푸시 분기: 캐시가 바뀌었고 active 일 때만.
        async def run(mode, first, second):
            gw = make_gateway()
            gw.mode = mode
            await gw.coap_handler(led_poll(), ADDR)
            gw.spawned.clear()
            node = gw.nodes[HWID]

            class FakeRsp:
                status = 200
                def __init__(self, on): self._on = on
                async def json(self): return {"on": self._on}
                async def __aenter__(self): return self
                async def __aexit__(self, *exc): return False

            class FakeHttp:
                def __init__(self): self.on = first
                def get(self, url): return FakeRsp(self.on)

            gw.http = FakeHttp()
            await gw._refresh_led(node)        # 첫 조회: led_known 아직 없음
            gw.http.on = second
            await gw._refresh_led(node)        # 변화 감지 지점
            return gw.spawned

        self.assertIn("_push_led", asyncio.run(run("active", False, True)))
        self.assertNotIn("_push_led", asyncio.run(run("idle", False, True)))
        self.assertNotIn("_push_led", asyncio.run(run("active", True, True)))


class LoadConfigTest(unittest.TestCase):
    def test_partial_file_merges_over_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gateway.toml"
            path.write_text(
                '[server]\nbase_url = "http://10.0.0.2:8000/"\n'
                '[nodes]\n"aabb" = "SHELF-B2"\n',
                encoding="utf-8")
            cfg = gateway.load_config(path)
        self.assertEqual(cfg["server"]["base_url"], "http://10.0.0.2:8000/")
        self.assertEqual(cfg["server"]["timeout_s"], 3.0)        # 기본값 유지
        self.assertEqual(cfg["coap"]["base_path"], "shelf")      # 기본값 유지
        self.assertEqual(cfg["nodes"], {"aabb": "SHELF-B2"})
        # Gateway 는 base_url 의 후행 슬래시를 정리한다
        self.assertEqual(gateway.Gateway(cfg).base_url, "http://10.0.0.2:8000")


if __name__ == "__main__":
    unittest.main()
