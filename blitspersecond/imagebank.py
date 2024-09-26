from .imagemap import ImageMap


class ImageBank(object):
    """
    Manages a collection of ImageMap objects loaded from files, ensuring caching and efficient access.
    ImageMaps are stored by their filename to prevent duplicate loading.
    """

    def __init__(self):
        # Dictionary to store ImageMap instances, keyed by filename
        self._imagemaps = {}

    def get(self, file, tilesize=None):
        """
        Loads an ImageMap from the given file and caches it by its filename.
        If the ImageMap has already been loaded, it returns the cached version.

        :param file: The filename of the image to load.
        :param tilesize: Optional tuple (width, height) to specify the size of the tiles.
        :return: The loaded ImageMap instance.
        """
        if file not in self._imagemaps:
            self._imagemaps[file] = ImageMap(file, tilesize)
        return self._imagemaps[file]

    def unset(self, file):
        """
        Unloads the ImageMap associated with the given file by removing it from the cache.

        :param file: The filename of the image to unload.
        :raises KeyError: If the file is not loaded.
        """
        if file in self._imagemaps:
            del self._imagemaps[file]
        else:
            raise KeyError(f"ImageMap for file '{file}' is not loaded.")

    def __getitem__(self, file):
        """
        Allows access to the cached ImageMap by file name.
        Raises a KeyError if the file is not loaded.

        :param file: The filename of the image to access.
        :return: The ImageMap instance.
        """
        if file in self._imagemaps:
            return self._imagemaps[file]
        else:
            raise KeyError(f"ImageMap for file '{file}' is not loaded.")

    def __setitem__(self, file, value):
        """
        Allows assignment via indexing. Setting a value of `None` is equivalent to calling `unset`.
        If a non-`None` value is passed, a ValueError will be raised, as only unloading is supported through this method.

        :param file: The filename of the image to unload.
        :param value: Must be `None` to unload the image.
        :raises ValueError: If the value is not `None`.
        """
        if value is None:
            self.unset(file)
        else:
            raise ValueError("Only 'None' is allowed when unsetting an image map.")

    def __len__(self):
        """
        Returns the number of loaded ImageMap instances.

        :return: Number of loaded ImageMap instances.
        """
        return len(self._imagemaps)

    def __iter__(self):
        """
        Allows iteration over all loaded ImageMap instances.

        :return: An iterator over the loaded ImageMap objects.
        """
        return iter(self._imagemaps.values())
