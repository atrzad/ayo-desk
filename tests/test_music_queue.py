import random
import unittest

from ayo_desk.music.queue import PlayQueue, REPEAT_ALL, REPEAT_ONE, SHUFFLE_ALBUMS, SHUFFLE_OFF, SHUFFLE_TRACKS

TRACKS = [f"/m/{n:02d}.mp3" for n in range(10)]


def queue(seed=1):
    return PlayQueue(random.Random(seed))


class QueueTests(unittest.TestCase):
    def test_plays_in_order_and_stops_at_the_end(self):
        q = queue()
        self.assertEqual(q.replace(TRACKS, start=8), TRACKS[8])
        self.assertEqual(q.advance(), TRACKS[9])
        self.assertIsNone(q.advance())
        self.assertIsNone(q.current)

    def test_manual_next_wraps_but_automatic_end_does_not(self):
        q = queue()
        q.replace(TRACKS, start=9)
        self.assertEqual(q.advance(automatic=False), TRACKS[0])

    def test_repeat_all_and_repeat_one(self):
        q = queue()
        q.replace(TRACKS, start=9)
        q.set_repeat(REPEAT_ALL)
        self.assertEqual(q.peek(), TRACKS[0])
        self.assertEqual(q.advance(), TRACKS[0])
        q.set_repeat(REPEAT_ONE)
        self.assertEqual(q.advance(), TRACKS[0])
        self.assertEqual(q.advance(automatic=False), TRACKS[1], "Próxima manual sai do repetir uma")

    def test_shuffle_never_repeats_a_track_before_all_played(self):
        for seed in range(20):
            q = queue(seed)
            q.set_shuffle(SHUFFLE_TRACKS)
            first = q.replace(TRACKS, start=3)
            self.assertEqual(first, TRACKS[3], "A faixa escolhida toca primeiro")
            played = [first]
            while (path := q.advance()) is not None:
                played.append(path)
            self.assertEqual(sorted(played), TRACKS)

    def test_new_lap_does_not_start_with_the_track_that_just_ended(self):
        for seed in range(30):
            q = queue(seed)
            q.set_shuffle(SHUFFLE_TRACKS)
            q.set_repeat(REPEAT_ALL)
            q.replace(TRACKS[:3])
            last = [q.current] + [q.advance() for _ in range(2)]
            self.assertNotEqual(q.advance(), last[-1])

    def test_unshuffle_restores_original_order_around_current(self):
        q = queue()
        q.replace(TRACKS, start=0)
        q.set_shuffle(SHUFFLE_TRACKS)
        q.advance()
        current = q.current
        q.set_shuffle(SHUFFLE_OFF)
        self.assertEqual(q.current, current)
        self.assertEqual(q.items(), TRACKS)
        self.assertEqual(q.index, TRACKS.index(current))

    def test_album_shuffle_keeps_album_order(self):
        tracks = [f"/m/{album}/{n}.mp3" for album in "ABCD" for n in range(3)]
        q = queue(4)
        q.set_shuffle(SHUFFLE_ALBUMS, group_of=lambda p: p.split("/")[2])
        q.replace(tracks, start=0)
        order = q.items()
        self.assertEqual(order[:3], tracks[:3], "O álbum atual termina antes")
        for start in range(0, 12, 3):
            block = order[start:start + 3]
            self.assertEqual(len({p.split("/")[2] for p in block}), 1)
            self.assertEqual(block, sorted(block))

    def test_play_next_enqueue_move_and_remove(self):
        q = queue()
        q.replace(TRACKS[:3])
        q.play_next(["/x/next.mp3"])
        q.enqueue(["/x/last.mp3"])
        self.assertEqual(q.items(), [TRACKS[0], "/x/next.mp3", TRACKS[1], TRACKS[2], "/x/last.mp3"])
        q.move(4, 1)
        self.assertEqual(q.advance(), "/x/last.mp3")
        with self.assertRaises(ValueError):
            q.remove(q.index)
        q.remove(0)
        self.assertEqual(q.current, "/x/last.mp3")
        q.clear_upcoming()
        self.assertEqual(q.items(), ["/x/last.mp3"])

    def test_same_file_twice_and_remove_path(self):
        q = queue()
        q.replace([TRACKS[0], TRACKS[1], TRACKS[0]])
        q.advance()
        q.remove_path(TRACKS[0])
        self.assertEqual(q.items(), [TRACKS[1]])
        self.assertEqual(q.current, TRACKS[1])

    def test_back_restarts_list_head(self):
        q = queue()
        q.replace(TRACKS, start=1)
        self.assertEqual(q.back(), TRACKS[0])
        self.assertEqual(q.back(), TRACKS[0])

    def test_saved_queue_survives_restart_and_missing_files(self):
        q = queue()
        q.set_shuffle(SHUFFLE_TRACKS)
        q.replace(TRACKS, start=2)
        q.advance()
        q.set_repeat(REPEAT_ALL)
        saved = q.to_dict()
        current = q.current
        gone = next(p for p in TRACKS if p != current)
        restored = queue()
        self.assertEqual(restored.restore(saved, exists=lambda p: p != gone), current)
        self.assertEqual(restored.current, current)
        self.assertEqual(restored.shuffle, SHUFFLE_TRACKS)
        self.assertEqual(restored.repeat, REPEAT_ALL)
        self.assertNotIn(gone, restored.items())
        restored.set_shuffle(SHUFFLE_OFF)
        self.assertEqual(restored.items(), [p for p in TRACKS if p != gone])


if __name__ == "__main__":
    unittest.main()
