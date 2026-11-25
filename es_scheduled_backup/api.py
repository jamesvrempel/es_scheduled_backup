"""Buttons on the settings page call these."""

import subprocess

import frappe
from frappe import _

from es_scheduled_backup import runner, utils


def has_app_permission():
	return "System Manager" in frappe.get_roles()


@frappe.whitelist()
def run_now():
	frappe.only_for("System Manager")
	settings = runner.get_settings()
	if not runner.is_source_site(settings):
		frappe.throw(
			_("This site is {0}, but the source site in the settings is {1}. Runs only start on the source site.").format(
				frappe.bold(frappe.local.site), frappe.bold(settings.source_site or "-")
			)
		)
	runner.close_stale_runs()
	if runner.run_in_progress():
		frappe.throw(_("A run is already in progress. Check the log."))
	runner.enqueue_run("Manual")
	return {"queued": True}


@frappe.whitelist()
def test_connection():
	"""Log in to the destination and report what is in place there."""
	frappe.only_for("System Manager")
	settings = runner.get_settings()
	try:
		script = utils.connection_test_script(
			settings.destination_bench_path,
			settings.destination_site,
			settings.destination_incoming_file,
			settings.restore_script,
			check_restore=bool(settings.restore_on_destination),
		)
		cmd = utils.remote_command(
			settings.ssh_user,
			settings.destination_host,
			settings.ssh_key_path,
			settings.ssh_port,
			script,
		)
	except utils.ConfigError as exc:
		return {"ok": False, "output": str(exc)}

	try:
		result = subprocess.run(cmd, capture_output=True, text=True, timeout=45, check=False)
	except subprocess.TimeoutExpired:
		return {"ok": False, "output": _("No answer from the destination after 45 seconds.")}
	except FileNotFoundError:
		return {"ok": False, "output": _("ssh is not installed on this server.")}

	output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part and part.strip())
	ok = result.returncode == 0 and "MISSING" not in output
	return {"ok": ok, "output": output or _("No output (exit code {0}).").format(result.returncode)}
