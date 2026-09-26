"""Play queue: order, shuffle without repeats, repeat modes and "play next". No GTK here."""
import random

SHUFFLE_OFF, SHUFFLE_TRACKS, SHUFFLE_ALBUMS = "off", "tracks", "albums"
REPEAT_OFF, REPEAT_ALL, REPEAT_ONE = "off", "all", "one"
REPEAT_CYCLE = {REPEAT_OFF: REPEAT_ALL, REPEAT_ALL: REPEAT_ONE, REPEAT_ONE: REPEAT_OFF}


class PlayQueue:
    """Entries have ids so the same file can appear twice and "unshuffle" restores the real order."""

    def __init__(self, rng=None):
        self.rng = rng or random.Random()
        self.paths = {}          # entry id -> path
        self.order = []          # entry ids in play order
        self.original = []       # entry ids in the order they were added
        self.index = -1
        self.shuffle = SHUFFLE_OFF
        self.repeat = REPEAT_OFF
        self.group_of = None     # path -> album key, for album shuffle
        self._ids = 0

    def __len__(self):
        return len(self.order)

    def _new(self, paths):
        ids = []
        for path in paths:
            self._ids += 1
            self.paths[self._ids] = str(path)
            ids.append(self._ids)
        return ids

    @property
    def current(self):
        return self.paths[self.order[self.index]] if 0 <= self.index < len(self.order) else None

    def items(self):
        return [self.paths[entry] for entry in self.order]

    def upcoming(self):
        return [(position, self.paths[self.order[position]]) for position in range(self.index + 1, len(self.order))]

    def replace(self, paths, start=0):
        """Play a new list (an album, a search result...) starting at `start`."""
        self.paths.clear()
        self.order = self._new(paths)
        self.original = list(self.order)
        self.index = min(max(0, start), len(self.order) - 1) if self.order else -1
        if self.shuffle != SHUFFLE_OFF and self.order:
            # The chosen track plays first; everything else is shuffled after it.
            self.order.insert(0, self.order.pop(self.index))
            self.index = 0
            self._shuffle_after(0)
        return self.current

    def _shuffled(self, entries):
        if self.shuffle == SHUFFLE_ALBUMS and self.group_of:
            groups = {}
            for entry in entries:
                groups.setdefault(self.group_of(self.paths[entry]), []).append(entry)
            blocks = list(groups.values())
            self.rng.shuffle(blocks)
            return [entry for block in blocks for entry in block]
        entries = list(entries)
        self.rng.shuffle(entries)  # Fisher–Yates: a permutation, so nothing repeats before the end.
        return entries

    def _shuffle_after(self, position):
        """Keep what already played and the current track; shuffle only what comes next."""
        if self.shuffle == SHUFFLE_ALBUMS and self.group_of and 0 <= position < len(self.order):
            # Finish the current album first, then shuffle the other albums.
            current = self.order[position]
            album = self.group_of(self.paths[current])
            rest = self.order[position + 1:]
            where = {entry: n for n, entry in enumerate(self.original)}
            same = sorted((e for e in rest if self.group_of(self.paths[e]) == album
                           and where[e] > where[current]), key=where.get)
            other = [e for e in rest if e not in same]
            self.order = self.order[:position + 1] + same + self._shuffled(other)
            return
        # Move the current entry to `position` then shuffle the remainder.
        head, rest = self.order[:position + 1], self.order[position + 1:]
        self.order = head + self._shuffled(rest)

    def set_shuffle(self, mode, group_of=None):
        if mode not in (SHUFFLE_OFF, SHUFFLE_TRACKS, SHUFFLE_ALBUMS):
            raise ValueError(mode)
        self.group_of = group_of or self.group_of
        current = self.order[self.index] if 0 <= self.index < len(self.order) else None
        self.shuffle = mode
        if mode == SHUFFLE_OFF:
            self.order = list(self.original)
            self.index = self.order.index(current) if current is not None else -1
        elif self.order:
            if current is None:
                self.order = self._shuffled(self.original)
                self.index = 0
            else:
                played = self.order[:self.index]
                others = [e for e in self.original if e != current and e not in played]
                self.order = played + [current] + others
                self._shuffle_after(self.index)

    def set_repeat(self, mode):
        if mode not in REPEAT_CYCLE:
            raise ValueError(mode)
        self.repeat = mode

    def advance(self, automatic=True):
        """Next path to play, or None to stop. `automatic` is True when a track ended by itself."""
        if not self.order:
            return None
        if automatic and self.repeat == REPEAT_ONE and self.current:
            return self.current
        if self.index + 1 < len(self.order):
            self.index += 1
            return self.current
        if automatic and self.repeat == REPEAT_OFF:
            self.index = len(self.order)
            return None
        # Wrap around: a new shuffle order each lap, avoiding the track that just played.
        last = self.order[-1] if self.order else None
        if self.shuffle != SHUFFLE_OFF and len(self.order) > 1:
            self.order = self._shuffled(self.original)
            if self.order[0] == last:
                self.order.append(self.order.pop(0))
        self.index = 0
        return self.current

    def peek(self):
        """What `advance(automatic=True)` would return, without moving (used for gapless)."""
        if not self.order:
            return None
        if self.repeat == REPEAT_ONE:
            return self.current
        if self.index + 1 < len(self.order):
            return self.paths[self.order[self.index + 1]]
        if self.repeat == REPEAT_ALL and self.shuffle == SHUFFLE_OFF:
            return self.paths[self.order[0]]
        return None

    def back(self):
        if not self.order:
            return None
        if self.index > 0:
            self.index -= 1
        elif self.repeat == REPEAT_ALL:
            self.index = len(self.order) - 1
        else:
            self.index = 0
        return self.current

    def jump(self, position):
        if not 0 <= position < len(self.order):
            raise IndexError(position)
        self.index = position
        return self.current

    def play_next(self, paths):
        ids = self._new(paths)
        at = self.index + 1
        self.order[at:at] = ids
        if 0 <= self.index < len(self.order) - len(ids):
            anchor = self.original.index(self.order[self.index]) + 1
        else:
            anchor = len(self.original)
        self.original[anchor:anchor] = ids
        if self.index < 0:
            self.index = 0
        return ids

    def enqueue(self, paths):
        ids = self._new(paths)
        self.order.extend(ids)
        self.original.extend(ids)
        if self.index < 0 and self.order:
            self.index = 0
        return ids

    def move(self, source, target):
        """Drag-and-drop reorder; the order the user chose becomes the "unshuffled" order too."""
        if not (0 <= source < len(self.order) and 0 <= target < len(self.order)):
            raise IndexError(source)
        current = self.order[self.index] if 0 <= self.index < len(self.order) else None
        entry = self.order.pop(source)
        self.order.insert(target, entry)
        if current is not None:
            self.index = self.order.index(current)
        self.original = list(self.order)

    def remove(self, position):
        if position == self.index:
            raise ValueError("A faixa atual não pode ser removida da fila.")
        entry = self.order.pop(position)
        self.original.remove(entry)
        del self.paths[entry]
        if position < self.index:
            self.index -= 1

    def remove_path(self, path):
        """Drop every upcoming or played entry of a file that left the library (not the current one)."""
        for position in reversed(range(len(self.order))):
            if self.paths[self.order[position]] == path and position != self.index:
                self.remove(position)

    def clear_upcoming(self):
        dropped = self.order[self.index + 1:]
        self.order = self.order[:self.index + 1]
        self.original = [e for e in self.original if e not in set(dropped)]
        for entry in dropped:
            del self.paths[entry]

    def to_dict(self):
        position = {entry: n for n, entry in enumerate(self.order)}
        return {"paths": self.items(), "original": [position[e] for e in self.original],
                "index": self.index, "shuffle": self.shuffle, "repeat": self.repeat}

    def restore(self, data, exists=lambda path: True):
        """Rebuild a saved queue, skipping files that disappeared meanwhile."""
        paths = data.get("paths") or []
        current = paths[data.get("index", -1)] if 0 <= data.get("index", -1) < len(paths) else None
        keep = [n for n, path in enumerate(paths) if exists(path)]
        self.paths.clear()
        ids = self._new([paths[n] for n in keep])
        by_old = dict(zip(keep, ids))
        self.order = ids
        original = [by_old[n] for n in data.get("original", []) if n in by_old]
        self.original = original if len(original) == len(ids) else list(ids)
        self.shuffle = data.get("shuffle", SHUFFLE_OFF) if data.get("shuffle") in (SHUFFLE_OFF, SHUFFLE_TRACKS, SHUFFLE_ALBUMS) else SHUFFLE_OFF
        self.repeat = data.get("repeat", REPEAT_OFF) if data.get("repeat") in REPEAT_CYCLE else REPEAT_OFF
        old_index = data.get("index", -1)
        self.index = ids.index(by_old[old_index]) if old_index in by_old else (0 if ids else -1)
        return current if old_index in by_old else None
