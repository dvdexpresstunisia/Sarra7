import unittest

import numpy as np

from core.echo import EchoGuard, band_energies


class EchoGuardTests(unittest.TestCase):
    def test_band_energies_accepts_sounddevice_mono_callback_shape(self):
        sample_time = np.arange(1024, dtype=np.float32) / 16000
        samples = (1000 * np.sin(2 * np.pi * 440 * sample_time))
        samples = samples.astype(np.int16).reshape(-1, 1)

        energies = band_energies(samples, 16000)

        self.assertEqual(energies.shape, (8,))
        self.assertTrue(np.isfinite(energies).all())

    def test_sustained_voice_in_callback_shape_triggers_barge_in(self):
        guard = EchoGuard()
        output_time = np.arange(1024, dtype=np.float32) / 24000
        output = (12000 * np.sin(2 * np.pi * 440 * output_time))
        output = output.astype(np.int16)
        silence = np.zeros((1024, 1), dtype=np.int16)
        voice_time = np.arange(1024, dtype=np.float32) / 16000
        voice = (12000 * np.sin(2 * np.pi * 1400 * voice_time))
        voice = voice.astype(np.int16).reshape(-1, 1)

        for _ in range(20):
            guard.note_output(output, 24000, 0.8)
            guard.is_barge_in_speech(silence, 16000, 0.0)

        interruptions = []
        for _ in range(guard.required_blocks):
            guard.note_output(output, 24000, 0.8)
            interruptions.append(
                guard.is_barge_in_speech(voice, 16000, 0.7)
            )

        self.assertTrue(interruptions[-1])


if __name__ == "__main__":
    unittest.main()
