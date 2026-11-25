app_name = "es_scheduled_backup"
app_title = "ES Scheduled Backup"
app_publisher = "Enterprise Systems AU"
app_description = "Scheduled backup of a site, pushed to a second server and restored there as a working copy."
app_email = "accounts@enterprisesystems.com.au"
app_license = "mit"

add_to_apps_screen = [
	{
		"name": "es_scheduled_backup",
		"logo": "/assets/es_scheduled_backup/images/logo.svg",
		"title": "ES Scheduled Backup",
		"route": "/app/es-scheduled-backup-settings",
		"has_permission": "es_scheduled_backup.api.has_app_permission",
	}
]

scheduler_events = {
	"cron": {
		# Checks whether a run is due. The schedule itself is set on the settings page.
		"*/5 * * * *": ["es_scheduled_backup.runner.run_if_due"],
	},
	"daily": ["es_scheduled_backup.runner.clear_old_logs"],
}
