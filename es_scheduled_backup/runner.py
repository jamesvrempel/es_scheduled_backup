"""Runs the scheduled backup: back up, push to the destination, restore there."""

import os
import subprocess
import time

import frappe
from frappe.utils import add_days, add_to_date, get_datetime, now_datetime

from es_scheduled_backup import utils

SETTINGS = "ES Scheduled Backup Settings"
LOG = "ES Scheduled Backup Log"

# One step (a backup copy, a file sync or the restore) may take this long.
STEP_TIMEOUT = 2 * 60 * 60
# The whole background job may take this long.
JOB_TIMEOUT = 5 * 60 * 60
# A run still marked Running after this long is treated as dead.
STALE_AFTER_HOURS = 6
JOB_ID = "es_scheduled_backup_run"


class StepFailed(Exception):
	pass


def get_settings():
	return frappe.get_single(SETTINGS)


def is_source_site(settings):
	"""Runs only happen on the site named in the settings.

	A restored copy carries these settings in its database. Its own site name
	is different, so it never pushes a backup anywhere.
	"""
	return bool(settings.source_site) and settings.source_site.strip() == frappe.local.site


def run_in_progress():
	cutoff = add_to_date(now_datetime(), hours=-STALE_AFTER_HOURS)
	return frappe.db.exists(LOG, {"status": "Running", "started": (">", cutoff)})


def close_stale_runs():
	cutoff = add_to_date(now_datetime(), hours=-STALE_AFTER_HOURS)
	for name in frappe.get_all(LOG, filters={"status": "Running", "started": ("<=", cutoff)}, pluck="name"):
		frappe.db.set_value(
			LOG,
			name,
			{"status": "Failed", "error": f"No result after {STALE_AFTER_HOURS} hours; marked as failed."},
		)


def run_if_due():
	"""Scheduler entry point, called every five minutes."""
	settings = get_settings()
	if not settings.enabled or not is_source_site(settings):
		return

	now = now_datetime()
	try:
		slot = utils.latest_slot(now, settings.frequency, settings.hourly_minute, settings.nightly_time)
	except utils.ConfigError:
		frappe.log_error(title="ES Scheduled Backup: schedule is not valid")
		return

	last_slot = get_datetime(settings.last_slot) if settings.last_slot else None
	if last_slot and last_slot >= slot:
		return

	# Record the slot first, so a slow or failing run is not retried every five minutes.
	frappe.db.set_single_value(SETTINGS, "last_slot", slot, update_modified=False)
	frappe.db.commit()
	enqueue_run("Scheduled")


def enqueue_run(trigger):
	kwargs = {"queue": "long", "timeout": JOB_TIMEOUT, "trigger": trigger}
	try:
		frappe.enqueue("es_scheduled_backup.runner.run_backup", job_id=JOB_ID, deduplicate=True, **kwargs)
	except TypeError:
		# Older Frappe without job de-duplication.
		frappe.enqueue("es_scheduled_backup.runner.run_backup", **kwargs)


def run_backup(trigger="Scheduled"):
	"""Do one full run. Called from a background worker."""
	settings = get_settings()
	if not is_source_site(settings):
		return
	close_stale_runs()
	if run_in_progress():
		return

	started = now_datetime()
	clock = time.monotonic()
	steps = []
	backup_file = None
	log = None

	try:
		# The backup is taken before the log row is written, so the copy's
		# restored database does not hold a row stuck at "Running".
		backup_file = take_backup()
		steps.append(f"== Backup ==\n{backup_file}")
		log = start_log(started, trigger, settings, backup_file)

		steps.append(push_backup(settings, backup_file))
		if settings.sync_public_files:
			steps.append(sync_files(settings, "public"))
		if settings.sync_private_files:
			steps.append(sync_files(settings, "private"))
		if settings.restore_on_destination:
			steps.append(restore(settings))

		finish(log, settings, "Success", started, clock, steps, backup_file)
	except Exception as exc:
		frappe.db.rollback()
		if log is None:
			log = start_log(started, trigger, settings, backup_file)
		error = str(exc) if isinstance(exc, (StepFailed, utils.ConfigError)) else frappe.get_traceback()
		finish(log, settings, "Failed", started, clock, steps, backup_file, error=error)
		notify_failure(settings, log, error)


