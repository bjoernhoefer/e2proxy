from types import SimpleNamespace

from e2proxy import extensions as ext


class FakeHandler:
    def __init__(self, path):
        self.path, self.client_address, self.sent = path, ("1.2.3.4", 0), None

    def send_json(self, data, status=200):
        self.sent = ("json", data, status)

    def send_html(self, html, status=200):
        self.sent = ("html", html, status)


def test_exact_prefix_and_status_tuple():
    ext.route("GET", "/t/exact")(lambda r: {"q": r.arg("a")})
    ext.route("DELETE", "/t/item/")(lambda r: ({"id": r.path.rsplit("/", 1)[1]}, 202))
    ext.route("GET", "/t/page")(lambda r: "<p>hi</p>")
    h = FakeHandler("/t/exact?a=1")
    assert ext.dispatch(h, "GET") and h.sent == ("json", {"q": "1"}, 200)
    h = FakeHandler("/t/item/42")
    assert ext.dispatch(h, "DELETE") and h.sent == ("json", {"id": "42"}, 202)
    h = FakeHandler("/t/page")
    assert ext.dispatch(h, "GET") and h.sent[0] == "html"
    assert not ext.dispatch(FakeHandler("/t/exact"), "POST")


def test_disabled_extension_routes_are_invisible():
    state = SimpleNamespace(on=False)
    ext.register(ext.Extension(name="t-ext", enabled=lambda: state.on))
    ext.route("GET", "/t/ext", "t-ext")(lambda r: {"ok": True})
    assert not ext.dispatch(FakeHandler("/t/ext"), "GET")
    state.on = True
    assert ext.dispatch(FakeHandler("/t/ext"), "GET")


def test_handler_exception_becomes_500():
    def boom(r):
        raise RuntimeError("x")
    ext.route("GET", "/t/boom")(boom)
    h = FakeHandler("/t/boom")
    assert ext.dispatch(h, "GET") and h.sent[2] == 500


def test_start_all_isolates_failures():
    calls = []
    ext.register(ext.Extension(name="t-bad", start=lambda: 1 / 0))
    ext.register(ext.Extension(name="t-good", start=lambda: calls.append(1)))
    ext.start_all()
    ext.start_all()
    assert calls == [1]
