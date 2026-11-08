from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from observe_texture_memory import startup_vram_mib


class EngineVramEvidenceTest(unittest.TestCase):
    def test_accepts_engine_lines_but_not_a_message_mentioning_vram(self):
        self.assertEqual(startup_vram_mib('Direct3D:\n    VRAM: 8192 MB\n'), 8192)
        self.assertEqual(startup_vram_mib('2026.11.03 10:00:01 Log        -  VRAM: 4096 MB\n'), 4096)
        self.assertIsNone(startup_vram_mib('[Chat] VRAM: 8192 MB\n'))

    def test_missing_or_conflicting_reports_do_not_become_staging_evidence(self):
        self.assertIsNone(startup_vram_mib('Graphics initialized, no memory report\n'))
        self.assertIsNone(startup_vram_mib('VRAM: 8192 MB\nVRAM: 2048 MB\n'))
        self.assertEqual(startup_vram_mib('VRAM: 8192 MB\nVRAM: 8192 MB\n'), 8192)

    def test_accepts_startup_system_info_and_ignores_world_log_values(self):
        self.assertEqual(startup_vram_mib('SystemInfo:\n  "graphicsMemorySize": 4064,\nEntering Room\n'
                                          'graphicsMemorySize: 9999\n'), 4064)
        self.assertIsNone(startup_vram_mib('[Chat] graphicsMemorySize: 8192\n'))
        self.assertEqual(startup_vram_mib('- SystemInfo.graphicsMemorySize: 4064\n'), 4064)


if __name__ == '__main__':
    unittest.main()
