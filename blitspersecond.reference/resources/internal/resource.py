class Resource:
    """A cached text file (shader source today): identity from the path hash,
    content lazy-read on first access. ResourceManager.fetch() mints and
    memo-caches these; nothing outside the machinery names the type."""

    def __init__(self, filename: str, id: int):
        self._filename = filename
        self._id = id
        self._loaded = False

    @property
    def id(self) -> int:
        return self._id

    def __hash__(self) -> int:
        return self.id

    def __eq__(self, other):
        if not isinstance(other, Resource):
            return NotImplemented
        return self._filename == other._filename

    @property
    def content(self) -> str:
        if not self._loaded:
            with open(self._filename, "r", encoding="utf-8") as f:
                self._content = f.read()
            self._loaded = True
        return self._content
