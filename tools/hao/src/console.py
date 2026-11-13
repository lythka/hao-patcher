"""The hao commands' output: five detail levels over one stream of events (the owner-endorsed console mockups).

  quiet   --quiet       progress and the one question that matters
  steps   (default)     one line per step, each with its check
  files   --show        one line per file or repair; full reports on failure
  edits   --show-edits  origin, bytes and reason of each change
  json    --log json    every event as one JSON line, for tools and CI

Every event is also kept (Console.events) so a command can write it into its record (the install record, a build
record). Lines a level does not show are still events. Output goes to OUT (stdout); questions read from ASK.
"""
import json
import sys
import time

LEVELS = ('quiet', 'steps', 'files', 'edits', 'json')


def level_of(a):
    """The level an argparse namespace's --quiet / --show / --show-edits / --log json chose."""
    if getattr(a, 'log', None) == 'json':
        return 'json'
    if getattr(a, 'quiet', False):
        return 'quiet'
    if getattr(a, 'show_edits', False):
        return 'edits'
    if getattr(a, 'show', False):
        return 'files'
    return 'steps'


def add_level_flags(parser):
    g = parser.add_mutually_exclusive_group()
    g.add_argument('--quiet', action='store_true', help='progress only')
    g.add_argument('--show', action='store_true', help='one line per file or repair')
    g.add_argument('--show-edits', action='store_true', help='every edit: origin, bytes, reason')
    g.add_argument('--log', choices=['json'], help='every event as one JSON line')


class Stopped(Exception):
    """A check failed: the command stops and Console.stopped prints what happened, why it matters, what to do."""

    def __init__(self, what, why=None, cause=None, options=(), untouched=None):
        super().__init__(what)
        self.what, self.why, self.cause, self.options, self.untouched = what, why, cause, list(options), untouched


class Console:
    def __init__(self, level='steps', out=None, ask=None, clock=time.monotonic):
        if level not in LEVELS:
            raise ValueError('level is one of %s' % ', '.join(LEVELS))
        self.level = level
        self.out = out or sys.stdout
        self.ask = ask or input
        self.clock = clock
        self.events = []
        self.total = 0
        self.index = 0
        self.started = clock()
        enc = (getattr(self.out, 'encoding', None) or 'utf-8').lower()
        self.ok = '✓' if 'utf' in enc else 'ok'
        self.bad = '✗' if 'utf' in enc else 'FAILED'

    # -- plumbing
    def at(self, level):
        """Whether text meant for LEVEL is shown (json shows events, not text)."""
        return self.level != 'json' and LEVELS.index(self.level) >= LEVELS.index(level)

    def print(self, text='', level='steps'):
        if self.at(level):
            print(text, file=self.out, flush=True)

    def event(self, ev, **fields):
        e = dict(ev=ev, t=round(self.clock() - self.started, 3), **fields)
        self.events.append(e)
        if self.level == 'json':
            print(json.dumps(e, default=str), file=self.out, flush=True)
        return e

    # -- the shapes the mockups use
    def banner(self, text, **fields):
        self.event('run.start', text=text, **fields)
        self.print(text)

    def steps(self, n):
        self.total, self.index = n, 0

    def step(self, title, note=None, **fields):
        self.index += 1
        self.event('step.start', step=title, index=self.index, of=self.total, **fields)
        if self.level == 'quiet':
            self.print('%s...' % title, 'quiet')
        else:
            head = '[%d/%d] %s' % (self.index, self.total, title) if self.total else title
            self.print(head + ('  ' + note if note else ''))

    def item(self, text, level='steps', ok=None, **fields):
        """One line under a step; OK True adds the check mark. Kept as an event when FIELDS are given."""
        if fields:
            self.event(fields.pop('ev', 'item'), text=text, ok=ok, **fields)
        self.print('  ' + text + (('  ' + self.ok) if ok else ''), level)

    def detail(self, text, level='edits'):
        self.print('            ' + text, level)

    def note(self, text, **fields):
        self.event('note', text=text, **fields)
        self.print('  note  ' + text, 'steps')

    def warning(self, text):
        self.event('warning', text=text)
        if self.level != 'json':
            print('warning: ' + text, file=sys.stderr, flush=True)

    def confirm(self, question, word, given=None):
        """Ask QUESTION and require WORD typed (or GIVEN, from a flag). False when it does not match."""
        self.event('confirm', question=question, word=word, given=given is not None)
        if self.level != 'json' or given is None:
            print(question, file=self.out, flush=True)
        answer = given if given is not None else self.ask('Type %s to continue: ' % word)
        ok = answer.strip() == word
        self.event('confirmed' if ok else 'declined')
        return ok

    def done(self, text, **fields):
        self.event('run.end', ok=True, text=text, seconds=round(self.clock() - self.started, 1), **fields)
        self.print('%s %s' % (self.ok, text), 'quiet')

    def stopped(self, x):
        self.event('run.end', ok=False, what=x.what, why=x.why, cause=x.cause, options=x.options)
        if self.level == 'json':
            return
        w = sys.stderr
        print('\nSTOPPED %s' % (x.untouched or ''), file=w)
        for label, text in (('What happened', x.what), ('Why it matters', x.why), ('Likely cause', x.cause)):
            if text:
                print('  %-17s%s' % (label, text), file=w)
        if x.options:
            print('\n  What you can do', file=w)
            for i, o in enumerate(x.options, 1):
                print('    %d  %s' % (i, o), file=w)
        w.flush()


def human_size(n):
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1000 or unit == 'TB':
            return ('%d %s' if unit == 'B' else '%.1f %s') % (n, unit)
        n /= 1000.0
