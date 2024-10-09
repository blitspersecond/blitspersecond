class T:
    def __init__(self, width, height):
        self._width = width
        self._height = height

    @property
    def height(self):
        return self._height

    @property
    def width(self):
        return self._width


def create_quad(x, y, texture):
    x2 = x + texture.width
    y2 = y + texture.height
    return x, y, x2, y, x2, y2, x, y2


t = T(640, 360)

q = create_quad(0, 0, t)

print(q)
