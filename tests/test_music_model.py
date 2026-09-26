import unittest

from ayo_desk.music import tags
from ayo_desk.music.ui.model import Library, UNKNOWN_ARTIST, VARIOUS, artist_names, duration_text


def track(path, **fields):
    data = tags.empty(path)
    data.update(fields)
    data["search"] = tags.search_text(data)
    return data


def names(store):
    return [store.get_item(n).name for n in range(store.get_n_items())]


class LibraryModelTests(unittest.TestCase):
    def test_untagged_folder_is_one_album_even_with_features(self):
        library = Library()
        library.load([
            track("/m/Divino/01 - Eu Choro.mp3", title="Eu Choro", album="Divino", artist="Venere Vai Venus", track_no=1),
            track("/m/Divino/02 - Medusa.mp3", title="Medusa", album="Divino", artist="Venere Vai Venus, Sotam", track_no=2),
            track("/m/Divino/03 - Ego.mp3", title="Ego", album="Divino", track_no=3),
        ], root="/m")
        self.assertEqual(names(library.albums), ["Divino"])
        album = library.albums.get_item(0)
        self.assertEqual([t.title for t in album.tracks], ["Eu Choro", "Medusa", "Ego"])
        self.assertEqual(album.artist, "Venere Vai Venus")
        self.assertEqual(library.get("/m/Divino/03 - Ego.mp3").display_artist, "Venere Vai Venus",
                         "Faixa sem artista herda o artista do álbum")
        self.assertIn("Sotam", names(library.artists))
        sotam = library.artist_by_name["sotam"]
        self.assertEqual([t.title for t in sotam.tracks], ["Medusa"])
        self.assertEqual([a.name for a in sotam.appears_on], ["Divino"])

    def test_same_album_name_in_two_folders_stays_apart_unless_album_artist_matches(self):
        library = Library()
        library.load([
            track("/m/A/Greatest Hits/1.mp3", album="Greatest Hits", artist="A"),
            track("/m/B/Greatest Hits/1.mp3", album="Greatest Hits", artist="B"),
            track("/m/Box/CD 1/1.mp3", album="Box", album_artist="Banda", disc_no=1),
            track("/m/Box/CD 2/1.mp3", album="Box", album_artist="Banda", disc_no=2),
        ], root="/m")
        self.assertEqual(sorted(names(library.albums)), ["Box", "Greatest Hits", "Greatest Hits"])
        box = next(library.albums.get_item(n) for n in range(4) if library.albums.get_item(n).name == "Box")
        self.assertEqual([t.disc_no for t in box.tracks], [1, 2])

    def test_compilation_unknown_artist_and_folders(self):
        library = Library()
        library.load([
            track("/m/Mix/1.mp3", album="Mix", artist="Um"),
            track("/m/Mix/2.mp3", album="Mix", artist="Dois"),
            track("/m/Mix/3.mp3", album="Mix", artist="Três"),
            track("/m/solta.mp3", title="Solta"),
        ], root="/m")
        mix = next(library.albums.get_item(n) for n in range(2) if library.albums.get_item(n).name == "Mix")
        self.assertEqual(mix.artist, VARIOUS)
        self.assertNotIn(VARIOUS, names(library.artists))
        self.assertEqual(names(library.artists)[-1], UNKNOWN_ARTIST)
        self.assertEqual(names(library.folders), ["Mix", "Pasta principal"])

    def test_reload_keeps_track_objects_and_touch_is_safe(self):
        library = Library()
        library.load([track("/m/a.mp3", title="A")])
        first = library.get("/m/a.mp3")
        library.load([track("/m/a.mp3", title="A2")])
        self.assertIs(library.get("/m/a.mp3"), first)
        self.assertEqual(first.title, "A2")
        library.touch("/m/a.mp3", "/nao/existe.mp3", None)

    def test_helpers(self):
        self.assertEqual(artist_names("Tyler, The Creator"), ["Tyler, The Creator"])
        self.assertEqual(artist_names("Joji feat. Rich Brian"), ["Joji", "Rich Brian"])
        self.assertEqual(duration_text(3725), "1:02:05")
        self.assertEqual(duration_text(65), "1:05")


if __name__ == "__main__":
    unittest.main()
