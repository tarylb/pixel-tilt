"""
boot.py -- runs once, before code.py, every time the board powers on
or resets.

By default CircuitPython leaves the filesystem writable to your
computer (so you can drag-and-drop code.py onto CIRCUITPY) and
read-only to the code that's running -- which means the game normally
CAN'T save a progress file on its own.

This flips that: on a normal power-up, the running game gets write
access so it can save progress. If you want to edit files from your
computer instead, hold either button (or both) while plugging in
power -- that keeps CIRCUITPY writable from your computer for that
boot, and the game just won't be able to save progress during that
session.
"""

import board
import digitalio
import storage

left = digitalio.DigitalInOut(board.SCK)
left.direction = digitalio.Direction.INPUT
left.pull = digitalio.Pull.UP

right = digitalio.DigitalInOut(board.A1)
right.direction = digitalio.Direction.INPUT
right.pull = digitalio.Pull.UP

# .value is True when NOT pressed (pulled up), False when held down.
held = not left.value or not right.value
storage.remount("/", readonly=held)
