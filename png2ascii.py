#!/usr/bin/python3

from collections import namedtuple
from copy import deepcopy
from time import sleep

import argparse
import os
import shutil
import sys
import zlib

HB = "▀"

class Chunk:
	def __init__(self, stream: bytes):
		self.length = stream.read(4)
		self.length = sum(int(b) * 256**(3 - i) for i, b in enumerate(self.length))
		self.type = stream.read(4)
		self.data = stream.read(self.length)
		self.crc = stream.read(4)
	
	def __repr__(self):
		if self.type != b"IDAT":
			return f"{self.type}({self.length}): {self.data[:32]}"
		return f"{self.type}({self.length})"

class PNG:
	def __init__(self, path: str):
		self._signature = None
		self._ihdr = None
		self._idat = []
		self._plte = None
		self.chunks = []
		self.pixel_map = []
		self.original_pixel_map = []

		with open(path, "rb") as image: 
			signature = image.read(8)
			if signature != b"\x89PNG\x0d\x0a\x1a\x0a":
				raise ValueError("Not a PNG file")
			self._signature = signature
			chunk = Chunk(image)
			if chunk.type == b"IHDR":
				self._ihdr = chunk
			self.chunks.append(chunk)
			while chunk.type != b"IEND":
				chunk = Chunk(image)
				if chunk.type == b"PLTE":
					self._plte = chunk
				elif chunk.type == b"IDAT":
					self._idat.append(chunk)
				self.chunks.append(chunk)

		self.compressed_size = sum(data.length for data in self._idat)
		IHDR = namedtuple("IHDR", ["width", "height", "depth", "colortype", "compression_method", "filter_method", "interlacing_method"])
		self.ihdr = IHDR(width=sum(int(b)*256**(3-i) for i,b in enumerate(self._ihdr.data[:4])), 
						 height=sum(int(b)*256**(3-i) for i,b in enumerate(self._ihdr.data[4:8])), 
						 depth=int(self._ihdr.data[8]),
						 colortype=int(self._ihdr.data[9]),
						 compression_method=int(self._ihdr.data[10]),
						 filter_method=int(self._ihdr.data[11]),
						 interlacing_method=int(self._ihdr.data[12]))

		colortype_lookup = {
			0: "grayscale",
			2: "truecolor",
			3: "indexed",
			4: "grayscale_alpha",
			6: "truecolor_alpha"
		}
		self.channel_count = {
			"indexed": 1,
			"grayscale": 1,
			"grayscale_alpha": 2,
			"truecolor": 3,
			"truecolor_alpha": 4
		}

		self.width = self.ihdr.width
		self.height = self.ihdr.height
		self.original_width = self.width
		self.original_height = self.height
		self.depth = self.ihdr.depth
		self.colortype = colortype_lookup[self.ihdr.colortype]
		self.filter_method = self.ihdr.filter_method
		self.raw = self.uncompress_data()
		self.bytes_per_pixel = self.channel_count[self.colortype]

	def __repr__(self):
		return f"PNG {self.width}x{self.height} depth: {self.depth} colortype: {self.colortype} filter method: {self.filter_method} - data size: {fmt_num(self.compressed_size, chr(0x27))} bytes"

	def display(self, orig_y=0, orig_x=0, max_height=None, max_width=None):
		w, h = shutil.get_terminal_size()
		if max_height is None:
			max_height = self.height
		if max_width is None:
			max_width = w

		orig_y = min(self.height - 1, orig_y)
		orig_x = min(self.width - 1, orig_x)
		height = min(self.height - orig_y, max_height)
		width  = min(self.width - orig_x, max_width)
		#print(f"{height=}, {width=}, {orig_y=}, {orig_x=}")
		for y in range(0, len(self.pixel_map), 2):
			for x in range(0, len(self.pixel_map[y])):
				px = self.pixel_map[y][x]
				r, g, b = px.rgb
				if y == len(self.pixel_map) - 1:
					sys.stdout.write(f"\x1b[m\x1b[38;2;{r};{g};{b}m{HB}\x1b[m")
				else:
					r2, g2, b2 = self.pixel_map[y+1][x].rgb
					sys.stdout.write(f"\x1b[38;2;{r};{g};{b}m\x1b[48;2;{r2};{g2};{b2}m{HB}\x1b[m")
			sys.stdout.write("\n")
		sys.stdout.flush()
	
	def get_line(self, line_num: int):
		start = (line_num - 1)*(self.width*self.bytes_per_pixel + 1)
		end = start + self.width*self.bytes_per_pixel + 1
		return self.raw[start:end]
		
	def parse_rawline(self, y: int, raw_line: bytes, width: int, origy=0, origx=0):
		scanline = []
		filter_type = raw_line[0]
		x = 0
		for i in range(1, self.width*self.bytes_per_pixel + 1, self.bytes_per_pixel):
			pixel = Pixel(filter_type, raw_line[i:i+self.bytes_per_pixel], x=x, y=y)
			scanline.append(pixel)
			x += 1
		return scanline
		
	def parse_raw(self, height=None, width=None, origy=0, origx=0):
		scanlines = []
		if not height:
			height = self.height
		if not width:
			width = self.width
		for y in range(self.height):
			raw_line = self.get_line(y + 1)
			scanline = self.parse_rawline(y, raw_line, width, origy, origx)
			scanlines.append(scanline)
		self.original_pixel_map = [*scanlines]
		self.pixel_map = deepcopy(self.original_pixel_map)
	
	def nearest_neighbour(self, height, width):
		pixel_map = []
		for y in range(height):
			row = []
			y_src = int(y * self.height / height)
			for x in range(width):
				x_src = int(x * self.width / width)
				px_src = self.pixel_map[y_src][x_src]
				px_dst = px_src.copy()
				px_dst.x = x
				px_dst.y = y
				px_dst.unfiltered = True
				row.append(px_dst)
			pixel_map.append(row)
		self.height = height
		self.width = width
		del self.pixel_map
		self.pixel_map = pixel_map
	
	def bicubic(self, height, width):
		pixel_map = []
		def cubic_weight(t, a=-0.5):
			T = abs(t)
			if T <= 1:
				return (a + 2)*T**3 - (a + 3)*T**2 + 1
			elif T < 2:
				return a*T**3 - 5*a*T**2 + 8*a*T - 4*a 
			return 0

		for y in range(height):
			y_src = y * self.height / height
			dy = y_src - int(y_src)
			row = []
			for x in range(width):
				x_src = x * self.width / width
				x0, y0 = (int(x_src), int(y_src))
				y_top = max(0, y0 - 1)
				x_left = max(0, x0 - 1)
				x_right = min(x0 + 1, self.width - 1)
				x_right2 = min(x0 + 2, self.width - 1)
				y_bottom = min(y0 + 1, self.height - 1)
				y_bottom2 = min(y0 + 2, self.height - 1)
				neighbours = [[(x_left, y_top)    , (x0, y_top)    , (x_right, y_top)    , (x_right2, y_top)],
				              [(x_left, y0)       , (x0, y0)       , (x_right, y0)       , (x_right2, y0)],
				              [(x_left, y_bottom) , (x0, y_bottom) , (x_right, y_bottom) , (x_right2, y_bottom)],
				              [(x_left, y_bottom2), (x0, y_bottom2), (x_right, y_bottom2), (x_right2, y_bottom2)]]

				dx = x_src - int(x_src)
				Rs = []
				# Horizontal pass
				for line in neighbours:
					R_red = 0
					R_green = 0
					R_blue = 0
					for i in range(-1, 3):
						xi, yi = line[i+1]
						R_red += cubic_weight(i - dx)*self.original_pixel_map[yi][xi].red
						R_green += cubic_weight(i - dx)*self.original_pixel_map[yi][xi].green
						R_blue += cubic_weight(i - dx)*self.original_pixel_map[yi][xi].blue
					Rs.append((R_red, R_green, R_blue))

				# Vertical pass
				r = 0
				g = 0
				b = 0
				for i in range(-1, 3):
					r += cubic_weight(i - dy)*Rs[i+1][0]
					g += cubic_weight(i - dy)*Rs[i+1][1]
					b += cubic_weight(i - dy)*Rs[i+1][2]
				r = max(0, min(int(r), 255))
				g = max(0, min(int(g), 255))
				b = max(0, min(int(b), 255))
				px = Pixel(0, bytes([r, g, b]), x, y)
				px.unfiltered = True
				row.append(px)
			pixel_map.append(row)

		del self.pixel_map
		self.pixel_map = pixel_map

	def bilinear(self, height, width):
		pixel_map = []

		for y in range(height):
			y_src = y * self.height / height
			row = []
			for x in range(width):
				x_src = x * self.width / width
				(x0, y0) = (int(x_src), int(y_src))
				(x1, y1) = (x0 + 1, y0 + 1)
				dx = x_src - x0
				dy = y_src - y0
				weight_ul = (1 - dx)*(1 - dy)
				weight_ur = dx*(1 - dy)
				weight_bl = (1 - dx)*dy
				weight_br = dx*dy
				
				y1 = min(y1, len(self.original_pixel_map) - 1)
				x1 = min(x1, len(self.original_pixel_map[y1]) - 1)
				ul = self.original_pixel_map[y0][x0]
				ur = self.original_pixel_map[y0][x1]
				bl = self.original_pixel_map[y1][x0]
				br = self.original_pixel_map[y1][x1]
				r = int(weight_ul*ul.red + weight_ur*ur.red + weight_bl*bl.red + weight_br*br.red)
				g = int(weight_ul*ul.green + weight_ur*ur.green + weight_bl*bl.green + weight_br*br.green)
				b = int(weight_ul*ul.blue + weight_ur*ur.blue + weight_bl*bl.blue + weight_br*br.blue)

				px_dst = Pixel(0, bytes([r, g, b]), x, y)
				px_dst.unfiltered = True
				row.append(px_dst)
			pixel_map.append(row)

				
		del self.pixel_map
		self.pixel_map = pixel_map

	def resize(self, height, width, method="nearest"):
		"""
		Resize to width x height using one of the following methods:
		- "nearest": for nearest neighbour interpolation
		- "bilinear": for bilinear interpolation
		- "bicubic": for bicubic interpolation
		"""
		if (height, width) == (self.height, self.width):
			return

		if method == "nearest":
			self.nearest_neighbour(height, width)
		elif method == "bilinear":
			self.bilinear(height, width)
		elif method == "bicubic":
			self.bicubic(height, width)

	
	def uncompress_data(self):
		pixels = b""
		for chunk in self._idat:
			pixels += chunk.data
		return zlib.decompress(pixels)
	
	def unfilter(self):
		for y in range(min(self.height, len(self.pixel_map))):
			for x in range(min(self.width, len(self.pixel_map[y]))):
				px = self.pixel_map[y][x]
				if px.unfiltered:
					pass
				elif px.filter_type == 1:
					self.pixel_map[y][x] = self.un_sub(px)
				elif px.filter_type == 2:
					self.pixel_map[y][x] = self.un_up(px)
				elif px.filter_type == 3:
					self.pixel_map[y][x] = self.un_avg(px)
				else:
					self.pixel_map[y][x] = self.un_paeth(px)

				px.red = int((1 - px.alpha/255) * 0 + (px.alpha/255) * px.red)
				px.green = int((1 - px.alpha/255) * 0 + (px.alpha/255) * px.green)
				px.blue = int((1 - px.alpha/255) * 0 + (px.alpha/255) * px.blue)
					
	def un_paeth(self, px):
		if px.x == 0:
			left = Pixel(0, b"\x00"*self.bytes_per_pixel, 0, px.y)
		else:
			left = self.pixel_map[px.y][px.x - 1]
		if px.y == 0:
			up = Pixel(0, b"\x00"*self.bytes_per_pixel, px.x, 0)
		else:
			up = self.pixel_map[px.y - 1][px.x]
		if px.x == 0 or px.y == 0:
			upleft = Pixel(0, b"\x00"*self.bytes_per_pixel, max(0, px.x - 1), max(0, px.y - 1))
		else:
			upleft = self.pixel_map[px.y - 1][px.x - 1]

		pr = left.red + up.red - upleft.red
		pr_left = abs(pr - left.red)
		pr_up = abs(pr - up.red)
		pr_upleft = abs(pr - upleft.red)
		if pr_left <= pr_up and pr_left <= pr_upleft:
			px.red = (px.red + left.red) % 256
		elif pr_up <= pr_upleft:
			px.red = (px.red + up.red) % 256
		else:
			px.red = (px.red + upleft.red) % 256

		pg = left.green + up.green - upleft.green
		pg_left = abs(pg - left.green)
		pg_up = abs(pg - up.green)
		pg_upleft = abs(pg - upleft.green)

		if pg_left <= pg_up and pg_left <= pg_upleft:
			px.green = (px.green + left.green) % 256
		elif pg_up <= pg_upleft:
			px.green = (px.green + up.green) % 256
		else:
			px.green = (px.green + upleft.green) % 256

		pb = left.blue + up.blue - upleft.blue
		pb_left = abs(pb - left.blue)
		pb_up = abs(pb - up.blue)
		pb_upleft = abs(pb - upleft.blue)

		if pb_left <= pb_up and pb_left <= pb_upleft:
			px.blue = (px.blue + left.blue) % 256
		elif pb_up <= pb_upleft:
			px.blue = (px.blue + up.blue) % 256
		else:
			px.blue = (px.blue + upleft.blue) % 256

		pa = left.alpha + up.alpha - upleft.alpha
		pa_left = abs(pa - left.alpha)
		pa_up = abs(pa - up.alpha)
		pa_upleft = abs(pa - upleft.alpha)

		if pa_left <= pa_up and pa_left <= pa_upleft:
			px.alpha = (px.alpha + left.alpha) % 256
		elif pa_up <= pa_upleft:
			px.alpha = (px.alpha + up.alpha) % 256
		else:
			px.alpha = (px.alpha + upleft.alpha) % 256

		px.unfiltered = True

		return px

	def un_avg(self, px):
		avg = lambda a, b: int((a + b) / 2)
		if px.x == 0:
			left = Pixel(0, b"\x00"*self.bytes_per_pixel, 0, px.y)
		else:
			left = self.pixel_map[px.y][px.x - 1]
		if px.y == 0:
			up = Pixel(0, b"\x00"*self.bytes_per_pixel, px.x, 0)
		else:
			up = self.pixel_map[px.y - 1][px.x]

		px.red += avg(left.red, up.red)
		px.green += avg(left.green, up.green)
		px.blue += avg(left.blue, up.blue)
		px.alpha += avg(left.alpha, up.alpha)
		px.red = px.red % 256
		px.green = px.green % 256
		px.blue = px.blue % 256
		px.alpha = px.alpha % 256

		px.raw = bytes([px.red, px.green, px.blue, px.alpha]) 
		px.unfiltered = True
		return px

	def un_sub(self, px):
		if px.x == 0:
			left = Pixel(0, b"\x00"*self.bytes_per_pixel, 0, px.y)
		else:
			left = self.pixel_map[px.y][px.x - 1]
		px.red += left.red
		px.green += left.green
		px.blue += left.blue
		px.alpha += left.alpha
		px.red = px.red % 256
		px.green = px.green % 256
		px.blue = px.blue % 256
		px.alpha = px.alpha % 256

		px.raw = bytes([px.red, px.green, px.blue, px.alpha]) 
		px.unfiltered = True
		return px
	
	def un_up(self, px):
		if px.y == 0:
			up = Pixel(0, b"\x00"*self.bytes_per_pixel, px.x, 0)
		else:
			up = self.pixel_map[px.y - 1][px.x]
		px.red += up.red
		px.green += up.green
		px.blue += up.blue
		px.alpha += up.alpha
		px.red = px.red % 256
		px.green = px.green % 256
		px.blue = px.blue % 256
		px.alpha = px.alpha % 256

		px.raw = bytes([px.red, px.green, px.blue, px.alpha]) 
		px.unfiltered = True
		return px


