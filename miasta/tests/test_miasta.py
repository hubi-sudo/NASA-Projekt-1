import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import miasta


class IndexTests(unittest.TestCase):
    def test_normalized_difference_keeps_sign_and_handles_zero(self):
        a = np.array([0.3, 0.1, 0.0, np.nan], dtype='float32')
        b = np.array([0.1, 0.3, 0.0, 0.2], dtype='float32')
        out = miasta.nd(a, b)
        np.testing.assert_allclose(out[:2], [0.5, -0.5], rtol=1e-6)
        self.assertTrue(np.isnan(out[2]) and np.isnan(out[3]))

    def test_indices_on_typical_spectra(self):
        r = {i: np.array(v, dtype='float32') for i, v in zip(range(2, 8), [
            [0.02, 0.09, 0.04], [0.04, 0.10, 0.06], [0.02, 0.11, 0.04],
            [0.35, 0.16, 0.02], [0.15, 0.20, 0.01], [0.06, 0.17, 0.005]])}
        ix = miasta.calculate_indices(r)
        self.assertLess(ix['NDBI'][0], 0)
        self.assertGreater(ix['NDBI'][1], 0)
        self.assertGreater(ix['MNDWI'][2], 0)
        self.assertGreater(ix['IBI'][1], ix['IBI'][0])
        self.assertGreater(ix['NDVI'][0], ix['NDVI'][1])

    def test_encode_roundtrip(self):
        lo, hi = miasta.INDICES['NDBI']['range']
        v = np.array([lo, 0.0, hi, 5.0, np.nan], dtype='float32')
        code = miasta.encode(v, lo, hi)
        self.assertEqual(code[0], 1)
        self.assertEqual(code[2], 255)
        self.assertEqual(code[3], 255)
        self.assertEqual(code[4], 0)
        back = lo + (code[1] - 1) / 254 * (hi - lo)
        self.assertAlmostEqual(back, 0.0, delta=(hi - lo) / 254)

    def test_quality_mask(self):
        qa = np.array([66, 1, 66 | 8, 66 | 32, 68], dtype='uint16')
        radsat = np.zeros(5, dtype='uint16')
        np.testing.assert_array_equal(miasta.quality_mask(qa, radsat), [True, False, False, False, True])
        radsat[0] = 1 << 7
        self.assertFalse(miasta.quality_mask(qa, radsat)[0])


if __name__ == '__main__':
    unittest.main()