def take_backup():
	"""Database-only backup of this site. Returns the absolute file path."""
	from frappe.utils.backups import new_backup

	backup = new_backup(ignore_files=True, force=True)
	path = os.path.abspath(backup.backup_path_db)
	if not os.path.isfile(path):
		raise StepFailed(f"Backup reported {path} but the file is not there.")
	return path


def push_backup(settings, backup_file):
	cmd = utils.rsync_command(
		backup_file,
		settings.ssh_user,
		settings.destination_host,
		utils.check_path(settings.destination_incoming_file, "Incoming backup file"),
		settings.ssh_key_path,
		settings.ssh_port,
	)
	return run_step("Copy backup to destination", cmd)


def sync_files(settings, visibility):
	source = os.path.abspath(frappe.get_site_path(visibility, "files")) + "/"
	cmd = utils.rsync_command(
		source,
		settings.ssh_user,
		settings.destination_host,
		utils.site_files_path(settings.destination_bench_path, settings.destination_site, visibility),
		settings.ssh_key_path,
		settings.ssh_port,
		delete=bool(settings.delete_removed_files),
	)
	return run_step(f"Sync {visibility} files", cmd)


def restore(settings):
	cmd = utils.remote_command(
		settings.ssh_user,
		settings.destination_host,
		settings.ssh_key_path,
		settings.ssh_port,
		utils.check_path(settings.restore_script, "Restore script"),
	)
	return run_step("Restore on destination", cmd)


def run_step(title, cmd):
	try:
		result = subprocess.run(cmd, capture_output=True, text=True, timeout=STEP_TIMEOUT, check=False)
	except subprocess.TimeoutExpired:
		raise StepFailed(f"{title}: no result after {STEP_TIMEOUT // 60} minutes.") from None
	except FileNotFoundError as exc:
		raise StepFailed(f"{title}: {exc.filename} is not installed on this server.") from None

	output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part and part.strip())
	if result.returncode != 0:
		raise StepFailed(f"{title} failed (exit code {result.returncode}).\n{utils.tail(output, 4000)}")
	return f"== {title} ==\n{utils.tail(output, 3000) or 'done'}"


def start_log(started, trigger, settings, backup_file):
	log = frappe.get_doc(
		{
			"doctype": LOG,
			"status": "Running",
			"trigger": trigger,
			"started": started,
			"destination": f"{settings.destination_host} / {settings.destination_site}",
			"backup_file": os.path.basename(backup_file) if backup_file else None,
		}
	)
	log.insert(ignore_permissions=True)
	frappe.db.commit()
	return log


def finish(log, settings, status, started, clock, steps, backup_file, error=None):
	seconds = time.monotonic() - clock
	finished = now_datetime()
	frappe.db.set_value(
		LOG,
		log.name,
		{
			"status": status,
			"finished": finished,
			"duration": utils.format_duration(seconds),
			"output": utils.tail("\n\n".join(steps), 20000),
			"error": utils.tail(error, 8000) if error else None,
		},
	)
	status_fields = {
		"last_run": started,
		"last_status": status,
		"last_duration": utils.format_duration(seconds),
		"last_backup_file": os.path.basename(backup_file) if backup_file else None,
	}
	for field, value in status_fields.items():
		# update_modified=False so a settings form left open can still be saved.
		frappe.db.set_single_value(SETTINGS, field, value, update_modified=False)
	frappe.db.commit()


def notify_failure(settings, log, error):
	if not (settings.notify_on_failure and settings.notify_email):
		return
	try:
		frappe.sendmail(
			recipients=[settings.notify_email],
			subject=f"Scheduled backup to {settings.destination_host} failed",
			message=(
				f"<p>The scheduled backup from <b>{frappe.local.site}</b> to "
				f"<b>{frappe.utils.escape_html(settings.destination_host)}</b> failed.</p>"
				f"<pre>{frappe.utils.escape_html(utils.tail(error, 3000))}</pre>"
				f"<p>Log: {frappe.utils.get_url_to_form(LOG, log.name)}</p>"
			),
		)
		frappe.db.commit()
	except Exception:
		frappe.log_error(title="ES Scheduled Backup: could not send failure email")


def clear_old_logs():
	"""Daily scheduler entry point."""
	settings = get_settings()
	days = int(settings.keep_logs_days or 30)
	if days <= 0:
		return
	frappe.db.delete(LOG, {"creation": ("<", add_days(now_datetime(), -days))})