class Pixel:
	def __init__(self, filter_type, data, x, y):
		self.filter_type = filter_type
		self.raw = data
		self.x = x
		self.y = y
		self.red = b2i(self.raw[0])
		self.green = b2i(self.raw[1])
		self.blue = b2i(self.raw[2])
		self.alpha = b2i(b"\xff")
		if len(self.raw) == 4:
			self.alpha = b2i(self.raw[3])
		self.unfiltered = not bool(filter_type)
	
	def __repr__(self):
		return f"\x1b[48;2;{self.red};{self.green};{self.blue}m \x1b[m"
	
	def copy(self):
		px = Pixel(self.filter_type, self.raw, self.x, self.y)
		for att in ("red", "green", "blue", "alpha", "unfiltered", "x", "y"):
			setattr(px, att, getattr(self, att))
		return px

	@property
	def rgb(self):
		return (self.red, self.green, self.blue)
	
	@rgb.setter
	def rgb(self, value):
		r, g, b = value
		self.red = r
		self.green = g
		self.blue = b

	@property
	def rgba(self):
		return (self.red, self.green, self.blue, self.alpha)
	
	@rgba.setter
	def rgba(self, value):
		r, g, b, a = value
		self.rgb = (r, g, b)
		self.alpha = a

def b2i(seq: bytes):
	"""
	Convert a byte sequence into an integer
	"""
	if type(seq) == int:
		return seq
	return sum(int(b) * 256**(len(seq) - 1 - i) for i, b in enumerate(seq))

