"""Small JSON list store: in-memory cache, atomic writes, thread-safe."""
import copy
import json
import os
import threading

from e2proxy.logs import log


class JsonList:
    def __init__(self, path):
        self.path = path
        self._lock = threading.RLock()
        self._items = None

    def _load(self):
        if self._items is None:
            try:
                with open(self.path, encoding="utf-8") as f:
                    data = json.load(f)
                self._items = data if isinstance(data, list) else []
            except FileNotFoundError:
                self._items = []
            except (OSError, ValueError) as e:
                log.error(f"{self.path} unlesbar ({e}) — starte leer, Datei bleibt erhalten")
                os.replace(self.path, self.path + ".corrupt")
                self._items = []
        return self._items

    def _save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._items, f, indent=1, ensure_ascii=False)
        os.replace(tmp, self.path)  # atomic: power loss never leaves half a file

    def all(self):
        with self._lock:
            return copy.deepcopy(self._load())

    def get(self, item_id):
        with self._lock:
            return copy.deepcopy(next((x for x in self._load() if x.get("id") == item_id), None))

    def upsert(self, item):
        with self._lock:
            items = self._load()
            for i, x in enumerate(items):
                if x.get("id") == item["id"]:
                    items[i] = copy.deepcopy(item)
                    break
            else:
                items.append(copy.deepcopy(item))
            self._save()

    def update(self, item_id, **changes):
        with self._lock:
            for x in self._load():
                if x.get("id") == item_id:
                    x.update(changes)
                    self._save()
                    return copy.deepcopy(x)
        return None

    def remove(self, predicate):
        with self._lock:
            before = len(self._load())
            self._items = [x for x in self._items if not predicate(x)]
            if len(self._items) != before:
                self._save()
            return before - len(self._items)
