class Console:
    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.grid = [[0 for _ in range(width)] for _ in range(height)]
        # cursor position
        self._x = 0
        self._y = 0

    @property
    def x(self):
        return self._x

    @x.setter
    def x(self, x):
        if 0 <= x < self.width:
            self._x = x

    @property
    def y(self):
        return self._y

    @y.setter
    def y(self, y):
        if 0 <= y < self.height:
            self._y = y

    @property
    def char(self):
        return self.grid[self.y][self.x]

    @char.setter
    def char(self, char: int):
        if 0 <= char <= 127:  # ASCII range for int8
            self.grid[self._y][self._x] = char
        else:
            raise ValueError("Character must be an ASCII code (0-127)")

    def clear(self):
        """Clears the console grid by setting all elements to 0."""
        self.grid = [[0 for _ in range(self.width)] for _ in range(self.height)]

    def __str__(self):
        """Returns a string representation of the console grid as ASCII characters."""
        return "\n".join(
            "".join(chr(cell) if cell > 0 else " " for cell in row) for row in self.grid
        )

    def _advance(self):
        """Advances the cursor in the x direction, wraps to the next line if needed.
        Scrolls the console up if the cursor goes beyond the last line.
        """
        # Advance x
        self._x += 1

        # Check if x is out of bounds
        if self._x >= self.width:
            self._x = 0
            self._y += 1  # Move to the next line

            # Check if y is out of bounds
            if self._y >= self.height:
                self._y = self.height - 1  # Reset y to the last line
                # Scroll the grid up by one line
                self.grid.pop(0)  # Remove the top line
                # Add a new empty line at the bottom
                self.grid.append([0 for _ in range(self.width)])

    def _process_line_endings(self, text: str) -> str:
        """Converts various line-ending sequences into Unix-style '\n' line endings."""
        i = 0
        output = []
        while i < len(text):
            if text[i] == "\r":
                if i + 1 < len(text) and text[i + 1] == "\n":
                    i += 1  # Skip the next character for \r\n
                output.append("\n")
            elif text[i] == "\n":
                if i + 1 < len(text) and text[i + 1] == "\r":
                    i += 1  # Skip the next character for \n\r
                output.append("\n")
            elif text[i] == "\\":
                output.append("\\")  # Treat standalone backslash as literal
            else:
                output.append(text[i])
            i += 1
        return "".join(output)

    def write(self, text: str):
        """Writes a string of ASCII characters to the console, processing line endings."""
        processed_text = self._process_line_endings(text)

        for char in processed_text:
            if char == "\n":
                self._x = 0
                self._y += 1
                if self._y >= self.height:
                    self._y = self.height - 1
                    self.grid.pop(0)
                    self.grid.append([0 for _ in range(self.width)])
            elif 0 <= ord(char) <= 127:  # Ensure it's pure ASCII
                self.char = ord(char)  # Set character at the current cursor position
                self._advance()  # Move cursor to the next position
            else:
                raise ValueError("String contains non-ASCII characters")

    def print(self, text: str):
        """Prints a string with word wrapping based on spaces and dashes, and appends a newline if needed."""
        # Process line endings first
        processed_text = self._process_line_endings(text)

        # Split processed text into words based on spaces and dashes
        words = processed_text.replace("-", " - ").split()

        for word in words:
            # Handle explicit newlines in the word list
            if word == "\n":
                self._x = 0
                self._y += 1
                if self._y >= self.height:
                    self._y = self.height - 1
                    self.grid.pop(0)
                    self.grid.append([0 for _ in range(self.width)])
                continue

            # Check for word wrapping
            if len(word) + self._x > self.width:
                self._x = 0
                self._y += 1
                if self._y >= self.height:
                    self._y = self.height - 1
                    self.grid.pop(0)
                    self.grid.append([0 for _ in range(self.width)])

            # Write each character in the word
            for char in word:
                if 0 <= ord(char) <= 127:
                    self.write(char)
                else:
                    raise ValueError("String contains non-ASCII characters")

            # Add a space after the word if there's room
            if self._x < self.width:
                self.write(" ")

        # Add a newline if the text doesn’t end with one
        if self._y < self.height and (self._x > 0 or text and text[-1] != "\n"):
            self.write("\n")


# 1. Cursor Movement Methods
# Move Cursor: Add methods like move_cursor_to(x, y), move_cursor_up(), move_cursor_down(), move_cursor_left(), and move_cursor_right() to allow direct control over the cursor position. You could implement these with bounds checking to ensure the cursor doesn’t move out of the grid.
# Home and End: Methods like cursor_to_home() to move to the top-left corner and cursor_to_end() to move to the end of the last line with content.
# 2. Backspace Functionality
# Add a backspace() method that moves the cursor back by one position and clears the character at that position. This would allow for more interactive text manipulation, such as simulating typing behavior.
# 3. Text Formatting
# Foreground and Background Colors: Add basic support for ANSI color codes (if simulating a terminal), allowing text to be printed in different colors. This could be as simple as using different ASCII codes to represent colors and creating a color mapping in the __str__ method for visualization.
# Bold and Underline: If you want to enhance the console for a richer text experience, you could also add basic formatting tags like bold or underline. It would require additional data storage for each cell to track the formatting, which could then be processed in __str__.
# 4. Clear Line and Scroll Down
# Clear Line: A clear_line() method could clear the current line and reset the cursor to the start of that line. This would be useful for implementing more interactive console-like behaviors.
# Scroll Down: Implement a scroll_down() method that adds a blank line at the top and pushes everything down one row. This could be helpful for managing output in cases where you want to maintain a fixed history view but keep the latest information at the bottom.
# 5. Text Selection and Copy/Paste
# Select and Copy: Allow for selecting a range of text from the console grid and copying it to a string, perhaps using a method like copy(x1, y1, x2, y2).
# Paste: Implement a paste(text) method to insert text at the current cursor position, wrapping and scrolling as necessary. This could make the Console more interactive and versatile for text manipulation.
# 6. Line and Word Count Methods
# Line Count: Add a count_lines() method that returns the number of lines with any text in them. This could be useful for more complex operations where you need to know how much content is currently displayed.
# Word Count: Implement a count_words() method to return the total number of words in the console grid. This might be useful for analytics or debugging.
# 7. Save and Load Content
# Save to File: A save_to_file(filename) method could save the current contents of the console grid to a text file.
# Load from File: Complement this with a load_from_file(filename) method to load text from a file directly into the console grid.
# 8. Find and Replace
# Implement basic search functionality with find(word), which would return the positions of all occurrences of the specified word.
# A replace(old_word, new_word) method could replace all instances of a word in the console grid. This would be useful for text-editing scenarios.
# 9. Text Alignment
# Left, Right, and Center Alignment: Add methods like align_left(), align_right(), and center_text() to automatically align text within a line or across multiple lines. This could make the console better suited for text layout purposes.
# 10. History Management
# Undo and Redo: Implement basic undo() and redo() functionality to revert the console grid to previous states. You could keep a stack of previous states for this, making it possible to navigate back and forth through text modifications.
# 11. Border and Frame Drawing
# A draw_border() method could add a border around the text area. This would be visually appealing if you’re trying to create UI elements like pop-up boxes within the console.
# Expand this with methods to draw horizontal and vertical lines, which could be useful for making tables or structured layouts.
# Each of these features would add depth and flexibility to the Console class, making it a more powerful tool for creating interactive text-based applications. If any of these functionalities sound interesting, I can help with the implementation!
