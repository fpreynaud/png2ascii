# png2ascii

Python script to convert a PNG image into ASCII art so it can be displayed in a terminal (well, as long as the terminal can display half blocks "▀" (U+2580), and has RGB color support.)

Usage:

```bash
python3 png2ascii.py [-h] [-r {nearest,bilinear,bicubic}]
                    [-w WIDTH] [-H HEIGHT] [-k]
                    path

positional arguments:
  path                  Path to the image

options:
  -h, --help            show this help message and exit
  -r {nearest,bilinear,bicubic}, --resize-mode {bilinear,nearest,bicubic} 
                        Resizing algorithm to use
  -w WIDTH, --max-width WIDTH
                        Resize to this width
  -H HEIGHT, --max-height HEIGHT
                        Resize to this height
  -k, --keep-ratio      Keep width/height ration when resizing. Ignored if both --max-width and --max-height are given

```

Note: For pictures that are more than `$COLUMNS` pixels wide (with `$COLUMNS` being the number of cells in one line of your terminal), you probably want to resize to `$COLUMNS` or lower with the `-w` flag. 
Use the `-k` flag to keep the original aspect ratio when resizing, so your image is not weirdly stretched or compressed.

Limitations:
- It only handles RGB and RGBa PNG images, and probably breaks if you give it a grayscale/grayscale with alpha or indexed-color PNG
- It's pretty slow