def fmt_num(n, thousand_sep=" ", decimal_sep="."):
	s = str(n)
	if "." in s:
		int_part, dec_part = s.split(".")
		dec_part = decimal_sep + dec_part
	else:
		int_part, dec_part = s, ""
	l = []
	for i in range(len(int_part), 0, -3):
		l.insert(0, int_part[max(0, i-3):i])
	return thousand_sep.join(l) + dec_part

parser = argparse.ArgumentParser()
parser.add_argument("-x", "--origx", help="Origin abcissa", default=0, type=int)
parser.add_argument("-y", "--origy", help="Origin ordinate", default=0, type=int)
parser.add_argument("-i", "--info", help="Display information about image", action="store_true")
parser.add_argument("-r", "--resize-mode", choices=["bilinear", "nearest", "bicubic"], default="nearest")
parser.add_argument("-w", "--max-width", help="Resize to this width", type=int)
parser.add_argument("-H", "--max-height", help="Resize to this height", type=int)
parser.add_argument("-k", "--keep-ratio", help="Keep width/height ration when resizing ignored if both --max-width and --max-height are given", action="store_true")
parser.add_argument("path", help="Path to the image")
args = parser.parse_args()

path = args.path

if not os.path.isfile(path) and path != "-":
	exit(1)
	
