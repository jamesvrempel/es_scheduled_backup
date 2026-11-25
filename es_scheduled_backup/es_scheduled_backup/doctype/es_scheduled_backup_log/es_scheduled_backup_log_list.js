frappe.listview_settings["ES Scheduled Backup Log"] = {
	get_indicator(doc) {
		const colours = { Success: "green", Failed: "red", Running: "orange" };
		return [__(doc.status), colours[doc.status] || "gray", "status,=," + doc.status];
	},
};
