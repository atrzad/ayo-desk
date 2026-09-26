"""Library views: songs, albums, artists, genres and folders, each with a detail page."""
from gi.repository import Adw, Gio, Gtk, Pango

from .. import tags
from .covers import Cover
from .model import Track, UNKNOWN_ARTIST, VARIOUS, long_duration
from .tracks import ALBUM_COLUMNS, LIBRARY_COLUMNS, TrackTable


def label(text, *classes, **props):
    widget = Gtk.Label(label=text, xalign=0, ellipsize=Pango.EllipsizeMode.END, **props)
    for css in classes:
        widget.add_css_class(css)
    return widget


def count_text(tracks):
    return f"{len(tracks)} música" + ("s" if len(tracks) != 1 else "") + \
        (f" · {long_duration(sum(t.duration for t in tracks))}" if tracks else "")


def play_buttons(on_play, on_shuffle):
    box = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
    play = Gtk.Button(label="Tocar")
    play.set_child(Adw.ButtonContent(icon_name="media-playback-start-symbolic", label="Tocar"))
    play.add_css_class("suggested-action")
    play.add_css_class("pill")
    play.connect("clicked", lambda _b: on_play())
    shuffle = Gtk.Button()
    shuffle.set_child(Adw.ButtonContent(icon_name="media-playlist-shuffle-symbolic", label="Aleatório"))
    shuffle.add_css_class("pill")
    shuffle.connect("clicked", lambda _b: on_shuffle())
    box.append(play)
    box.append(shuffle)
    return box


class Header(Gtk.Box):
    """Cover + title block used at the top of album, artist, genre and folder pages."""

    def __init__(self, cover_size=180, icon="media-optical-cd-audio-symbolic", circular=False):
        super().__init__(spacing=24, margin_top=24, margin_bottom=12, margin_start=24, margin_end=24)
        self.add_css_class("detail-header")
        self.cover = Cover(cover_size, icon, circular)
        self.append(self.cover)
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.CENTER, hexpand=True)
        self.kind = label("", "caption-heading", "dim-label")
        self.title = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.title.add_css_class("title-1")
        self.subtitle = Gtk.Button(halign=Gtk.Align.START)
        self.subtitle.add_css_class("flat")
        self.subtitle.add_css_class("link-button")
        self.subtitle_label = label("", "title-4")
        self.subtitle.set_child(self.subtitle_label)
        self.meta = label("", "dim-label")
        self.actions = Gtk.Box(spacing=8, margin_top=10)
        for widget in (self.kind, self.title, self.subtitle, self.meta, self.actions):
            info.append(widget)
        self.append(info)


class Navigated(Adw.Bin):
    """Adw.NavigationView is final, so views hold one and forward push/pop."""

    def __init__(self):
        super().__init__()
        self.nav = Adw.NavigationView()
        self.set_child(self.nav)

    def add(self, page):
        self.nav.add(page)

    def push(self, page):
        self.nav.push(page)

    def pop_to_tag(self, tag):
        self.nav.pop_to_tag(tag)


class Page(Adw.NavigationPage):
    def __init__(self, title, child, tag=None):
        super().__init__(title=title, child=child)
        if tag:
            self.set_tag(tag)


def scrolled(child):
    window = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
    clamp = Adw.Clamp(maximum_size=1200, tightening_threshold=900, child=child)
    window.set_child(clamp)
    return window


class SongsView(Gtk.Box):
    def __init__(self, controller):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.controller = controller
        top = Gtk.Box(spacing=12, margin_top=18, margin_bottom=12, margin_start=24, margin_end=24)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        titles.append(label("Músicas", "title-1"))
        self.summary = label("", "dim-label")
        titles.append(self.summary)
        top.append(titles)
        top.append(play_buttons(lambda: self.table.play_from(0), lambda: self.table.play_from(0, shuffle=True)))
        self.append(top)
        self.table = TrackTable(controller, controller.library.tracks, LIBRARY_COLUMNS)
        self.table.set_margin_start(12)
        self.table.set_margin_end(12)
        self.append(self.table)
        self.table.sorted.connect("items-changed", lambda *_: self.update_summary())
        self.update_summary()

    def update_summary(self):
        shown = [self.table.sorted.get_item(n) for n in range(self.table.sorted.get_n_items())]
        total = self.controller.library.tracks.get_n_items()
        text = count_text(shown)
        if self.table.terms:
            text = f"{len(shown)} de {total} músicas encontradas"
        self.summary.set_text(text)

    def search(self, text):
        self.table.set_search(text)
        self.update_summary()


TILE = 168


def album_tile():
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    box.cover = Cover(TILE, "media-optical-cd-audio-symbolic")
    box.title = label("", "heading")
    box.subtitle = label("", "dim-label", "caption")
    for widget in (box.cover, box.title, box.subtitle):
        box.append(widget)
    # Keep the labels as wide as the cover so the text lines up with it.
    clamp = Adw.Clamp(maximum_size=TILE, tightening_threshold=TILE, child=box, margin_top=8, margin_bottom=8,
                      margin_start=8, margin_end=8)
    clamp.add_css_class("album-tile")
    clamp.cover, clamp.title, clamp.subtitle = box.cover, box.title, box.subtitle
    return clamp


