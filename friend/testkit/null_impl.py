"""A program that does nothing: ignores its arguments and stdin, prints nothing, never exits by itself.

Used by `runner.py --null-target`. A test that passes against this asserts nothing about the
program under test (it is vacuous) and is sent back to the test author.
"""
import sys
import time

for _ in sys.stdin:
    pass
while True:
    time.sleep(3600)
