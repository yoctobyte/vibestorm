import base64
import unittest


def _be(value: int, size: int) -> bytes:
    return value.to_bytes(size, "big")


class EventQueueDecodeTests(unittest.TestCase):
    def test_decode_enable_simulator_from_python_structure(self) -> None:
        from vibestorm.event_queue.events import (
            EnableSimulatorEvent,
            decode_event_queue_payload,
        )

        payload = {
            "id": 7,
            "events": [
                {
                    "message": "EnableSimulator",
                    "body": {
                        "SimulatorInfo": [
                            {
                                "Handle": _be(0x0102030405060708, 8),
                                "IP": bytes([127, 0, 0, 1]),
                                "Port": 9000,
                                "RegionSizeX": _be(256, 4),
                                "RegionSizeY": _be(256, 4),
                            }
                        ]
                    },
                }
            ],
        }

        batch = decode_event_queue_payload(payload)

        self.assertEqual(batch.ack_id, 7)
        self.assertEqual(len(batch.events), 1)
        event = batch.events[0]
        self.assertIsInstance(event, EnableSimulatorEvent)
        self.assertEqual(event.handle, 0x0102030405060708)
        self.assertEqual(event.ip, "127.0.0.1")
        self.assertEqual(event.port, 9000)
        self.assertEqual(event.region_size_x, 256)
        self.assertEqual(event.region_size_y, 256)

    def test_decode_teleport_finish(self) -> None:
        from vibestorm.event_queue.events import (
            TeleportFinishEvent,
            decode_event_queue_payload,
        )

        payload = {
            "id": 3,
            "events": [
                {
                    "message": "TeleportFinish",
                    "body": {
                        "Info": [
                            {
                                "AgentID": "11111111-2222-3333-4444-555555555555",
                                "LocationID": _be(4, 4),
                                "SimIP": bytes([10, 0, 0, 5]),
                                "SimPort": 13000,
                                "RegionHandle": _be(0xABCD, 8),
                                "SeedCapability": "http://sim/seed",
                                "SimAccess": 13,
                                "TeleportFlags": _be(8, 4),
                                "RegionSizeX": _be(512, 4),
                                "RegionSizeY": _be(512, 4),
                            }
                        ]
                    },
                }
            ],
        }

        event = decode_event_queue_payload(payload).events[0]

        self.assertIsInstance(event, TeleportFinishEvent)
        self.assertEqual(event.sim_ip, "10.0.0.5")
        self.assertEqual(event.sim_port, 13000)
        self.assertEqual(event.region_handle, 0xABCD)
        self.assertEqual(event.seed_capability, "http://sim/seed")
        self.assertEqual(event.sim_access, 13)
        self.assertEqual(event.teleport_flags, 8)
        self.assertEqual(event.region_size_x, 512)

    def test_decode_crossed_region(self) -> None:
        from vibestorm.event_queue.events import (
            CrossedRegionEvent,
            decode_event_queue_payload,
        )

        payload = {
            "events": [
                {
                    "message": "CrossedRegion",
                    "body": {
                        "AgentData": [
                            {
                                "AgentID": "aaaa1111-2222-3333-4444-555555555555",
                                "SessionID": "bbbb1111-2222-3333-4444-555555555555",
                            }
                        ],
                        "Info": [
                            {"LookAt": [1.0, 0.0, 0.0], "Position": [128.0, 129.0, 25.0]}
                        ],
                        "RegionData": [
                            {
                                "RegionHandle": _be(0xFF00, 8),
                                "SeedCapability": "http://new/seed",
                                "SimIP": bytes([192, 168, 1, 2]),
                                "SimPort": 9001,
                                "RegionSizeX": _be(256, 4),
                                "RegionSizeY": _be(256, 4),
                            }
                        ],
                    },
                }
            ]
        }

        batch = decode_event_queue_payload(payload)

        self.assertIsNone(batch.ack_id)
        event = batch.events[0]
        self.assertIsInstance(event, CrossedRegionEvent)
        self.assertEqual(event.sim_ip, "192.168.1.2")
        self.assertEqual(event.position, (128.0, 129.0, 25.0))
        self.assertEqual(event.region_handle, 0xFF00)

    def test_unknown_event_preserved(self) -> None:
        from vibestorm.event_queue.events import UnknownEvent, decode_event_queue_payload

        payload = {"events": [{"message": "SomethingNew", "body": {"x": 1}}]}

        event = decode_event_queue_payload(payload).events[0]

        self.assertIsInstance(event, UnknownEvent)
        self.assertEqual(event.message, "SomethingNew")
        self.assertEqual(event.body, {"x": 1})

    def test_empty_events_list(self) -> None:
        from vibestorm.event_queue.events import decode_event_queue_payload

        batch = decode_event_queue_payload({"id": 1})

        self.assertEqual(batch.ack_id, 1)
        self.assertEqual(batch.events, ())

    def test_non_map_payload_raises(self) -> None:
        from vibestorm.event_queue.events import (
            EventQueueDecodeError,
            decode_event_queue_payload,
        )

        with self.assertRaises(EventQueueDecodeError):
            decode_event_queue_payload([1, 2, 3])

    def test_end_to_end_through_llsd_parser(self) -> None:
        from vibestorm.caps.llsd import parse_xml_value
        from vibestorm.event_queue.events import (
            EnableSimulatorEvent,
            decode_event_queue_payload,
        )

        handle_b64 = base64.b64encode(_be(0x2A, 8)).decode("ascii")
        ip_b64 = base64.b64encode(bytes([127, 0, 0, 1])).decode("ascii")
        size_b64 = base64.b64encode(_be(256, 4)).decode("ascii")
        xml = (
            "<llsd><map>"
            "<key>id</key><integer>5</integer>"
            "<key>events</key><array><map>"
            "<key>message</key><string>EnableSimulator</string>"
            "<key>body</key><map><key>SimulatorInfo</key><array><map>"
            f"<key>Handle</key><binary>{handle_b64}</binary>"
            f"<key>IP</key><binary>{ip_b64}</binary>"
            "<key>Port</key><integer>9000</integer>"
            f"<key>RegionSizeX</key><binary>{size_b64}</binary>"
            f"<key>RegionSizeY</key><binary>{size_b64}</binary>"
            "</map></array></map>"
            "</map></array>"
            "</map></llsd>"
        ).encode("utf-8")

        batch = decode_event_queue_payload(parse_xml_value(xml))

        self.assertEqual(batch.ack_id, 5)
        event = batch.events[0]
        self.assertIsInstance(event, EnableSimulatorEvent)
        self.assertEqual(event.handle, 0x2A)
        self.assertEqual(event.ip, "127.0.0.1")
        self.assertEqual(event.region_size_x, 256)


if __name__ == "__main__":
    unittest.main()
