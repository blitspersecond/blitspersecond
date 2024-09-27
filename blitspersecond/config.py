import os
import configparser
from typing import List


class Config(object):
    """
    A Singleton configuration handler for managing settings across the application.

    This class handles loading, saving, and providing access to configuration options
    via an .ini file. Configuration values can be accessed directly by section (e.g., `config.video.width`)
    or, for convenience, options in the 'default' section can be accessed as top-level attributes
    (e.g., `config.debug`).

    The configuration is loaded from 'config.ini' in the current working directory.
    If the file does not exist, it is created with default values.
    """

    _instance = None  # Singleton instance

    def __new__(cls) -> "Config":
        """
        Create a new instance of the Config class if one does not already exist.
        Implements the Singleton pattern to ensure that only one instance of the
        class exists at any time.
        """
        if cls._instance is None:
            cls._instance = super(Config, cls).__new__(cls)
            cls._instance._init_config()
        return cls._instance

    def _init_config(self) -> None:
        """
        Initialize the configuration by loading from the file or setting default values.

        If the config file exists, it will be loaded. If not, default values are used, and the
        config file will be created with those defaults.
        """
        self.config = configparser.ConfigParser(allow_no_value=True)
        self.config_file = os.path.join(os.getcwd(), "config.ini")

        # Default values for the configuration
        self.defaults = {
            "default": {
                "debug": False,
                "show_fps": False,
            },
            "window": {
                "width": 640,
                "height": 360,
                "fullscreen": False,
                "vsync": False,
                "framerate": 60,
                "scale": 1,
            },
            "audio": {
                "volume": 50,
            },
        }

        # Load or initialize config
        self.load_config()

    def load_config(self) -> None:
        """
        Load the configuration from the config.ini file.

        If the file does not exist, default values are loaded, and a new config file
        is created. Duplicate options in the file will raise a ValueError.
        """
        if os.path.exists(self.config_file):
            try:
                self._check_for_duplicates()
                self.config.read(self.config_file)
            except configparser.DuplicateOptionError as e:
                raise ValueError(
                    f"Duplicate option found: {e.option} in section {e.section}, at line {e.lineno}."
                )
        else:
            # If the file doesn't exist, load defaults and save them to a new file
            self.config.read_dict(self.defaults)
            self.save_config()

        # Merge any missing defaults into the loaded config
        for section, options in self.defaults.items():
            if not self.config.has_section(section):
                self.config.add_section(section)
            for option, value in options.items():
                if not self.config.has_option(section, option):
                    self.config.set(section, option, value)

        # Save the updated config back to the file
        self.save_config()

    def _check_for_duplicates(self):
        """
        Manually check the configuration file for any duplicate keys.

        Raises a ValueError with the line number if a duplicate key is found.
        """
        with open(self.config_file, "r") as file:
            lines = file.readlines()

        seen = {}
        for lineno, line in enumerate(lines, start=1):
            if "=" in line:
                key = line.split("=")[0].strip()
                if key in seen:
                    section = seen[key]
                    raise ValueError(
                        f"Duplicate key '{key}' found in section '{section}' on line {lineno}"
                    )
                seen[key] = self._get_current_section(lines, lineno)

    def _get_current_section(self, lines: List, lineno: int) -> str:
        """
        Helper method to find the current section based on the line number.

        This method is used when checking for duplicate keys to determine which section
        a duplicate belongs to.

        Args:
            lines (list): The list of lines from the config file.
            lineno (int): The current line number.

        Returns:
            str: The section name in which the key was found.
        """
        for i in range(lineno - 1, -1, -1):
            line = lines[i].strip()
            if line.startswith("[") and line.endswith("]"):
                return line[1:-1]  # Extract the section name without brackets
        return "unknown"

    def save_config(self):
        """
        Save the current configuration back to the config.ini file.

        Any changes made to the configuration are written to the file immediately.
        """
        with open(self.config_file, "w") as configfile:
            self.config.write(configfile)

    def __getattr__(self, section: str) -> "_SectionProxy":
        """
        Allow dynamic access to configuration sections and options.

        Sections can be accessed as attributes of the config object (e.g., `config.video`),
        and options in the 'default' section can be accessed directly (e.g., `config.debug`).

        Args:
            section (str): The name of the section or option being accessed.

        Returns:
            _SectionProxy: A proxy object that allows access to the options within the section.

        Raises:
            AttributeError: If the section or option does not exist.
        """
        # If the section is 'default', enable access directly through the root
        if section in self.defaults["default"]:
            return self._SectionProxy(self.config, "default", self).__getattr__(section)

        # Proxy section access like config.video or config.audio
        if section in self.config.sections() or section in self.defaults:
            return self._SectionProxy(self.config, section, self)

        raise AttributeError(f"No such section or default option: {section}")

    class _SectionProxy(object):
        """
        Private proxy class for handling access to individual sections.

        This class is used internally by the Config class to handle dynamic access
        to sections and their options. It allows getting and setting options dynamically.
        """

        def __init__(
            self,
            config: configparser.ConfigParser,
            section: str,
            config_instance: "Config",
        ) -> None:
            """
            Initialize the SectionProxy for a given section.

            Args:
                config (ConfigParser): The configuration object.
                section (str): The name of the section.
                config_instance (Config): The main Config instance.
            """
            self._config = config
            self._section = section
            self._config_instance = config_instance

        def __getattr__(self, option: str):
            """
            Get the value of a specific option in the section.

            Args:
                option (str): The name of the option to retrieve.

            Returns:
                The value of the option, automatically converted to the appropriate type.

            Raises:
                AttributeError: If the option does not exist.
            """
            if self._config.has_option(self._section, option):
                value = self._config.get(self._section, option)
                try:
                    # Convert to appropriate type
                    if value.isdigit():
                        return int(value)
                    if value.lower() in ["true", "false"]:
                        return value.lower() == "true"
                    return value
                except ValueError:
                    return value
            raise AttributeError(f"No such option: {option}")

        def __setattr__(self, option: str, value: str):
            """
            Set the value of an option in the section and save the configuration.

            Args:
                option (str): The name of the option to set.
                value (str): The value to set for the option.
            """
            if option.startswith("_"):
                super().__setattr__(option, value)
            else:
                self._config.set(self._section, option, str(value))
                self._config_instance.save_config()
