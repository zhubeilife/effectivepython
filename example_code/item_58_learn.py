#!/usr/bin/python3

# Copyright 2014-2019 Brett Slatkin, Pearson Education Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Reproduce book environment
import random

random.seed(1234)

import logging
from pprint import pprint
from sys import stdout as STDOUT

# Write all output to a temporary directory
import atexit
import gc
import io
import os
import tempfile
import threading
from threading import Lock

TEST_DIR = tempfile.TemporaryDirectory()
atexit.register(TEST_DIR.cleanup)

# Make sure Windows processes exit cleanly
OLD_CWD = os.getcwd()
atexit.register(lambda: os.chdir(OLD_CWD))
os.chdir(TEST_DIR.name)


def close_open_files():
    everything = gc.get_objects()
    for obj in everything:
        if isinstance(obj, io.IOBase):
            obj.close()


atexit.register(close_open_files)


ALIVE = "*"
EMPTY = "-"


class Grid:
    def __init__(self, height, width):
        self.height = height
        self.width = width
        self.rows = []
        for _ in range(self.height):
            self.rows.append([EMPTY] * self.width)

    def get(self, y, x):
        return self.rows[y % self.height][x % self.width]

    def set(self, y, x, state):
        self.rows[y % self.height][x % self.width] = state

    def __str__(self):
        output = ""
        for row in self.rows:
            for cell in row:
                output += cell
            output += "\n"
        return output


class LockGrid(Grid):
    def __init__(self, height, width):
        super().__init__(height, width)
        self.lock = Lock()

    def __str__(self):
        with self.lock:
            return super().__str__()

    def get(self, y, x):
        with self.lock:
            return super().get(y, x)

    def set(self, y, x, state):
        with self.lock:
            return super().set(y, x, state)


from queue import Queue


class ClosableQueue(Queue):
    SENTINEL = object()

    def close(self):
        self.put(self.SENTINEL)

    def __iter__(self):
        while True:
            item = self.get()
            try:
                if item is self.SENTINEL:
                    return
                yield item
            finally:
                self.task_done()


from threading import Thread


class StoppableWorker(Thread):
    def __init__(self, func, in_queue, out_queue, **kwargs):
        super(StoppableWorker, self).__init__(**kwargs)
        self.func = func
        self.in_queue = in_queue
        self.out_queue = out_queue

    def run(self):
        for item in self.in_queue:
            result = self.func(item)
            self.out_queue.put(result)


def game_logic(state, neighbors):
    if state == ALIVE:
        if neighbors < 2:
            return EMPTY     # Die: Too few
        elif neighbors > 3:
            return EMPTY     # Die: Too many
    else:
        if neighbors == 3:
            return ALIVE     # Regenerate
    return state


def game_logic_thread(item):
    y, x, state, neighbors = item
    try:
        next_stae = game_logic(state, neighbors)
    except Exception as e:
        next_stae = e
    return (y, x, next_stae)


def count_neighbors(y, x, get):
    n_ = get(y - 1, x + 0)  # North
    ne = get(y - 1, x + 1)  # Northeast
    e_ = get(y + 0, x + 1)  # East
    se = get(y + 1, x + 1)  # Southeast
    s_ = get(y + 1, x + 0)  # South
    sw = get(y + 1, x - 1)  # Southwest
    w_ = get(y + 0, x - 1)  # West
    nw = get(y - 1, x - 1)  # Northwest
    neighbor_states = [n_, ne, e_, se, s_, sw, w_, nw]
    count = 0
    for state in neighbor_states:
        if state == ALIVE:
            count += 1
    return count


def count_neighbors_thread(item):
    y, x, state, get = item
    try:
        neighbors = count_neighbors(y, x, get)
    except Exception as e:
        neighbors = e
    return (y, x, state, neighbors)


class SimulationError(Exception):
    pass


class ColumnPrinter:
    def __init__(self):
        self.columns = []

    def append(self, data):
        self.columns.append(data)

    def __str__(self):
        row_count = 1
        for data in self.columns:
            row_count = max(
                row_count, len(data.splitlines()) + 1)

        rows = [''] * row_count
        for j in range(row_count):
            for i, data in enumerate(self.columns):
                line = data.splitlines()[max(0, j - 1)]
                if j == 0:
                    padding = ' ' * (len(line) // 2)
                    rows[j] += padding + str(i) + padding
                else:
                    rows[j] += line

                if (i + 1) < len(self.columns):
                    rows[j] += ' | '

        return '\n'.join(rows)

# import IPython
# IPython.embed()

in_queue = ClosableQueue()
neighbors_queue = ClosableQueue()
out_queue = ClosableQueue()

neighbors_threads = []
game_logic_threads = []

for _ in range(5):
    thread = StoppableWorker(count_neighbors_thread, in_queue, neighbors_queue)
    thread.start()
    neighbors_threads.append(thread)

for _ in range(5):
    thread = StoppableWorker(game_logic_thread, neighbors_queue, out_queue)
    thread.start()
    game_logic_threads.append(thread)

def simulation_pipeline(
        grid: LockGrid,
        in_queue: ClosableQueue,
        neighbors_queue: ClosableQueue,
        out_queue: ClosableQueue
):
    for y in range(grid.height):
        for x in range(grid.width):
            state = grid.get(y, x)
            in_queue.put((y, x, state, grid.get))

    in_queue.join()
    neighbors_queue.join()
    # TODO
    # can we make sure after the neighbors queue close
    # all the jobs are done? so we can call out_queue.close()?
    out_queue.close()

    next_grid = LockGrid(grid.height, grid.width)
    for item in out_queue:
        y, x, state = item
        next_grid.set(y, x, state)

    return next_grid


columns = ColumnPrinter()
lockgrid = LockGrid(5, 9)
lockgrid.set(0, 3, ALIVE)
lockgrid.set(1, 4, ALIVE)
lockgrid.set(2, 2, ALIVE)
lockgrid.set(2, 3, ALIVE)
lockgrid.set(2, 4, ALIVE)

# import IPython
# IPython.embed()

for _ in range(5):
    columns.append(str(lockgrid))
    lockgrid = simulation_pipeline(lockgrid, in_queue, neighbors_queue, out_queue)

print(columns)

for _ in neighbors_threads:
    in_queue.close()

for _ in game_logic_threads:
    neighbors_queue.close()

for thread in neighbors_threads:
    thread.join()

for thread in game_logic_threads:
    thread.join()