def bind_album(box, group):
    box.cover.set_key(group.cover)
    box.title.set_text(group.name)
    box.subtitle.set_text(group.subtitle)
    box.set_tooltip_text(f"{group.name}\n{group.subtitle}")


class AlbumGrid(Gtk.GridView):
    def __init__(self, model, on_activate):
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", lambda _f, item: item.set_child(album_tile()))
        factory.connect("bind", lambda _f, item: bind_album(item.get_child(), item.get_item()))
        selection = Gtk.NoSelection(model=model)
        super().__init__(model=selection, factory=factory, max_columns=12, min_columns=1,
                         single_click_activate=True)
        self.add_css_class("album-grid")
        self.connect("activate", lambda _grid, position: on_activate(selection.get_item(position)))


# name: (key, newest/largest first)
SORTS = {
    "Título": (lambda g: (tags.fold(g.name), tags.fold(g.artist)), False),
    "Artista": (lambda g: (tags.fold(g.artist), g.year or 0, tags.fold(g.name)), False),
    "Ano": (lambda g: (g.year or 0, tags.fold(g.name)), True),
    "Adicionados recentemente": (lambda g: (g.added_at or "", tags.fold(g.name)), True),
}


class AlbumsView(Navigated):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        top = Gtk.Box(spacing=12, margin_top=18, margin_bottom=6, margin_start=24, margin_end=24)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        titles.append(label("Álbuns", "title-1"))
        self.summary = label("", "dim-label")
        titles.append(self.summary)
        top.append(titles)
        self.order = Gtk.DropDown.new_from_strings(list(SORTS))
        self.order.set_valign(Gtk.Align.CENTER)
        self.order.set_tooltip_text("Ordenar álbuns")
        self.order.connect("notify::selected", lambda *_: self.sorter.changed(Gtk.SorterChange.DIFFERENT))
        top.append(self.order)
        box.append(top)
        self.sorter = Gtk.CustomSorter.new(self._compare, None)
        self.sorted = Gtk.SortListModel(model=controller.library.albums, sorter=self.sorter)
        grid = AlbumGrid(self.sorted, self.open_album)
        window = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        window.set_child(grid)
        box.append(window)
        self.add(Page("Álbuns", box, "albums"))
        controller.library.albums.connect("items-changed", lambda *_: self.update())
        self.update()

    def _compare(self, a, b, _data=None):
        key, reverse = SORTS[list(SORTS)[self.order.get_selected()]]
        ka, kb = (key(b), key(a)) if reverse else (key(a), key(b))
        return Gtk.Ordering.SMALLER if ka < kb else Gtk.Ordering.LARGER if ka > kb else Gtk.Ordering.EQUAL

    def update(self):
        count = self.controller.library.albums.get_n_items()
        self.summary.set_text(f"{count} álbum" if count == 1 else f"{count} álbuns")

    def open_album(self, group):
        self.push(album_page(self.controller, group))


def album_page(controller, group):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    header = Header()
    header.cover.set_key(group.cover, full=True)
    header.kind.set_text("ÁLBUM")
    header.title.set_text(group.name)
    header.subtitle_label.set_text(group.artist)
    clickable = group.artist not in (VARIOUS, UNKNOWN_ARTIST)
    header.subtitle.set_sensitive(clickable)
    header.subtitle.connect("clicked", lambda _b: controller.open_artist(group.artist))
    header.meta.set_text(" · ".join(filter(None, [str(group.year or ""), count_text(group.tracks)])))
    discs = sorted({t.disc_no or 1 for t in group.tracks})

    def play(shuffle=False):
        controller.play_paths(group.paths(), 0, shuffle=shuffle)
    header.actions.append(play_buttons(play, lambda: play(True)))
    box.append(header)
    for disc in discs:
        members = [t for t in group.tracks if (t.disc_no or 1) == disc]
        if len(discs) > 1:
            box.append(label(f"Disco {disc}", "heading", margin_start=24, margin_top=12, margin_bottom=4))
        store = Gio.ListStore(item_type=Track)
        store.splice(0, 0, members)
        table = TrackTable(controller, store, ALBUM_COLUMNS, scroll=False, sortable=False)
        table.set_margin_start(12)
        table.set_margin_end(12)
        box.append(table)
    box.set_margin_bottom(24)
    return Page(group.name, scrolled(box), "album")


def group_row(icon, circular):
    box = Gtk.Box(spacing=14, margin_top=6, margin_bottom=6, margin_start=12, margin_end=12)
    box.cover = Cover(44, icon, circular)
    texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER, hexpand=True)
    box.title = label("", "heading")
    box.subtitle = label("", "dim-label", "caption")
    texts.append(box.title)
    texts.append(box.subtitle)
    box.append(box.cover)
    box.append(texts)
    box.append(Gtk.Image(icon_name="go-next-symbolic"))
    return box


