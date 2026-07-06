"""ユニットテスト共有の Fake Firestore（ScopedClient 互換の最小実装）。"""


class _Snap:
    def __init__(self, path, data):
        self._d = data
        self.id = path.split("/")[-1]
        self.exists = data is not None

    def to_dict(self):
        return self._d


class _Doc:
    def __init__(self, store, path):
        self._store = store
        self._path = path

    def set(self, data, merge=False):
        if merge and self._path in self._store:
            self._store[self._path].update(data)
        else:
            self._store[self._path] = dict(data)

    def update(self, data):
        self._store.setdefault(self._path, {}).update(data)

    def get(self):
        return _Snap(self._path, self._store.get(self._path))


class _Query:
    def __init__(self, docs):
        self._docs = docs

    def where(self, field, _op, val):
        return _Query([d for d in self._docs if (d._d or {}).get(field) == val])

    def get(self):
        return self._docs


class _Collection:
    def __init__(self, store, name):
        self._store = store
        self._name = name

    def _docs(self):
        out = []
        prefix = self._name + "/"
        for path, data in self._store.items():
            if path.startswith(prefix) and "/" not in path[len(prefix) :]:
                out.append(_Snap(path, data))
        return out

    def get(self):
        return self._docs()

    def where(self, field, _op, val):
        return _Query([d for d in self._docs() if (d._d or {}).get(field) == val])

    def document(self, doc_id):
        return _Doc(self._store, f"{self._name}/{doc_id}")


class FakeDB:
    def __init__(self):
        self.store: dict[str, dict] = {}

    def collection(self, name):
        return _Collection(self.store, name)

    def document(self, path):
        return _Doc(self.store, path)

    def docs(self, collection: str) -> list[dict]:
        """コレクション直下のドキュメント一覧（テストのアサーション用）。"""
        prefix = collection + "/"
        return [
            v for k, v in self.store.items() if k.startswith(prefix) and "/" not in k[len(prefix) :]
        ]
