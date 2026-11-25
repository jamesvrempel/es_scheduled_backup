frappe.ui.form.on("ES Scheduled Backup Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Test Connection"), () => {
			if (!save_first(frm)) return;
			frappe.call({
				method: "es_scheduled_backup.api.test_connection",
				freeze: true,
				freeze_message: __("Connecting to the destination..."),
			}).then((r) => {
				const result = r.message || {};
				frappe.msgprint({
					title: result.ok ? __("Connection works") : __("Connection problem"),
					indicator: result.ok ? "green" : "red",
					message: `<pre style="white-space: pre-wrap">${frappe.utils.escape_html(
						result.output || ""
					)}</pre>`,
				});
			});
		});

		frm.add_custom_button(__("Run Now"), () => {
			if (!save_first(frm)) return;
			frappe.confirm(
				__(
					"Back up this site now and restore it onto {0}? The copy there will be overwritten.",
					[frappe.utils.escape_html(frm.doc.destination_site || "")]
				),
				() => {
					frappe.call({ method: "es_scheduled_backup.api.run_now" }).then(() => {
						frappe.show_alert({
							message: __("Run started. Open the log to follow it."),
							indicator: "green",
						});
					});
				}
			);
		});

		frm.add_custom_button(__("View Log"), () => {
			frappe.set_route("List", "ES Scheduled Backup Log");
		});

		show_status(frm);
	},
});

function save_first(frm) {
	if (frm.is_dirty()) {
		frappe.msgprint(__("Save the settings first."));
		return false;
	}
	return true;
}

function show_status(frm) {
	if (frm.doc.source_site && frm.doc.source_site !== frappe.boot.sitename) {
		frm.dashboard.set_headline(
			__("This site is {0}. Runs only start on the source site, {1}.", [
				frappe.utils.escape_html(frappe.boot.sitename || ""),
				frappe.utils.escape_html(frm.doc.source_site),
			])
		);
		return;
	}
	if (!frm.doc.enabled) {
		frm.dashboard.set_headline(__("Scheduled runs are off. Run Now still works."));
		return;
	}
	frm.call("next_run").then((r) => {
		if (r.message) {
			frm.dashboard.set_headline(__("Next scheduled run: {0}", [r.message]));
		}
	});
}