class GroupList(Navigated):
    """Artists, genres or folders: a searchable list that opens a detail page."""

    def __init__(self, controller, title, model, page_factory, icon, circular=False):
        super().__init__()
        self.controller = controller
        self.page_factory = page_factory
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=18, margin_bottom=6,
                      margin_start=24, margin_end=24)
        top.append(label(title, "title-1"))
        self.summary = label("", "dim-label")
        top.append(self.summary)
        self.entry = Gtk.SearchEntry(placeholder_text=f"Filtrar {title.lower()}", margin_top=8)
        top.append(self.entry)
        box.append(top)
        self.filter = Gtk.CustomFilter.new(self._matches, None)
        filtered = Gtk.FilterListModel(model=model, filter=self.filter)
        self.entry.connect("search-changed", lambda *_: self.filter.changed(Gtk.FilterChange.DIFFERENT))
        factory = Gtk.SignalListItemFactory()

        def setup(_f, item):
            item.set_child(group_row(icon, circular))

        def bind(_f, item):
            row, group = item.get_child(), item.get_item()
            row.cover.set_key(group.cover)
            row.title.set_text(group.name)
            row.subtitle.set_text(group.subtitle)
        factory.connect("setup", setup)
        factory.connect("bind", bind)
        selection = Gtk.NoSelection(model=filtered)
        view = Gtk.ListView(model=selection, factory=factory, single_click_activate=True)
        view.add_css_class("navigation-sidebar")
        view.add_css_class("group-list")
        view.connect("activate", lambda _v, position: self.open(selection.get_item(position)))
        window = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        window.set_child(Adw.Clamp(maximum_size=900, child=view))
        box.append(window)
        self.add(Page(title, box, "list"))
        model.connect("items-changed", lambda *_: self.update(model))
        self.update(model)

    def _matches(self, group, _data=None):
        terms = tags.fold(self.entry.get_text()).split()
        return all(term in tags.fold(group.name) for term in terms)

    def update(self, model):
        self.summary.set_text(f"{model.get_n_items()} no total")

    def open(self, group):
        if group is not None:
            self.push(self.page_factory(self.controller, group))


def tracks_page(controller, group, kind, icon, columns=LIBRARY_COLUMNS, circular=False):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    header = Header(140, icon, circular)
    header.cover.set_key(group.cover)
    header.kind.set_text(kind)
    header.title.set_text(group.name)
    header.subtitle.set_visible(False)
    header.meta.set_text(count_text(group.tracks))
    store = Gio.ListStore(item_type=Track)
    store.splice(0, 0, group.tracks)
    table = TrackTable(controller, store, columns, scroll=False)
    header.actions.append(play_buttons(lambda: table.play_from(0), lambda: table.play_from(0, shuffle=True)))
    box.append(header)
    albums = getattr(group, "albums", []) + getattr(group, "appears_on", [])
    if group.kind == "artist" and albums:
        for title, members in (("Álbuns", group.albums), ("Aparece em", group.appears_on)):
            if not members:
                continue
            box.append(label(title, "title-4", margin_start=24, margin_top=12))
            flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, max_children_per_line=8,
                               min_children_per_line=2, margin_start=16, margin_end=16, activate_on_single_click=True)
            for album in members:
                tile = album_tile()
                bind_album(tile, album)
                tile.group = album
                flow.append(tile)
            flow.connect("child-activated", lambda _f, child: controller.open_album(child.get_child().group))
            box.append(flow)
        box.append(label("Músicas", "title-4", margin_start=24, margin_top=12, margin_bottom=4))
    table.set_margin_start(12)
    table.set_margin_end(12)
    box.append(table)
    box.set_margin_bottom(24)
    return Page(group.name, scrolled(box), group.kind)


def artist_page(controller, group):
    return tracks_page(controller, group, "ARTISTA", "avatar-default-symbolic", circular=True)


def genre_page(controller, group):
    return tracks_page(controller, group, "GÊNERO", "view-grid-symbolic")


def folder_page(controller, group):
    return tracks_page(controller, group, "PASTA", "folder-music-symbolic",
                       columns=("number", "title", "artist", "album", "duration", "plays", "rating"))


def artists_view(controller):
    return GroupList(controller, "Artistas", controller.library.artists, artist_page, "avatar-default-symbolic", True)


def genres_view(controller):
    return GroupList(controller, "Gêneros", controller.library.genres, genre_page, "view-grid-symbolic")


def folders_view(controller):
    return GroupList(controller, "Pastas", controller.library.folders, folder_page, "folder-music-symbolic")


def find_group(store, key):
    for position in range(store.get_n_items()):
        group = store.get_item(position)
        if group.key == key:
            return group
    return None
