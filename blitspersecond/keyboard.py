from pyglet.window import key
import sys


class Keyboard:
    def __init__(self):
        # Mode handling
        self.mode = "control"  # Start in control mode by default

        # Track the state of keys (True if held, False if released)
        self._key_state = {}

        # Text buffer for text input mode
        self._text_buffer = ""

        # Map key combinations to their actions
        self._key_combinations = {}

    def switch_mode(self, mode):
        """
        Switch between 'control' and 'text' modes.
        """
        # Print the buffer to stdout when switching from text mode to control mode
        if self.mode == "text" and mode == "control":
            print(f"Exiting text mode. Final text buffer: {self._text_buffer}")
            sys.stdout.flush()
        self.mode = mode
        if mode == "text":
            print("Switched to text input mode")
        elif mode == "control":
            print("Switched to control mode")

    def add_key_combination(self, modifiers, target_key, action):
        """
        Register a new key combination for control mode.

        :param modifiers: Set of modifier keys (e.g., {key.MOD_CTRL, key.MOD_ALT})
        :param target_key: The main key to activate the combination (e.g., key.H)
        :param action: The function to call when the combination is detected
        """
        self._key_combinations[(frozenset(modifiers), target_key)] = action

    def on_key_press(self, symbol, modifiers):
        if self.mode == "control":
            # Update control-specific states
            # Update the state to indicate the key is pressed
            if not self._key_state.get(symbol, False):
                self._key_state[symbol] = True
                print(f"Key pressed: {symbol}, state updated to True")

            # Convert the modifiers bitmask into a set of individual modifiers and add them to _key_state
            for modifier in self._get_active_modifiers(modifiers):
                self._key_state[modifier] = True

            # Check if any key combinations should trigger an action
            active_modifiers = self._get_active_modifiers(modifiers)
            for (
                required_modifiers,
                target_key,
            ), action in self._key_combinations.items():
                if symbol == target_key and required_modifiers.issubset(
                    active_modifiers
                ):
                    action()
        elif self.mode == "text":
            # No key press logic for text mode; handled by on_text
            pass

    def on_key_release(self, symbol, modifiers):
        if self.mode == "control":
            # Update the state to indicate the key is released
            if self._key_state.get(symbol, False):
                self._key_state[symbol] = False
                print(f"Key released: {symbol}, state updated to False")

    def is_key_held(self, key_symbol):
        """
        Check if a specific key is currently held down.

        :param key_symbol: The key to check
        :return: True if the key is held down, False otherwise
        """
        return (
            self._key_state.get(key_symbol, False) if self.mode == "control" else False
        )

    def is_combination_held(self, modifiers, target_key):
        """
        Check if a specific combination of modifiers and a target key is held down.

        :param modifiers: Set of modifier keys (e.g., {key.MOD_CTRL, key.MOD_ALT})
        :param target_key: The main key to check (e.g., key.H)
        :return: True if all modifiers and the target key are held down, False otherwise
        """
        # Check if the target key is held
        if not self.is_key_held(target_key):
            return False

        # Check if all required modifiers are currently held
        active_modifiers = {
            mod for mod in self._key_state if self._key_state.get(mod, False)
        }
        return modifiers.issubset(active_modifiers)

    def on_text(self, text):
        """
        Handle character input in text mode.
        """
        if self.mode == "text":
            self._text_buffer += text
            # Print the updated buffer to stdout
            print(f"Text buffer: {self._text_buffer}")
            sys.stdout.flush()

    def on_text_motion(self, motion):
        """
        Handle cursor navigation and text manipulation in text mode.
        """
        if self.mode == "text":
            if motion == key.MOTION_BACKSPACE:
                self._text_buffer = self._text_buffer[:-1]  # Simple backspace handling
                print(f"Text buffer after backspace: {self._text_buffer}")
            # Additional text navigation/editing can be added as needed

    def get_text_buffer(self):
        """
        Retrieve the current text buffer.
        """
        return self._text_buffer

    def clear_text_buffer(self):
        """
        Clear the text buffer.
        """
        self._text_buffer = ""

    def _get_active_modifiers(self, modifiers):
        # Translate the bitmask of active modifiers into a set of individual modifier constants
        active_modifiers = set()
        if modifiers & key.MOD_CTRL:
            active_modifiers.add(key.MOD_CTRL)
        if modifiers & key.MOD_ALT:
            active_modifiers.add(key.MOD_ALT)
        if modifiers & key.MOD_SHIFT:
            active_modifiers.add(key.MOD_SHIFT)
        if modifiers & key.MOD_COMMAND:
            active_modifiers.add(key.MOD_COMMAND)
        return active_modifiers
