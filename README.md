# png2ascii

Python script to convert a PNG image into ASCII art so it can be displayed in a terminal (well, as long as the terminal can display half blocks "▀" (U+2580), and has RGB color support.)

To use it, just do :

```bash
python3 png2ascii.py your_image.png
```

to display the result.

Limitations:
- It doesn't resize nor crop the source image by itself, so it won't display properly if image width is greater than your terminal's $COLUMNS
- It only handles RGB and RGBa PNG images, and probably breaks if you give it a grayscale/grayscale with alpha or indexed-color PNG
