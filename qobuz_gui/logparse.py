"""Turns qobuz-dl's terminal output (ANSI colours, tqdm bars, y/n prompts) into GUI events."""

import html
import re

ESC_RE = re.compile(r"\x1b\[([0-9;?]*)([A-Za-z])")
PROGRESS_RE = re.compile(r"(\d{1,3})%\|")
PROMPT_RE = re.compile(r"\[\s*y\s*/\s*n\s*\]\s*:?\s*$", re.IGNORECASE)

COLORS = {
    31: "#e5534b", 32: "#3fa65a", 33: "#c99a2e", 34: "#4a8fe7",
    35: "#b55fd1", 36: "#2aa7b8", 90: "#8a8a8a",
    91: "#e5534b", 92: "#3fa65a", 93: "#c99a2e", 94: "#4a8fe7",
    95: "#b55fd1", 96: "#2aa7b8",
}


def plain(text):
    return ESC_RE.sub("", text)


def to_html(text):
    """Converts one line with ANSI SGR codes to HTML spans.

    qobuz-dl uses colorama's Style.DIM as its "colour off" marker, so code 2 is
    treated like a reset rather than as dim text.
    """
    out = []
    color = None
    bold = False
    pos = 0

    def open_span():
        styles = []
        if color:
            styles.append(f"color:{color}")
        if bold:
            styles.append("font-weight:600")
        return f'<span style="{";".join(styles)}">' if styles else ""

    current_open = ""
    for m in ESC_RE.finditer(text):
        chunk = text[pos:m.start()]
        if chunk:
            out.append(html.escape(chunk))
        pos = m.end()
        if m.group(2) != "m":
            continue  # cursor movement / line clearing: nothing to draw
        codes = [int(c) for c in m.group(1).split(";") if c.isdigit()] or [0]
        for code in codes:
            if code in (0, 2, 22, 39):
                color = None
                if code in (0, 22):
                    bold = False
            elif code == 1:
                bold = True
            elif code in COLORS:
                color = COLORS[code]
        if current_open:
            out.append("</span>")
        current_open = open_span()
        out.append(current_open)
    tail = text[pos:]
    if tail:
        out.append(html.escape(tail))
    if current_open:
        out.append("</span>")
    return "".join(out).replace("  ", "&nbsp; ")


def _last_visible(segments):
    for seg in reversed(segments):
        if plain(seg).strip():
            return seg
    return ""


class OutputParser:
    """Feed raw process output in; get a list of events back.

    Events:
      ("line", html, plain_text)   a finished log line
      ("progress", percent, label) a tqdm bar update
      ("prompt", plain_text)       the process is waiting for a y/n answer
    """

    def __init__(self):
        self.buffer = ""
        self._prompted = None
        self._last_progress = None

    def feed(self, data):
        events = []
        self.buffer += data.replace("\r\n", "\n")
        *lines, self.buffer = self.buffer.split("\n")
        for raw in lines:
            segments = raw.split("\r")
            for seg in segments[:-1]:
                events.extend(self._progress_event(seg))
            final = _last_visible(segments)
            text = plain(final)
            if PROGRESS_RE.search(text):
                events.extend(self._progress_event(final))
                continue
            if not text.strip() and raw.strip("\r"):
                continue  # a line that only cleared a progress bar
            events.append(("line", to_html(final), text))
        self._prompted = None if lines else self._prompted

        if self.buffer:
            segments = self.buffer.split("\r")
            for seg in segments[:-1]:
                events.extend(self._progress_event(seg))
            self.buffer = segments[-1]
            text = plain(self.buffer)
            if PROGRESS_RE.search(text):
                events.extend(self._progress_event(self.buffer))
            elif PROMPT_RE.search(text) and text != self._prompted:
                self._prompted = text
                events.append(("prompt", text.strip()))

        # A bar segment can be seen twice (as the unfinished tail, then again when the
        # next \r arrives); drop the repeat.
        deduped = []
        for ev in events:
            if ev[0] == "progress":
                if ev == self._last_progress:
                    continue
                self._last_progress = ev
            deduped.append(ev)
        return deduped

    def answered(self):
        """Call after writing an answer: the prompt text becomes a normal log line."""
        events = []
        if self.buffer:
            events.append(("line", to_html(self.buffer), plain(self.buffer)))
        self.buffer = ""
        self._prompted = None
        return events

    def flush(self):
        events = []
        if plain(self.buffer).strip():
            events.append(("line", to_html(self.buffer), plain(self.buffer)))
        self.buffer = ""
        return events

    @staticmethod
    def _progress_event(seg):
        text = plain(seg)
        m = PROGRESS_RE.search(text)
        if not m:
            return []
        label = text[:m.start()].strip().rstrip(":").strip()
        tail = text[m.end():]
        stats = tail.split("|", 1)[-1].strip() if "|" in tail else ""
        return [("progress", min(100, int(m.group(1))), f"{label} {stats}".strip())]
