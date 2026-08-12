#!/usr/bin/python3

from collections import namedtuple
from time import sleep
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
		self.depth = self.ihdr.depth
		self.colortype = colortype_lookup[self.ihdr.colortype]
		self.filter_method = self.ihdr.filter_method
		self.raw = self.uncompress_data()
		self.bytes_per_pixel = self.channel_count[self.colortype]

	def __repr__(self):
		return f"PNG {self.width}x{self.height} depth: {self.depth} colortype: {self.colortype} filter method: {self.filter_method} - data size: {fmt_num(self.compressed_size, chr(0x27))} bytes"

	def display(self):
		for y in range(0, self.height, 2):
			#sys.stdout.write(f"{self.pixel_map[y][0].filter_type},{self.pixel_map[min(y+1, self.height - 1)][0].filter_type}")
			for x in range(self.width):
				px = self.pixel_map[y][x]
				r, g, b = px.rgb
				if y == self.height - 1:
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
		
	def parse_rawline(self, y: int, raw_line: bytes):
		scanline = []
		filter_type = raw_line[0]
		x = 0
		for i in range(1, self.width*self.bytes_per_pixel + 1, self.bytes_per_pixel):
			pixel = Pixel(filter_type, raw_line[i:i+self.bytes_per_pixel], x=x, y=y)
			scanline.append(pixel)
			x += 1
		return scanline
		
	def parse_raw(self):
		scanlines = []
		for y in range(self.height):
			raw_line = self.get_line(y + 1)
			scanline = self.parse_rawline(y, raw_line)
			scanlines.append(scanline)
		self.pixel_map = [*scanlines]
	
	
	def uncompress_data(self):
		pixels = b""
		for chunk in self._idat:
			pixels += chunk.data
		return zlib.decompress(pixels)
	
	def unfilter(self):
		for y in range(self.height):
			#print(self.pixel_map[y][0].filter_type)
			for x in range(self.width):
				px = self.pixel_map[y][x]
				if px.unfiltered:
					continue
				if px.filter_type == 1:
					self.pixel_map[y][x] = self.un_sub(px)
				elif px.filter_type == 2:
					self.pixel_map[y][x] = self.un_up(px)
				elif px.filter_type == 3:
					self.pixel_map[y][x] = self.un_avg(px)
				else:
					self.pixel_map[y][x] = self.un_paeth(px)
					
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

		px.raw = bytes([px.red, px.green, px.blue]) 
		# only add alpha to px.raw if it originally had an alpha byte
		if len(px.raw) == 4:
			px.raw += bytes([px.alpha])
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

		px.raw = bytes([px.red, px.green, px.blue]) 
		# only add alpha to px.raw if it originally had an alpha byte
		if len(px.raw) == 4:
			px.raw += bytes([px.alpha])
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

		px.raw = bytes([px.red, px.green, px.blue]) 
		# only add alpha to px.raw if it originally had an alpha byte
		if len(px.raw) == 4:
			px.raw += bytes([px.alpha])
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
		self.alpha = b2i(b"\x00")
		if len(self.raw) == 4:
			self.alpha = b2i(self.raw[3])
		self.unfiltered = not bool(filter_type)
	
	def __repr__(self):
		return f"\x1b[48;2;{self.red};{self.green};{self.blue}m \x1b[m"
		#return "Px(" + "".join(f"{attr}={getattr(self, attr)}, " for attr in ("filter_type", "x", "y", "red", "green", "blue")) + ")"

	@property
	def rgb(self):
		return (self.red, self.green, self.blue)

	@property
	def rgba(self):
		return (self.red, self.green, self.blue, self.alpha)

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

if len(sys.argv) < 2:
	exit(1)

path = sys.argv[1]

if not os.path.isfile(path):
	exit(1)
	
image = PNG(path)
#print(image)
w, h = shutil.get_terminal_size()
image.parse_raw()
image.unfilter()
image.display()
