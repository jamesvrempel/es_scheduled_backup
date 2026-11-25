import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_datetime, now_datetime

from es_scheduled_backup import utils

SCHEDULE_FIELDS = ("enabled", "frequency", "hourly_minute", "nightly_time")
STATUS_FIELDS = ("last_slot", "last_run", "last_status", "last_duration", "last_backup_file")


def schedule_value(doc, field):
	"""A schedule field in a form that compares equal before and after a save."""
	value = doc.get(field)
	if field == "nightly_time":
		try:
			return utils.parse_time(value)
		except utils.ConfigError:
			return None
	if field == "frequency":
		return value or ""
	return int(value or 0)


class ESScheduledBackupSettings(Document):
	def validate(self):
		if not self.source_site:
			self.source_site = frappe.local.site
		self.keep_status_from_database()
		try:
			self.check_values()
		except utils.ConfigError as exc:
			frappe.throw(str(exc), title=_("Check the settings"))
		self.reset_slot_if_schedule_changed()

	def keep_status_from_database(self):
		"""The run status fields belong to the background job, not the form.

		A form left open during a run holds old values for them, so take the
		stored values instead of whatever the form sent.
		"""
		for field in STATUS_FIELDS:
			self.set(field, frappe.db.get_single_value(self.doctype, field))

	def check_values(self):
		self.source_site = utils.check_site(self.source_site, "Source site")
		self.destination_host = utils.check_host(self.destination_host)
		self.destination_site = utils.check_site(self.destination_site, "Destination site")
		self.ssh_user = utils.check_user(self.ssh_user)
		self.ssh_port = utils.check_port(self.ssh_port)
		self.ssh_key_path = utils.check_path(self.ssh_key_path, "SSH key path")
		self.destination_bench_path = utils.check_path(self.destination_bench_path, "Destination bench path")
		self.destination_incoming_file = utils.check_path(self.destination_incoming_file, "Incoming backup file")
		if self.restore_on_destination:
			self.restore_script = utils.check_path(self.restore_script, "Restore script")

		if self.frequency == "Hourly":
			self.hourly_minute = utils.check_minute(self.hourly_minute)
		elif self.frequency == "Nightly":
			utils.parse_time(self.nightly_time)
		else:
			raise utils.ConfigError("Frequency must be Hourly or Nightly.")

		if self.destination_site == self.source_site and self.destination_host in ("localhost", "127.0.0.1"):
			raise utils.ConfigError("The destination cannot be the source site itself.")
		if self.notify_on_failure and not self.notify_email:
			raise utils.ConfigError("Enter an email address for failure alerts.")

	def reset_slot_if_schedule_changed(self):
		"""Start counting from now when the schedule is switched on or changed.

		Without this, switching the schedule on would start a run straight away
		for the slot that has just passed.
		"""
		if not self.enabled:
			return
		before = self.get_doc_before_save()
		changed = before is None or any(
			schedule_value(before, field) != schedule_value(self, field) for field in SCHEDULE_FIELDS
		)
		if changed or not self.last_slot:
			self.last_slot = utils.latest_slot(
				now_datetime(), self.frequency, self.hourly_minute, self.nightly_time
			)

	@frappe.whitelist()
	def next_run(self):
		"""When the next scheduled run will start, for display on the form."""
		frappe.only_for("System Manager")
		if not self.enabled:
			return None
		try:
			upcoming = utils.next_slot(
				now_datetime(), self.frequency, self.hourly_minute, self.nightly_time
			)
		except utils.ConfigError:
			return None
		if self.last_slot and get_datetime(self.last_slot) < utils.latest_slot(
			now_datetime(), self.frequency, self.hourly_minute, self.nightly_time
		):
			return _("within 5 minutes")
		return frappe.utils.format_datetime(upcoming)
