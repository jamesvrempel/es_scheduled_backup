"""Pure helpers for ES Scheduled Backup.

Nothing in this module imports frappe, so it can be unit-tested on its own.
"""

import re
import shlex
from datetime import datetime, time, timedelta

HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.\-]*[A-Za-z0-9])?$")
USER_RE = re.compile(r"^[a-z_][a-z0-9_\-]*$")
SITE_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.\-]*[A-Za-z0-9])?$")
PATH_RE = re.compile(r"^/[A-Za-z0-9._/\-]+$")

FREQUENCIES = ("Hourly", "Nightly")


class ConfigError(ValueError):
	"""Raised when a setting has a value that is not safe to use in a command."""


def check_host(value):
	value = (value or "").strip()
	if not HOST_RE.match(value):
		raise ConfigError(f"Destination host {value!r} must be a hostname or IP address.")
	return value


def check_user(value):
	value = (value or "").strip()
	if not USER_RE.match(value):
		raise ConfigError(f"SSH user {value!r} is not a valid Linux user name.")
	return value


def check_site(value, label="Site"):
	value = (value or "").strip()
	if not SITE_RE.match(value):
		raise ConfigError(f"{label} {value!r} is not a valid site name.")
	return value


def check_path(value, label="Path"):
	value = (value or "").strip()
	if not PATH_RE.match(value) or ".." in value.split("/"):
		raise ConfigError(
			f"{label} {value!r} must be an absolute path using only letters, digits, . _ - and /."
		)
	return value


def check_port(value):
	try:
		port = int(value or 22)
	except (TypeError, ValueError):
		raise ConfigError(f"SSH port {value!r} is not a number.") from None
	if not 1 <= port <= 65535:
		raise ConfigError(f"SSH port {port} is out of range.")
	return port


def check_minute(value):
	try:
		minute = int(value or 0)
	except (TypeError, ValueError):
		raise ConfigError(f"Minute {value!r} is not a number.") from None
	if not 0 <= minute <= 59:
		raise ConfigError("Minute past the hour must be between 0 and 59.")
	return minute


def parse_time(value):
	"""Return a datetime.time from a Time field value.

	Frappe hands Time fields back as a timedelta from the database and as a
	string from a form, so accept both, and a time object too.
	"""
	if isinstance(value, time):
		return value.replace(microsecond=0)
	if isinstance(value, timedelta):
		seconds = int(value.total_seconds()) % 86400
		return time(seconds // 3600, (seconds % 3600) // 60, seconds % 60)
	if isinstance(value, str) and value.strip():
		parts = value.strip().split(".")[0].split(":")
		try:
			numbers = [int(p) for p in parts]
		except ValueError:
			raise ConfigError(f"Time {value!r} is not in HH:MM:SS form.") from None
		while len(numbers) < 3:
			numbers.append(0)
		hour, minute, second = numbers[:3]
		if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
			raise ConfigError(f"Time {value!r} is out of range.")
		return time(hour, minute, second)
	raise ConfigError("A run time is required for a nightly schedule.")


def latest_slot(now, frequency, hourly_minute=0, nightly_time=None):
	"""Return the most recent scheduled moment at or before `now`."""
	if frequency == "Hourly":
		slot = now.replace(minute=check_minute(hourly_minute), second=0, microsecond=0)
		if slot > now:
			slot -= timedelta(hours=1)
		return slot
	if frequency == "Nightly":
		at = parse_time(nightly_time)
		slot = now.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
		if slot > now:
			slot -= timedelta(days=1)
		return slot
	raise ConfigError(f"Frequency {frequency!r} must be one of {', '.join(FREQUENCIES)}.")


def next_slot(now, frequency, hourly_minute=0, nightly_time=None):
	"""Return the next scheduled moment strictly after `now`."""
	step = timedelta(hours=1) if frequency == "Hourly" else timedelta(days=1)
	return latest_slot(now, frequency, hourly_minute, nightly_time) + step


def is_due(now, last_slot, frequency, hourly_minute=0, nightly_time=None):
	"""True when a scheduled moment has passed that has not been run yet."""
	slot = latest_slot(now, frequency, hourly_minute, nightly_time)
	if last_slot is None:
		return True
	if isinstance(last_slot, str):
		last_slot = datetime.fromisoformat(last_slot.split(".")[0])
	return last_slot < slot


def ssh_command(key_path, port):
	"""The ssh invocation as a list, without the destination."""
	return [
		"ssh",
		"-i",
		check_path(key_path, "SSH key path"),
		"-p",
		str(check_port(port)),
		"-o",
		"BatchMode=yes",
		"-o",
		"StrictHostKeyChecking=accept-new",
		"-o",
		"ConnectTimeout=20",
	]


def remote(user, host):
	return f"{check_user(user)}@{check_host(host)}"


def rsync_command(source, dest_user, dest_host, dest_path, key_path, port, delete=False):
	"""rsync `source` to `dest_path` on the destination over ssh.

	A trailing slash on `source` means "the contents of this folder".
	"""
	cmd = ["rsync", "-a", "-e", shlex.join(ssh_command(key_path, port))]
	if delete:
		cmd.append("--delete")
	cmd.append(source)
	cmd.append(f"{remote(dest_user, dest_host)}:{dest_path}")
	return cmd


def remote_command(dest_user, dest_host, key_path, port, script):
	"""Run one shell snippet on the destination."""
	return [*ssh_command(key_path, port), remote(dest_user, dest_host), script]


def site_files_path(bench_path, site, visibility):
	"""Folder of a site's uploaded files on the destination, with a trailing slash."""
	if visibility not in ("public", "private"):
		raise ConfigError("visibility must be public or private")
	bench_path = check_path(bench_path, "Destination bench path").rstrip("/")
	return f"{bench_path}/sites/{check_site(site, 'Destination site')}/{visibility}/files/"


def connection_test_script(bench_path, site, incoming_file, restore_script, check_restore=True):
	"""Shell snippet that reports what is and is not in place on the destination."""
	bench_path = check_path(bench_path, "Destination bench path").rstrip("/")
	site_dir = f"{bench_path}/sites/{check_site(site, 'Destination site')}"
	incoming_dir = check_path(incoming_file, "Incoming backup file").rsplit("/", 1)[0] or "/"
	lines = [
		'echo "Connected to: $(hostname)"',
		_exists_line("-d", site_dir, "Site folder"),
		_exists_line("-d", incoming_dir, "Incoming folder"),
	]
	if check_restore:
		lines.append(_exists_line("-x", check_path(restore_script, "Restore script"), "Restore script"))
	return "; ".join(lines)


def _exists_line(flag, path, label):
	quoted = shlex.quote(path)
	return (
		f'if [ {flag} {quoted} ]; then echo "OK      {label}: {path}"; '
		f'else echo "MISSING {label}: {path}"; fi'
	)


def format_duration(seconds):
	seconds = int(round(seconds or 0))
	if seconds < 60:
		return f"{seconds} s"
	minutes, rest = divmod(seconds, 60)
	if minutes < 60:
		return f"{minutes} min {rest} s"
	hours, minutes = divmod(minutes, 60)
	return f"{hours} h {minutes} min"


def tail(text, limit=8000):
	"""Keep the end of long command output so a log row stays a sensible size."""
	text = text or ""
	if len(text) <= limit:
		return text
	return "... (earlier output trimmed)\n" + text[-limit:]