tmp_img = "/tmp/.img.png"
if path == "-":
	print("Reading image from standard input", file=sys.stderr)
	raw_image = sys.stdin.buffer.read()
	with open(tmp_img, "wb") as f:
		f.write(raw_image)
	path = tmp_img

image = PNG(path)

if args.info:
	print(image)
	exit(0)

w, h = shutil.get_terminal_size()
print("Parsing image data", file=sys.stderr)
image.parse_raw(args.max_height, args.max_width, args.origy, args.origx)
print("Unfiltering", file=sys.stderr)
image.unfilter()
image.original_pixel_map = deepcopy(image.pixel_map)

ratio = image.width / image.height

width, height = image.width, image.height
if args.max_height and args.max_width:
	width, height = args.max_width, args.max_height
elif args.max_height and not args.max_width:
	height = args.max_height
	if args.keep_ratio:
		width = max(1, int(ratio * args.max_height))
		width = min(w, width)
elif not args.max_height and args.max_width:
	width = args.max_width
	if args.keep_ratio:
		height = max(1, int(args.max_width // ratio))
print("Resizing", file=sys.stderr)
image.resize(height, width, args.resize_mode)
print("Displaying", file=sys.stderr)
image.display()

if os.path.isfile(tmp_img):
	os.remove("/tmp/.img.png")
