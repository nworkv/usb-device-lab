import random
import unittest
import zlib
from usb_device_lab.mutator import WireMutator

class LegacyWireMutator(WireMutator):
    def apply(self, channel, payload, maximum=65535):
        if payload is None:
            return None
        data = bytearray(payload)
        if len(data) > maximum:
            raise ValueError('response too large')
        if channel not in self.streams:
            self.streams[channel] = random.Random(self.seed ^ zlib.crc32(channel.encode()))
        rng = self.streams[channel]
        if rng.random() < self.probability:
            op = rng.randrange(3)
            p = rng.randrange(len(data)) if data else 0
            if op == 0 and data:
                data[p] ^= 1 << rng.randrange(8)
            elif op == 1 and data:
                del data[p]
            elif len(data) < maximum:
                data[p:p] = bytes([rng.randrange(256)])
        return bytes(data)

class WireCopyRegressionTests(unittest.TestCase):
    def test_legacy_output_and_prng_match(self):
        for probability in (0, 0.05, 0.5, 1):
            old = LegacyWireMutator({'seed': 42, 'probability': probability})
            new = WireMutator({'seed': 42, 'probability': probability})
            for i in range(2000):
                payload = bytes(range(i % 256))
                channel = str(i % 7)
                self.assertEqual(old.apply(channel, payload), new.apply(channel, payload))
            for channel in old.streams:
                self.assertEqual(old.streams[channel].getstate(), new.streams[channel].getstate())

    def test_no_mutation_reuses_immutable_bytes(self):
        payload = b'x' * 65535
        self.assertIs(WireMutator().apply('ep', payload), payload)

    def test_mutable_input_returns_snapshot(self):
        payload = bytearray(b'abc')
        result = WireMutator().apply('ep', payload)
        payload[0] = 0
        self.assertEqual(result, b'abc')
        self.assertIsInstance(result, bytes)

    def test_size_limit_and_none(self):
        mutator = WireMutator()
        self.assertIsNone(mutator.apply('ep', None))
        with self.assertRaises(ValueError):
            mutator.apply('ep', b'ab', maximum=1)
        self.assertEqual(mutator.streams, {})

if __name__ == '__main__':
    unittest.main()
