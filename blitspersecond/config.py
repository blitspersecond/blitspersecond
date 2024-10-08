import yaml
import os


class ConfigSection:
    def __init__(self, config, section):
        self._config = config
        self._section = section

    def __getattr__(self, key):
        # Provide access to subkeys as attributes
        try:
            return self._config._config_data[self._section][key]
        except KeyError:
            raise AttributeError(f"No such attribute: {self._section}.{key}")

    def __setattr__(self, key, value):
        # Set subkeys as attributes
        if key in {"_config", "_section"}:
            super().__setattr__(key, value)
        else:
            if self._section not in self._config._config_data:
                self._config._config_data[self._section] = {}
            self._config._config_data[self._section][key] = value


class Config:
    _instance = None
    _default = {
        "core": {
            "show_fps": False,
            "show_metrics": False,
        },
        "window": {
            "height": 360,
            "width": 640,
            "scale": 1,
            "framerate": 60,
            "vsync": False,
        },
        "framebuffer": {
            "depth": 8,
        },
    }
    _config_data = {}  # Class-level config data to ensure shared state

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(Config, cls).__new__(cls, *args, **kwargs)
            cls._load_config()  # Call without passing `cls`
        return cls._instance

    @classmethod
    def _load_config(cls):
        # Load or create config file
        if os.path.exists("config.yml"):
            with open("config.yml", "r") as f:
                yaml_config = yaml.safe_load(f) or {}
        else:
            yaml_config = {}

        # Overlay file config onto default config
        cls._config_data = {**cls._default, **yaml_config}

    def __getattr__(self, section):
        # Dynamically create and return a ConfigSection
        if section not in self._config_data:
            self._config_data[section] = {}  # Initialize section if it doesn't exist
        return ConfigSection(self, section)

    def save(self):
        try:
            with open("config.yml", "w") as f:
                yaml.dump(self._config_data, f)
            print("Configuration successfully saved to config.yml")
        except Exception as e:
            print(f"Failed to save configuration: {e}")

    def __eq__(self, other):
        return isinstance(other, Config) and self._config_data == other._config_data


c = Config()
c.save()
