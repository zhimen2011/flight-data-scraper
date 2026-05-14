"""Helpers for stable flight occurrence keys and CSV filename prefixes."""

from dataclasses import dataclass
import re


TIME_TOKEN_RE = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})(?:[ T](?P<h>\d{2}):?(?P<m>\d{2}):?(?P<s>\d{2}))?"
)


@dataclass(frozen=True)
class FlightKey:
    flight: str
    date: str
    time_token: str = ""
    suffix: str = ""

    @property
    def key(self):
        parts = [self.flight, self.date]
        if self.time_token:
            parts.append(self.time_token)
        if self.suffix:
            parts.append(self.suffix)
        return "_".join(part for part in parts if part)

    @property
    def date_key(self):
        parts = [self.date]
        if self.time_token:
            parts.append(self.time_token)
        if self.suffix:
            parts.append(self.suffix)
        return "_".join(part for part in parts if part)


def time_token_from_text(value):
    """Return HHMMSS from a timestamp-like value, or an empty string."""
    if not value:
        return ""
    text = str(value).strip()
    match = TIME_TOKEN_RE.search(text)
    if not match or not match.group("h"):
        return ""
    return f"{match.group('h')}{match.group('m')}{match.group('s')}"


def date_from_text(value, fallback=""):
    if not value:
        return fallback
    text = str(value).strip()
    match = TIME_TOKEN_RE.search(text)
    if match:
        return match.group("date")
    return fallback


def build_flight_key(flight, date, takeoff_time_or_token=""):
    """Build a stable key/prefix: FLIGHT_YYYY-MM-DD[_HHMMSS]."""
    flight = str(flight or "").strip().upper()
    date = date_from_text(date, str(date or "").strip())
    token = time_token_from_text(takeoff_time_or_token)
    if not token and re.fullmatch(r"\d{6}", str(takeoff_time_or_token or "").strip()):
        token = str(takeoff_time_or_token).strip()
    return f"{flight}_{date}_{token}" if token else f"{flight}_{date}"


def parse_flight_key(key):
    """Parse FLIGHT_YYYY-MM-DD[_HHMMSS][_suffix] while tolerating legacy keys."""
    parts = str(key or "").split("_")
    if len(parts) >= 2:
        flight = parts[0]
        date = parts[1]
        token = ""
        suffix = ""
        if len(parts) >= 3:
            if re.fullmatch(r"\d{6}", parts[2]):
                token = parts[2]
                suffix = "_".join(parts[3:])
            else:
                suffix = "_".join(parts[2:])
        return FlightKey(flight, date, token, suffix)
    return FlightKey(str(key or ""), "")


def display_datetime_from_key(key):
    parsed = parse_flight_key(key)
    if not parsed.date:
        return str(key or "")
    if not parsed.time_token:
        return parsed.date
    token = parsed.time_token
    display = f"{parsed.date} {token[0:2]}:{token[2:4]}:{token[4:6]}"
    return f"{display} {parsed.suffix}" if parsed.suffix else display


def split_flight_key(key):
    """Return (flight, date_key) from an analysis key or CSV prefix."""
    parsed = parse_flight_key(key)
    return parsed.flight, parsed.date_key
