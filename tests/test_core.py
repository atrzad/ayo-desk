import math
import os
from pathlib import Path
import tempfile
import unittest
from ayo_desk.core import Calculator, Store


class CalculatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.calc = Calculator()

    def test_precedence_and_scientific_functions(self):
        examples = {"2+3*4": 14, "(2+3)*4": 20, "2^3^2": 512, "-2^2": -4,
                    "2^-2": .25, "sqrt(144)+log(100)+ln(e)": 15, "sin(pi/2)": 1,
                    "cos(0)": 1, "200*10%": 20, "1,5 × 4": 6, "1e3/2": 500,
                    "abs(-9)": 9, "floor(2.9)+ceil(2.1)": 5}
        for expression, expected in examples.items():
            with self.subTest(expression=expression):
                self.assertAlmostEqual(self.calc.evaluate(expression), expected)

    def test_malformed_and_unsafe_input(self):
        for expression in ("", "1/0", "sqrt(-1)", "ln(0)", "1e309", "2^1024", "1+", "(2+3",
                           "2 3", "unknown(5)", "__import__('os')", "1;echo hi", "1\0+2",
                           "(" * 200 + "1" + ")" * 200, "-" * 200 + "1", "1" * 4097):
            with self.subTest(expression=expression[:30]):
                with self.assertRaises(ValueError):
                    self.calc.evaluate(expression)

    def test_results_survive_a_previous_error(self):
        with self.assertRaises(ValueError):
            self.calc.evaluate("1/0")
        self.assertEqual(self.calc.evaluate("6*7"), 42)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "data.sqlite3"
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_event_lifecycle_and_persistence(self):
        event = self.store.save_event("2026-09-20", "09:05", "Reunião d'Ávila", "Levar notas\nSegunda linha")
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.events("2026-09-20")[0]["title"], "Reunião d'Ávila")
        self.store.save_event("2026-09-21", "", "Dia inteiro", event_id=event)
        self.assertEqual(len(self.store.events("2026-09-20")), 0)
        self.assertEqual(self.store.event_days(2026, 9), [21])
        self.store.delete_event(event)
        self.assertEqual(self.store.event_days(2026, 9), [])
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_invalid_event_is_not_stored(self):
        for day, time, title in (("2026-02-30", "", "x"), ("2026-09-20", "24:00", "x"),
                                 ("2026-09-20", "", "  ")):
            with self.assertRaises(ValueError):
                self.store.save_event(day, time, title)
        self.assertEqual(self.store.events("2026-09-20"), [])

    def test_bounded_and_separate_histories(self):
        self.store.log("calculator", "1+1", "2")
        for i in range(220):
            self.store.log("network", f"Network {i}")
        self.assertEqual(len(self.store.history("network")), 100)
        self.assertEqual(self.store.history("network")[0]["title"], "Network 219")
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM history WHERE category='network'").fetchone()[0], 200)
        self.store.clear_history("network")
        self.assertEqual(self.store.history("network"), [])
        self.assertEqual(len(self.store.history("calculator")), 1)

    def test_playlist_deduplicates_and_never_deletes_music(self):
        music = Path(self.tmp.name) / "song ' with spaces.ogg"
        music.write_bytes(b"sample")
        self.store.add_tracks([music, music])
        self.assertEqual(self.store.tracks(), [str(music)])
        self.store.remove_track(str(music))
        self.assertEqual(self.store.tracks(), [])
        self.assertTrue(music.exists())


if __name__ == "__main__":
    unittest.main()
